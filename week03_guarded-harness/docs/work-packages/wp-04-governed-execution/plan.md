# WP-04 治理执行内核实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-04 治理执行内核 |
| 文档状态 | done |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-05 |
| 对应规格 | `spec.md` v1.0.0 / frozen |
| 前置依赖 | WP-00 v1.3.0、WP-01 v0.4.0、WP-02 v0.2.0、WP-03 v1.0.0 |
| 施工前置 | 已满足：D-37 至 D-44 已确认 |
| 完成日期 | 2026-11-05 |
| 当前结论 | T00 至 T09 已完成，验收报告 accepted |
| 下游影响 | WP-05 只读工具、WP-06 写/命令、WP-07 恢复、WP-10 Agent、WP-11 CLI |

本计划不重新定义规格。决策确认后先冻结并提升 `spec.md` 版本，再同步本计划并进入施工。施工中发现
接口或事务语义冲突时，必须停止当前任务，先修订规格和相关上游契约。

## 2. 计划目标与完成定义

WP-04 完成后必须仅通过公共接口和测试 handler 证明：

1. Registry 只保存可信注册信息，模型快照不暴露 handler；
2. Policy 对可见性、角色权限、执行模式和风险进行确定性判定；
3. Gate 按固定顺序完成 schema、语义/路径、权限、风险、审批、重复和预算检查；
4. 所有拒绝在 handler 调用前发生，副作用计数为 0；
5. 未审写操作进入 `WAITING_APPROVAL`，审批后重新过 Gate；
6. 审批绑定、claim、consume 和 replay suppression 使写 handler 最多成功执行一次；
7. PREPARED、STARTED 和确定/未知结果形成可重开读取的事实链；
8. State/Audit/Approval 故障时不执行新副作用，也不返回伪造成功；
9. observation 有界、脱敏、结构化，拒绝审计满足飞书 A1；
10. G1/G2/G3/G4 以及相关 S1/S3/C2/C3/H3 包内责任有自动化测试；
11. 目标测试、全量测试、Ruff、格式检查和 `pip check` 通过；
12. 验收报告基于实际命令与证据给出结论。

WP-04 完成不等于真实工具、Checkpoint、Agent、CLI 或 Completion 完成。

## 3. 文件范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── registry.py         # 新增：可信工具注册与模型快照
├── policy.py           # 新增：权限、模式、风险和可见性策略
├── gate.py             # 新增：固定检查链与执行生命周期
├── domain.py           # 扩展：Gate/Registry/Policy 公共类型、错误码
├── __init__.py         # 扩展：公共导出
├── state_store.py      # 追加只读 revision/租约复核公共方法
└── approval.py         # 原则上不改；仅当确认接口缺口时做最小兼容修订

tests/
├── test_registry_policy.py # 新增
├── test_gate.py            # 新增
├── test_domain.py          # 按公共模型扩展
├── test_state_checkpoint.py # 增加 revision/租约与审批收口契约
└── conftest.py             # 按需增加 fake handler/fault fixture

docs/work-packages/wp-04-governed-execution/
├── spec.md
├── plan.md
└── acceptance-report.md    # 完成实测后创建
```

本包不创建 `checkpoint.py`、`tools/` 生产 handler、`harness.py`、`agent_adapter.py` 或 `cli.py`。

### 3.2 依赖方向

```text
domain.py
   ^
   |
policy.py      registry.py
       \        /
          gate.py
        /  |  |  \
       v   v  v   v
 State Audit Approval Sandbox

