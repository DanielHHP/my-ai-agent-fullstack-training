# WP-13 真实模型 Provider 与模型调用治理实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-13 真实模型 Provider 与模型调用治理 |
| 文档状态 | done |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-09 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-07、WP-10、WP-11、WP-12 accepted |
| 已确认决策 | D-87 至 D-100 |
| 间接依赖 | WP-01、WP-04、WP-05、WP-06 的既有公共契约 |
| 当前结论 | T00-T09 完成；离线回归、SDK 协议仿真和外部 DeepSeek live smoke 均已通过 |
| 下游影响 | P1 模型服务接入、真实 Provider smoke test、后续 Provider 扩展 |

本计划不重新定义规格。若评审改变配置 schema、调用状态、预算、持久化格式、恢复语义或
错误码，必须先更新 `spec.md` 和必要的设计/决策文档，再调整任务卡。

## 2. 计划目标与完成定义

WP-13 完成后必须通过公共入口证明：

1. v2 配置可由正式 `load_config` 严格加载，v1 仍可由测试注入 Fake Model；
2. 标准 `run/resume` 能从可信 Provider 构造 `smolagents.Model`；
3. 原始 `ToolCallingAgent` 决策循环保持不变；
4. 每次模型调用先经过 Run/租约/wall-time/预算/审计检查；
5. 网络调用使用显式端点、受控凭据、有限超时和显式重试；
6. 响应、usage、大小和 tool call 结构在进入 Agent 前被校验；
7. 模型调用事实、usage、成本和审计可按 `model_call_id` 查询；
8. 缺失 usage 不按 0 计费；预算不足时 Provider 调用次数为 0；
9. 模型调用时间预算受剩余 wall-time 约束；
10. STARTED 后崩溃恢复为 unknown 且不自动重发；
11. `approvals` 和 `inspect` 不触达网络；
12. 默认测试离线，真实 smoke test 显式 opt-in；
13. P0 全量测试、Ruff lint 和格式检查保持通过；
14. `acceptance-report.md` 由实际证据生成，不得用本计划预期代替。

## 3. 文件布局

```text
src/guarded_harness/
├── config.py             # v1/v2 联合配置、ModelConfig、Endpoint、Pricing
├── domain.py             # ModelCallId、ModelCallStatus、错误码和结果类型
├── model_runtime.py      # Provider Factory、SecretResolver、GovernedModel
├── harness.py            # 每次 Run 创建模型调用边界并装配 Agent
├── state_store.py        # model call、reservation、usage 查询和事务
├── storage.py            # migration v8、model_calls/reservation 表
├── audit.py              # model call 事件和关联字段
├── cli.py                # v2 Provider Factory 装配、稳定错误映射
└── __init__.py           # 仅导出已实现且冻结的 P1 公共类型

tests/
├── test_model_config.py       # schema v2、端点、凭据引用和指纹
├── test_model_runtime.py      # fake provider、响应/usage 校验、重试和限流
├── test_model_calls.py        # STARTED/结果/预算/reservation/审计
├── test_harness_loop.py       # 扩展：真实调用边界下仍由 ToolCallingAgent 决策
├── test_cli.py                # 扩展：run/resume Factory、inspect/approvals 零网络
└── live/
    └── test_live_model_smoke.py

examples/config/
└── model-openai-chat-completions.yaml

docs/work-packages/wp-13-real-model-provider/
├── spec.md
├── plan.md
└── acceptance-report.md       # 实测完成后创建
```

`tests/live/` 中的测试必须默认 skip，仅由显式 marker/环境变量启用。其 skip 不得被用于掩盖
默认离线测试缺失。

## 4. 依赖与准入

```text
WP-01 State/Audit ─────────────┐
WP-04 Gate + WP-10 Agent ──────┼──> WP-13
WP-11 CLI + WP-12 P0 交付 ─────┘
```

准入条件：

1. WP-07、WP-10、WP-11、WP-12 验收报告均为 `accepted`；
2. 当前 P0 全量测试、Ruff lint 和格式检查有可复现绿色基线；
3. `smolagents==1.26.0`、`OpenAIServerModel`、`Model.generate` 和
   `ToolCallingAgent.run(stream=True)` 契约探针通过；
4. D-87 至 D-100 已确认，`spec.md` 完成最终评审；
5. `plan.md` 批准；
6. 不修改任何既有 migration 和 append-only Audit 事实。

