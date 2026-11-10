# WP-13 真实模型 Provider 与模型调用治理工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-13 真实模型 Provider 与模型调用治理 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-09 |
| 冻结日期 | 2026-11-10 |
| 优先级 | P1 |
| 依赖 | WP-07 accepted；WP-10 accepted；WP-11 accepted；WP-12 accepted |
| 间接依赖 | WP-01 State/Audit、WP-04 Gate、WP-05/WP-06 工具边界 |
| 下游工作包 | 无；后续 Provider 扩展需单独评审 |
| 对应需求 | `REQ-MODEL-001` 至 `REQ-MODEL-018` |
| 对应设计 | `design.md` 第 3、4、5.5、9、10、12、13、14、15 节 |
| 相关决策 | D-07、D-14、D-16、D-17、D-19、D-20、D-73、D-77、D-78、D-87 至 D-100 |

本文定义 WP-13 在 P1 阶段接入真实模型服务时必须满足的行为、安全边界、持久化语义和验收条件。
version 2 Provider、SecretResolver、模型调用审计、预算、migration v8 和本地真实 SDK 协议测试
已经实现；外部模型服务 live smoke 仍需端点与凭据证据。version 1 仍保持
`CLI_MODEL_UNAVAILABLE` 的 P0 注入语义。

文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-13 必须在保留 `smolagents.ToolCallingAgent` 决策循环和现有 Gate、Sandbox、State、Audit、
Checkpoint、Completion 边界的前提下，形成一个可配置、可审计、失败关闭的真实模型调用闭环：

1. 标准 `guarded-harness run/resume` 能从严格校验的配置构造真实 `smolagents.Model`；
2. 首个 Provider 固定支持 `openai_chat_completions_v1`，不通过 YAML 或模型输入动态 import；
3. 凭据只能通过受控 `SecretResolver` 获取，不进入任务配置、配置指纹、State、Audit、日志或
   子进程环境；
4. 每次 `Model.generate` 都经过 Harness 拥有的模型调用边界，先做 Run/租约/时间/预算检查，
   再发起网络请求；
5. 模型响应返回可用于工具调用的 `ChatMessage`，并满足大小、工具调用 ID、参数规模和 token
   usage 约束；
6. 每次模型调用有稳定 `model_call_id`、逻辑调用归组 ID、非空 Step、开始/结束事实、稳定错误码、
   usage 和成本结算；
7. 调用超时、重试、限流和结果未知不会绕过审计或盲重放；
8. 恢复时创建新模型和新 Agent，并重新校验模型配置身份、凭据可用性、状态和预算；
9. `approvals` 和 `inspect` 不创建模型、不访问网络、不读取密钥；
10. 自动化验收默认离线；真实 Provider 只作为显式 opt-in smoke test。

## 3. 范围与非目标

### 3.1 范围内

- 配置 schema v2 的 `model` 配置块和严格校验；
- OpenAI Chat Completions v1 Provider 工厂，使用 `smolagents` 公开模型类；
- 受控 `SecretResolver` 和受控端点策略；
- `GovernedModel`/ModelCallGateway 公开包装器；
- 模型调用状态、usage、成本预留和审计事件；
- 下一次单调 SQLite migration（基线为 v8）；
- `run/resume` 的标准 Provider 装配；
- 模型调用失败、超时、限流、响应格式错误和恢复拒绝的稳定错误码；
- 离线 fake Provider 测试和真实服务 opt-in smoke test；
- 需求、设计、决策、测试计划、运维说明、工作包索引和验收报告模板的同步。

### 3.2 非目标

- 不实现 LiteLLM、Bedrock、Azure、Anthropic 等额外 Provider 适配；
- 不实现模型自动路由、fallback、跨 Provider 负载均衡或多模型辩论；
- 不评估模型回答的质量、代码能力或安全对齐；
- 不把模型自述变成验收证据；
- 不提供 Web/TUI 配置界面、远程密钥托管、多租户凭据或分布式 limiter；
- 不允许通过 YAML、CLI argv、环境变量或模型工具参数加载任意 Python Provider；
- 不保存完整 prompt、完整模型响应、隐藏推理、原始 provider payload 或敏感 header；
- 不修改已发布 migration、历史 Audit 事件或 P0 的 `UNKNOWN_RESULT` 语义；
- 不把真实 Provider smoke test 设为普通 `pytest -q` 的必需前置。

## 4. 术语与不变量

### 4.1 术语

