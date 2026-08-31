# 设计决策记录

本文档记录阶段 0 中已经确认的设计决策，并补充需要结合参考项目分析的内容。

关联文档：

- [requirements.md](./requirements.md)
- [task-breakdown.md](./task-breakdown.md)
- [design.md](./design.md)

## 1. 决策总览

| ID | 决策点 | 结论 |
| --- | --- | --- |
| D1 | 是否保留 Chat Completions | 保留，并支持 `chat`、`responses`、`messages`、`all` 协议能力 |
| D2 | SSE 对外格式 | 沿用参考项目的原始 SSE 透明转发 |
| D3 | 重试次数语义 | 首次请求 + 最多 3 次重试，总计最多 4 次请求 |
| D4 | 跨协议 fallback | 不允许 |
| D5 | 限流维度 | 按公开模型别名独立限流 |
| D6 | Anthropic 结构化输出 | 系统提示注入 Schema + 本地校验修复 |
| D7 | Anthropic Prompt 位置 | 渲染后写入 `system` 字段 |
| D8 | `/v1/models` 鉴权 | 沿用参考项目，要求 Bearer Key 鉴权 |

## 2. D1：保留 Chat Completions 与协议能力配置

结论：

- 保留 `/v1/chat/completions` 入口。
- 协议能力不再使用 `both`，改为：
  - `chat`
  - `responses`
  - `messages`
  - `all`
- 适配器扩展为：
  - `OpenAIChatAdapter`
  - `OpenAIResponsesAdapter`
  - `AnthropicMessagesAdapter`

影响：

- 对外 API 面同时支持 Chat Completions、Responses 和 Messages。
- 配置和路由过滤需要支持三种协议能力。
- `all` 表示供应商自身支持的协议能力；OpenAI 的 `all` 指 `chat` 和 `responses`，Anthropic 的 `all` 当前指 `messages`。

## 3. D2：SSE 处理方式

参考项目中的处理方式：

- `UpstreamClient.open_stream` 使用 `Accept: text/event-stream` 打开上游流。
- `GatewayService.stream` 通过 `response.aiter_bytes()` 读取上游原始字节。
- 网关对下游执行 `yield chunk`，即透明转发上游原始 SSE 数据块。
- `_collect_stream_data` 只在内部解析 `data:` 行，用于提取文本增量、usage 和 checkpoint，不修改向下游发送的数据。

结论：

- 继续采用参考项目的原始 SSE 透明转发。
- 各协议入口返回各协议原生 SSE 事件格式。
- 适配器内部解析 `UnifiedStreamEvent`，仅用于 TTFT、usage、checkpoint 和错误检测。

这样能最大程度保持客户端兼容性，并减少流式协议转换带来的复杂度。

## 4. D3：重试次数语义

结论：

- “最多 3 次重试”表示首次请求之后最多再发起 3 次重试。
- 单次模型调用最多发生 4 次上游请求。
- 配置字段建议使用 `retry.max_retries: 3`。
- 重试间隔采用指数退避，并保留随机抖动。

## 5. D4：不允许跨协议 fallback

结论：

- 模型别名中的候选路由只能服务当前入口协议。
- `ModelRouter.candidates(model, protocol)` 必须按入口协议过滤路由。
- 不在 Chat Completions、Responses 和 Messages 之间跨协议切换模型。
- 首选供应商失败后，只允许在同协议的供应商之间 fallback。

## 6. D5：按模型独立限流

结论：

- 限流器以公开模型别名为 key。
- 每个模型使用独立令牌桶，互不影响。
- 某个模型超限只影响该模型，不影响其他模型。
- 超限返回 HTTP `429` 和 `Retry-After`。

后续如需要按调用方隔离，可在不破坏接口的前提下扩展为“模型 + API Key 指纹”维度。

## 7. D6：Anthropic 结构化输出

结论：