依赖不满足时只允许文档、兼容性探针和无网络 fake fixture，不得提前宣称真实服务可用。

## 5. 决策门禁

施工前必须关闭：

```text
WP13-Q01 配置与部署模型边界             -> D-87
WP13-Q02 首个 Provider 与公开适配       -> D-88
WP13-Q03 凭据解析和端点安全             -> D-89
WP13-Q04 ModelCallGateway 注入位置      -> D-90
WP13-Q05 调用前预算预留                 -> D-91
WP13-Q06 调用状态、审计和响应上限       -> D-92
WP13-Q07 超时、限流和重试边界           -> D-93
WP13-Q08 恢复与模型配置不一致           -> D-94
WP13-Q09 CLI Provider 可用性和只读命令  -> D-95
WP13-Q10 离线测试与 live smoke          -> D-96、D-100
WP13-Q11 TokenCounter 与上下文超限       -> D-97
WP13-Q12 结构化 tool call 与调用 ID      -> D-98
WP13-Q13 Step 与 attempt 身份            -> D-99
```

任一决策变更时，同步 `decisions.md`、`design.md`、`spec.md` 和测试矩阵。

## 6. 详细设计

### 6.1 配置与 SecretResolver

1. 将顶层版本建模为 v1/v2 联合类型，保持 v1 既有字段与行为。
2. v2 增加必填 `model` 块；所有字段 `extra="forbid"`。
3. `ModelConfig` 只允许 `openai_chat_completions_v1` Provider 和窄范围字段；不接收任意
   `client_kwargs`、header、动态类名或 SDK 参数。
4. `credential_ref` 由固定 allowlist 的 `SecretResolver` 解析。测试注入 fake resolver；
   生产 resolver 只读取显式允许的环境变量。
5. 配置规范化后计算 fingerprint，包含全部非秘密字段；密钥值不进入任何 Pydantic dump。
6. endpoint 校验使用标准 URL parser：优先 HTTPS；localhost/HTTP 必须有显式窄开关。
7. pricing 使用 Decimal 转换并设置上界，防止 NaN、无限值或异常大成本。
8. 配置必填 `tokenizer_id`；只能解析固定 registry，调用前必须用对应 TokenCounter 证明输入
   不超过 `max_input_tokens`。未知 tokenizer、计数失败或超限时本地返回稳定错误。
9. 首版不实现上下文压缩、旧 observation 截断或隐式模型摘要；超限只能失败或将任务拆分。

示例解析流程：

```text
load_config(path)
  -> validate v2 model block
  -> normalize endpoint and pricing
  -> resolve credential_ref through SecretResolver
  -> construct raw smolagents.Model
  -> never persist resolved secret
```

Provider 构造失败必须在 Harness 创建 Run 或触发仓库副作用之前完成；实际认证网络请求只在
Governed Model 的 STARTED 事务之后发生。

### 6.2 Provider Factory

首个实现固定为：

```text
OpenAIChatCompletionsProvider
  -> smolagents.OpenAIServerModel(
       model_id=...,
       api_base=...,
       api_key=<resolved secret>,
       retry=False,
       client_kwargs={
         "max_retries": 0,
         "timeout": request_timeout_seconds,
         "http_client": <httpx.Client(trust_env=False, follow_redirects=False)>,
       },
     )
```

要求：

- 不使用 `LiteLLMModel` 或 Responses API，避免 WP-13 引入广域 Provider 兼容面；
- SDK 与 smolagents 隐式重试均关闭；
- 不为 `generate` 注册 monkey patch；
- 不把真实客户端暴露给模型、工具或可读 Audit；
- Provider 只返回 Raw Model，预算、审计、超时和 unknown 语义由 Governed Model 统一处理；
- `isinstance(raw_model, smolagents.Model)` 和公开方法契约必须验证。

OpenAI SDK 和 `tiktoken` TokenCounter 依赖加入 `pyproject.toml` 默认运行时依赖，不增加
`real-model` extra；包不可用或版本不兼容时返回 `MODEL_PROVIDER_UNAVAILABLE`，不能回退
Fake Model。

### 6.3 GovernedModel 与 ModelCallGateway

新增 `model_runtime.py`：

```text
GovernedModel(Model)
  - model_id
  - delegate: Model
  - call_service: ModelCallService
  - generate(messages, stop_sequences, response_format, tools_to_call_from, **kwargs)
```