tests -> public API
```

禁止：

- `registry.py` 或 `policy.py` 导入 State、Audit、Approval 或 Sandbox；
- `gate.py` 使用 StateStore/ApprovalStore 的私有方法或直接 SQL；
- handler 获取原始参数、租约 token 或绕过 Gate 的写接口；
- 根据 `code_understanding`/`test_repair` 任务名在 Gate 内散落分支；
- 修改或 monkey patch `smolagents`。

## 4. 施工前上游契约修订

### 4.1 Revision 读取缺口

当前 WP-01 的条件更新都要求 `expected_revision`：

```text
StateStore.create_invocation(..., expected_run_revision, ...)
StateStore.record_budget_delta(BudgetDeltaCommand(expected_run_revision=...), ...)
StateStore.transition_run(..., expected_revision, ...)
StateStore.transition_invocation(..., expected_revision, ...)
```

但公开 `Run`、`ToolInvocation` 和 `RunSnapshot` 都没有当前 revision，Gate 无法在不读取私有字段或
局部 SQL 的前提下安全组合事务。施工前必须由需求方选择：

1. 推荐：在 WP-01 追加只读公共接口：

   ```text
   StateStore.get_run_revision(run_id) -> int
   StateStore.get_invocation_revision(run_id, tool_call_id) -> int
   ```

2. 备选：扩展 `RunSnapshot`/`ToolInvocation` 公共模型并升级 schema。

方案 1 不改变数据库表，但需要 WP-01 规格兼容版本和契约测试。方案 2 会扩大持久化/API 兼容面。
不得采用 `_get_run_revision` 私有调用。

### 4.2 ToolObservation 缺口

现有模型无法完整表达：

- `WAITING_APPROVAL` 对应的 `approval_id`；
- replay suppression 对应的多个原始证据；
- 输出被有界截断。

按 D-38 升级 `ToolObservation` schema v2，并保持对旧 `evidence_id` 的派生兼容。该变更需同步
领域契约测试、包根导出和 WP-10 摘要/observation 转换计划。

## 5. 详细设计

### 5.1 Registry

内部可信对象：

```text
ToolDefinition:
  spec: ToolSpec
  handler: ToolHandler
  normalize_args: JsonNormalizer | None
```

注册流程：

```text
validate ToolDefinition type
  -> validate ToolSpec fields/JSON schema
  -> validate handler callable
  -> validate normalizer callable/None
  -> reject duplicate tool name
  -> reject model-visible name collision
  -> store immutable entry
```

要求：

- 注册只发生在可信装配阶段；没有公开的模型注册工具调用；
- 重复名称注册与重新绑定 handler 均失败；
- `snapshot` 返回按工具名稳定排序的 `ToolSpec`，字段深拷贝或重新校验；
- snapshot 可以应用可见性过滤，但 Gate 执行时仍重新检查可见性；
- handler 或 normalizer 不得出现在序列化结果、审计或 observation 中。

### 5.2 Policy

拟议不可变策略：

```text
PolicyEngine:
  allowed_tool_names
  role_permissions
  task_approval_tools
  read_execution_limit = 2
  write_execution_limit = 1
```

`ToolExecutionContext` 由 Harness 可信构造。Policy 阶段：

```text
is_visible =
    tool.name in registry
    and tool.name in allowed_tool_names

permission_union = union(role_permissions[role] for role in context.roles)
permissions_ok = spec.required_permissions subset of permission_union

mode_ok = context.mode == EXECUTE
          or spec.risk == READ

approval_required =
    spec.approval_requirement == REQUIRED
    or spec.name in task_approval_tools
```

角色名、权限和工具名都必须去除控制字符并做配置阶段校验。角色权限不从模型参数生成。任务模式或
角色配置来源见 D-43。

### 5.3 Schema 与规范化

Registry 在注册时验证 JSON Schema 可编译，且根 object 明确拒绝额外字段。Gate 每次运行使用同一个
validator：

```text
validate raw args against spec.input_schema
  -> reject invalid JSON values, depth > 32, nodes > 10_000, encoded size > 64 KiB
  -> deep-freeze/copy validated args
  -> call trusted normalizer if present
  -> revalidate normalized args against the same schema
  -> normalize every path argument through Sandbox
  -> calculate canonical JSON and args digest
```

normalizer 只能做纯语义转换，不得读写仓库、启动进程或更改安全字段。normalizer 抛出异常统一映射为
`GATE_SEMANTIC_INVALID`，不泄漏内部异常细节。

路径参数：

- `READ` 工具使用 `Sandbox.check_read`；
- `WRITE`/`EXECUTE` 工具使用 `Sandbox.check_write`；
- 上下文中的工作区不能覆盖 Sandbox 已绑定工作区；
- 存储和摘要使用规范化 workspace-relative POSIX 路径。

`CommandEffectClass` 和可信命令执行仍由具体 handler 调用 Sandbox；Gate 至少保证模型请求只包含
`command_id`，不包含 `argv`、env、cwd、shell、timeout 或可执行路径。

### 5.4 审批阶段

Gate 查询当前 Run 现有审批，并要求：

- `(run_id, tool_call_id)` 最多一张；
- 参数摘要、工具名、Run、任务、工作区和配置指纹匹配；
- `PENDING` 表示继续等待；
- `APPROVED` 可在通过重复/预算检查后 claim；
- `REJECTED/EXPIRED/INVALIDATED/CONSUMED` 均不可执行；
- `CLAIMED` 只有在 WP-07 明确恢复且 Invocation 仍 PREPARED 时可由显式 `resume_claim` 复用。

首次等待事务：

```text
BEGIN IMMEDIATE
  read current run revision
  create PREPARED invocation
  read updated run revision
  ApprovalStore.create(...)
  transition_run(RUNNING -> WAITING_APPROVAL)
  audit Gate WAITING decision
