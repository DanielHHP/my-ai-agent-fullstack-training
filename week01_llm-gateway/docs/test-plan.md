# LLM 统一模型调用服务验收测试计划

## 1. 文档目的

本文档用于支撑 `week01_llm-gateway` 模块的整体验收测试，依据以下文档制定：

- [requirements.md](./requirements.md)
- [design.md](./design.md)
- [decisions.md](./decisions.md)
- [task-breakdown.md](./task-breakdown.md)

验收范围覆盖配置加载、协议适配器、模型路由、非流式调用、SSE 流式输出、结构化输出、Prompt 版本管理、可观测性、统一错误、鉴权、限流和管理接口。

所有模型服务商调用均通过 `httpx.MockTransport` 模拟，不消耗真实模型额度，不依赖真实 API Key，也不访问外部网络。

## 2. 验收目标

本次验收重点验证以下能力：

1. 三个协议入口 `/v1/chat/completions`、`/v1/responses`、`/v1/messages` 能正确路由到对应适配器。
2. 模型别名、多供应商、协议能力声明、同协议 fallback、加权轮询与熔断符合设计。
3. 非流式调用返回各协议原生结构，SSE 流式调用透明转发并正确记录 TTFT。
4. 结构化输出本地校验与修复重试符合预期。
5. Prompt 模板创建、版本、激活、渲染和模型调用中的 `prompt_ref` 注入正确。
6. 用量账本正确记录 token、延迟、TTFT、重试、fallback、修复次数、成本和错误状态。
7. 鉴权、限流、统一错误对象、健康检查和就绪检查符合设计。
8. 全链路不产生真实模型服务商调用。

## 3. 验收范围

### 3.1 包含范围

- 配置加载与校验
- 协议适配器层
- 模型路由层
- `GatewayService` 非流式与流式编排
- API 路由层
- Prompt 服务
- 结构化输出服务
- Usage 账本
- 安全与限流
- 管理接口
- 健康与就绪检查

### 3.2 不包含范围

- 真实模型服务商联调
- Redis 等分布式限流和熔断实现验证
- 生产部署、压测和容量测试
- Docker 镜像发布验证

## 4. 测试分层

| 层级 | 验证对象 | 主要手段 |
| --- | --- | --- |
| L1 单元测试 | 配置解析、协议解析、适配器构造、错误映射、结构化解析、Prompt 渲染、限流器 | 直接调用函数或 Pydantic 模型 |
| L2 服务测试 | `ModelRouter`、`UpstreamClient`、`GatewayService.complete/stream` | `httpx.MockTransport` + 临时 SQLite |
| L3 API 集成测试 | FastAPI 各端点、鉴权限流、异常响应、管理接口 | `TestClient` 或 `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` |
| L4 端到端冒烟 | 从 HTTP 请求到 mock 上游到用量落库 | `create_app(config, http_client=mock_client)` |
| L5 HTTP 黑盒验证 | 真实 Uvicorn 进程、外部 HTTP 请求、响应头、SSE 流式传输、进程生命周期 | `curl` 或 `httpx` 访问本地临时端口 |

## 5. Mock 模型服务侧方案

### 5.1 核心原则

- 不调用真实模型服务商。
- 在 `UpstreamClient` 注入 `httpx.MockTransport`。
- 通过 URL、请求头、请求体和调用次数控制响应分支。
- 测试中记录上游请求，用于断言适配器行为。

### 5.2 Mock 接入方式

```python
def handler(request: httpx.Request) -> httpx.Response:
    # 根据 request.url、request.headers、request.body 分支返回
    return httpx.Response(200, json={...})

mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
```

API 集成测试通过 `create_app(config, http_client=mock_client)` 注入 mock client。

### 5.3 需要模拟的响应类型

- 非流式 JSON 响应
- SSE `text/event-stream` 响应
- HTTP 可重试状态码：408、409、429、500、502、503、504
- HTTP 普通 4xx：400、401、404、422
- 网络异常：`httpx.ConnectError`、`httpx.ReadTimeout`

### 5.4 非流式 Mock 响应示例

OpenAI Chat Completions 成功：

```json
{
  "id": "chatcmpl_1",
  "object": "chat.completion",
  "model": "gpt-primary",
  "choices": [
    {"message": {"role": "assistant", "content": "hello"}}
  ],
  "usage": {"prompt_tokens": 5, "completion_tokens": 3}
}
```

OpenAI Responses 成功：

```json
{
  "id": "resp_1",
  "object": "response",
  "status": "completed",
  "model": "gpt-primary",
  "output_text": "hello",
  "usage": {"input_tokens": 5, "output_tokens": 3}
}
```