`generate` 仅做薄适配：

1. 通过公开 `call_service.before_call(...)` 创建当前 Agent Step，并执行状态、租约、
   wall-time、预算、tokenizer 和 reservation；
2. 获得本次 `model_call_id`、`logical_model_call_id`、attempt、当前 `step_id` 和有效 timeout；
3. 调用 `delegate.generate(...)`；
4. 通过 `call_service.validate_response(...)` 校验 `ChatMessage`、usage、tool calls 和大小；
5. 通过 `call_service.record_success(...)` 原子写 usage、成本、摘要和 Audit；
6. 返回原 `ChatMessage`，不改变工具调用 ID 或解析逻辑。

异常映射：

- 明确的认证、参数、模型不存在、协议不支持：确定失败；
- 429 且 Provider 明确未生成：仅按策略有限重试；
- read timeout、连接中断、5xx、响应截断：记录可能的 side-effect/unknown，不自动重试；
- 成功返回但 usage/响应非法：`MODEL_USAGE_INVALID`/`MODEL_RESPONSE_INVALID`，不交给 Agent。

不得覆盖 `ToolCallingAgent._step_stream`、`_run_stream` 或私有 memory 字段。

首版只接受 Chat Completions 结构化 tool calls，不调用文本 fallback；tool-call ID 必须非空且
唯一，Provider 缺失时拒绝，不自动合成。

### 6.4 模型调用状态与持久化

`domain.py` 新增：

```text
ModelCallId = modelcall_[0-9a-f]{32}
LogicalModelCallId = logicalcall_[0-9a-f]{32}

ModelCallStatus =
    PREPARED
    STARTED
    SUCCEEDED
    FAILED
    TIMED_OUT
    UNKNOWN_RESULT
    REJECTED
```

新增稳定错误码至少包括：

```text
MODEL_CONFIG_INVALID
MODEL_PROVIDER_UNAVAILABLE
MODEL_CREDENTIAL_UNAVAILABLE
MODEL_ENDPOINT_DENIED
MODEL_AUTH_FAILED
MODEL_NOT_FOUND
MODEL_RATE_LIMITED
MODEL_TIMEOUT
MODEL_PROTOCOL_UNSUPPORTED
MODEL_INPUT_TOO_LARGE
MODEL_RESPONSE_INVALID
MODEL_RESPONSE_TOO_LARGE
MODEL_TOOL_CALLS_INVALID
MODEL_USAGE_INVALID
MODEL_CALL_UNKNOWN
```

`storage.py` migration v8 增加：

- `model_calls`
- `model_cost_reservations`
- `audit_events.model_call_id`（nullable）
- `audit_events.audit_schema`（旧行回填 1，新 Model 事件使用 2）

每次重试使用独立 `model_calls` 行和 `attempt_number`，不另建可漂移的第二套 attempt 事实。
同一逻辑调用通过 `logical_model_call_id` 归组。STARTED 行必须有非空 `step_id`；Harness 在
首次模型调用前创建 Step。`model_calls` 至少保存 spec 中的字段；不得保存完整 messages、
raw response 或 key。

调用前事务：

```text
BEGIN IMMEDIATE
  verify run/lease/budget
  insert/advance model call STARTED
  reserve worst-case cost
  append MODEL_CALL_STARTED
COMMIT
```

调用后事务：

```text
BEGIN IMMEDIATE
  verify lease/revision
  settle actual usage or preserve unknown reservation
  write bounded response summary
  append result Audit
COMMIT
```

### 6.5 预算和成本

新增 `ModelPricingPolicy`，替代 P1 真实调用的单一 per-token 近似：

```text
input_per_million
output_per_million
version
```

调用前：

- `remaining_cost = max_token_cost - settled_cost - active_reservations`
- `reservation = ceil(max_input_tokens * input_rate + max_output_tokens * output_rate)`
- 调用前以固定 `tokenizer_id` 对应的 TokenCounter 证明输入不超过 `max_input_tokens`
- reservation 不足时不调用 Provider；
- wall-time timeout 取模型配置与剩余 wall-time 的最小值。

调用后：