- **Provider**：把统一模型配置转换为真实网络客户端并返回 `smolagents.Model` 的受信托适配器。
- **Raw Model**：Provider 构造的底层模型；只负责传输，不负责授权、预算或审计。
- **Governed Model**：Harness 为单次 Run 注入的 `smolagents.Model` 包装器，所有 `generate`
  必须经过 ModelCallGateway。
- **Model Call**：一次逻辑上的模型生成尝试；每次重试是独立 attempt，但共享同一调用上下文。
- **Reservation**：调用前按最坏 token 上限预留的资源；调用后按实际 usage 结算，未知结果不发
  释放未确认预留。

### 4.2 必须保持的不变量

1. 没有有效的 `RUNNING`、租约和预算，不得发起真实模型请求。
2. 没有持久化 `MODEL_CALL_STARTED`，不得发起真实模型请求。
3. `step_callbacks`、HTTP 客户端日志或 `smolagents` 内部日志不能替代模型调用门禁。
4. 模型返回的工具调用仍然只是候选；执行前必须再次经过 `GovernedTool -> Gate`。
5. 真实 Provider 不返回 token usage 时不得按 0 计费或继续。
6. Provider 或 SDK 的隐式重试必须关闭；所有重试由 WP-13 的显式策略记录和审计。
7. 模型配置变化、凭据引用缺失或端点变化不得在旧 Run 中静默继续。
8. 模型调用已开始但结果未落盘时，恢复为 `MODEL_CALL_UNKNOWN`，不得自动重发。
9. 密钥值不得出现在配置指纹、数据库、审计、日志、异常消息或 CLI 输出中。
10. 模型服务不可用不等于任务失败证据；最终任务结论仍由 Completion 独立决定。

## 5. 外部契约

### 5.1 配置 schema v2

真实 Provider 只允许在 `version: 2` 配置中使用。`version: 1` 保持 P0 兼容，只能通过可信
`model_factory` 注入 Fake Model 或测试模型。

目标配置：

```yaml
version: 2
model:
  provider: openai_chat_completions_v1
  model_id: model-snapshot-or-deployment-id
  endpoint: https://model-service.example.com/v1
  credential_ref: MODEL_API_KEY
  tokenizer_id: tiktoken:o200k_base
  request_timeout_seconds: 120
  connect_timeout_seconds: 10
  max_input_tokens: 120000
  max_output_tokens: 4096
  max_response_bytes: 1048576
  max_tool_calls_per_response: 8
  requests_per_minute: 60
  max_retry_attempts: 1
  pricing:
    version: provider-price-v1
    input_per_million: "2.50"
    output_per_million: "10.00"
```

字段约束：

- `provider` 必须是固定枚举；WP-13 只允许 `openai_chat_completions_v1`。
- `model_id` 是稳定的服务标识；支持时优先使用不可变快照或部署版本。
- `endpoint` 默认必须为 HTTPS；localhost/私网 HTTP 需要显式开关且只能命中 allowlist。
- `credential_ref` 是引用名，不是密钥值；允许名称由代码固定 allowlist 或可信 SecretResolver
  映射决定。
- `tokenizer_id` 必须来自固定 registry，并与目标模型兼容；未知 tokenizer 直接拒绝配置。
- `max_input_tokens`、`max_output_tokens`、`max_response_bytes` 和
  `max_tool_calls_per_response` 必须为正整数且有平台上限。
- 发起请求前必须使用配置 `tokenizer_id` 对应的固定 TokenCounter 证明输入不超过
  `max_input_tokens`；未知 tokenizer、无法计数或输入超限时
  `MODEL_USAGE_INVALID`/`MODEL_INPUT_TOO_LARGE` 失败关闭。
- `max_retry_attempts` 默认 1，范围 0 至 3；只允许对明确未生成内容的限流或连接建立失败重试。
- `pricing` 来自可信配置，不从模型响应、环境变量或 Provider 价格接口动态读取。
- 所有字段均进入非秘密配置指纹；`credential_ref` 名称可以进入指纹，解析后的值不得进入。

### 5.2 Provider 与凭据

WP13-API-001：Provider 构造只能使用固定 allowlist 和公开 `smolagents.OpenAIServerModel`
Chat Completions 路径，不允许配置任意 import path、类名、Responses API、可调用对象或任意
`kwargs`。

WP13-API-002：`SecretResolver` 返回的密钥只进入客户端构造函数；调用方不能通过
`RunRequest`、CLI 参数、模型工具参数或普通日志覆盖。