Anthropic Messages 成功：

```json
{
  "id": "msg_1",
  "type": "message",
  "role": "assistant",
  "model": "claude-sonnet-4-5",
  "content": [{"type": "text", "text": "hello"}],
  "usage": {
    "input_tokens": 5,
    "output_tokens": 3,
    "cache_read_input_tokens": 2,
    "cache_creation_input_tokens": 1
  }
}
```

上游错误示例：

```json
{"error": {"message": "upstream unavailable"}}
```

### 5.5 流式 Mock 响应示例

OpenAI Chat SSE：

```text
data: {"choices":[{"delta":{"content":"Hello"}}]}

data: {"choices":[{"delta":{"content":" world"}}]}

data: {"choices":[{"delta":{},"finish_reason":"stop","usage":{"prompt_tokens":5,"completion_tokens":3}}]}

data: [DONE]
```

OpenAI Responses SSE：

```text
data: {"type":"response.output_text.delta","delta":"Hello"}

data: {"type":"response.completed","response":{"id":"resp_1","usage":{"input_tokens":5,"output_tokens":3}}}
```

Anthropic Messages SSE：

```text
data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}

data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

data: {"type":"message_delta","usage":{"output_tokens":3}}

data: {"type":"message_stop"}
```

## 6. 测试环境与准备

| 项目 | 要求 |
| --- | --- |
| Python | 推荐使用 Python 3.12；当前 `.venv` 为 Python 3.14，`pytest-asyncio` 可能出现 deprecation warning，不影响验收判断 |
| 依赖 | `python -m pip install -r requirements.txt` |
| 配置 | 测试内使用 `GatewayConfig.model_validate({...})` 构造配置，不读取真实 `gateway.yaml` |
| 上游 | 全部通过 `httpx.MockTransport` 模拟 |
| SQLite | 使用 `tmp_path / "gateway.db"` 临时文件，不污染仓库 `data/` |
| API Key | 测试配置显式传入 `api_keys`，不依赖环境变量 |
| 执行命令 | `python -m pytest -q` |
| 静态检查 | `ruff check .` |

