# 需求拆解与实施计划

## 1. 文档目的

本文档基于 [requirements.md](./requirements.md) 中的整体需求，结合参考项目现有实现，将工程目标拆解为可执行、可验证的实施阶段。

参考项目：

```text
/Users/app_dev/code/ai_agent/ai_agent_training/ai-agent-fullstack-training/course_code/week01/1-7/llm-gateway
```

参考项目本质上是一个 OpenAI-compatible 多供应商网关。其配置加载、错误封装、路由重试、结构化校验、Prompt 版本、用量账本均可复用；但当前需求新增了 Anthropic Messages API 适配器，并且“按模型独立限流、最多 3 次重试、TTFT”等要求需要调整，不应整目录复制。

## 2. 需求范围

本工程需要支持：

- OpenAI Responses API
- OpenAI Chat Completions API
- Anthropic Messages API

并实现：

- 统一抽象层与适配器模式
- 基于 `model` 字段的动态路由
- SSE 流式输出
- 结构化输出
- Prompt 模板版本管理
- Token、延迟、TTFT 可观测性
- 统一错误码、指数退避重试、按模型独立限流

## 3. 参考项目复用判断

| 参考模块 | 当前需求下的处理方式 |
| --- | --- |
| `app/config.py` | 基本复用，补充 `protocol`/`api` 能力声明、按模型限流配置 |
| `app/schemas.py` | 基本复用，新增统一请求/响应模型与 Messages API 入参 |
| `app/core/errors.py` | 基本复用，保持 OpenAI 风格统一错误对象 |
| `app/core/security.py` | 基本复用，继续使用 `SecretStr` + `hmac.compare_digest` |
| `app/core/rate_limit.py` | 需要重构，从全局限流改为按模型独立限流注册表 |
| `app/services/upstream.py` | 保留通用 HTTP 客户端职责，路由函数不直接创建 httpx 请求 |
| `app/services/router.py` | 复用路由、熔断、fallback 骨架，增加协议适配器选择 |
| `app/services/gateway.py` | 复用编排思路，但把 OpenAI 专属协议逻辑抽到 adapter 层 |
| `app/services/structured.py` | 复用 JSON 提取与 Schema 校验，补充 Anthropic 结构化策略 |
| `app/services/prompts.py` | 基本复用，继续使用 Jinja2 Sandbox 与 StrictUndefined |
| `app/services/usage.py` | 复用 SQLite 账本，扩展 TTFT 与 Anthropic usage 归一化 |
| `tests/` | 复用 `MockTransport` 思路，新增适配器与按模型限流测试 |

新增的协议适配器建议独立组织：

```text
app/services/adapters/
├── base.py          # 统一适配器接口
├── openai_chat.py   # OpenAI Chat Completions API
├── openai_responses.py  # OpenAI Responses API
└── anthropic_messages.py  # Anthropic Messages API
```

## 4. 任务拆解

建议按依赖顺序拆成 8 个阶段，每个阶段都有可验证的产出。

### 阶段 0：设计定稿与 API 面决策

先确定协议契约，避免后续模块反复返工。

阶段 0 已确认的决策包括：

- 对外提供 `/v1/chat/completions`、`/v1/responses` 和 `/v1/messages`，内部复用统一 `GatewayService`。
- 路由协议能力使用 `chat`、`responses`、`messages`、`all`，不再使用 `both`。
- SSE 沿用参考项目的透明转发方式，网关不强制改写为统一事件格式。
- 重试语义为“首次请求 + 最多 3 次重试”，总计最多 4 次请求。
- 不允许跨协议 fallback。
- 按公开模型别名独立限流。
- Anthropic 结构化输出采用系统提示注入 Schema + 本地校验修复。
- Anthropic Prompt 渲染后写入 `system` 字段。
- `/v1/models` 与模型调用一致，要求 Bearer Key 鉴权。

### 阶段 1：工程脚手架与配置加载

实现项目骨架、依赖和配置基础能力。

产出：

- `requirements.txt`
- `gateway.example.yaml`
- `app/config.py`
- 基础 FastAPI 生命周期
- 环境变量 `${ENV_VAR}` 与 `${ENV_VAR:-default}` 展开
- 启动时校验 `models` 引用的 `provider` 已声明
- 每个模型路由声明 `protocol` 能力

配置示意：

```yaml
models:
  smart:
    strategy: priority
    routes:
      - provider: openai
        model: gpt-5.2
        api: all
      - provider: openai-backup
        model: gpt-5-mini
        api: responses
  claude-fast:
    strategy: priority
    routes:
      - provider: anthropic
        model: claude-sonnet-4-5
        api: messages
```

验收：

- 配置加载失败时有明确错误。
- 不同协议模型能正确解析。

### 阶段 2：统一抽象层与协议适配器

这是本次工程最核心的改动。

建议先定义统一适配器接口：

```python
class ProtocolAdapter:
    def build_request(...) -> UpstreamRequest
    def parse_response(...) -> UnifiedResponse
    def parse_stream_event(...) -> UnifiedStreamEvent | None
    def normalize_usage(...) -> NormalizedUsage
    def map_error(...) -> GatewayError
```

随后实现：

- `OpenAIChatAdapter`
- `OpenAIResponsesAdapter`
- `AnthropicMessagesAdapter`

需要封装的内容包括：

- 上游路径与鉴权头
- 请求体结构转换
- 响应正文结构转换
- SSE 事件解析
- 错误对象归一化
- usage 字段归一化

`ModelRouter` 负责根据 `model` 字段找到 `provider`、`upstream_model` 和对应 adapter。`UpstreamClient` 仍只负责通用 HTTP 请求与流式打开，不在路由函数里直接拼 httpx 请求。