WP13-API-003：`credential_ref` 未配置、名称不在 allowlist、环境值缺失或为空时，在发起网络
调用前以 `MODEL_CREDENTIAL_UNAVAILABLE` 失败关闭。

WP13-API-004：不读取 `.env`，不执行 dotenv 自动加载，不把凭据写入子进程环境。
HTTP client 必须 `trust_env=False`，不继承 `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`。

### 5.3 ModelCallGateway

Governed Model 必须实现公开 `smolagents.Model.generate` 契约，并委托 Raw Model 完成实际生成。
它不得改变 ToolCallingAgent 的提示词、工具选择或 ReAct 循环。

调用生命周期：

```text
Model.generate(messages, ...)
  -> 校验 Run 状态、租约、模型配置和剩余 wall-time
  -> 创建并持久化当前 Agent Step
  -> 固定 tokenizer 检查输入、预留最坏成本
  -> 原子写 model_call STARTED + reservation + Audit
  -> Raw Model.generate
  -> 校验响应和 usage
  -> 原子写 model_call 结果 + 实际成本结算 + Audit
  -> 返回 ChatMessage
```

任何前置检查失败时，Raw Model 调用次数必须为 0。STARTED ModelCall 必须绑定非空 `step_id`。
任何调用后校验失败时，不得把响应交给 Agent；必须持久化确定失败或未知事实并以稳定错误终止
当前安全流程。

### 5.4 Run 与 Resume

- `START` 创建 Run 后，Harness 为当前 Run 构造一次 Governed Model；
- `RESUME` 必须创建新的 Raw Model 和 Governed Model，不复用旧网络客户端或旧 memory；
- 恢复前重新校验配置指纹、模型 Provider、`model_id`、端点、定价策略和允许的模型能力；
- 模型配置不一致时不得静默换模型继续，必须返回 `HARNESS_CONFIG_RELOAD_FAILED` 或
  `RECOVERY_CONFIG_MISMATCH`；
- 若存在状态为 `STARTED` 且无确定结果的 model call，Run 进入
  `BLOCKED`，使用 `StopReason=UNKNOWN_RESULT` 和错误码 `MODEL_CALL_UNKNOWN`；模型调用次数
  不因 resume 清零，也不自动重新发送。

### 5.5 CLI 行为

- 标准 `run/resume` 可以从 version 2 配置构造真实模型；
- `approvals` 和 `inspect` 永远不加载凭据、不创建 Provider、不访问网络；
- 配置、凭据、端点或 Provider 构造失败必须发生在仓库副作用前；
- `--json` 输出只包含稳定错误码和有界脱敏说明；
- 现有退出码保持不变；模型认证、限流、超时、响应非法和未知结果通常映射为 `FAILED=6`，
  具体以稳定错误码区分；
- 真实服务 smoke test 必须显式 opt-in，不能由普通测试、配置或模型输入自动启用。

## 6. 行为契约

### 6.1 Provider 能力

WP13-BEH-001：Provider 必须支持 Chat Completions `ToolCallingAgent` 所需的工具 schema 和
结构化 tool calls。首版不接受文本内容中的 tool-call fallback，不调用
`parse_tool_calls` 作为兼容路径；没有结构化 tool calls 的响应失败关闭。

WP13-BEH-002：模型 ID 是配置身份的一部分。使用移动别名时，Provider/服务版本只能作为
非权威诊断信息记录，不能声称可复现完全相同的远端模型。

WP13-BEH-003：端点必须精确匹配协议、主机和路径；禁止 cross-host redirect、userinfo、URL
凭据片段、file/data scheme 和未声明的代理。

WP13-BEH-004：Raw Model 的 SDK 重试和 `smolagents` 的 `ApiModel.retry` 必须关闭，避免
治理层之外的隐式请求。Governed Model 不得并发调用同一 Run 的模型服务。

### 6.2 预算与成本