- Anthropic Messages API 无与 OpenAI 完全一致的 `response_format` 语义。
- 初始实现通过系统提示注入 JSON Schema 和“仅返回合法 JSON”约束。
- 模型返回后仍使用本地 `jsonschema` 二次校验。
- 校验失败时构造修复消息并重试，重试次数由 `structured_output_retries` 控制。
- 当前阶段明确采用上述简化方案，不升级为 Anthropic tool schema。

tool schema 仅作为后续可选扩展，不在本次实现范围内。

### 7.1 升级到 tool schema 的主要卡点

以下内容仅为后续扩展评估，不代表当前阶段范围。如果后续将 D6 升级为 Anthropic tool schema，需要重点处理以下问题：

- **Schema 子集限制**：Anthropic 对 `input_schema` 的限制比标准 JSON Schema 更严格，`$ref`、`$defs`、`patternProperties`、递归、根级 `oneOf`/`anyOf`/`allOf` 等可能被拒绝；对象通常需要 `additionalProperties: false`，部分长度、数值和字符串 format 约束也需要降级。网关必须先做 Schema 归一化、预校验和可读的错误提示。
- **输出形态变化**：tool schema 返回的是 `content` 中的 `tool_use` 和 `input` 字段，而不是普通 `content_text`。当前 `UnifiedResponse` 需要扩展 `tool_use_id`、`tool_name`、`tool_input` 等字段。
- **流式解析变化**：流式返回通过 `content_block_start` 和 `input_json_delta` 的 `partial_json` 增量生成 JSON，不能继续按文本 delta 处理，需要维护增量 JSON 缓冲并支持不完整 JSON 容错。
- **强制调用不保证**：设置 `tool_choice` 后模型仍可能返回文本、不调用工具、调用错误工具或返回多个 `tool_use`。网关需要检测这些情况并决定重试或报错。
- **修复重试更复杂**：校验失败后不能简单追加普通 assistant/user 文本，而需要追加 assistant 的 `tool_use` 和 user 的 `tool_result`，并保持 `tool_use_id` 匹配，否则 Anthropic 会返回 400。
- **适配器抽象需要扩展**：`apply_structured_output` 需要构造 `tools` 和 `tool_choice`，`parse_response`、`parse_stream_line` 需要识别工具调用事件。OpenAI Responses 与 Anthropic 的结构化输出策略会进一步分化。
- **模型能力差异**：不同 Anthropic 模型对 strict tool use 和 Schema 子集支持可能不同，需要按模型声明能力并在不支持时自动回退到系统提示注入方案。
- **可观测性适配**：工具调用和结构化输出可能影响 input/output token 统计，`NormalizedUsage` 需要继续准确映射 usage 字段。

建议升级顺序：

1. 先实现 Anthropic Schema 归一化与预校验。
2. 扩展统一响应和流式事件模型，支持 `tool_use`/`tool_result`。
3. 实现增量 JSON 解析和 TTFT 采集。
4. 实现基于 `tool_result` 的结构化修复循环。
5. 增加按模型能力的测试矩阵和自动回退策略。

## 8. D7：Anthropic Prompt 位置

结论：

- 网关渲染后的系统 Prompt 写入 Anthropic Messages API 的 `system` 字段。
- 不把系统 Prompt 混入 `messages` 数组中的 `system` 角色。
- 保持 Anthropic Messages API 的原生语义。

## 9. D8：`/v1/models` 鉴权

参考项目中已有相关实现：

- `app/core/security.py` 提供 `authenticate` 依赖。
- `app/api/routes.py` 中模型列表、Prompt 管理、管理接口均通过 `Identity` 依赖进行鉴权。
- 只有 `/healthz` 和 `/readyz` 不要求鉴权。

结论：

- `/v1/models` 要求 Bearer API Key 鉴权。
- 行为与参考项目保持一致。
- `/healthz` 和 `/readyz` 继续不鉴权。