- input/output usage 必须在请求上限内；
- actual cost 以实际 usage 和版本化价格计算；
- 成功或明确未生成失败时释放 reservation 差额；
- timeout/5xx/响应丢失保持 reservation，防止后续盲目超支；
- 所有金额使用 Decimal，禁止 float。
- 上下文超限不截断、不摘要、不隐式压缩，直接失败关闭。

现有 `BudgetUsage.token_cost` 继续表示已结算成本；未释放 reservation 在可用预算计算中单独扣除，
不能伪装为已发生成本。

### 6.6 响应和 tool call 校验

在返回 Agent 前检查：

- 类型为 `ChatMessage`；
- 非空 usage 且非负整数；
- `input_tokens <= max_input_tokens`、`output_tokens <= max_output_tokens`；
- 内容、工具名、参数和 raw summary 的总大小不超过 `max_response_bytes`；
- 工具调用数不超过上限；
- call ID 非空且同一响应内唯一；
- `final_answer` 不能与其他工具调用混合；
- 只接受结构化 tool calls，不允许从 content 文本 fallback 解析；
- Provider 缺失 call ID 时拒绝，不生成 synthetic ID；
- 工具 schema 仍由 adapter 和 Gate 最终验证。

超大或非法响应必须失败关闭，不得截断后继续，也不得通过提示词要求模型“重答”后自动绕过预算。

### 6.7 恢复

恢复顺序：

```text
load config v2
  -> validate config fingerprint
  -> resolve credential and build new Raw Model
  -> inspect model calls
  -> if STARTED without result: BLOCKED + UNKNOWN_RESULT + MODEL_CALL_UNKNOWN
  -> if SUCCEEDED: use persisted bounded result
  -> if FAILED/TIMED_OUT allowed: apply retry policy or stop
  -> build new GovernedModel and new Agent
  -> agent.run(stream=True)
```

`MODEL_CALL_UNKNOWN` 第一版不提供自动 reconcile 或 force resume。需要继续任务时启动新 Run，
避免重复计费、重复生成和不确定的工具提案。

### 6.8 CLI 集成

1. `main` 在 `run/resume` 分支调用 `load_config`，从 v2 配置构造 Provider。
2. `approvals`/`inspect` 只使用 State/Audit/Approval 服务，分支中不引用 Provider。
3. 模型 Factory 返回 Raw Model，Harness 在每次 Run 内包装为 GovernedModel。
4. 配置或凭据错误在 `Harness.run()` 前返回 `MODEL_*`；已有 Run 的恢复配置不一致进入
   `HARNESS_CONFIG_RELOAD_FAILED`。
5. 退出码继续使用 P0 映射：错误类别映射 6，预算 4，阻塞 5；不另造与 Run 结果冲突的码。
6. stdout/stderr/JSON 使用统一 Redactor，不输出 endpoint 查询参数、header、key 或完整响应。
7. HTTP client 固定 `trust_env=False` 和 `follow_redirects=False`，不继承代理环境变量。

### 6.9 测试实施

离线 fake 层：

```text
FakeModelTransport
  - queue ChatMessage/errors
  - record messages/tools and call count
  - simulate 429/timeout/malformed/usage missing
  - never touch network
```

重点测试：

- schema v2 和 v1 兼容；
- secret resolver 和 fingerprint；
- call start 失败时 transport calls=0；
- reservation 和 budget；
- response bounds、tool-call IDs 和 usage；
- timeout/retry，确认 SDK 无二次隐式请求；
- model call 事务和 Audit；
- crash runner 在 STARTED 后硬退出；
- CLI 只读命令零 Provider/零网络；
- P0 六个必须测试文件回归。

live smoke：

- 使用独立 `live_model` marker；
- 测试前必须显式要求 `GUARDED_HARNESS_LIVE_MODEL=1`、配置路径和凭据；
- 发布验收配置必须固定模型快照、端点 origin、价格和单次预算，拒绝计划中标记为移动别名的
  `model_id`；
- 使用最小只读代码理解任务和极低预算；
- 断言至少一个 `MODEL_CALL_SUCCEEDED`、一个 Gate 工具调用和 `ACCEPTED`；
- 日志不记录 prompt、raw response 或密钥；
- 该测试不作为默认 CI/交付前置。

## 7. 任务卡

### WP13-T00 契约冻结与兼容探针