WP13-BEH-005：模型调用前必须创建并持久化 Agent Step，并检查 steps、tool calls、wall-time、
当前 settled cost 和未释放 reservation；任一不足时固定进入
`STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，网络调用次数为 0。

WP13-BEH-006：单次模型调用预留成本按可信 `max_input_tokens` 和 `max_output_tokens` 上限及
版本化价格策略计算，使用 Decimal。调用前必须用配置 `tokenizer_id` 对应的固定 TokenCounter
证明输入未超限；未知 tokenizer、计数失败或输入超限时不得调用 Provider。实际 usage 不得
超过请求上限，否则按 `MODEL_USAGE_INVALID` 失败关闭。

WP13-BEH-007：请求必须携带 `max_output_tokens` 对应的 Chat Completions 参数；超时取
`min(model.request_timeout_seconds, remaining_wall_time)`。首版不得通过截断旧 observation、
上下文摘要或隐式压缩绕过 `max_input_tokens`。

WP13-BEH-008：调用成功时把实际 input/output token 和成本写入账本并释放剩余 reservation。
调用明确失败且可证明未生成内容时释放 reservation；超时、连接中断或服务端 5xx 等无法确认
计费的场景保留 reservation 并标记 unknown。

### 6.3 响应校验

WP13-BEH-009：Raw Model 必须返回 `smolagents.models.ChatMessage`；否则拒绝并记录
`MODEL_RESPONSE_INVALID`。

WP13-BEH-010：响应必须包含非负、可解析的 input/output token usage。缺失、负数、非整数、
超出请求上限或与 Provider 明确报错矛盾时返回 `MODEL_USAGE_INVALID`。

WP13-BEH-011：响应内容、`raw` 摘要和工具调用参数总大小必须受
`max_response_bytes` 限制；超限不得截断后继续执行，而是失败关闭。

WP13-BEH-012：同一次响应内最多允许 `max_tool_calls_per_response` 个调用；调用 ID 必须非空、
唯一且稳定，不得自动合成。工具名和参数必须进入现有 adapter/Gate 校验。不能使用
`final_answer` 与其它工具混合的响应绕过 Gate。

WP13-BEH-013：完整响应、chain-of-thought、`raw` provider payload 和完整消息列表不得写入
State/Audit；只保存有界摘要、协议版本、工具名、调用 ID、usage、耗时和结果状态。

### 6.4 状态、事务与审计

WP13-BEH-014：新增 `model_calls` 事实，至少记录：

```text
model_call_id, logical_model_call_id, run_id, step_id, provider, model_id,
config_fingerprint, attempt_number, status,
started_at, ended_at, input_tokens, output_tokens,
actual_cost, reservation_cost, error_code,
provider_request_id, request_fingerprint
```

`model_call_id` 使用稳定前缀和 UUIDv4。每次重试是独立 ModelCall 和独立计费行，通过
`logical_model_call_id` 归组，不另建 attempt 表。STARTED 状态必须绑定非空 `step_id`；只有
调用前拒绝或历史迁移记录可以为空。

WP13-BEH-015：调用前 STARTED、预算 reservation 和 Audit 必须在同一事务提交；调用后的
SUCCEEDED/FAILED/TIMED_OUT/UNKNOWN、usage、成本结算和 Audit 在下一事务提交。

WP13-BEH-016：Audit 新增 `MODEL_CONFIG_REJECTED`、`MODEL_CALL_STARTED`、
`MODEL_CALL_SUCCEEDED`、`MODEL_CALL_FAILED`、`MODEL_CALL_TIMED_OUT`、
`MODEL_CALL_RATE_LIMITED`、`MODEL_CALL_UNKNOWN`、`MODEL_CALL_USAGE_INVALID`，并支持
`model_call_id` 关联。新事件使用 `audit_schema=2`，旧事件保持 schema 1 canonical payload 和
哈希语义；不得重写或重算历史链。不得保存原始消息或凭据。

WP13-BEH-017：模型调用 STARTED 后进程崩溃、结果事务未提交，恢复必须保留该 call 为不可确认
状态；`inspect` 可显示模型、开始时间、reservation 和关联 Step，但不自动重新发送。

### 6.5 重试、超时与失败关闭

WP13-BEH-018：只允许对 Provider 明确返回“请求未被接受/未生成内容”的 429 或连接建立失败进行
有限重试；read timeout、响应截断、5xx、TLS 之后的中断和响应解析失败默认视为 unknown，
不得自动重试。

WP13-BEH-019：每次 attempt 独立记录 model call/attempt 事实和 reservation；重试总次数受
配置上限与 Run 预算共同限制。某次重试预算不足时停止，不越过上限再试。

WP13-BEH-020：认证失败、端点拒绝、协议不支持、模型不存在和能力不匹配使用稳定
`MODEL_*` 错误码；错误文本必须脱敏、限长，不包含请求体、header 或凭据。

### 6.6 恢复

WP13-BEH-021：同一 Run 不允许从 `MODEL_CALL_UNKNOWN` 自动恢复；人工核对后只能启动新 Run
或通过未来单独评审的显式 reconcile 契约处理，不得在 WP-13 第一版增加强放行后门。

WP13-BEH-022：模型调用成功且响应已持久化、但工具尚未执行时，可以从新的 Agent 和确定性
continuation context 继续；任何工具调用仍重新经过 Gate。不得恢复旧模型 memory。

WP13-BEH-023：恢复时 Raw Model 创建失败或凭据缺失，不能把 Run 标为模型质量失败或任务失败
证据；应保留明确的基础设施失败事实，按失败关闭停止。

## 7. 状态机、事务与崩溃窗口

模型调用状态：

```text
PREPARED -> STARTED -> SUCCEEDED
                    \-> FAILED
                    \-> TIMED_OUT
                    \-> UNKNOWN_RESULT