COMMIT
```

审批创建或 Run 状态更新任一失败时共同回滚，不能留下“审批存在但 Run 仍 RUNNING”或相反的半状态。

批准执行事务：

```text
BEGIN IMMEDIATE
  re-read run/approval/invocation
  verify Gate phases 1..8
  ApprovalStore.claim(...)
  transition_run(WAITING_APPROVAL -> RUNNING)
  record TOOL_CALLS budget delta once
  audit Gate ALLOW
COMMIT

BEGIN IMMEDIATE
  transition_invocation(PREPARED -> STARTED)
  append TOOL_STARTED
COMMIT
```

claim 成功但第二事务失败时，Invocation 仍 PREPARED，交由 WP-07 的显式 `resume_claim` 恢复；不得
自动执行。

### 5.5 无审批执行路径

```text
preflight:
  load run
  verify lease and trusted context
  registry/visibility
  schema
  normalize/path/command semantics
  permission
  mode/risk
  approval not required
  repeat/replay
  budget

intent transaction:
  create PREPARED invocation
  record TOOL_CALLS +1 exactly once
  append Gate ALLOW

start transaction:
  transition PREPARED -> STARTED
  append TOOL_STARTED

execute:
  call handler outside DB transaction

result transaction:
  validate HandlerResult
  add evidence drafts
  transition STARTED -> terminal
  append result audit
```

如果 handler 返回确定失败且策略允许安全重试，本包首期仍不自动重试；`retryable` 只作为 observation
元数据和 WP-06 的判定依据。该行为由 D-42 确认。

### 5.6 Handler 调用与超时

Handler 是同步 callable。Gate 不允许它直接打开数据库、修改状态或绕过 Sandbox。

调用前后：

```text
started_at = clock.now()
result = handler(normalized_args, context)
elapsed = clock.now() - started_at

if elapsed > spec.timeout_seconds:
    ignore claimed success/failure as unconfirmed
    return UNKNOWN_RESULT
```

Python 无法安全终止任意线程，因此：

- 本包不通过后台线程“假装硬超时”；线程继续执行会产生更危险的晚期副作用；
- 真实文件/进程 handler 必须在 WP-05/WP-06 中把阻塞交给 Sandbox；
- 测试 handler 必须合作并遵守 `ToolSpec.timeout_seconds`；
- 超时后迟到返回一律按 `UNKNOWN_RESULT`，不自动重试、不 consume 审批；
- 恶意或无限阻塞的 in-process handler 是剩余风险，必须写入验收报告；
- D-41 明确本包不实现不安全线程回收；需要硬超时时应另行评审进程隔离。

### 5.7 HandlerResult 映射

| Handler 结果/异常 | Gate 状态 | 审批 |
| --- | --- | --- |
| `SUCCEEDED` | `SUCCEEDED` | 确定结果，consume |
| `FAILED` + 稳定 code | `FAILED` | 确定结果，consume |
| `TIMED_OUT` + idempotent + 已停止证据 | `TIMED_OUT` | 确定结果，consume |
| `TIMED_OUT` + non-idempotent | `UNKNOWN_RESULT` | 不 consume |
| `UNKNOWN_RESULT` | `UNKNOWN_RESULT` | 不 consume |
| 未结构化异常且无“未产生副作用”证据 | `UNKNOWN_RESULT` | 不 consume |
| 明确声明“未产生副作用”的可重试只读失败 | `FAILED`，`retryable=true` | consume，若已审批 |

handler 返回非法类型、非法状态/错误码组合、超出输出上限或与 `output_kind` 不符时：

- 若 handler 尚未被调用？不适用；
- 已经执行但结果无法验证：保守标注 `UNKNOWN_RESULT`，不得猜成功；
- 观测返回 `GATE_RESULT_INVALID` 或 `GATE_OUTPUT_INVALID`，不自动重试。

### 5.8 重复与 replay

Gate 从 StateStore 可验证快照或只读查询计算：

```text
repeat_key = (run_id, tool_name, args_digest)
executed_count = count(invocations matching repeat_key that entered STARTED)
```

默认：

```text
READ: executed_count >= 2 -> GATE_REPEATED_CALL
WRITE/EXECUTE: executed_count >= 1:
    if prior terminal success -> GATE_REPLAY_SUPPRESSED
    else -> GATE_REPEATED_CALL