- 目标：冻结 v2 配置、模型调用状态、错误码、usage、reservation 和恢复语义。
- 前置：D-87 至 D-100 已确认。
- 文件：`domain.py`、`config.py`、`tests/test_domain.py`、`tests/test_config.py`、规格。
- 正常：v1/v2 均可严格加载；v2 model block 可用。
- 拒绝：未知字段、动态 Provider、非法 URL、非法价格、密钥字段注入。
- 退出：契约测试和 smolagents 公开接口探针通过。

### WP13-T01 Provider 与 SecretResolver

- 目标：提供 OpenAI Chat Completions v1 Raw Model Factory 和默认安装依赖。
- 文件：`model_runtime.py`、`pyproject.toml`、`tests/test_model_config.py`。
- 正常：fake secret + valid endpoint 构造 `smolagents.Model`。
- 拒绝：凭据缺失、Provider 缺失、非 allowlist 端点、HTTPS/私网规则不满足。
- 退出：构造失败网络和仓库副作用均为 0。

### WP13-T02 GovernedModel

- 目标：所有 `generate` 经过 ModelCallGateway，并在首次调用前创建 Step。
- 文件：`model_runtime.py`、`harness.py`、`tests/test_model_runtime.py`。
- 正常：结构化 ChatMessage 返回原生 ToolCallingAgent 工具循环。
- 拒绝：无 lease、非 RUNNING、文本 fallback、空/重复 call ID、invalid response、usage 缺失。
- 退出：`step_callbacks` 变更不影响门禁，Agent 核心未修改。

### WP13-T03 持久化与 Audit

- 目标：迁移 v8 和 model call 生命周期。
- 文件：`storage.py`、`state_store.py`、`audit.py`、`domain.py`、相关测试。
- 正常：STARTED/结束/usage/reservation 可跨进程读取，v1/v2 Audit 哈希按各自 schema 校验。
- 拒绝：审计或调用记录写失败时网络调用为 0。
- 退出：Audit chain 有效且 `inspect` 不泄露原始消息。

### WP13-T04 预算与成本

- 目标：preflight、reservation、settlement 和 unknown reservation。
- 文件：`model_runtime.py`、`state_store.py`、`domain.py`、预算测试。
- 正常：固定 tokenizer 通过后按 input/output usage 结算实际成本。
- 拒绝：预算不足、未知 tokenizer、输入超限、usage 超请求上限、价格非法。
- 退出：调用前拒绝 provider calls=0，unknown 不释放未确认预留。

### WP13-T05 超时、限流与重试

- 目标：关闭 SDK 隐式重试，并实现显式 attempt。
- 文件：`model_runtime.py`、`state_store.py`、`tests/test_model_runtime.py`。
- 正常：429/连接建立失败在预算内有限重试。
- 拒绝：read timeout、5xx、malformed response 不自动重试。
- 退出：每次 attempt、错误码和耗时均审计。

### WP13-T06 CLI Provider 装配

- 目标：标准 run/resume 可使用 v2 Provider。
- 文件：`cli.py`、`__main__.py`、`tests/test_cli.py`。
- 正常：run/resume 构造 Factory；inspect/approvals 零模型。
- 拒绝：凭据/Provider 不可用时仓库副作用为 0。
- 退出：stdout/stderr/JSON 脱敏，P0 退出码兼容。

### WP13-T07 恢复与崩溃

- 目标：model STARTED 后硬退出恢复为 unknown。
- 文件：`state_store.py`、`checkpoint.py`、`harness.py`、`tests/test_model_calls.py`。
- 正常：已确认成功结果不重新请求。
- 拒绝：配置变化、unknown call、旧 reservation 冲突被阻断。
- 退出：跨进程恢复无网络重放，`inspect` 可解释原因。

### WP13-T08 示例、测试与 live smoke

- 目标：提供无密钥示例、manually gated live test 和测试矩阵。
- 文件：`examples/config/model-openai-chat-completions.yaml`、`tests/live/`、`docs/test-plan.md`。
- 正常：普通 pytest 完全离线；live marker 只在显式授权和固定模型快照下运行。
- 拒绝：默认测试中发现真实网络、skip 掩盖或密钥文本。
- 退出：测试计划和证据格式更新。

### WP13-T09 文档与验收

- 目标：实现与需求、设计、决策、运维、索引和报告一致。
- 文件：`requirements.md`、`design.md`、`decisions.md`、`operations.md`、索引和验收报告。
- 正常：文档明确 P1 与 P0 边界。
- 拒绝：把计划写成已实现或把 Fake Model 结果标注为真实模型。
- 退出：所有质量门禁和 live smoke 证据写入 `acceptance-report.md`。