PREPARED --预算/配置/租约拒绝--> REJECTED
STARTED  --进程崩溃/结果未持久化--> UNKNOWN_RESULT（恢复时物化）
```

事务边界：

| 时点 | 同一事务必须提交 |
| --- | --- |
| 调用前 | model call STARTED、attempt、reservation、Audit STARTED |
| 成功响应后 | usage、actual cost、reservation 结算、响应摘要、Audit SUCCEEDED |
| 明确失败后 | 稳定错误码、reservation 结算/释放、Audit FAILED/TIMED_OUT |
| 恢复判定后 | model call UNKNOWN、Run `BLOCKED/UNKNOWN_RESULT`、错误码 `MODEL_CALL_UNKNOWN`、Audit UNKNOWN |

崩溃窗口：

| 崩溃位置 | 可确认事实 | 恢复动作 |
| --- | --- | --- |
| reservation 前 | 无 attempt | 可重新做前置检查，网络调用 0 |
| STARTED 前 | 无模型请求事实 | 可重新做前置检查，网络调用 0 |
| STARTED 后、请求发出前 | 无法排除请求，保守视为已开始 | `MODEL_CALL_UNKNOWN`，不自动重发 |
| 网络响应后、结果事务前 | 可能有计费但无确定结果 | `MODEL_CALL_UNKNOWN`，不自动重发 |
| 成功结果已提交 | 有 usage 和响应摘要 | 使用已持久化事实继续，不重复请求 |
| 明确失败已提交 | 有稳定错误码 | 依据错误类型和策略继续或终止，不超出预算 |

## 8. 兼容、迁移与依赖

- `smolagents` 继续精确固定在 `1.26.0`；任何升级必须重新验证 `Model.generate`、
  `ChatMessage`、tool call、usage 和 `ToolCallingAgent.run(stream=True)` 契约；
- `pyproject.toml` 将经过约束的 OpenAI SDK 和 `tiktoken` TokenCounter 依赖加入默认运行时依赖，
  不新增 `real-model` optional extra；安装或版本不满足时返回 `MODEL_PROVIDER_UNAVAILABLE`，
  不得静默回退到 Fake Model；
- 配置 schema v2 与 v1 共存；v1 不获得真实 Provider；
- SQLite 下一次 migration 固定为 v8。若在施工前已有更高版本，必须顺延到下一未占用版本，
  不得修改既有 migration；
- migration 为 `audit_events` 增加可空 `model_call_id` 和 `audit_schema`，旧行回填 schema 1，
  新 `MODEL_*` 事件使用 schema 2；
- `model_calls` 和 reservation 记录的关键事实不得删除仍在 STARTED/UNKNOWN 的调用；
- P0 的 372 项基线测试必须继续通过；WP-13 新增测试不得依赖真实网络。

## 9. 测试要求

默认离线测试至少覆盖：

1. v2 合法配置、未知字段、版本混用、非法 Provider 和非法 URL；
2. 凭据缺失、allowlist 拒绝、值不进入 fingerprint/State/Audit/日志；
3. fake HTTP/provider 返回正常 tool call 和 final answer；
4. 401、403、404、429、5xx、连接拒绝、read timeout 和 malformed response；
5. 缺失/负数/超大 usage；
6. 响应过大、tool call 超过上限、重复 ID、空 ID、非法参数；
7. 前置预算不足时网络调用次数为 0；
8. reservation 预留、成功结算、明确失败释放和 unknown 保留；
9. 审计 STARTED 失败时 network call 为 0；
10. STARTED 后硬退出恢复为 model unknown 且不重发；
11. resume 时模型配置/端点/价格策略变化被拒绝；
12. `approvals`/`inspect` 不实例化 Provider、不访问网络；
13. 原生 ToolCallingAgent 仍负责决策，所有工具调用仍经过 Gate；
14. P0 Fake Model 测试保持全绿。

真实服务 smoke test：

- 使用独立 marker，例如 `live_model`，默认 skip；
- 只允许显式环境变量和手动命令启用；
- 发布验收配置必须固定 OpenAI Chat Completions 模型快照、端点 origin、价格和单次预算；
  自动化拒绝被标记为移动别名的 `model_id`；
- 使用低预算、只读代码理解任务；
- 验证真实响应产生 tool call、进入 Gate、产生 model call Audit 和最终 Completion；
- 不在 CI、普通 `pytest -q` 或交付验收中默认执行；
- 失败日志和证据必须脱敏。

## 10. 验收条件

| ID | 验收条件 |
| --- | --- |
| WP13-AC-001 | version 2 配置严格加载，固定 `openai_chat_completions_v1` 和 tokenizer registry 有正反测试 |
| WP13-AC-002 | 标准 `run/resume` 能通过可信 Factory 获取 `smolagents.Model`，不修改 Agent 核心 |
| WP13-AC-003 | Fake Provider 的结构化 tool call 和 final answer 通过现有 Gate/Completion，文本 fallback 被拒绝 |
| WP13-AC-004 | 无凭据、非法端点或 Provider 不可用时网络和仓库副作用均为 0 |
| WP13-AC-005 | 凭据值不出现在 fingerprint、SQLite、Audit、日志、CLI 或异常字段 |
| WP13-AC-006 | 每次 STARTED 模型调用绑定非空 `step_id`；attempt 可关联 `logical_model_call_id` 和 usage |
| WP13-AC-007 | 模型缺失或非法 usage 失败关闭，不按 0 计费 |
| WP13-AC-008 | tokenizer 未知、输入超限或预算不足时 Provider 调用次数为 0 |
| WP13-AC-009 | reservation 和实际成本结算正确，未知结果不释放保守预留 |
| WP13-AC-010 | SDK/smolagents 隐式重试关闭，显式重试 attempt 均被审计 |
| WP13-AC-011 | timeout 不超过剩余 wall-time，超时不会自动重发 |
| WP13-AC-012 | response/tool call 大小、数量、空 ID 或重复 ID 校验失败时不得交给 Gate |
| WP13-AC-013 | 工具调用仍经过 GovernedTool 和 Gate，模型无法直接触达 handler |
| WP13-AC-014 | model STARTED 后硬退出恢复为 MODEL_CALL_UNKNOWN 且不重发 |
| WP13-AC-015 | resume 时模型配置、端点、价格策略或 Provider 不匹配被拒绝 |
| WP13-AC-016 | `approvals`/`inspect` 不创建模型、不读取凭据、不访问网络 |
| WP13-AC-017 | 认证、限流、超时、协议和响应错误映射到稳定 `MODEL_*` 错误码 |
| WP13-AC-018 | 固定模型快照的 live smoke 可显式执行，普通测试无网络依赖 |
| WP13-AC-019 | migration v8 可重复初始化，旧 v7 数据库可安全升级且 P0 数据不受损 |
| WP13-AC-020 | P0 全量测试、目标测试、Ruff lint 和格式检查全部通过 |

## 11. 评审门禁

冻结门禁已关闭：

- D-87 至 D-100 已确认，作为本规格冻结输入；
- 确认本规格冻结和配置 schema v2 方案；
- 确认首个 Provider 仅为 `openai_chat_completions_v1`；
- 确认真实 OpenAI SDK 和固定 TokenCounter 为默认安装依赖；
- 确认凭据引用名称 allowlist；
- 确认未知 tokenizer、上下文超限和文本 tool-call fallback 均失败关闭；
- 确认 reservation 与 unknown 结果保留策略；
- 确认 `MODEL_CALL_UNKNOWN` 不自动恢复；
- 确认发布验收清单中的固定模型快照、端点、预算和人工 live smoke 方式；
- 更新 `docs/requirements.md`、`docs/design.md`、`docs/decisions.md`、`docs/test-plan.md`、
  `docs/operations.md`、工作包索引和任务拆解；
- `plan.md` 通过评审后由需求方批准。

规格已冻结。需求方已完成 DeepSeek live smoke，验收报告已转为 `accepted`。
