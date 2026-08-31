# 阶段 0 设计细化

## 1. 设计目标

本文档细化 [task-breakdown.md](./task-breakdown.md) 中的阶段 0，目标是锁定后续实现所依赖的协议契约，包括：

- 对外 API 面
- 统一请求、响应、流式事件和 usage 数据模型
- 协议适配器接口
- 模型路由与配置模型
- 结构化输出、Prompt 版本、可观测性、错误和限流的统一语义

阶段 0 完成后，后续阶段不应再频繁修改跨模块接口。

## 2. 对外 API 面

建议对外保留两个协议兼容入口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/chat/completions` | OpenAI Chat Completions 兼容入口 |
| POST | `/v1/responses` | OpenAI Responses API 入口 |
| POST | `/v1/messages` | Anthropic Messages API 入口 |
| GET | `/v1/models` | 返回公开模型别名及其协议能力 |
| POST | `/v1/prompts` | 创建 Prompt 新版本 |
| GET | `/v1/prompts` | 查询 Prompt 版本 |
| GET | `/v1/prompts/{id}` | 查询指定 Prompt 版本 |
| POST | `/v1/prompts/{id}/render` | 渲染 Prompt |
| GET | `/admin/usage` | 查询调用用量 |
| GET | `/admin/routes` | 查询路由和熔断状态 |
| GET | `/healthz` | 存活检查 |
| GET | `/readyz` | 就绪检查 |

`/v1/chat/completions`、`/v1/responses` 和 `/v1/messages` 应共享同一个内部 `GatewayService`，只在外层做协议入参校验和响应序列化。

## 3. 统一数据模型

统一模型定义在 `app/schemas.py` 或独立的 `app/services/protocols.py` 中，用于适配器之间交换数据。

### 3.1 协议类型

```python
ProtocolName = Literal["chat_completions", "openai_responses", "anthropic_messages"]
```

### 3.2 统一消息

```python
class UnifiedMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_call_id: str | None = None
```

初始阶段只完整支持文本消息。图片、工具调用和复杂内容块由适配器按能力透传或显式拒绝。

### 3.3 统一请求

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

`model` 始终是网关公开别名。适配器负责将它替换为供应商真实模型 ID。

### 3.4 统一响应

```python
class UnifiedResponse(BaseModel):
    id: str
    protocol: ProtocolName
    model: str
    content_text: str
    usage: NormalizedUsage
    raw: dict[str, Any]
```

`content_text` 是从响应中提取出的稳定文本字段，用于结构化校验、修复和 checkpoint。

### 3.5 统一流式事件

```python
class UnifiedStreamEvent(BaseModel):
    type: Literal["text_delta", "usage", "done", "error"]
    delta: str | None = None
    usage: NormalizedUsage | None = None
    error: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None
```

适配器把 OpenAI 和 Anthropic 的流式事件归一化为上述事件，用于内部处理。对下游仍透明转发上游原始 SSE 数据块，不强制改写为统一事件格式。

### 3.6 统一 usage

```python
class NormalizedUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    token_details: dict[str, int] = Field(default_factory=dict)
```

顶层字段是跨协议稳定字段，`token_details` 保留供应商特有的分类统计。

## 4. 协议适配器接口

新增 `app/services/adapters/`，定义统一接口：

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

需要实现三个适配器：

- `OpenAIChatAdapter`
- `OpenAIResponsesAdapter`
- `AnthropicMessagesAdapter`

路由函数和 `GatewayService` 不直接创建 httpx 请求，只消费 `UpstreamRequest`。

## 5. 模型路由与配置

每个模型别名通过 `routes` 声明可用的供应商、上游模型和协议能力。同一条路由链按请求入口协议过滤，不跨协议 fallback。

```yaml
providers:
  openai:
    protocol: openai_responses
    base_url: https://api.openai.com
    api_key: ${OPENAI_API_KEY}
  anthropic:
    protocol: anthropic_messages
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}

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

配置校验规则：

- `models.<alias>.routes[].provider` 必须存在于 `providers`。
- `api` 取值只允许 `chat`、`responses`、`messages`、`all`，不再使用 `both`。
- `all` 表示该供应商自身支持的协议能力；例如 OpenAI 供应商的 `all` 指 `chat` 和 `responses`，Anthropic 供应商的 `all` 指 `messages`。
- `strategy` 支持 `priority` 和 `weighted_round_robin`。
- `weighted_round_robin` 只在相同协议的路由中轮询。

`ModelRouter.candidates(model, protocol)` 返回按策略排序后的 `RouteTarget` 列表，并负责熔断状态过滤和协议能力过滤。

## 6. 非流式调用流程

1. 根据入口解析 `protocol`。
2. 校验 `model` 是否存在，且协议与入口匹配。
3. 若包含 `prompt_ref`，渲染 Prompt 并合并到统一请求。
4. 应用结构化输出策略。
5. 使用 `ModelRouter` 获取候选路由。
6. 由 `ProtocolAdapter.build_request` 构造 `UpstreamRequest`。
7. `UpstreamClient.request_json` 执行 HTTP 调用。
8. 网络错误、超时和可重试状态码按退避策略重试。
9. 当前路由失败时，在同协议候选路由中 fallback。
10. 成功后由 adapter 解析为 `UnifiedResponse`。
11. 结构化输出需要时，本地 `jsonschema` 校验并修复重试。
12. 记录 usage、latency、retries、fallbacks。