```

判定在任何新 handler 调用前完成。Replay suppression：

```text
load prior invocation and evidence by run/tool_call/signature
  -> return DENIED observation
  -> code GATE_REPLAY_SUPPRESSED
  -> evidence_ids = prior evidence
  -> handler_calls += 0
```

同一 `tool_call_id` 名称或参数不同必须先返回 `GATE_TOOL_CALL_ID_CONFLICT`，不能被当作普通
replay。

### 5.9 预算检查

Gate 从当前 Run usage 检查：

- `usage.tool_calls < budget.max_tool_calls`；
- `usage.steps < budget.max_steps`；任务级实际 step 计数由 Harness 写，本包只拒绝已耗尽状态；
- 当前 wall time 不超过最大墙钟时间；
- Token/成本 usage 不超过上限。

工具调用预算在意图事务中通过幂等 ledger 增量 1。若 ledger 写入失败，整个意图事务回滚，不写
STARTED。

Gate 返回的预算观察只表示本次调用不能继续。最终 Run `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`
由 WP-10 Harness 负责。

### 5.10 Gate 决策审计

每个候选调用写一条 `GATE_DECISION`：

```text
actor = trusted transaction actor
tool_call_id = model-visible original ID
tool_name = canonical registered name
phase = rejecting phase / APPROVAL / EXECUTION
decision = ALLOW / DENY
rule_id = stable chain rule
input_summary = bounded redacted args digest / path list
output_summary = status/code/approval/evidence IDs
```

拒绝规则至少包括：

```text
GATE_VISIBILITY_V1
GATE_SCHEMA_V1
GATE_SEMANTIC_V1
GATE_PERMISSION_V1
GATE_MODE_V1
GATE_RISK_V1
GATE_APPROVAL_V1
GATE_REPEAT_V1
GATE_BUDGET_V1
GATE_ALLOW_V1
```

审计在 phase 决定之后、返回模型之前尝试提交。拒绝审计失败时也不得执行 handler。

### 5.11 输出与脱敏

流程：

```text
validate HandlerResult.output
  -> Redactor.redact
  -> canonical-size measurement
  -> if over spec.output_limit_bytes:
       replace output with bounded preview object
       set truncated=true
  -> ensure JSON serializable and finite
  -> cap message/summary
  -> persist redacted summary
  -> return ToolObservation
```

建议截断 wrapper 为：

```json
{
  "output_kind": "TEXT",
  "preview": "<bounded preview>",
  "omitted_bytes": 1234
}
```

不得从敏感 token 中间截断后仍保留可识别片段；先脱敏再裁剪。

### 5.12 并发与锁

```text
Gate:
  _run_locks: dict[run_id, threading.RLock]

with lock_for(run_id):
    preflight
    intent/claim transaction
    STARTED transaction
    handler
    result transaction