验收测试不得依赖 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY` 等真实环境变量，也不得访问外部域名。

## 7. 功能场景与验证内容

### A. 配置加载与协议能力声明

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-CFG-01 | 环境变量展开 | 设置临时环境变量后加载配置 | `${ENV_VAR}` 被替换；`${ENV_VAR:-default}` 在变量不存在时使用默认值 |
| ACC-CFG-02 | 路由 provider 未声明 | 配置中 `models.smart.routes[0].provider=missing` | 启动或加载配置时抛错，错误信息包含 provider |
| ACC-CFG-03 | 协议能力解析 | `api` 为 `chat,responses`、`messages`、`all` | 正确解析为协议集合 |
| ACC-CFG-04 | `all` 保留字约束 | `api` 为 `all,chat` | 解析失败，提示 `all` 不能与其他协议组合 |
| ACC-CFG-05 | 未知协议值 | `api` 为 `chat,both` | 解析失败，`both` 不允许 |
| ACC-CFG-06 | 默认配置值 | 缺省 `retry`、`rate_limit`、`circuit_breaker` | 使用默认值：每路由最多 3 次重试、限流 60/min、burst 10、熔断阈值 5、冷却 30s |

### B. 协议适配器与请求构造

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-ADP-01 | 适配器注册 | `get_adapter("chat"/"responses"/"messages")` | 返回对应 `OpenAIChatAdapter`、`OpenAIResponsesAdapter`、`AnthropicMessagesAdapter` |
| ACC-ADP-02 | OpenAI Chat 请求构造 | `model=smart`，protocol 为 chat | 上游 URL 为 `<base>/v1/chat/completions`；使用 `Authorization: Bearer <key>`；`body.model` 替换为供应商模型 ID |
| ACC-ADP-03 | OpenAI Responses 请求构造 | `input="hello"` 或消息数组 | URL 为 `<base>/v1/responses`；`input` 转换为设计格式；`instructions` 正确保留 |
| ACC-ADP-04 | Anthropic Messages 请求构造 | `model=claude-fast`，缺少 `max_tokens` | URL 为 `<base>/v1/messages`；使用 `x-api-key`，不带 `Authorization`；缺少 `max_tokens` 返回 422、`param=max_tokens`、`code=missing_required_parameter` |
| ACC-ADP-05 | Anthropic usage 归一化 | mock usage 包含 cache token 字段 | `cached_tokens` 等于 cache read 与 creation 之和；`input_tokens`、`output_tokens` 正确 |
| ACC-ADP-06 | Anthropic 结构化 Schema 注入 | 请求 Messages 并指定 `response_format` | 上游 `system` 中注入 JSON Schema 和“只输出合法 JSON”约束 |

### C. 模型路由与 /v1/models

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-RTR-01 | 未知模型别名 | `model=demo` | 返回 404，`code=model_not_found`，`param=model` |
| ACC-RTR-02 | 入口协议不支持 | 模型只声明 `chat`，请求 `/v1/messages` | 返回 422，`code=protocol_not_supported` |
| ACC-RTR-03 | 不允许跨协议 fallback | 首选 chat 路由失败，下一候选只支持 responses | 只能同协议 fallback，不切换到 responses |
| ACC-RTR-04 | priority 路由顺序 | 多个同协议候选 | 按配置顺序选择 |
| ACC-RTR-05 | weighted_round_robin | 同协议多候选，设置 weight | 请求按权重轮询，计数器只按模型别名维护 |
| ACC-RTR-06 | provider disabled | 首选 provider `enabled=false` | 跳过禁用 provider，选择健康路由 |
| ACC-RTR-07 | 熔断器打开 | 连续失败达到阈值 | 冷却期内返回 503 `no_healthy_route`；冷却期后可恢复 |
| ACC-RTR-08 | `/v1/models` | 带 Bearer Key 访问 | 返回 `object=list`；`supported_protocols` 为所有路由协议并集，排序稳定 |

### D. 非流式调用、重试与 fallback

| 用例 ID | 场景 | Mock 行为 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-GW-01 | 单路由成功 | 200 + 协议原生 JSON | 返回上游 `raw` 结构不变；`metrics.status=success`；`retries=0`、`fallbacks=0` |
| ACC-GW-02 | 每条路由最多 3 次重试 | 首选路由连续返回 500 | 首选路由总计请求 4 次；重试计数为 3 |
| ACC-GW-03 | fallback 后重试计数重置 | 首选失败 4 次，次选第一次成功 | `retries` 等于首选路由重试次数，`fallbacks=1`；次选不继承首选重试计数 |
| ACC-GW-04 | 可重试状态码 | 返回 408、429、500、502、503、504 | 触发指数退避重试 |
| ACC-GW-05 | 网络异常可重试 | `httpx.ConnectError` 或 `ReadTimeout` | 捕获为 `UpstreamError` 并重试 |
| ACC-GW-06 | 普通 4xx 不重试但 fallback | 首选返回 400，次选成功 | 首选只调用 1 次，`retries=0`，`fallbacks=1` |
| ACC-GW-07 | 所有候选路由失败 | 所有路由返回 500 | 抛统一 `GatewayError`，HTTP 502 `upstream_error`；用量仍记录 `status=error` |
| ACC-GW-08 | 三协议端到端非流式 | 分别走 chat/responses/messages 模型 | 各入口响应为对应协议原生结构，上游 URL、鉴权、请求体正确 |

### E. SSE 流式输出

| 用例 ID | 场景 | Mock 行为 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-STREAM-01 | Chat SSE 透明转发 | 返回 Chat SSE 原始字节 | 下游收到原始 SSE 字节，不强制改写；响应 `Content-Type=text/event-stream` |
| ACC-STREAM-02 | Responses SSE 透明转发 | 返回 `response.output_text.delta` | 下游收到原生 Responses 事件；内部能提取内容增量和 usage |
| ACC-STREAM-03 | Anthropic SSE 透明转发 | 返回 `message_start/content_block_delta/message_delta/message_stop` | 下游收到原生 Anthropic 事件；usage 归一化正确 |
| ACC-STREAM-04 | TTFT | 首个内容增量事件前延迟 | `first_token_ms` 非空且反映首个内容增量事件时间 |
| ACC-STREAM-05 | 首字节前重试/fallback | 首选流式连接返回 500 | 在下游未收到字节前可重试和 fallback；已收到内容后不再切换模型 |
| ACC-STREAM-06 | 已发送内容后上游错误 | 先发送若干 chunk，再发送错误 | 停止续传，发送 SSE 错误事件和结束标记 |
| ACC-STREAM-07 | 客户端断开取消上游 | 通过 `is_disconnected` 模拟断开 | 上游流被关闭，指标记录 `status=cancelled`、`status_code=499` |

### F. 结构化输出

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-SCHEMA-01 | 合法 JSON 且符合 Schema | 模型返回合法 JSON | 原样返回成功响应，`repair_retries=0` |
| ACC-SCHEMA-02 | Markdown code fence 包裹 JSON | 模型返回 ` ```json {...} ``` ` | 本地提取成功，返回合法内容 |
| ACC-SCHEMA-03 | 非法 JSON 自动修复一次 | 首次返回非法 JSON，修复后返回合法 JSON | `repair_retries=1`，最终返回合法 JSON |
| ACC-SCHEMA-04 | 修复超过配置次数 | 每次修复都返回非法 JSON | 返回 422，`code=structured_output_error` |
| ACC-SCHEMA-05 | Anthropic 结构化注入 | 请求 Messages 并指定 `response_format` | 上游请求 `system` 中已注入 Schema，返回后仍本地 `jsonschema` 校验 |
| ACC-SCHEMA-06 | Chat 与 Responses 结构化参数解析 | `response_format` 或 `text.format` | 正确提取 `StructuredOutputSpec.schema/name/strict` |

### G. Prompt 版本管理

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-PRM-01 | 创建并激活版本 | 创建同 ID 多版本 | 版本号递增，新版本默认 `activate=true` |
| ACC-PRM-02 | 查询激活版本或指定版本 | `GET /v1/prompts/{id}` 带或不带 version | 不指定返回激活版本，指定返回精确版本 |
| ACC-PRM-03 | 模板渲染变量替换 | `content="Review {{language}} code."`，传入变量 | 渲染结果正确 |
| ACC-PRM-04 | 缺失变量 | 不传必填变量 | 返回 422，`code=prompt_render_error` |
| ACC-PRM-05 | 非法 Jinja 模板 | `content="{% if %}broken"` | 返回 422，`code=prompt_render_error` |
| ACC-PRM-06 | 模型调用中 `prompt_ref` 注入 | 请求三种协议并带 `prompt_ref` | Chat 按 `prepend/append` 插入；Responses 写入 `instructions` 且放在已有内容之前；Anthropic 写入 `system` 且放在已有内容之前 |
| ACC-PRM-07 | 未知 Prompt ID | 模型调用引用不存在的 Prompt | 返回统一错误 |

### H. 可观测性与用量账本

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-USAGE-01 | 成功调用记录 | 成功非流式调用 | `input_tokens`、`output_tokens`、`cached_tokens` 正确；`latency_ms` 非空；`status=success` |
| ACC-USAGE-02 | 成本计算 | 配置 `pricing` | `cost_usd` 按成功响应 token 与价格计算 |
| ACC-USAGE-03 | TTFT 记录 | 流式调用 | `first_token_ms` 非空；`stream=true` |
| ACC-USAGE-04 | 重试/fallback 记录 | 首选失败、次选成功 | `retries`、`fallbacks` 正确；失败尝试 token 汇总到 `retry_*` 字段 |
| ACC-USAGE-05 | 结构化修复记录 | 首次结构化校验失败后修复 | `repair_retries=1`，成功后的 token 与 cost 正确 |
| ACC-USAGE-06 | 失败调用记录 | 所有路由失败 | 仍写入一条 `status=error`，包含 `error_type`、`error_message` |
| ACC-USAGE-07 | 敏感信息脱敏 | 有 Prompt 正文、用户消息、API Key | SQLite 中不保存 API Key 原文、Prompt 正文、用户消息正文；只保存 API Key 指纹 |
| ACC-USAGE-08 | `/admin/usage` 查询 | 多次调用后查询 | 返回 `{"data":[...]}`，按时间倒序；`limit` 参数生效，范围为 1..1000 |

### I. 统一错误、鉴权与限流

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-SEC-01 | 健康检查无需鉴权 | `GET /healthz`、`GET /readyz` | 返回 200，`{"status":"ok"}` |
| ACC-SEC-02 | 业务接口需要鉴权 | 无 Authorization 请求 `/v1/models`、`/admin/usage` | 返回 401 |
| ACC-SEC-03 | Bearer Key 鉴权 | 正确或错误 key | 正确通过，错误返回 401，`code=invalid_api_key` |
| ACC-SEC-04 | x-api-key 兼容 | `x-api-key` 头 | 正确鉴权 |
| ACC-SEC-05 | 开发模式 IP 指纹 | 不配置 `api_keys` | 使用客户端 IP 指纹，不要求 API Key |
| ACC-SEC-06 | 模型端点限流 | `burst=1`，连续请求 2 次 | 第二次返回 429，`code=rate_limit_exceeded`，响应含 `Retry-After` |
| ACC-SEC-07 | 非模型端点不限流 | 连续访问 `/v1/prompts`、`/admin/routes` | 不触发限流 |
| ACC-SEC-08 | 统一错误格式 | 各类错误 | 响应均为 `{"error":{"message","type","param","code",...}}` |

### J. 管理接口与健康检查

| 用例 ID | 场景 | 输入或触发方式 | 预期验证点 |
| --- | --- | --- | --- |
| ACC-ADMIN-01 | `/admin/routes` | 正常访问 | 返回 `models` 路由配置，`protocols` 已从 `api` 展开；`circuits` 反映熔断状态 |
| ACC-ADMIN-02 | `/admin/usage` | 正常访问 | 返回用量列表 |
| ACC-ADMIN-03 | `/v1/models` | 正常访问 | 返回模型别名及 `supported_protocols` |
| ACC-ADMIN-04 | 未知字段策略 | 模型调用传额外字段 | 模型调用入口允许接收未知字段但不向上游透传；Prompt 和管理接口严格校验并返回统一错误 |

## 8. 建议测试执行顺序

1. 先运行纯单元测试：配置、协议解析、适配器、安全、限流、结构化、Prompt 基础能力。
2. 再运行服务层测试：`ModelRouter`、`UpstreamClient`、`GatewayService.complete`。
3. 再运行流式测试：SSE 转发、TTFT、断开取消、首字节后错误。
4. 再运行结构化输出修复测试。
5. 再运行 API 集成测试：三种协议端点、`/v1/models`、Prompt 端点、管理端点。
6. 最后运行端到端冒烟：一个 mock 上游贯穿“请求 → 适配器 → 上游 mock → 响应或 SSE → SQLite 用量记录”。

这样可以在低层先定位问题，再逐步验证端到端链路，避免直接用 HTTP 集成测试掩盖底层错误。

## 9. 验收通过标准

必须同时满足：

1. `python -m pytest -q` 全部通过。
2. `ruff check .` 通过。
3. 所有验收用例均不访问真实模型服务商，`httpx.MockTransport` 覆盖全部上游调用路径。
4. 三种协议非流式入口都能返回协议原生结构。
5. 三种协议流式入口都能以 SSE 透明转发，并正确记录 TTFT。
6. 重试语义满足：每路由最多 3 次重试、fallback 后重新计数、普通 4xx 不重试但允许同协议 fallback。
7. 结构化输出非法结果能自动修复，超过次数返回统一错误。
8. Prompt 能创建、查询、按版本引用和渲染。
9. 用量账本能记录 token、延迟、TTFT、重试、fallback、修复和成本，且不保存敏感原文。
10. 鉴权限流语义符合设计：模型端点限流，其他端点只鉴权，超限返回 429。
11. 管理接口和健康检查状态正确。
12. HTTP 黑盒验证场景全部通过。

如果发现实现与文档不一致，先以 `docs/design.md`、`docs/decisions.md` 和 `AGENTS.md` 为基准判定；确有实现偏差的，记录为验收问题项。

## 10. 自动化测试流水线

验收测试通过仓库根目录下的两个脚本分别执行白盒和黑盒流水线：

```bash
bash scripts/run_acceptance_tests.sh
bash scripts/run_http_acceptance_tests.sh
```

白盒流水线 `scripts/run_acceptance_tests.sh` 会依次执行：

1. 输出 Python、pytest、ruff、Git 分支和提交等环境信息。
2. 使用 `ruff check app tests scripts` 执行静态检查。
3. 使用 `pytest` 执行 `tests/` 下全部测试。
4. 生成 JUnit XML 和 Markdown 测试摘要。

生成文件位于 `reports/`：

```text
reports/
├── environment.txt
├── junit.xml
├── summary.md
├── blackbox/
│   ├── uvicorn.log
│   └── *.body / *.headers
└── blackbox-summary.md
```

黑盒流水线 `scripts/run_http_acceptance_tests.sh` 会：

1. 加载 `tests/configs/acceptance.yaml`。
2. 通过 `scripts/blackbox_app.py` 启动 Uvicorn，并将上游模型服务替换为 `httpx.MockTransport`。
3. 使用 `curl` 从外部请求本地 `127.0.0.1:18000`。
4. 验证健康检查、鉴权、模型列表、非流式 Chat、SSE、限流和优雅退出。
5. 生成 `reports/blackbox-summary.md`。

测试配置独立维护在：

```text
tests/configs/acceptance.yaml
```

该文件已纳入 Git 管理，只包含 `.invalid` 域名和测试用假密钥，不包含真实服务商地址或密钥。测试通过 `tests/conftest.py` 加载该配置，并在需要时覆盖 `database_url` 为 `tmp_path` 下的临时 SQLite 文件。

## 11. HTTP 黑盒验证场景（补充）

本节补充 L5 黑盒验证场景，用于覆盖进程内 `TestClient` 无法验证的真实 Uvicorn 服务、TCP/HTTP 网络层、响应头、SSE 流式传输和进程生命周期。

当前 `scripts/run_acceptance_tests.sh` 负责 L1-L4 白盒场景，`scripts/run_http_acceptance_tests.sh` 负责 L5 黑盒冒烟场景。

### 11.1 运行前提

- 被测网关使用 `tests/configs/acceptance.yaml`。
- 模型上游仍不得调用真实服务商。
- 当前黑盒流水线通过 `scripts/blackbox_app.py` 启动 Uvicorn，并使用 `httpx.MockTransport` 替换上游模型服务。
- 网关监听 `127.0.0.1:18000`，不需要额外启动独立 mock 上游进程。
- 使用 `curl` 或 `httpx` 从外部发起 HTTP 请求。

### 11.2 黑盒冒烟场景清单

为避免与白盒测试重复，黑盒只保留无法通过进程内测试覆盖的高价值部署/网络冒烟场景：

| 用例 ID | 场景 | 请求方式与输入 | 预期验证点 |
| --- | --- | --- | --- |
| BH-01 | 服务启动与存活 | `GET /healthz` | 返回 200，`{"status":"ok"}`，无需鉴权 |
| BH-02 | 就绪检查 | `GET /readyz` | 返回 200，`{"status":"ok"}`，无需鉴权 |
| BH-03 | 未鉴权业务接口 | 无 Authorization 请求 `/v1/models` | 返回 401，统一 OpenAI 风格错误对象 |
| BH-04 | 模型列表 | `GET /v1/models`，携带 `Authorization: Bearer test-key` | 返回 200，`object=list`，包含 `smart`、`claude-fast`，`supported_protocols` 正确 |
| BH-05 | Chat Completions 非流式 | `POST /v1/chat/completions`，模型 `smart` | 返回 200，响应为 OpenAI Chat Completions 原生结构；响应包含 `X-Request-ID` |
| BH-06 | Chat SSE 流式 | `curl -N` 请求 `stream=true` | 响应 `Content-Type` 为 `text/event-stream`；收到原始 SSE chunk；包含 `X-Request-ID`、`Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no` |
| BH-07 | 限流黑盒行为 | 使用独立测试密钥 `rate-limit-key` 连续发送三个模型请求 | 前两次 200，第三次 429，响应包含 `Retry-After` |
| BH-08 | 服务关闭行为 | 向 Uvicorn 发送 SIGTERM | Uvicorn 日志包含 `Shutting down` 和 `Application shutdown complete`，临时 SQLite 和报告文件不损坏 |

### 11.3 建议的黑盒命令示例

存活检查：

```bash
curl -i http://127.0.0.1:18000/healthz
```

未鉴权模型列表：

```bash
curl -i http://127.0.0.1:18000/v1/models
```

鉴权模型列表：

```bash
curl -i \
  -H "Authorization: Bearer test-key" \
  http://127.0.0.1:18000/v1/models