验收：

- 同一个业务请求传入 Chat、Responses 或 Messages 模型时，能分别走到正确 adapter，并生成正确上游请求。

### 阶段 3：非流式调用、重试与 fallback

在 `GatewayService` 中打通非流式调用链：

- 模型别名解析
- adapter 请求构造
- 上游调用
- 错误分类
- 指数退避重试
- 首选供应商失败后的 fallback
- 可重试状态码与网络错误、超时的处理

与当前需求强相关：

- 重试最多 3 次，指数退避并加抖动；即首次请求加最多 3 次重试，总计最多 4 次请求。
- 普通 4xx 不重试。
- 重试和 fallback 次数要能进入用量记录。
- 继续保留参考项目中的进程内熔断器，但保持接口独立。

验收：

- `MockTransport` 模拟“首供应商 500、次供应商成功”，断言重试次数和 fallback 次数正确。

### 阶段 4：SSE 流式输出

实现 `stream=true` 的流式调用：

- 上游流式请求
- SSE 逐块转发
- TTFT 计算
- 客户端断开时取消上游请求
- 流已发送内容后不再切换模型
- 流中出现错误时发送 SSE 错误事件并结束

适配器需要处理：

- OpenAI Responses 的流式事件
- Anthropic 的 `message_start`、`content_block_delta`、`message_delta`、`message_stop`

沿用参考项目的处理方式：下游透明转发上游原始 SSE 数据块，网关在内部仅解析 SSE 用于提取内容、usage 和 TTFT，不强制改写为统一事件格式。

验收：

- 两种协议都能以 SSE 返回。
- 能记录 TTFT。
- 断开后上游被取消。

### 阶段 5：结构化输出

实现 `response_format` 约束与本地修复：

- 从请求中提取 JSON Schema。
- 对模型输出提取文本内容。
- 处理 Markdown 代码块包裹的 JSON。
- 使用 `jsonschema` 本地二次校验。
- 校验失败后构造修复指令并重试。

需要注意：

- Anthropic 本身没有与 OpenAI 完全一致的 `response_format` 语义，需要在 adapter 内将结构化要求映射成系统提示、输出约束或本地校验策略。
- 当前阶段不升级 Anthropic tool schema，只采用系统提示注入 Schema + 本地 `jsonschema` 校验修复。

验收：

- 非法 JSON 或不符合 Schema 时，自动修复一次并返回合法 JSON。
- 修复失败则返回统一错误。

### 阶段 6：Prompt 模板版本管理

复用并完善参考项目的 Prompt 能力：

- 创建模板并生成版本
- 激活版本
- 查询模板与指定版本
- 模板渲染
- 变量替换
- Jinja2 `SandboxedEnvironment` + `StrictUndefined`

数据表建议至少包含：

```text
id, version, name, description, role, content, is_active, created_at
```

验收：

- 同一 ID 可创建多版本。
- 可显式引用版本。
- 缺失变量或非法模板返回统一错误。

### 阶段 7：可观测性账本

实现每次调用的 Token、延迟和路由信息记录：

- 输入 Token、输出 Token、缓存 Token
- 总延迟
- TTFT
- 重试次数
- fallback 次数
- 公开模型、供应商、上游模型
- 状态码与错误类型
- 使用的 Prompt ID 与版本

adapter 负责将 OpenAI 和 Anthropic 的 usage 结构统一为：

```text
input_tokens
output_tokens
cached_tokens
```

用量记录仍只保存 API Key 的不可逆短指纹，不保存密钥原文、Prompt 正文或用户消息正文。

验收：

- `/admin/usage` 能查到 Token 分类、总延迟、TTFT、重试和 fallback 信息。

### 阶段 8：限流、管理接口与测试收敛

实现韧性基础和管理能力：

- 统一错误封装。
- Bearer API Key 鉴权。
- 按模型独立限流，超限返回 `429`。
- `/admin/routes` 查看路由与熔断状态。
- `/healthz`、`/readyz`。
- `/v1/models` 返回公开模型别名，并要求 Bearer Key 鉴权。

最终补齐测试：

- 适配器请求构造
- 模型路由与 fallback
- 重试次数
- 结构化输出修复
- Prompt 版本与渲染
- SSE 流式、TTFT、断开取消
- Anthropic usage 归一化
- 按模型独立限流返回 429
- 统一错误对象

测试统一使用 `httpx.MockTransport`，不消耗真实模型额度。

## 5. 推进顺序

建议实现顺序为：

1. 阶段 0：先锁定协议与 API 面设计。
2. 阶段 1：搭建配置与生命周期。
3. 阶段 2：实现适配器接口，这是后续所有能力的基础。
4. 阶段 3 → 4：先打通非流式，再处理 SSE。
5. 阶段 5 → 6：补充结构化输出与 Prompt 版本。
6. 阶段 7 → 8：接上可观测性与限流管理，并完成测试收敛。

## 6. 里程碑

| 里程碑 | 完成标志 |
| --- | --- |
| M1：协议契约定稿 | 统一请求/响应/流式事件模型、错误模型、模型路由配置已确定 |
| M2：双协议基础打通 | OpenAI Responses 与 Anthropic Messages 的非流式调用可用 |
| M3：流式与结构化可用 | SSE 转发、TTFT、结构化输出校验与修复可用 |
| M4：平台能力完整 | Prompt 版本、用量账本、按模型限流、管理接口可用 |
| M5：测试收敛 | 核心路径均有 MockTransport 测试，`pytest` 与 `ruff` 通过 |