```

首期所有调用串行，因为 Agent 尚未接入且无需过早优化。锁只防止同进程调度交叉；跨进程仍由
lease、`BEGIN IMMEDIATE`、条件 UPDATE 和审批 revision 保证。锁对象按 Run 有界回收或在 Gate
生命周期内保留，不进入持久化。

不得在持锁或数据库事务期间调用真实模型；handler 执行期间锁保留，但数据库事务已提交。

## 6. 施工任务卡

决策确认并冻结规格后按以下顺序施工。每张任务卡先建立失败测试，再实现能力。

### WP04-T00 上游 revision 与 observation 契约

| 项目 | 内容 |
| --- | --- |
| 目标 | 按 D-37/D-38 追加公共读取接口和 v2 observation 字段 |
| 前置 | 规格冻结；WP-01/WP-02 契约修订已批准 |
| 涉及文件 | `domain.py`、`state_store.py`、`__init__.py`、相关契约测试 |
| 对应规格 | 第 6.4、10.2、15 节；D-37、D-38 |
| 完成条件 | Gate 不使用私有方法/直接 SQL；公共 revision 读取和旧 observation 兼容测试通过 |

### WP04-T01 公共类型、错误码与 Registry

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 `ExecutionMode`、`ToolExecutionContext`、`ToolCallRequest`、`ToolDefinition`、`ToolHandler`、`HandlerResult`、`EvidenceDraft` 和 Registry |
| 前置 | T00 |
| 涉及文件 | `domain.py`、`registry.py`、`__init__.py`、`tests/test_registry_policy.py`、`tests/test_domain.py` |
| 对应规格 | 第 6、8.1、13 节；WP04-AC-001、002、005 |
| 正常路径 | 合法工具注册、稳定快照、公共模型 round-trip |
| 拒绝路径 | 重名、非法 handler/schema、模型快照泄漏 handler |
| 完成条件 | snapshot 不含内部实现；错误码稳定；契约测试通过 |

### WP04-T02 Policy

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现可见性、角色权限、plan/execute 模式、风险和审批需求判定 |
| 前置 | T01；D-43 已确认 |
| 涉及文件 | `policy.py`、`domain.py`、`tests/test_registry_policy.py` |
| 对应规格 | 第 6.3、7 节；WP04-AC-003、009、010、011、029 |
| 正常路径 | 有权限只读、风险允许、任务可见 |
| 拒绝路径 | 不在白名单、缺权限、plan 写、风险不允许、审批 REQUIRED 被覆盖 |
| 完成条件 | 所有策略输入均为可信值；模型快照可见后执行仍重新检查 |

### WP04-T03 Schema 与语义规范化

| 项目 | 内容 |
| --- | --- |
| 目标 | 集成 JSON Schema、canonical JSON、normalizer、深度/节点/大小边界和 args digest |
| 前置 | T01；D-39 已确认 |
| 涉及文件 | `gate.py`、`registry.py`、`domain.py`、`pyproject.toml`、`tests/test_gate.py` |
| 对应规格 | 第 8 节；WP04-AC-005、006、007、008 |
| 正常路径 | 合法参数稳定规范化并产生相同摘要 |
| 拒绝路径 | 缺字段、类型错误、额外字段、非有限值、超限、normalizer 异常 |
| 完成条件 | G1 四类输入错误均 handler=0；摘要与 WP-02 canonical 规则一致 |

### WP04-T04 路径参数与 Sandbox 预检

| 项目 | 内容 |
| --- | --- |
| 目标 | 根据 `path_arguments` 和风险调用 Sandbox；拒绝路径逃逸和命令注入字段 |
| 前置 | T03 |
| 涉及文件 | `gate.py`、`tests/test_gate.py`、必要 fixture |
| 对应规格 | 第 7、8.2、13 节；WP04-AC-006、007、008 |
| 正常路径 | 合法路径规范化后传给 handler；模型不能覆盖 Sandbox 工作区 |
| 拒绝路径 | `artifacts/../../.env`、越界绝对路径、符号链接逃逸、argv/env/cwd/shell 字段 |
| 完成条件 | S1/S3 包内 Gate 证据成立；handler/I/O/进程计数为 0 |

### WP04-T05 审批等待与恢复执行

| 项目 | 内容 |
| --- | --- |
| 目标 | 创建 PREPARED Invocation + PENDING Approval + Run WAITING；批准后重新过 Gate、claim、RUNNING、STARTED |
| 前置 | T00、T02、T04；WP-02 接口 |
| 涉及文件 | `gate.py`、`domain.py`、`tests/test_gate.py` |
| 对应规格 | 第 7.1、9.1、10.2 节；WP04-AC-012、013、014、019 |
| 正常路径 | G3 无 handler 等待；G4 第一执行成功，第二拒绝 |
| 拒绝路径 | 篡改、过期、跨 Run、CLAIMED/CONSUMED 重放、claim 并发 |
| 完成条件 | 审批和 Run 状态同事务；handler 在 claim/STARTED 前始终为 0 |

### WP04-T06 执行生命周期

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 PREPARED -> STARTED -> terminal、事务边界、证据写入和结果审计 |
| 前置 | T01、T05 |
| 涉及文件 | `gate.py`、`domain.py`、`tests/test_gate.py` |
| 对应规格 | 第 10、11 节；WP04-AC-019、020、021、022 |
| 正常路径 | 成功/确定失败状态和证据可重开读取 |
| 拒绝/故障 | STARTED 写失败不调用 handler；结果事务失败不返回成功；非幂等超时 UNKNOWN |
| 完成条件 | handler 前 STARTED 可从新连接读取；C2 包内证据成立 |

### WP04-T07 重复、replay 与预算

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现规范化重复键、只读两次上限、写 replay suppression、tool_call 预算幂等增量 |
| 前置 | T04、T06；D-40/D-42 已确认 |
| 涉及文件 | `gate.py`、`domain.py`、`tests/test_gate.py` |
| 对应规格 | 第 9.2、9.3 节；WP04-AC-015、016、017、018、027 |
| 正常路径 | 首次/第二次只读执行，预算恰好扣一次 |
| 拒绝路径 | G2 第三次、G4 重放、同 ID 不同参数、预算耗尽 |
| 完成条件 | handler 最多次数和证据复用可机械证明；不自动重试 |

### WP04-T08 审计、output 与失败关闭

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 Gate 决策审计、统一脱敏、输出上限、异常映射和所有基础设施失败路径 |
| 前置 | T02、T03、T06、T07 |
| 涉及文件 | `gate.py`、`audit.py`（仅事件枚举如确需追加）、`domain.py`、`tests/test_gate.py` |
| 对应规格 | 第 11、12、13 节；WP04-AC-021 至 026、028 |
| 正常路径 | A1 字段完整，observation 有界且无密钥 |
| 拒绝/故障 | allow/deny 审计失败、intent/start/result 写失败、非法 HandlerResult |
| 完成条件 | 所有故障 handler=0 或保守 UNKNOWN，不伪造成功 |

### WP04-T09 并发、文档与验收准备

| 项目 | 内容 |
| --- | --- |
| 目标 | 完成 Run 级串行、并发审批/写测试、文档索引和验收报告 |
| 前置 | T01 至 T08 |
| 涉及文件 | `gate.py`、`tests/test_gate.py`、`docs/README.md`、`docs/work-packages/index.md`、包内文档 |
| 对应规格 | 第 4、12、16、17 节；WP04-AC-027、029、030 |
| 正常路径 | 并发测试最终 handler=1、审批 consume=1、审计序稳定 |
| 拒绝/故障 | 任意关键 skip、错误计数或上游接口绕过都阻止 accepted |
| 完成条件 | 全量验证通过，acceptance-report 实际复跑并评审 |

## 7. 施工顺序与可提交增量

```text
决策确认 -> 冻结 spec
  -> T00 上游契约修订
  -> T01 Registry/公共类型
  -> T02 Policy
  -> T03 Schema/语义
  -> T04 Sandbox 预检
  -> T05 审批等待/claim
  -> T06 执行生命周期
  -> T07 repeat/replay/budget
  -> T08 audit/output/failure
  -> T09 并发/文档/验收