## 7. 流式调用流程

1. 使用 `UpstreamClient.open_stream` 打开上游 SSE。
2. 适配器逐行解析 `UnifiedStreamEvent`。
3. 检测客户端断开，断开时取消上游读取并记录为 `cancelled`。
4. 首次出现内容增量时记录 TTFT。
5. 向下游发送内容。
6. 已发送内容后不切换模型；失败时发送错误事件和结束标记。
7. 流结束后记录 usage 和延迟。

TTFT 定义为“首个内容增量事件”出现的时间，而不是首个 HTTP 字节到达的时间。

SSE 对外采用参考项目的透明转发方式：下游收到的 `data:` 事件保持上游协议原生格式。适配器只在内部解析事件，用于提取内容增量、usage、TTFT 和 checkpoint，不强制改写下游事件格式。

## 8. 结构化输出

统一结构化输出规格：

```python
class StructuredOutputSpec(BaseModel):
    schema: dict[str, Any]
    name: str | None = None
    strict: bool = False
```

处理规则：

- OpenAI Responses：优先使用原生 `text.format` 传入 JSON Schema。
- Anthropic Messages：初始阶段通过系统提示注入 Schema 和“仅返回 JSON”约束，再由本地 `jsonschema` 二次校验。
- 当前阶段明确不升级 Anthropic tool schema；tool schema 仅作为后续可选扩展，不在本次实现范围内。
- 所有模型输出都不可信，必须本地解析和校验。
- 支持提取 Markdown code fence 中的 JSON。
- 校验失败后构造修复消息重试，重试次数由 `structured_output_retries` 控制。
- 流式输出一旦开始，不进行无损结构化修复；严格业务协议应使用非流式接口。

## 9. Prompt 版本管理

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
- 渲染使用 Jinja2 `SandboxedEnvironment` 与 `StrictUndefined`。

不同协议的插入位置：

- OpenAI Responses：系统 Prompt 写入 `instructions`。
- Anthropic Messages：系统 Prompt 写入 `system` 字段。
- 如果仍保留 Chat Completions 兼容入口：按 `prepend`/`append` 插入 `messages`。

## 10. 可观测性

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
cached_input_tokens
cache_creation_input_tokens
token_details
cost_usd
latency_ms
first_token_ms
retries
fallbacks
error_type
error_message
prompt_id
prompt_version
```

用量记录只保存 API Key 的不可逆短指纹，不保存密钥原文、Prompt 正文或用户消息正文。

## 11. 错误映射

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
| `prompt_not_found` | 404 |
| `prompt_render_error` | 422 |
| `structured_output_error` | 422 |
| `upstream_error` | 502 / 503 |
| `service_unavailable_error` | 503 |
| `stream_error` | 502 |

Anthropic 错误类型在 adapter 内映射为上述统一类型。

## 12. 按模型独立限流

建议将限流器从单实例全局限流重构为：

```python
class ModelRateLimiter:
    def check(self, model: str) -> None: ...
    def status(self) -> dict[str, RateLimitStatus]: ...
```

配置示例：

```yaml
rate_limit:
  default:
    requests_per_minute: 60
    burst: 10
  models:
    smart:
      requests_per_minute: 120
      burst: 20
    claude-fast:
      requests_per_minute: 30
      burst: 5
```

默认按公开模型别名独立限流，即 `D5` 已确认方案。一个模型耗尽不影响其他模型。超限返回 `429` 和 `Retry-After`。

## 13. 目标目录结构

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
│   │   ├── openai.py
│   │   └── anthropic.py
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

## 14. 阶段 0 验收标准

- 统一请求、响应、流式事件和 usage 模型已定义。
- 两个协议适配器接口已定义，接口边界不依赖具体 httpx 调用细节。
- 模型配置支持协议声明和同协议路由 fallback。
- SSE、结构化输出、Prompt、错误、用量、限流的语义已写入本文档。
- 后续阶段可以直接基于本文档实现，无需再改跨模块接口。

## 15. 已确认决策

| ID | 决策点 | 确认方案 |
| --- | --- | --- |
| D1 | 是否保留 `/v1/chat/completions` | 保留 Chat Completions 支持，路由协议能力使用 `chat`、`responses`、`messages`、`all`，不再使用 `both` |
| D2 | 对外 SSE 格式 | 沿用参考项目的透明转发方案，内部仅解析用于 usage、TTFT 和 checkpoint |
| D3 | “最多 3 次重试”的语义 | 首次请求 + 最多 3 次重试，总计最多 4 次请求 |
| D4 | 是否允许跨协议 fallback | 不允许，按入口协议过滤候选路由 |
| D5 | 限流维度 | 按公开模型别名独立限流 |
| D6 | Anthropic 结构化输出方案 | 系统提示注入 Schema + 本地 `jsonschema` 校验修复 |
| D7 | Prompt 在 Anthropic 中的位置 | 渲染后写入 `system` 字段 |
| D8 | `/v1/models` 是否必须鉴权 | 沿用参考项目实现，要求 Bearer Key 鉴权；`/healthz` 和 `/readyz` 不鉴权 |