```

Chat Completions 非流式：

```bash
curl -i \
  -X POST http://127.0.0.1:18000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","messages":[{"role":"user","content":"hello"}]}'
```

Chat Completions SSE：

```bash
curl -N \
  -X POST http://127.0.0.1:18000/v1/chat/completions \
  -H "Authorization: Bearer test-key" \
  -H "Content-Type: application/json" \
  -d '{"model":"smart","stream":true,"messages":[{"role":"user","content":"hello"}]}'
```

### 11.4 黑盒验收通过标准

- 所有 BH 冒烟场景均返回预期状态码、响应头和响应体。
- 黑盒测试过程中所有上游请求均发往本地 mock 上游，不访问真实服务商。
- SSE 场景能通过网络层正常逐块返回，响应头包含必要的防缓冲头。
- 限流和鉴权在真实 HTTP 服务下行为与设计一致。
- 服务进程能正常启动和优雅退出。

## 12. 用例追踪表

下表用于把第 7 节和第 11 节的验收用例映射到当前自动化测试，便于后续评审快速判断覆盖情况。

| 用例 ID | 覆盖测试 |
| --- | --- |
| ACC-CFG-01 | `tests/test_config.py::test_load_config_expands_env_and_resolves_db_path` |
| ACC-CFG-02 | `tests/test_config.py::test_config_rejects_unknown_provider` |
| ACC-CFG-03 | `tests/test_config.py::test_parse_protocols_supports_all` |
| ACC-CFG-04 | `tests/test_config.py::test_parse_protocols_rejects_all_combination` |
| ACC-CFG-05 | `tests/test_config.py::test_parse_protocols_rejects_unknown_protocol` |
| ACC-CFG-06 | `tests/test_config.py::test_config_defaults_match_acceptance_expectations` |
| ACC-ADP-01 | `tests/test_adapters.py::test_adapter_registry_selects_correct_adapter` |
| ACC-ADP-02 | `tests/test_adapters.py::test_openai_chat_adapter_builds_expected_upstream_request` |
| ACC-ADP-03 | `tests/test_adapters.py::test_openai_responses_adapter_converts_input`、`tests/test_adapters.py::test_openai_responses_adapter_keeps_instructions` |
| ACC-ADP-04 | `tests/test_adapters.py::test_anthropic_adapter_uses_x_api_key_and_requires_max_tokens` |
| ACC-ADP-05 | `tests/test_adapters.py::test_anthropic_adapter_sums_cache_tokens` |
| ACC-ADP-06 | `tests/test_adapters.py::test_anthropic_adapter_injects_structured_schema_into_system`、`tests/test_structured.py::test_complete_validates_anthropic_structured_output_end_to_end` |
| ACC-RTR-01 | `tests/test_router_upstream.py::test_model_router_filters_routes_by_protocol_and_returns_adapter` |
| ACC-RTR-02 | 同上 |
| ACC-RTR-03 | 同上 |
| ACC-RTR-04 | `tests/test_router_upstream.py::test_router_preserves_priority_order` |
| ACC-RTR-05 | `tests/test_router_upstream.py::test_router_weighted_round_robin_uses_model_alias_counter` |
| ACC-RTR-06 | `tests/test_router_upstream.py::test_router_skips_disabled_provider` |
| ACC-RTR-07 | `tests/test_router_upstream.py::test_router_opens_and_recovers_circuit` |
| ACC-RTR-08 | `tests/test_admin_routes.py::test_models_admin_usage_and_routes_are_available_with_key` |
| ACC-GW-01 | `tests/test_gateway.py::test_complete_routes_to_adapter_and_returns_raw_response` |
| ACC-GW-02 | `tests/test_gateway.py::test_complete_exhausts_three_retries_per_route` |
| ACC-GW-03 | `tests/test_gateway.py::test_complete_retries_then_falls_back_to_next_route` |
| ACC-GW-04 | `tests/test_gateway.py::test_complete_retries_configured_retry_statuses` |
| ACC-GW-05 | `tests/test_gateway.py::test_complete_retries_network_errors` |
| ACC-GW-06 | `tests/test_gateway.py::test_complete_non_retryable_4xx_still_falls_back` |
| ACC-GW-07 | `tests/test_gateway.py::test_complete_raises_unified_error_when_all_routes_fail`、`tests/test_usage.py::test_gateway_records_error_usage_when_all_routes_fail` |
| ACC-GW-08 | `tests/test_acceptance_pipeline.py::test_chat_completions_end_to_end_uses_shared_acceptance_config`、`tests/test_acceptance_pipeline.py::test_responses_and_messages_endpoints_use_shared_acceptance_config` |
| ACC-STREAM-01 | `tests/test_gateway.py::test_stream_forwards_sse_and_records_first_token` |
| ACC-STREAM-02 | `tests/test_gateway.py::test_stream_supports_openai_responses_native_events`、`tests/test_usage.py::test_gateway_records_responses_stream_raw_usage_metadata` |
| ACC-STREAM-03 | `tests/test_gateway.py::test_stream_supports_anthropic_native_events`、`tests/test_usage.py::test_gateway_records_anthropic_stream_raw_usage_metadata` |
| ACC-STREAM-04 | `tests/test_gateway.py::test_stream_forwards_sse_and_records_first_token`、`tests/test_usage.py::test_gateway_records_stream_usage_and_ttft` |
| ACC-STREAM-05 | `tests/test_gateway.py::test_stream_retries_before_first_byte_and_falls_back` |
| ACC-STREAM-06 | `tests/test_gateway.py::test_stream_emits_error_after_content_without_fallback` |
| ACC-STREAM-07 | `tests/test_gateway.py::test_stream_cancels_upstream_on_client_disconnect` |
| ACC-SCHEMA-01 | `tests/test_structured.py::test_complete_accepts_valid_structured_output_without_repair` |
| ACC-SCHEMA-02 | `tests/test_structured.py::test_validate_structured_content_accepts_markdown_fenced_json`、`tests/test_structured.py::test_validate_structured_content_extracts_fenced_json_with_surrounding_text` |
| ACC-SCHEMA-03 | `tests/test_structured.py::test_complete_repairs_invalid_structured_output_once` |
| ACC-SCHEMA-04 | `tests/test_structured.py::test_complete_raises_structured_error_when_repair_budget_exhausted` |
| ACC-SCHEMA-05 | `tests/test_structured.py::test_complete_validates_anthropic_structured_output_end_to_end`、`tests/test_adapters.py::test_anthropic_adapter_injects_structured_schema_into_system` |
| ACC-SCHEMA-06 | `tests/test_routes.py::test_structured_spec_parses_openai_chat_json_schema`、`tests/test_routes.py::test_structured_spec_parses_responses_text_format` |
| ACC-PRM-01 | `tests/test_prompts.py::test_prompt_repository_creates_and_resolves_versions` |
| ACC-PRM-02 | 同上 |
| ACC-PRM-03 | 同上 |
| ACC-PRM-04 | `tests/test_prompts.py::test_prompt_repository_reports_missing_variable` |
| ACC-PRM-05 | `tests/test_prompts.py::test_prompt_repository_reports_invalid_template` |
| ACC-PRM-06 | `tests/test_prompts.py::test_prompt_ref_injects_into_all_three_protocols`、`tests/test_prompts.py::test_apply_prompt_prepends_chat_message`、`tests/test_prompts.py::test_apply_prompt_writes_responses_instructions_before_existing`、`tests/test_prompts.py::test_apply_prompt_prepends_anthropic_system_text_block` |
| ACC-PRM-07 | `tests/test_prompts.py::test_prompt_repository_reports_unknown_prompt_id` |
| ACC-USAGE-01 | `tests/test_usage.py::test_gateway_records_success_usage_and_prompt_metadata` |
| ACC-USAGE-02 | `tests/test_usage.py::test_usage_repository_round_trip_and_cost` |
| ACC-USAGE-03 | `tests/test_usage.py::test_gateway_records_stream_usage_and_ttft` |
| ACC-USAGE-04 | `tests/test_usage.py::test_gateway_records_retry_and_fallback_usage`、`tests/test_usage.py::test_gateway_records_attempted_routes_when_all_routes_fail` |
| ACC-USAGE-05 | `tests/test_usage.py::test_gateway_records_structured_repair_usage`、`tests/test_usage.py::test_gateway_structured_repair_cost_uses_each_upstream_model` |
| ACC-USAGE-06 | `tests/test_usage.py::test_gateway_records_error_usage_when_all_routes_fail` |
| ACC-USAGE-07 | `tests/test_usage.py::test_gateway_usage_does_not_persist_sensitive_payloads`、`tests/test_security_rate_limit.py::test_key_fingerprint_is_short_and_stable` |
| ACC-USAGE-08 | `tests/test_admin_routes.py::test_admin_usage_limit_parameter` |
| ACC-SEC-01 | `tests/test_admin_routes.py::test_healthz_and_readyz_do_not_require_auth` |
| ACC-SEC-02 | `tests/test_admin_routes.py::test_protected_routes_require_bearer_key` |
| ACC-SEC-03 | `tests/test_security_rate_limit.py::test_authenticate_accepts_bearer_key`、`tests/test_security_rate_limit.py::test_authenticate_rejects_invalid_key` |
| ACC-SEC-04 | `tests/test_security_rate_limit.py::test_authenticate_accepts_x_api_key`、`tests/test_admin_routes.py::test_protected_routes_accept_x_api_key` |
| ACC-SEC-05 | `tests/test_security_rate_limit.py::test_authenticate_uses_client_fingerprint_in_dev_mode` |
| ACC-SEC-06 | `tests/test_admin_routes.py::test_model_endpoint_rate_limits_after_burst`、`tests/test_acceptance_pipeline.py::test_rate_limit_uses_burst_from_acceptance_config` |
| ACC-SEC-07 | `tests/test_admin_routes.py::test_non_model_endpoints_do_not_rate_limit` |
| ACC-SEC-08 | 各错误路径测试中的 `error` 对象断言，如 `tests/test_routes.py::test_json_payload_rejects_invalid_json` |
| ACC-ADMIN-01 | `tests/test_admin_routes.py::test_models_admin_usage_and_routes_are_available_with_key` |
| ACC-ADMIN-02 | 同上 |
| ACC-ADMIN-03 | 同上 |
| ACC-ADMIN-04 | `tests/test_admin_routes.py::test_model_call_does_not_forward_unknown_fields`、`tests/test_admin_routes.py::test_prompt_creation_rejects_unknown_fields`、`tests/test_admin_routes.py::test_prompt_render_rejects_unknown_fields` |
| BH-01 | `scripts/run_http_acceptance_tests.sh` |
| BH-02 | 同上 |
| BH-03 | 同上 |
| BH-04 | 同上 |
| BH-05 | 同上 |
| BH-06 | 同上 |
| BH-07 | 同上 |
| BH-08 | 同上 |