## 8. 测试矩阵

| 风险 | 测试 | 期望 |
| --- | --- | --- |
| 配置注入 | v2 unknown field / Provider / tokenizer | `CONFIG_SCHEMA_INVALID` / `MODEL_CONFIG_INVALID` |
| 密钥泄漏 | resolver/redaction/fingerprint | 所有持久化输出无 secret |
| 端点逃逸 | scheme/host/redirect/userinfo | `MODEL_ENDPOINT_DENIED` |
| 预算超支 | reservation boundary | provider calls=0 或 STOPPED |
| 隐式重试 | fake transport count | 每次请求都在 audit 中 |
| usage 缺失 | fake response no usage | `MODEL_USAGE_INVALID` |
| 文本 tool-call fallback | ChatMessage content only | `MODEL_RESPONSE_INVALID` |
| 缺失 tool-call ID | fake structured call without id | `MODEL_TOOL_CALLS_INVALID` |
| Agent 绕过 | fake tool call | 只通过 GovernedTool/Gate |
| 审计失败 | before STARTED | network calls=0 |
| 崩溃恢复 | STARTED then os._exit | BLOCKED + UNKNOWN_RESULT + MODEL_CALL_UNKNOWN |
| 配置漂移 | resume with changed model block | config mismatch |
| 只读命令 | inspect/approvals | provider calls=0 |
| 真实服务 | live marker opt-in | tool call + completion accepted |

## 9. 质量门禁

施工与验收命令：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_model_config.py tests/test_model_runtime.py tests/test_model_calls.py -q
python -m pytest tests/test_harness_loop.py tests/test_cli.py tests/test_audit.py tests/test_state_checkpoint.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

真实 smoke test 由人工显式执行，不能替代上述门禁：

```bash
GUARDED_HARNESS_LIVE_MODEL=1 \
python -m pytest tests/live/test_live_model_smoke.py -q -m live_model
```

最终报告必须记录实际版本、命令、退出码、测试计数、live provider、模型 ID、预算、证据和剩余
限制；不能复制本文示例值。

## 10. 迁移与回滚

- migration v8 只追加新表和索引，不修改 v1-v7 或历史 audit 行；
- 新增 v8 前先备份数据库，并执行 schema integrity、foreign key 和旧 Run 读取测试；
- 若 v8 施工失败，停止写入并恢复备份，不通过代码删除 model call 或 reservation 行；
- 代码回滚必须同时拒绝读取不支持的 v8 数据库，不能静默降级为 P0；
- 配置 v2 回滚不影响 v1 测试配置；
- Provider 异常时可切回注入 Fake Model，但必须在新的 Run 中执行；旧 Run 不得改变模型身份。

## 11. 风险与剩余边界

- Provider 实际计费可能因缓存、套餐或服务端差异偏离配置价格；统一资源成本只能作为治理预算，
  不能冒充账单。
- 移动模型别名无法保证远端模型完全可复现；应优先固定版本。
- OpenAI Chat Completions 只保证协议形状，不保证模型工具调用质量。
- 不同兼容服务的 tokenizer 可能不同；无法建立可信输入上界时必须拒绝而非估算放行。
- 相同 Run 跨进程恢复时无法恢复旧模型 memory；依赖 continuation context 和重新决策。
- 网络路径、代理和 TLS 信任仍依赖宿主环境；WP-13 不宣称 OS 级网络隔离。
- 模型调用结果未知时保守保留 reservation，可能暂时降低可用预算，但不允许超支或盲重放。

## 12. 完成定义

只有以下条件全部满足，WP-13 才可从 `draft` 进入 `DONE`：

1. `spec.md` frozen、`plan.md` approved；
2. WP13-T00 至 WP13-T09 完成；
3. WP13-AC-001 至 WP13-AC-020 有实测证据；
4. P0 全量测试、目标测试、Ruff lint 和格式检查通过；
5. live smoke 至少对一类只读任务成功，并且未将 Fake Model 结果冒充真实模型；
6. 审计可关联 model call、tool call、approval、checkpoint 和 completion；
7. 文档、配置、测试和实现没有 P0/P1 状态冲突；
8. `acceptance-report.md` 已生成并复核为 `accepted`。