```

建议提交边界：

```text
feat(wp-01): expose revision reads for governed execution
test(wp-04): define registry and policy contracts
feat(wp-04): register trusted tool definitions and snapshots
feat(wp-04): enforce trusted visibility permissions and risk policy
feat(wp-04): validate and normalize tool arguments before handlers
feat(wp-04): pause unapproved calls for bound approval
feat(wp-04): persist tool execution intents and outcomes
feat(wp-04): suppress repeated writes and enforce tool budget
test(wp-04): prove gate failures never reach handlers
docs(wp-04): accept governed execution kernel
```

T00 未完成前不得实现 Gate 事务编排。T05/T06 未完成前不得向 WP-05/WP-06 交付执行入口。

## 8. 测试计划

### 8.1 测试基础设施

`tests/conftest.py` 可增加：

| Fixture/helper | 用途 | 约束 |
| --- | --- | --- |
| `gate_handler_counter` | 记录 handler 调用和参数 | 拒绝路径必须为 0 |
| `fake_tool_schema` | 固定 object schema，`additionalProperties=false` | 不依赖网络/真实工具 |
| `fake_clock` | 确定性时间和超时 | 不使用 sleep |
| `gate_storage` | 临时 SQLite + Run + lease | 不污染真实仓库 |
| `gate_faults` | 按 fault point 注入 State/Audit/Approval 故障 | 故障后仍可重开检查 |
| `approval_waiter` | 等待、批准、拒绝、篡改上下文 | 不启动 CLI |
| `bounded_output_tool` | 生成超限文本/JSON | 验证脱敏先于截断 |

### 8.2 `tests/test_registry_policy.py`

| 测试组 | 覆盖 |
| --- | --- |
| registry_registration | 合法注册、重名、重绑定、非法 spec/handler |
| registry_snapshot | 稳定排序、可见性过滤、无 handler 泄漏 |
| policy_visibility | 任务白名单、未知工具 |
| policy_permissions | 多角色并集、缺权限、权限大小写/非法角色 |
| policy_mode_risk | plan 禁止写/执行、风险策略 |
| approval_requirement | ToolSpec REQUIRED、任务 required_tools、不可降级 |

### 8.3 `tests/test_gate.py`

| 测试组 | 覆盖 |
| --- | --- |
| schema_semantics | 缺字段、类型、额外字段、空 patch、路径/命令注入 |
| side_effect_zero | 每个拒绝阶段 handler=0 |
| approval_wait | G3、重复等待复用同一审批、Run WAITING |
| approval_binding | 参数/工作区/Run/配置篡改、过期、跨 Run |
| approval_once | G4、并发 claim、consume 一次 |
| repeat_replay | G2、写 replay、原证据、ID 冲突 |
| budget | 上限边界、ledger 恰好一次、H3 部分证据 |
| lifecycle | PREPARED/STARTED/terminal、重开读取、非法转换 |
| unknown | 非幂等超时、未结构化异常、结果事务失败、不重试 |
| audit | ALLOW/DENY、A1 字段、audit 写失败、规则 ID |
| output | limit、truncated、脱敏、非法 output kind |
| concurrency | 同 Run 并行写/审批、审计顺序、最终执行一次 |
| determinism | 重复运行、稳定摘要、无网络/模型/凭据 |

### 8.4 规格场景映射

| 规格场景 | 测试入口 | 关键证据 |
| --- | --- | --- |
| WP04-AC-001、002 | `test_registry_registration_and_snapshot` | snapshot 无 handler，冲突拒绝 |
| WP04-AC-003、004 | `test_tool_visibility_and_lookup` | 稳定码，handler=0 |
| WP04-AC-005 | `test_schema_rejects_missing_extra_and_wrong_types` | G1，handler=0 |
| WP04-AC-006、008 | `test_semantic_and_command_injection_denied` | handler/进程=0 |
| WP04-AC-007 | `test_sandbox_path_escape_denied_before_handler` | S1，目标未读 |
| WP04-AC-009、010、011 | `test_policy_permission_mode_and_risk` | phase/rule 正确 |
| WP04-AC-012 | `test_unapproved_write_waits_without_side_effect` | G3，WAITING，handler=0 |
| WP04-AC-013 | `test_approval_binding_changes_are_denied` | 旧审批失效 |
| WP04-AC-014 | `test_approved_call_executes_once` | G4，handler=1 |
| WP04-AC-015 | `test_same_read_three_times_stops_third` | G2，handler=2 |
| WP04-AC-016 | `test_completed_write_replay_returns_original_evidence` | 新执行=0 |
| WP04-AC-017 | `test_tool_call_id_conflict_rejected` | handler=0 |
| WP04-AC-018 | `test_tool_budget_denied_before_handler` | H3 部分，handler=0 |
| WP04-AC-019 | `test_started_is_persisted_before_handler` | 新连接读取 STARTED |
| WP04-AC-020、021、022 | `test_execution_lifecycle_and_unknown_windows` | 状态/审批一致 |
| WP04-AC-023、024 | `test_audit_and_intent_fail_closed` | handler=0 |
| WP04-AC-025 | `test_denial_audit_contains_required_fields` | A1 |
| WP04-AC-026 | `test_observation_bounded_and_redacted` | 无密钥，truncated |
| WP04-AC-027 | `test_concurrent_calls_are_serialized` | handler 1、claim 1 |
| WP04-AC-028 | `test_invalid_handler_result_is_not_success` | UNKNOWN/失败 |
| WP04-AC-029 | `test_visible_snapshot_does_not_grant_execution` | 执行再检查 |
| WP04-AC-030 | `test_gate_tests_are_offline_and_repeatable` | 无网络/模型/凭据 |

## 9. 验收命令

规格冻结并完成施工后至少运行：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_registry_policy.py -q
python -m pytest tests/test_gate.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

验收报告必须记录实际退出码、测试数量、失败/skip、G1-G4 handler 次数、审批 claim/consume 次数、
STARTED 重开读取、审计 A1 字段、输出截断和执行环境。不得复制本文预期值作为结果。

## 10. 安全与故障矩阵

| 边界 | 预期失败点 | 必须留下的证据 |
| --- | --- | --- |
| 未知/不可见工具 | visibility | Gate DENY、rule、handler=0 |
| 额外字段/类型错误 | schema | G1、handler=0 |
| 路径穿越 | Sandbox semantic | S1、目标未读、handler=0 |
| 命令 argv/env 注入 | semantic | handler/进程=0 |
| 缺权限 | policy | actor/rule/decision |
| plan 写操作 | mode | `GATE_MODE_DENIED` |
| 未审写 | approval | WAITING、PENDING、handler=0 |
| 参数篡改 | approval binding | handler=0、旧审批不可执行 |
| 审批重放 | claim/consume/replay | handler 最多 1 |
| 重复只读 | repeat | 第三次拒绝，前两次计数 |
| 写 replay | prior evidence | handler/新写入=0 |
| 预算耗尽 | budget | handler=0、ledger 未多扣 |
| 意图写失败 | transaction rollback | 无 STARTED、handler=0 |
| STARTED 写失败 | start transaction | handler=0 |
| allow 审计失败 | audit | handler=0 |
| deny 审计失败 | audit | handler=0，不降级 |
| handler 超时 | lifecycle | UNKNOWN/TIMED_OUT，不重试 |
| 结果事务失败 | lifecycle | STARTED 保留，不返回成功 |
| 并发调用 | run lock + lease | 写 handler=1、claim=1 |
| 输出过大 | output bound | truncated、bounded preview |
| 敏感输出 | redaction | State/Audit/observation 无原值 |

## 11. 风险、限制与缓解

| 风险/限制 | 影响 | 处理 |
| --- | --- | --- |
| 同步 Python handler 无法安全强杀 | 超时线程可能继续运行 | D-41；真实阻塞交给 Sandbox，迟到结果 UNKNOWN |
| Gate 依赖 WP-01 revision 公开接口 | 无接口时只能使用私有方法/直接 SQL | D-37；WP-01 兼容修订已完成 |
| 重复上限按工具静态配置 | 某些只读任务可能需要更多读取 | 使用可信 PolicyConfig；超限拒绝，未来显式评审 |
| `jsonschema` 新增依赖 | 依赖面增加 | D-39；固定版本并进入 `pip check` |
| 审批等待/CLI 分离 | Gate 只返回 WAITING | WP-11 实现审批命令和跨进程 resume |
| 仅进程内锁 | 多进程并发不能只靠锁 | 同时要求 lease、DB transaction、条件更新 |
| 无真实工具 | G3 只证明 handler 未执行 | WP-06 以真实文件摘要补全“目标文件不变” |
| 输出截断 | 模型可能缺少完整结果 | 显式 truncated，下游不得把截断报告当完整证据 |
| handler 结果默认保守为未知 | 可能降低自动化成功率 | 安全优先；handler 必须返回明确结构化结果 |

## 12. 回滚策略

1. 施工前记录 WP-00 至 WP-03 验收基线和当前全量测试结果；
2. 公共契约修订单独提交，失败时先回滚 Gate 调用，不删除既有状态表；
3. 新增 Registry/Policy/Gate 按模块独立提交，出现安全回归时撤回公共导出和调用入口；
4. 不修改已发布 migration；WP-04 原则上不新增 migration；
5. 已有真实审计、审批或 Invocation 事实不得在回滚时删除，必须保留并人工判定；
6. 若审批事务窗口存在缺陷，关闭相关 Gate 执行路径，不得降级为无审批直写；
7. 下游 WP-05/WP-06 在 WP-04 验收前不得调用未冻结接口。

## 13. 下游交接

验收后向 WP-05/WP-06/WP-07/WP-10 交付：

- `ToolDefinition`、handler 协议、`HandlerResult`、`EvidenceDraft`；
- 工具快照、可见性、角色权限、模式/风险和审批需求策略；
- Gate 固定检查链、错误码和 observation 语义；
- 参数规范化、路径检查、canonical digest；
- G3 审批等待和 G4 一次性执行契约；
- PREPARED/STARTED/结果/UNKNOWN 生命周期与故障窗口；
- repeat/replay/budget 规则；
- 审计字段、rule ID 和脱敏/输出上限；
- 每个 WP04-AC 的测试入口和实际证据；
- 下游必须补的真实工具、进程硬超时、恢复和 Completion 责任。

WP-05/WP-06 的真实 handler 必须返回 `HandlerResult` 并复用 Sandbox，不得绕过 Gate 直接访问 State、
Audit、Approval 或仓库。WP-07 不得把 STARTED 自动恢复为成功；WP-10 必须保留 Agent 原始
`tool_call_id` 并只通过 Gate 执行。

## 14. 实际完成情况

规格、实现、测试和验收报告均已完成。

| 项目 | 状态 |
| --- | --- |
| `spec.md` | v1.0.0 / frozen |
| `plan.md` | v1.0.0 / done |
| 代码 | Registry、Policy、Gate 与契约修订已完成 |
| 测试 | 目标与全量回归通过，见验收报告 |
| 验收报告 | [acceptance-report.md](acceptance-report.md) v1.0.0 / accepted |
| 阻塞项 | 无 |
