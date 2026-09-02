# 阶段 0 设计细化（已确认版）

## 1. 设计目标

本文档是阶段 0 的最终设计基线，用于锁定后续实现所依赖的协议契约。所有内容均已经过设计决策确认。

## 2. 对外 API 面

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/chat/completions` | OpenAI Chat Completions 兼容入口 |
| POST | `/v1/responses` | OpenAI Responses API 入口 |
| POST | `/v1/messages` | Anthropic Messages API 入口 |
| GET | `/v1/models` | 返回公开模型别名及 `supported_protocols` |
| POST | `/v1/prompts` | 创建 Prompt 新版本 |
| GET | `/v1/prompts` | 查询 Prompt 版本 |
| GET | `/v1/prompts/{id}` | 查询指定 Prompt 版本 |
| POST | `/v1/prompts/{id}/render` | 渲染 Prompt |
| GET | `/admin/usage` | 查询调用用量 |
| GET | `/admin/routes` | 查询路由和熔断状态 |
| GET | `/healthz` | 存活检查 |
| GET | `/readyz` | 就绪检查 |

三个模型调用端点共享同一个内部 `GatewayService`，只在外层做协议入参校验和响应序列化。

非流式响应保持各协议原生结构。SSE 沿用参考项目透明转发。

## 3. 协议与适配器

支持以下协议：

- `chat_completions`
- `openai_responses`
- `anthropic_messages`

适配器：

- `OpenAIChatAdapter`
- `OpenAIResponsesAdapter`
- `AnthropicMessagesAdapter`

## 4. 统一数据模型

### 4.1 协议类型

```python
ProtocolName = Literal["chat_completions", "openai_responses", "anthropic_messages"]
```

### 4.2 统一消息

```python
class UnifiedMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_call_id: str | None = None
```

初始阶段只完整支持文本消息。图片、工具调用和复杂内容块由适配器按能力透传或显式拒绝。

### 4.3 统一请求

```python
class UnifiedRequest(BaseModel):
    model: str
    protocol: ProtocolName
    messages: list[UnifiedMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    response_format: StructuredOutputSpec | None = None
    prompt_ref: PromptReference | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

`model` 始终是网关公开别名，适配器负责替换为供应商真实模型 ID。

### 4.4 统一响应

```python
class UnifiedResponse(BaseModel):
    id: str
    protocol: ProtocolName
    model: str
    content_text: str
    usage: NormalizedUsage
    raw: dict[str, Any]
```

### 4.5 统一流式事件

```python
class UnifiedStreamEvent(BaseModel):
    type: Literal["text_delta", "usage", "done", "error"]
    delta: str | None = None
    usage: NormalizedUsage | None = None
    error: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None
```

该事件仅用于内部处理。对下游仍透明转发上游原始 SSE 数据块。

### 4.6 统一 usage

顶层字段只保留跨协议稳定字段：

```python
class NormalizedUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
```

供应商特有的 usage 细节放入 `UsageEvent.metadata`，不进入顶层账本字段。

## 5. 协议适配器接口

```python
class ProtocolAdapter(Protocol):
    name: ProtocolName

    def build_request(
        self,
        *,
        model: str,
        request: UnifiedRequest,
        request_id: str,
    ) -> UpstreamRequest: ...

    def parse_response(self, raw: dict[str, Any]) -> UnifiedResponse: ...

    def parse_stream_line(self, line: str) -> UnifiedStreamEvent | None: ...

    def normalize_usage(self, raw: dict[str, Any]) -> NormalizedUsage: ...

    def map_error(
        self,
        *,
        status_code: int,
        raw: dict[str, Any] | str,
    ) -> GatewayError: ...

    def apply_structured_output(
        self,
        request: UnifiedRequest,
        spec: StructuredOutputSpec,
    ) -> UnifiedRequest: ...
```

`UpstreamRequest` 由适配器构造，统一交给 `UpstreamClient` 执行：

```python
class UpstreamRequest(BaseModel):
    provider: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    stream: bool
    timeout: httpx.Timeout
```

上游鉴权头由适配器处理：

- OpenAI：`Authorization: Bearer <api_key>`
- Anthropic：`x-api-key: <api_key>`

## 6. 配置模型

### 6.1 Provider

沿用参考项目字段，不增加 `vendor` 或协议能力声明：

```python
class ProviderConfig(BaseModel):
    base_url: str
    api_key: SecretStr = SecretStr("")
    enabled: bool = True
    timeout_seconds: float = 120
    connect_timeout_seconds: float = 10
    extra_headers: dict[str, str] = Field(default_factory=dict)
```

### 6.2 路由能力

`api` 是逗号分隔字符串，支持：

```text
chat
responses
messages
all
```

`all` 是保留字，只能单独出现，字面表示 `chat + responses + messages`。配置不做供应商能力推断，完全信任路由声明。

```yaml
providers:
  openai:
    base_url: https://api.openai.com
    api_key: ${OPENAI_API_KEY}
  openai-backup:
    base_url: https://api.backup-openai.example
    api_key: ${OPENAI_BACKUP_API_KEY}
  anthropic:
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}

models:
  smart:
    strategy: priority
    routes:
      - provider: openai
        model: gpt-5.2
        api: "chat,responses"
      - provider: openai-backup
        model: gpt-5-mini
        api: "responses"
  claude-fast:
    strategy: priority
    routes:
      - provider: anthropic
        model: claude-sonnet-4-5
        api: "messages"
```

配置校验：

- `models.<alias>.routes[].provider` 必须存在于 `providers`。
- `api` 字符串必须能解析为合法协议集合。
- `all` 不能与其他协议值组合。
- 不校验供应商是否真的支持声明的协议。

## 7. 模型路由

- 同一公开模型别名可以聚合多协议路由。
- `ModelRouter.candidates(model, protocol)` 按入口协议过滤候选路由。
- 不允许跨协议 fallback。
- 同协议候选路由可以 fallback。
- `priority` 按配置顺序选择。
- `weighted_round_robin` 的计数器按模型别名维护，不区分入口协议。

如果模型别名存在，但没有支持当前入口协议的路由：

```json
{
  "error": {
    "message": "Model does not support the requested protocol",
    "type": "invalid_request_error",
    "param": "model",
    "code": "protocol_not_supported"
  }
}
```

HTTP 状态码为 `422`。

`/v1/models` 返回的 `supported_protocols` 是所有候选路由协议能力展开后的并集：

```json
{
  "object": "list",
  "data": [
    {
      "id": "smart",
      "object": "model",
      "created": 0,
      "owned_by": "llm-gateway",
      "supported_protocols": ["chat", "responses"]
    }
  ]
}
```

## 8. 重试、fallback 与熔断

重试语义：

- 每条路由最多重试 3 次，即每路由最多 4 次上游请求。
- fallback 到下一路由后，重试计数重新计算。
- 结构化修复沿用参考项目行为，重新进入候选路由循环。
- 不设置单次网关请求的全局总尝试上限。

配置字段：

```yaml
retry:
  max_retries_per_route: 3
  base_delay_seconds: 0.25
  max_delay_seconds: 4
  retry_statuses: [408, 409, 429, 500, 502, 503, 504]
```

退避采用指数退避，并加 `0.75~1.25` 随机抖动。

- 网络错误和超时可重试。
- 普通 4xx 不重试，但允许继续同协议 fallback。
- 4xx 也计入熔断器失败。

熔断器默认参数：

```yaml
circuit_breaker:
  failure_threshold: 5
  cooldown_seconds: 30
```

## 9. 流式输出

- 使用 `UpstreamClient.open_stream` 打开上游 SSE。
- 下游透明转发上游原始 SSE 数据块。
- 适配器只在内部解析事件，用于提取内容增量、usage、TTFT 和 checkpoint。
- TTFT 定义为第一个内容增量事件出现的时间。
- 客户端断开时取消上游请求，并记录为 `cancelled`。
- 已发送内容后不切换模型；失败时发送错误事件和结束标记。
- 流式 checkpoint 保留，默认关闭。

## 10. 结构化输出

统一结构化输出规格：

```python
class StructuredOutputSpec(BaseModel):
    schema: dict[str, Any]
    name: str | None = None
    strict: bool = False
```

处理规则：

- OpenAI Responses：优先使用原生 `text.format` 传入 JSON Schema。
- OpenAI Chat Completions：使用原生 `response_format`。
- Anthropic Messages：当前阶段采用系统提示注入 Schema + “仅返回合法 JSON”，本地 `jsonschema` 校验修复。
- 当前阶段不升级 Anthropic tool schema。
- 所有模型输出都不可信，必须本地解析和校验。
- 支持提取 Markdown code fence 中的 JSON。
- 结构化修复重试轮数由 `structured_output_retries` 控制，默认值为 1。
- 流式输出一旦开始，不进行无损结构化修复。

## 11. Prompt 版本管理

统一 Prompt 引用：

```python
class PromptReference(BaseModel):
    id: str
    version: int | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    position: Literal["prepend", "append"] = "prepend"
```

版本解析：

- 未指定 `version` 时使用当前激活版本。
- 指定 `version` 时使用精确版本。
- 创建新版本默认 `activate=true`。
- 渲染使用 Jinja2 `SandboxedEnvironment` 与 `StrictUndefined`。

插入规则：

- OpenAI Responses：系统 Prompt 写入 `instructions`，已有内容放在渲染结果之后。
- Anthropic Messages：系统 Prompt 写入 `system` 字段，渲染结果放在已有内容之前。
- Anthropic `system` 支持字符串和文本内容块数组；数组形式下将渲染结果作为新的 `{"type":"text","text":"..."}` 块插入开头。
- Chat Completions：按 `prepend`/`append` 插入消息。

## 12. 可观测性

`UsageEvent` 至少包含：

```text
request_id
api_key_hash
protocol
endpoint
requested_model
provider
upstream_model
stream
status
status_code
input_tokens
output_tokens
cached_tokens
cost_usd
latency_ms
first_token_ms
retries
fallbacks
repair_retries
retry_input_tokens
retry_output_tokens
retry_cached_tokens
retry_cost_usd
error_type
error_message
prompt_id
prompt_version
metadata
```

统计口径：

- `cost_usd` 只包含最终成功响应的成本。
- `retry_*` 记录所有非最终成功尝试的 Token 和成本。
- `retries` 只记录普通重试。
- `fallbacks` 记录跳过的候选路由数量。
- `repair_retries` 单独记录结构化修复次数。
- 供应商原始 usage 细节放入 `metadata`。
- 所有路由都失败时，仍记录一条 `status=error` 的用量事件。
- 用量记录只保存 API Key 的不可逆短指纹，不保存密钥原文、Prompt 正文或用户消息正文。

`/admin/usage` 返回：

```json
{
  "data": [
    {
      "request_id": "..."
    }
  ]
}
```

## 13. 错误映射

对外统一使用 OpenAI 风格错误对象：

```json
{
  "error": {
    "message": "Unknown model alias: demo",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found",
    "details": {}
  }
}
```

建议的错误类型：

| 类型 | HTTP 状态 |
| --- | --- |
| `authentication_error` | 401 |
| `invalid_request_error` | 400 / 404 / 422 |
| `rate_limit_error` | 429 |
| `model_not_found` | 404 |
| `protocol_not_supported` | 422 |
| `prompt_not_found` | 404 |
| `prompt_render_error` | 422 |
| `structured_output_error` | 422 |
| `upstream_error` | 502 / 503 |
| `service_unavailable_error` | 503 |
| `stream_error` | 502 |

Anthropic 错误类型在 adapter 内映射为上述统一类型。

## 14. 鉴权与限流

鉴权：

- 除 `/healthz`、`/readyz` 外，所有业务和管理端点要求 Bearer Key。
- 使用 `SecretStr` 和 `hmac.compare_digest`。
- 用量记录只保存 API Key 指纹。

限流：

- 按调用方身份限流。
- 只对 `/v1/chat/completions`、`/v1/responses`、`/v1/messages` 进行限流。
- Prompt、模型列表和管理接口不限流。
- 配置使用单层结构：

```yaml
rate_limit:
  enabled: true
  requests_per_minute: 60
  burst: 10
```

- 配置了 API Key 时，身份使用 API Key 指纹。
- 未配置 API Key 的开发模式，使用客户端 IP 指纹。
- 超限返回 `429`、`type="rate_limit_error"`、`code="rate_limit_exceeded"` 和 `Retry-After`。

## 15. 请求校验

- 模型调用入口允许未知字段并透传。
- Prompt 和管理接口严格校验。
- Anthropic `messages` 必须非空；缺失时返回 `422`。
- Anthropic `max_tokens` 必填；缺失时返回 `422`，`param="max_tokens"`，`code="missing_required_parameter"`。
- OpenAI Responses 的 `input` 支持字符串和消息数组，其他复杂结构尽量透传。

## 16. 目标目录结构

```text
app/
├── api/routes.py
├── core/
│   ├── errors.py
│   ├── security.py
│   └── rate_limit.py
├── services/
│   ├── adapters/
│   │   ├── base.py
│   │   ├── openai_chat.py
│   │   ├── openai_responses.py
│   │   └── anthropic_messages.py
│   ├── gateway.py
│   ├── upstream.py
│   ├── router.py
│   ├── prompts.py
│   ├── structured.py
│   └── usage.py
├── config.py
├── schemas.py
└── main.py
```

## 17. 阶段 0 验收标准

- 三个协议适配器接口已定义。
- 统一请求、响应、流式事件和 usage 模型已定义。
- 模型配置支持逗号分隔的 `api` 能力和同协议 fallback。
- 重试、fallback、结构化修复、SSE、Prompt、错误、用量、鉴权和限流语义均已写入本文档。
- 后续阶段可以直接基于本文档实现。

## 18. 阶段 2 当前实现的调用链路模块图

> 本图基于当前仓库中阶段 2 已落地代码整理。当前阶段已完成统一抽象层、三个协议适配器、模型路由筛选和通用上游 HTTP 客户端；`GatewayService` 及三个模型调用 API 入口尚未接入，因此下面的调用链中由“后续 `GatewayService` / 测试”发起，仅用于说明已实现模块之间的协作关系。

### 18.1 模块关系

```text
┌──────────────────────────────────────────────────────────────┐
│ app.config                                                   │
│ GatewayConfig / ProviderConfig / RouteTarget                 │
│ parse_protocols() / load_config()                            │
└─────────────────────────────┬────────────────────────────────┘
                              │ 读取并校验模型路由
                              v
┌──────────────────────────────────────────────────────────────┐
│ app.services.router.ModelRouter                              │
│ candidates() / resolve()                                     │
│ 协议过滤、熔断状态、加权轮询计数器                              │
└─────────────────────────────┬────────────────────────────────┘
                              │ get_adapter(protocol)
                              v
┌──────────────────────────────────────────────────────────────┐
│ ProtocolAdapter                                              │
└───────────────┬───────────────────┬──────────────────────────┘
                │                   │
                v                   v                           
┌───────────────────────┐ ┌─────────────────────┐ ┌──────────────────────────┐
│ OpenAIChatAdapter     │ │ OpenAIResponsesAdapter│ │ AnthropicMessagesAdapter │
└───────────┬───────────┘ └──────────┬──────────┘ └────────────┬─────────────┘
            │                        │                         │
            └────────────────────────┼─────────────────────────┘
                                     │ build_request() -> UpstreamRequest
                                     v
                       ┌─────────────────────────────────────┐
                       │ app.services.upstream.UpstreamClient│
                       │ request_json() / open_stream()      │
                       └─────────────────┬───────────────────┘
                                         │ POST / SSE
                                         v
                       ┌─────────────────────────────────────┐
                       │ httpx.AsyncClient / MockTransport    │
                       └─────────────────────────────────────┘

依赖模型：

- app.schemas：UnifiedRequest / UnifiedResponse / NormalizedUsage /
  UnifiedStreamEvent / UpstreamRequest
- app.core.errors：GatewayError / UpstreamError / error_payload()
```

### 18.2 单次模型调用的调用链

```text
后续 GatewayService / 测试
        │
        │ resolve(model, entry_protocol)
        v
ModelRouter
        │ candidates(model, entry_protocol)
        │ get_adapter(entry_protocol)
        v
ProtocolAdapter
        │ build_request(target, request, request_id, provider)
        │ 转换 URL、鉴权头、请求体、结构化输出约束
        v
UpstreamRequest
        │ request_json(req) / open_stream(req)
        v
UpstreamClient
        │ POST 上游，必要时携带 stream=true
        v
httpx.AsyncClient / MockTransport
        │ raw JSON / SSE bytes
        v
UpstreamClient
        │ dict / OpenStream
        v
ProtocolAdapter
        │ parse_response / parse_stream_event / normalize_usage / map_error
        v
UnifiedResponse / UnifiedStreamEvent / NormalizedUsage / GatewayError
```

### 18.3 当前实现需要注意的协议名称映射

阶段 2 代码中存在两组协议名称，调用链中必须保持边界清晰：

| 用途 | 取值 |
| --- | --- |
| 配置路由能力、`ModelRouter`、`get_adapter` | `chat`、`responses`、`messages` |
| `UnifiedRequest.protocol`、`ProtocolAdapter.protocol` | `chat_completions`、`openai_responses`、`anthropic_messages` |
| `ProtocolAdapter.name` | `chat`、`responses`、`messages` |

当前 `ModelRouter.resolve(model, entry_protocol)` 使用配置层短名称，`get_adapter` 也按短名称查找适配器；进入具体适配器后，统一请求模型中的 `protocol` 字段才使用完整协议名。
