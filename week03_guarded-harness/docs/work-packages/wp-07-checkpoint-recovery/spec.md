# WP-07 Checkpoint 与恢复工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-07 Checkpoint 与恢复 |
| 文档状态 | frozen |
| 规格版本 | 1.1.0 |
| 创建日期 | 2026-11-06 |
| 冻结日期 | 2026-11-06 |
| 依赖 | WP-01 v0.4.0 / accepted；WP-02 v0.2.0 / accepted；WP-03 v1.1.0 / accepted；WP-06 v1.0.0 / accepted |
| 下游工作包 | WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-STATE-001` 至 `REQ-STATE-003`、`REQ-AUDIT-001` 至 `REQ-AUDIT-004`、`REQ-CHECKPOINT-001` 至 `REQ-CHECKPOINT-004` |
| 对应设计 | `design.md` 第 5、6.4、9、10、13、14 节 |
| 相关决策 | D-01、D-08、D-09、D-11、D-16、D-18 至 D-28、D-30 至 D-36、D-37、D-40 至 D-42、D-48 至 D-58 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌表 revision 11 |
| 原文件任务 | P1-07、P1-08 的恢复部分 |

本文是 WP-07 已冻结的行为与验收契约，不表示 Checkpoint、恢复规划或 Gate 恢复入口已经实现。
第 14 节五项决策已按推荐方案确认并登记为 D-53 至 D-57。v1.1.0 追加
`APPROVAL_REQUESTED` 边界，以覆盖 C1 要求的待审批安全点。实现计划和任务拆分见
[plan.md](plan.md)。

文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-07 必须把 WP-01、WP-02 和 WP-06 已经持久化的状态、审批及执行结果组合为可重开、可校验、
不会重复副作用的恢复闭环：

1. 在模型步骤结束、工具调用形成确定结果、审批状态落定等安全边界保存完整 Checkpoint；
2. Checkpoint 与 StateStore、Audit、Approval 和 Run 当前业务状态保持事务一致；
3. Checkpoint 快照带版本和 canonical 哈希，读取时重新校验结构、哈希和关联事实；
4. 恢复前重新验证 schema、配置指纹、工作区身份、外围保护基线、审批、租约和审计链；
5. 对“未提交意图、PREPARED、CLAIMED + PREPARED、STARTED、结果已提交、Checkpoint 未提交”
   等窗口给出唯一、确定且可测试的恢复动作；
6. PREPARED 调用只能重新经过 Gate，不能由恢复模块直接调用 handler；
7. 已 STARTED 但无确定结果时标记 `UNKNOWN_RESULT` 并停止自动恢复，绝不重放非幂等副作用；
8. 已有确定结果的调用复用持久化结果；结果已提交但 Checkpoint 缺失时只补建 Checkpoint；
9. 配置、工作区、审批、证据或 schema 发生变化时进入 `BLOCKED` 或人工复核，不自动放行；
10. 生成有界、确定、可审计的 continuation context，供新进程创建新 Agent，而不是恢复旧
    Agent memory；
11. 所有恢复放行、阻塞、审批失效、证据失效和 Checkpoint 写入均追加审计；
12. 跨数据库重开和租约接管后，预算已用量、审批消费状态和已执行步骤保持不变。

WP-07 完成“恢复决策与安全续接材料”，但不创建 Agent、不实现 CLI `resume`，也不执行最终验收。

## 3. 范围与非目标

### 3.1 范围内

- `src/guarded_harness/checkpoint.py`；
- `src/guarded_harness/domain.py` 中 Checkpoint、恢复动作、continuation context 和稳定错误码；
- `src/guarded_harness/storage.py` 的追加式 migration；
- `src/guarded_harness/state_store.py` 中恢复所需的窄范围公共状态操作；
- `src/guarded_harness/gate.py` 中受控的 PREPARED 恢复重入入口；
- `src/guarded_harness/sandbox.py` 中不强制干净工作区的受控快照扩展；
- `src/guarded_harness/audit.py` 中恢复和证据失效事件类型；
- `tests/test_domain.py` 中 Checkpoint、Recovery 和 Continuation 严格模型契约；
- `tests/test_state_checkpoint.py` 的 Checkpoint、重开、崩溃窗口和恢复规划测试；
- `tests/test_gate.py` 的 PREPARED 恢复经 Gate、STARTED 未知结果和结果复用测试；
- `tests/test_sandbox.py` 的写入后工作区快照和兼容回归测试；
- WP-07 文档、索引和后续验收报告。

### 3.2 非目标

- 不重新实现 `ToolCallingAgent`、模型循环、Agent memory、Fake Model 或真实 Provider；
- 不创建 `harness.py`、`agent_adapter.py` 或 `cli.py`；
- 不实现 CLI `resume`、审批命令、等待轮询、日志视图和退出码；
- 不执行最终代码理解或测试修复验收，这些属于 WP-08/WP-09；
- 不自行批准、拒绝或重新创建审批；
- 不把 `UNKNOWN_RESULT` 自动升级为成功，也不通过人工确认直接开放同一次副作用；
- 不实现多仓库、分布式租约、跨主机恢复或 OS 容器级隔离；
- 不序列化旧 `ToolCallingAgent`、旧 memory、模型隐藏推理、完整对话或完整 Audit 事件流；
- 不修改已发布的 migration；只能追加新 migration；
- 不依赖 SQLite 私有表结构、StateStore 私有 helper 或沙箱私有方法完成公共能力。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。完整矩阵包含 G/S/C/A/H/T
共 19 条；WP-07 直接关闭 C1 至 C4 的恢复部分，并为 A2、H2、T1 提供支撑。

| 编号 | 优先级 | 飞书通过标准 | WP-07 责任 | 最终归属 |
| --- | --- | --- | --- | --- |
| C1 | P0 | 每个安全步骤后保存 Checkpoint，包含 run、step、计划、证据、预算和待审批动作 | 直接：定义快照 schema、安全边界、事务写入、哈希/重开校验和审计 | WP-07 / WP-10 / WP-12 |
| C2 | P0 | 写入完成后结果未保存时恢复，标记 `UNKNOWN_RESULT`，不得自动重放 | 直接：STARTED 无终态时原子标记未知并阻塞；恢复计划不产生执行动作 | WP-07 / WP-11 / WP-12 |
| C3 | P0 | 正常中断后换进程恢复，找回进度，已完成写入不重复，计数不清零 | 直接：跨数据库重开、新租约、结果复用、预算读取和 continuation context；Agent/CLI 接线由 WP-10/WP-11 完成 | WP-07 / WP-10 / WP-11 / WP-12 |
| C4 | P0 | 恢复时工作区已变化，旧证据或旧审批失效，停止并等待核对 | 直接：身份/外围保护/内容版本复核、审批失效、证据失效和 `BLOCKED` 收口 | WP-03 / WP-07 / WP-12 |
| A2 | P1 | 查看完整任务时可关联 Gate、Sandbox、State、Approval 和执行结果 | 支撑：恢复记录、Checkpoint、审批、Invocation、Evidence 和审计序号可稳定关联 | WP-08 / WP-09 / WP-12 |
| H2 | P0 | 测试通过后又修改源码，旧测试证据失效并要求重跑 | 支撑：恢复上下文只纳入当前版本有效证据；最终测试验收仍由 WP-09 判定 | WP-07 / WP-09 / WP-12 |
| T1 | P0 | 所有 P0 场景都有确定性测试且可重复通过 | 直接：WP07-AC 全部离线、可重复，崩溃窗口通过注入模拟 | WP-07 / WP-12 |

WP-07 不负责关闭 G/S/H1/H3，也不得把 C3 的数据库层证据表述为 CLI 端到端恢复已完成。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `checkpoint.py` | Checkpoint 快照、恢复校验、动作规划、恢复记录和 continuation context | 执行 handler、批准审批、启动 Agent、最终验收 |
| `state_store.py` | 完整业务快照、最后 Checkpoint 指针、证据失效和 Run/Invocation 条件更新 | 解释工作区变化或选择恢复动作 |
| `storage.py` | migration v5、事务、append-only 约束和完整性检查 | Checkpoint 业务语义 |
| `audit.py` | Checkpoint、恢复、审批/证据失效和阻塞事件 | 替代 StateStore |
| `approval.py` | 加载/失效审批、`resume_claim` 和绑定复核 | 恢复动作选择 |
| `sandbox.py` | 不强制干净的工作区快照、内容摘要和身份/外围比较 | 修改 Run、Invocation、Evidence 或 Approval |
| `gate.py` | PREPARED 调用的恢复重入、重新门禁、STARTED、handler 和结果持久化 | 选择恢复窗口或修改工作区基线 |
| `domain.py` | 严格、版本化、可序列化的公共模型和稳定错误 | 读取数据库或访问仓库 |

依赖方向：

```text
domain.py <──── checkpoint.py ────> config.py
                     │
                     ├──────────> storage.py
                     ├──────────> state_store.py
                     ├──────────> approval.py
                     ├──────────> audit.py
                     └──────────> sandbox.py

gate.py ────> registry.py + policy.py + sandbox.py
           └> state.py + audit.py + approval.py

WP-10/WP-11 ────> checkpoint.py + gate.py + sandbox.py + storage.py
```

禁止：

- `checkpoint.py` 直接调用工具 handler、执行测试命令或写目标仓库；
- `checkpoint.py` 通过直接 SQL 更新 Run、Invocation、Approval 或 Evidence；
- `gate.py` 根据任务名称选择恢复动作；
- 读取沙箱私有属性或复制一份独立的路径/工作区判定；
- 用模型 final answer、自然语言步骤或审计摘要替代持久化结构事实；
- 将审批 token、租约 token、敏感参数或未脱敏文件内容写入 Checkpoint/continuation；
- 在恢复中自动重试 `UNKNOWN_RESULT`、超时或任何非幂等副作用。

## 6. 公共接口与数据契约

### 6.1 稳定类型

WP-07 至少新增以下严格 Pydantic 类型或等价冻结值对象：

```text
CheckpointBoundaryKind =
    STEP_COMPLETED
    TOOL_SUCCEEDED
    TOOL_FAILED
    TOOL_TIMED_OUT
    TOOL_UNKNOWN_RESULT
    APPROVAL_REQUESTED
    APPROVAL_SETTLED
    COMPLETION_RECORDED

RecoveryAction =
    CONTINUE_AGENT
    WAIT_FOR_APPROVAL
    REENTER_GATE
    RESUME_CLAIM_THEN_REENTER_GATE
    REBUILD_CHECKPOINT_THEN_CONTINUE
    MARK_UNKNOWN_RESULT
    MANUAL_REVIEW
    ALREADY_TERMINAL

RecoveryStatus =
    RESUMABLE
    WAITING_APPROVAL
    MANUAL_REVIEW
    ALREADY_TERMINAL
```

所有模型使用 `extra="forbid"`、严格类型和显式 `schema_version=1`。枚举只允许追加；新增恢复
动作或状态时必须补契约测试并更新规格。`RecoveryStatus` 与 `RecoveryAction` 必须同时存在，
调用方不能用单个布尔值表达恢复结论。

### 6.2 CheckpointBoundary

```text
CheckpointBoundary(
    schema_version=1,
    kind: CheckpointBoundaryKind,
    boundary_ref: str,
    step_id: StepId | None,
    tool_call_id: ToolCallId | None,
    approval_id: ApprovalId | None,
    public_summary: str,
)
```

约束：

- `boundary_ref` 是调用方提供的稳定安全边界标识，必须非空、长度受限且不含控制字符；
- `STEP_COMPLETED` 必须有 `step_id`；工具类边界必须有 `tool_call_id`；审批请求/落定边界必须有
  `approval_id`；
- `public_summary` 只包含可公开、已脱敏的有界摘要，不包含隐藏推理；
- 同一 Run、同一边界 kind/ref 最多对应一个 Checkpoint；重复提交等价边界返回原 Checkpoint；
- 同一边界标识但快照摘要不同返回 `STATE_CONFLICT`，不得覆盖历史 Checkpoint。

### 6.3 Checkpoint 与快照

```text
Checkpoint(
    schema_version=1,
    checkpoint_id: CheckpointId,
    run_id: RunId,
    sequence: int,
    boundary: CheckpointBoundary,
    run_revision: int,
    last_audit_sequence: int,
    last_audit_hash: Sha256Digest,
    snapshot_digest: Sha256Digest,
    snapshot: CheckpointSnapshot,
    created_at: UtcDatetime,
)
```

`CheckpointSnapshot(schema_version=1)` 的快照 payload 至少包含：

- schema/version、Run 身份、任务类型、配置指纹和当前 Run 状态；
- 当前 step、全部有界 step 摘要和当前 `RunPlan`（可为首次计划设置前的 `null`）；
- 所有 ToolInvocation 的稳定顺序、状态、revision、参数摘要、结果摘要和 evidence IDs；
- 所有 Evidence 的 ID、类型、路径、行范围、内容摘要和当前 validity；
- 所有 Approval 的 ID、绑定摘要、状态、revision、expires/claimed/consumed 时间和关联调用；
- 预算上限与已用量，以及必要的 ledger 操作键摘要；
- stop reason、acceptance status、候选最终回答和 Completion 结论引用（若已存在）；
- 最后安全边界的 workspace identity、HEAD/`UNBORN`、index digest、workspace status/diff digest、
  外围保护状态、外围脏基线摘要及有界 workspace/external entries；
- 可用于 continuation 的最近步骤/工具 observation 摘要；
- checkpoint 创建时间和 schema version。

Checkpoint snapshot：

- 不保存 handler、Agent、Sandbox、任意绝对 evidence 全文或完整命令环境；
- 不保存审批 token、租约 token、API Key、令牌、密码或未脱敏参数；
- 默认上限为 1 MiB；超限返回 `CHECKPOINT_SNAPSHOT_TOO_LARGE`，不得无界降级；
- 使用固定 domain separation、UTF-8、排序键、固定分隔符和禁止 NaN 的 canonical JSON；
- 哈希输入至少包含 `{"schema":"guarded-harness/checkpoint/v1","snapshot":...}`；
- `snapshot_digest=sha256:<64 lowercase hex>`，读取时必须重新计算并逐字段校验。

Checkpoint 是“从哪个安全点继续”的只读事实，不替代 `runs`、`steps`、`tool_calls`、`evidence`、
`approvals`、`budget_ledger` 或 `audit_events`。

### 6.4 CheckpointService

可信装配入口：

```text
CheckpointService(
    storage: SQLiteStorage,
    config: ResolvedHarnessConfig,
    sandbox: Sandbox,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
)
```

公共操作：

```text
create_checkpoint(
    run_id,
    lease_token,
    boundary: CheckpointBoundary,
) -> Checkpoint

get_checkpoint(
    checkpoint_id,
) -> Checkpoint

latest_checkpoint(
    run_id,
) -> Checkpoint | None

verify_checkpoint(
    checkpoint_id,
    lease_token,
) -> CheckpointVerification

prepare_recovery(
    run_id,
    lease_token,
) -> RecoveryPlan

build_continuation_context(
    recovery_plan,
) -> ContinuationContext

render_continuation(
    context: ContinuationContext,
) -> str

get_recovery_record(
    recovery_id,
) -> RecoveryRecord

list_recovery_records(
    run_id,
    limit=100,
) -> tuple[RecoveryRecord, ...]
```

前置条件：

- `CheckpointService` 只能由可信装配代码构造；
- `config` 与 `sandbox` 必须绑定同一规范化工作区和 Git 根；
- `run_id` 已存在，调用方持有当前未过期 Run 租约；
- current config fingerprint 与 Run 的指纹一致，除非调用方只执行无副作用的 `get_checkpoint`；
- 调用方没有持有旧进程内存中的模型/工具状态作为恢复事实。

后置条件：

- `create_checkpoint` 要么完整提交 Checkpoint、Run 最后 Checkpoint 指针、审计和必要的状态更新，
  要么全部回滚；
- `prepare_recovery` 持久化稳定恢复决定；需要转为未知/阻塞时，状态、审批失效、证据失效和审计
  在同一事务内提交；
- 只读查询不能隐式创建 Checkpoint、续租、消费审批或改写 Run；
- 返回给调用方的模型/observation 只包含有界、脱敏结果。

### 6.5 CheckpointVerification

```text
CheckpointVerification(
    schema_version=1,
    checkpoint_id,
    run_id,
    valid: bool,
    checked_audit_sequence: int,
    code: StableErrorCode | None,
    reason: str | None,
    checked_at,
)
```

验证至少包括：

- Checkpoint ID、Run ID、sequence 和关联 step/tool/approval 存在且属于同一 Run；
- snapshot schema 与代码支持版本兼容；
- canonical snapshot digest、SQLite 索引字段与 payload 一致；
- snapshot 内的 Run revision 不大于当前 Run revision；
- `last_audit_sequence`/hash 存在，且从 genesis 到该序号完整 Audit 链有效；
- 快照内引用的 evidence、approval、step、invocation 和 plan 均真实存在；
- 当前 config/workspace 校验结果符合本次验证模式；
- 不把 Checkpoint 内旧 `is_valid=true` 误当作“当前仍有效证据”。

校验失败关闭。损坏或未知 schema 的 Checkpoint 不得被当作“没有 Checkpoint”而静默跳过。

### 6.6 RecoveryPlan

```text
RecoveryPlan(
    schema_version=1,
    recovery_id,
    run_id,
    checkpoint_id: CheckpointId | None,
    source_run_revision: int,
    status: RecoveryStatus,
    action: RecoveryAction,
    reason_code: StableErrorCode | None,
    invocation_id: ToolCallId | None,
    approval_id: ApprovalId | None,
    valid_evidence_ids: tuple[EvidenceId, ...],
    invalid_evidence_ids: tuple[EvidenceId, ...],
    continuation_context_digest: Sha256Digest | None,
    created_at,
)
```

约束：

- Status/action 固定配对：`CONTINUE_AGENT`、`REENTER_GATE`、
  `RESUME_CLAIM_THEN_REENTER_GATE` 和
  `REBUILD_CHECKPOINT_THEN_CONTINUE` 使用 `RESUMABLE`；`WAIT_FOR_APPROVAL` 使用
  `WAITING_APPROVAL`；`MARK_UNKNOWN_RESULT`、`MANUAL_REVIEW` 使用 `MANUAL_REVIEW`；
  `ALREADY_TERMINAL` 使用 `ALREADY_TERMINAL`；
- `REENTER_GATE` 和 `RESUME_CLAIM_THEN_REENTER_GATE` 必须恰好关联一个 PREPARED invocation；
- `source_run_revision` 记录规划时的 Run revision；生成 continuation 前必须与当前 revision 相同，
  否则 RecoveryPlan 已过期，必须重新规划；
- `MARK_UNKNOWN_RESULT` 必须关联 STARTED invocation，并让 Run 进入不可自动恢复状态；
- `WAIT_FOR_APPROVAL` 只用于存在 `PENDING` 审批且未过期；
- `REBUILD_CHECKPOINT_THEN_CONTINUE` 只用于结果已提交但对应安全边界 Checkpoint 缺失；
- `MANUAL_REVIEW` 不携带任何可执行授权、handler 参数或自动重试指令；
- 同一稳定输入重复规划返回同一事实或可识别已存在记录，不重复标记未知、不重复失效审批；
- RecoveryPlan 是恢复事实，不是工具授权；后续 PREPARED 执行仍必须经过 Gate。

恢复记录至少包含 `recovery_id`、`run_id`、source checkpoint、动作、状态、原因码、关联调用/
审批、配置/工作区摘要、规划时间和审计序号。记录追加后不可修改或删除。

### 6.7 ContinuationContext

```text
ContinuationContext(
    schema_version=1,
    run_id,
    checkpoint_id: CheckpointId | None,
    task_id,
    task_kind,
    task_description,
    config_fingerprint,
    workspace_root,
    git_root,
    current_status,
    stop_reason,
    budget: RunBudget,
    usage: BudgetUsage,
    remaining_budget: BudgetUsage,
    plan: RunPlan | None,
    completed_steps: tuple[ContinuationStep, ...],
    valid_evidence: tuple[ContinuationEvidence, ...],
    pending_invocation: ContinuationInvocation | None,
    pending_approval: ContinuationApproval | None,
    prohibited_actions: tuple[str, ...],
    context_digest,
    generated_at,
)
```

约束：

- 只从已校验的 StateStore/Checkpoint 结构事实生成；
- 步骤与 observation 按稳定顺序保留最近 10 项，单项摘要不超过 4,000 字符；
- 完整 continuation context 不超过 64 KiB，渲染文本不超过 64 KiB；
- valid evidence 只含当前文件版本仍匹配的事实；失效证据以 ID 加入 `invalid_evidence_ids`，
  但不得继续作为可引用的“已读事实”；
- pending invocation 只包含工具名、调用 ID、规范化参数摘要、状态、审批 ID 和下一步动作，
  不包含 handler、内部异常栈或可绕过 Gate 的执行对象；
- remaining budget 使用非负值，跨数据库重开后从持久化 usage 计算，不重置；
- `prohibited_actions` 至少明确禁止重放 STARTED/UNKNOWN、禁止复用已消费审批、禁止把模型
  自述当作交付证据；
- 渲染使用固定字段顺序与固定文本模板；相同输入在相同 schema/版本下得到逐字节相同结果；
- 不包含旧 Agent memory、模型隐藏推理、完整 Audit 事件流、租约 token、审批 token 或敏感环境。

恢复 Plan 与 continuation context 都不执行模型；WP-10/WP-11 使用它们创建新 Agent/CLI 会话。

### 6.8 Gate 的 PREPARED 恢复入口

为满足“PREPARED 且未 STARTED 只能重新经过 Gate”，WP-07 在 Gate 增加窄范围公共入口：

```text
Gate.reenter_prepared(
    run_id: RunId,
    tool_call_id: ToolCallId,
    context: ToolExecutionContext,
    lease_token: str,
) -> ToolObservation
```

前置条件：

- 调用方来自可信恢复编排，而不是模型直接调用；
- `context.run_id`、task、kind、actor 与 Run 一致；
- Invocation 存在且状态为 `PREPARED`；
- 工具名、规范化参数和参数摘要从持久化 Invocation 读取，不接受模型重新提供；
- 若存在审批，必须是同一绑定、有效且状态为 `APPROVED` 或 `CLAIMED`；
- 当前 checkpoint、配置、工作区和租约已由 WP-07 验证通过。

固定行为：

1. 重新执行 Registry 可见性、schema、语义、权限、风险、审批、重复调用和预算检查；
2. `CLAIMED + PREPARED` 先在同一事务调用 `ApprovalStore.resume_claim`，追加恢复审计；
3. 不再创建 Invocation、不重复扣减已经记录的 ToolCall 预算；
4. 在独立事务将 `PREPARED -> STARTED` 并写审计；
5. 只有 STARTED 提交成功后才调用持久化 Invocation 对应的可信 handler；
6. 结果仍按 WP-06 映射为确定结果或 `UNKNOWN_RESULT`；
7. 任一步失败都不得直接调用 handler，不得伪造成功结果。

该入口必须复用现有 Gate 检查函数，不能复制一套弱化门禁。若上游评审不接受修改 Gate 公共 API，
WP-07 必须保持 `blocked`，不能以直接调用 `approval.resume_claim` 加 handler 的方式绕过 Gate。

## 7. 行为契约

### 7.1 创建 Checkpoint

固定顺序：

```text
validate service/config/sandbox binding
  -> open BEGIN IMMEDIATE UnitOfWork
  -> verify lease
  -> validate boundary and Run state
  -> read complete RunSnapshot and audit head
  -> capture controlled workspace snapshot
  -> build canonical snapshot and digest
  -> check same-boundary idempotency/conflict
  -> insert append-only Checkpoint
  -> update runs.last_checkpoint_id and revision conditionally
  -> append CHECKPOINT_WRITTEN audit
  -> commit
```

重要语义：

- workspace capture、快照读取、Checkpoint insert、Run 指针和审计必须在同一事务语义内完成；若实现
  需要先在事务外扫描工作区，则必须在写入前重新验证 Run revision/工作区摘要未变化，否则失败关闭；
- Checkpoint 失败时不得修改 `runs.last_checkpoint_id`，不得只写审计或只写快照；
- Checkpoint 序号在 Run 内从 1 连续递增；
- `STEP_COMPLETED` 和工具结果边界要求 Run 为 `RUNNING`；审批落定允许
  `RUNNING/WAITING_APPROVAL`；最终验收边界允许 `VERIFYING/COMPLETED/FAILED`；
- `STEP_COMPLETED` 只能在 Step 已有合法终态时写入；
- 工具结果边界只能在 Invocation 已为 `SUCCEEDED/FAILED/TIMED_OUT/UNKNOWN_RESULT` 时写入；
- `APPROVAL_REQUESTED` 只用于审批已创建且处于 PENDING 的安全等待点；
- `APPROVAL_SETTLED` 必须对应审批已进入稳定状态，不能用 PENDING 冒充完成；
- `UNKNOWN_RESULT` Checkpoint 只能记录阻塞事实，不能授权恢复执行；
- 正常调用重复提交同一等价安全边界时返回已有 Checkpoint，不产生第二条审计；
- 同一 Run 的 Checkpoint 写入串行；并发重复调用最多成功创建一次。

### 7.2 安全边界与快照内容

至少支持：

1. 模型 step 完成；
2. 工具调用成功、明确失败、超时或结果未知；
3. 审批批准、拒绝、过期、失效、claim 恢复或消费落定；
4. 最终验收结果落定（WP-08/WP-09 后续调用）。

WP-07 提供 Checkpoint 入口，不规定 Agent 何时调用；WP-10 必须在这些边界调用并记录实际调用点。
只有 Checkpoint 成功写入后，Harness 才可把该边界视为“安全步骤完成”。

### 7.3 准备恢复

恢复前按固定顺序执行：

1. 验证 SQLite schema、migration checksum、foreign keys、quick check 和 Audit 链；
2. 验证调用方持有当前未过期租约，且 Run 不在不可自动恢复的终态；
3. 验证 current config fingerprint、workspace root、git root、workspace prefix；
4. 读取最新 Checkpoint 和完整 RunSnapshot，重新验证快照哈希与关联；
5. 验证 HEAD/index、identity、外围保护状态和外围脏基线；
6. 物化到期审批，禁止过期审批重新成为可执行授权；
7. 识别所有 PREPARED/STARTED Invocation 和 PENDING/APPROVED/CLAIMED/INVALIDATED Approval；
8. 对当前文件证据重新读取摘要，必要时将旧 Evidence 单向标记失效并审计；
9. 根据第 8 节矩阵选择唯一的 RecoveryAction；
10. 在需要时原子标记 `UNKNOWN_RESULT`/`BLOCKED`、失效审批并写恢复审计；
11. 生成并校验有界 ContinuationContext；
12. 返回 RecoveryPlan；只有 `RESUMABLE`/`WAITING_APPROVAL` 可以交给 WP-10/WP-11 继续编排。

读取、比较或审计任一失败时，不得返回可执行恢复动作；结果必须是 `MANUAL_REVIEW` 或结构化
失败，并保持已确定事实不被猜测替代。

### 7.4 恢复后的执行

- `CONTINUE_AGENT`：创建新 Agent，注入 continuation context，模型重新开始决策；所有后续工具调用
  仍经过 Gate。
- `WAIT_FOR_APPROVAL`：不创建执行动作；由 WP-11 展示审批并结束或等待。
- `REENTER_GATE`：使用持久化 Invocation 重新经过 Gate；模型不能改写其工具名/参数。
- `RESUME_CLAIM_THEN_REENTER_GATE`：先恢复既有 claim，再经 Gate 重新检查，最多启动一次 handler。
- `REBUILD_CHECKPOINT_THEN_CONTINUE`：先按第 8.7 节补建安全边界，再返回 `CONTINUE_AGENT` 或
  带有持久化结果的 continuation。
- `MARK_UNKNOWN_RESULT`/`MANUAL_REVIEW`：不自动调用模型执行副作用；WP-11 只能展示事实和人工
  核对入口。

恢复 `PREPARED` 调用时，若持久化参数与审批摘要不再一致或参数已因脱敏不可恢复，立即保留具体
Gate 错误码并把 Run 收口为 `BLOCKED/GATE_BLOCKED`。恢复器不得把普通拒绝当作可继续的安全
边界递归，也不得创建第二个同 boundary_ref 但快照不同的 Checkpoint。

### 7.5 证据与审批版本校验

- 文件型 Evidence（READ、DOCUMENT、带 path 的 DIFF）必须用 Sandbox 安全读取当前文件并比较
  `content_digest`；文件不存在、不可读或摘要变化时标记失效；
- COMMAND/TEST_REPORT 等无 path 证据保留原摘要，但其能否支撑当前源码版本仍由 WP-09 结合源码
  版本和测试节点判定；
- Evidence validity 只允许 `true -> false` 单向失效，不允许恢复为 true 掩盖漂移；
- `PENDING/APPROVED` 审批在配置、工作区身份、工具参数或绑定变化时失效；
- `CLAIMED + PREPARED` 审批只有全量校验通过后才可通过 `resume_claim` 复用；
- `CLAIMED + STARTED/UNKNOWN_RESULT` 审批不得撤销、重新 claim 或消费；
- `CONSUMED`、`REJECTED`、`EXPIRED`、`INVALIDATED` 永远不能重新生效；
- 旧审批失效后不得在同一 Run 中自动创建“等价但重新绑定”的审批；人工处理通常启动新 Run。

## 8. 崩溃窗口与恢复矩阵

WP-07 必须对以下窗口有确定性测试。“无明确结果”一律保守处理，不以工作区看起来正常为成功证据。

| ID | 持久化事实 | RecoveryAction | Run/Invocation 结果 |
| --- | --- | --- | --- |
| W0 | 无本次执行意图 | `CONTINUE_AGENT` | 模型可通过新 Agent 重新提议；重新经过正常 Gate |
| W1 | Invocation `PREPARED`，无审批且未 STARTED | `REENTER_GATE` | 使用同一持久化调用重新门禁；不直接调用 handler |
| W2 | Invocation `PREPARED`，审批 `APPROVED` | `REENTER_GATE` | Gate 重新绑定、claim、预算和权限检查后最多执行一次；任何 Gate 拒绝都转为 `BLOCKED/GATE_BLOCKED` |
| W3 | Invocation `PREPARED`，审批 `CLAIMED` | `RESUME_CLAIM_THEN_REENTER_GATE` | `resume_claim` 后写 STARTED，再执行；claim 不重复 |
| W4 | Invocation `STARTED`，无确定结果 | `MARK_UNKNOWN_RESULT` | 原子标记 UNKNOWN；Run `BLOCKED/UNKNOWN_RESULT/NOT_EVALUATED`；禁止自动重放 |
| W5 | handler 已返回但结果事务未提交，状态仍为 `STARTED` | `MARK_UNKNOWN_RESULT` | 与 W4 相同；不得按工作区变化猜测成功 |
| W6 | Invocation 已有确定终态，对应安全边界 Checkpoint 缺失 | `REBUILD_CHECKPOINT_THEN_CONTINUE` | 由确定结果和当前受控状态补建 Checkpoint，再复用结果 |
| W7 | 确定结果及 Checkpoint 都已提交 | `CONTINUE_AGENT` | 把持久化结果加入 continuation；不重复 handler，继续 Agent 决策 |
| W8 | `PENDING` 未过期审批 | `WAIT_FOR_APPROVAL` | 不执行、不自动批准，进程可安全退出 |
| W9 | `UNKNOWN_RESULT` 已持久化 | `MANUAL_REVIEW` | 永不自动重放；原审批保持 `CLAIMED` |
| W10 | 多个冲突的 PREPARED/STARTED 状态或状态机不自洽 | `MANUAL_REVIEW` + `BLOCKED` | 失败关闭，需人工核对 |
| W11 | 配置/工作区/证据/审批/schema/audit 校验失败 | `MANUAL_REVIEW` + `BLOCKED` | 不返回可执行动作 |

窗口判定优先级固定为：状态完整性 > schema/audit > 工作区身份/配置 > 未确定执行 >
审批状态 > 确定结果/Checkpoint 一致性 > continuation。不得让较后的 Checkpoint 重建掩盖较前的
安全校验失败。

### 8.1 W6 的受控重建

只有同时满足以下条件才允许 `REBUILD_CHECKPOINT_THEN_CONTINUE`：

- Invocation 具有 `SUCCEEDED`、`FAILED` 或 `TIMED_OUT` 确定结果；
- 不存在 STARTED、UNKNOWN_RESULT 或第二个冲突执行意图；
- 结果与审计链有效，审批消费状态一致（适用时）；
- HEAD/index、Git/workspace identity、配置指纹和外围保护基线未变化；
- 当前工作区差异可由自上一个 Checkpoint 以来已提交确定结果的 Evidence 归属，或没有差异；
- Evidence 的版本校验和失效处理已完成；
- 补建 Checkpoint 事务再次确认 Run revision、Invocation 终态和审计头未变化。

任何条件不满足时进入 `BLOCKED`，不得用“补建 Checkpoint”掩盖未知副作用或外部并发修改。

## 9. 状态、事务与不变量

### 9.1 状态

WP-07 不新增 Agent 状态机；继续使用 WP-00/WP-01 的 Run/Invocation/Approval 状态。恢复必须保持：

- `STARTED` 无确定结果 -> Invocation `UNKNOWN_RESULT`，Run `BLOCKED`，
  `stop_reason=UNKNOWN_RESULT`；
- 配置/工作区绑定变化 -> Run `BLOCKED`，`stop_reason=WORKSPACE_CHANGED`（配置变化也使用该
  稳定原因并在恢复 reason code 中区分）；
- schema/audit/Checkpoint 损坏 -> Run `BLOCKED`，使用稳定恢复错误码；
- `WAITING_APPROVAL + PENDING` -> 仍保持等待，不得推进 Run；
- 已确定结果且无未决执行 -> 按 continuation 继续，不修改原工具结果；
- 预算耗尽由 WP-10 终态规则处理，WP-07 只读取并保持 usage，不把预算重置或增加。

### 9.2 事务边界

- Checkpoint insert、`runs.last_checkpoint_id`、Run revision、Checkpoint 审计同一事务；
- STARTED -> UNKNOWN、Run -> BLOCKED、恢复记录和审计同一事务；
- 审批失效、Run -> BLOCKED 和审计同一事务；
- Evidence 单向失效与对应审计同一事务；
- W6 补建 Checkpoint 与其审计同一事务；
- 恢复计划持久化与任何需要立即落定的未知/阻塞状态变更同一事务；
- 不以进程内锁替代 lease、revision 和 SQLite condition update；
- 事务失败后不自动重试副作用，只能重新读取事实并重新规划。

### 9.3 不变量

| ID | 不变量 |
| --- | --- |
| WP07-INV-001 | 一个 Run 的 Checkpoint sequence 从 1 连续递增。 |
| WP07-INV-002 | Checkpoint、恢复记录和 Audit 均为追加式事实，不允许修改或删除。 |
| WP07-INV-003 | Checkpoint hash 不匹配、schema 不支持或引用缺失时必须失败关闭。 |
| WP07-INV-004 | PREPARED 调用不能被恢复模块直接执行，必须重新经过 Gate。 |
| WP07-INV-005 | STARTED 且无确定结果不能被解释为成功或自动重放。 |
| WP07-INV-006 | 已确定结果不能重复调用原 handler。 |
| WP07-INV-007 | 一个审批最多 claim/consume 一次，恢复不重置审批生命周期。 |
| WP07-INV-008 | 预算 usage 跨重开、恢复和租约接管保持不变。 |
| WP07-INV-009 | 旧 Checkpoint/Evidence 不得被当成当前工作区或当前代码版本事实。 |
| WP07-INV-010 | Continuation context 不包含旧 Agent memory、隐藏推理、完整 Audit 或秘密。 |
| WP07-INV-011 | 同一 Run 的恢复和 Checkpoint 写操作串行，审计顺序稳定。 |
| WP07-INV-012 | 任何自动恢复动作必须由 RecoveryPlan 的稳定动作表达。 |

## 10. 错误契约

WP-07 至少追加以下稳定错误码：

```text
CHECKPOINT_NOT_FOUND
CHECKPOINT_INVALID
CHECKPOINT_HASH_MISMATCH
CHECKPOINT_SCHEMA_UNSUPPORTED
CHECKPOINT_SEQUENCE_CONFLICT
CHECKPOINT_SNAPSHOT_TOO_LARGE
CHECKPOINT_WRITE_FAILED

RECOVERY_NOT_ALLOWED
RECOVERY_STATE_AMBIGUOUS
RECOVERY_CONFIG_MISMATCH
RECOVERY_WORKSPACE_CHANGED
RECOVERY_AUDIT_INVALID
RECOVERY_APPROVAL_INVALID
RECOVERY_EVIDENCE_INVALID
RECOVERY_CHECKPOINT_REQUIRED
RECOVERY_MANUAL_REVIEW_REQUIRED
RECOVERY_WRITE_FAILED
RECOVERY_CONTEXT_TOO_LARGE
```

| 错误 | 触发 | 重试 | 模型/调用方可见信息 |
| --- | --- | --- | --- |
| `CHECKPOINT_HASH_MISMATCH` | snapshot 内容与持久化 hash 不一致 | 否 | 指出 checkpoint/run，不返回原文 |
| `CHECKPOINT_SCHEMA_UNSUPPORTED` | schema 高于支持版本 | 否 | 要求人工迁移/前向修复 |
| `CHECKPOINT_WRITE_FAILED` | 快照、Run 指针或审计无法原子写入 | 否 | 边界失败，不得宣称安全 |
| `RECOVERY_WORKSPACE_CHANGED` | 身份/外围/内容变化且不可证明为已提交结果 | 否 | 阻塞并要求人工核对 |
| `RECOVERY_STATE_AMBIGUOUS` | STARTED、多执行意图或状态机不自洽 | 否 | 标记未知/阻塞，禁止重放 |
| `RECOVERY_APPROVAL_INVALID` | 审批过期、撤销、绑定变化或重放 | 否 | 旧授权失效，启动新 Run |
| `RECOVERY_MANUAL_REVIEW_REQUIRED` | 任何无法安全自动恢复的事实 | 否 | 返回核对入口，不提供执行授权 |
| `RECOVERY_CONTEXT_TOO_LARGE` | continuation 超限 | 否 | 保留安全摘要并阻塞，不截断成误导上下文 |

错误消息和审计摘要必须经过统一脱敏并有长度/节点限制；不得包含租约 token、审批 token、完整
参数、密钥、凭据或敏感文件内容。

## 11. 兼容、迁移与数据保留

- 追加 migration v5，修复/重建后的数据库必须保留 v1 至 v4 已发布 migration checksum；
- 新增 `checkpoints` 和 `recovery_records` 表及必要索引、唯一约束和 `BEFORE UPDATE/DELETE`
  append-only trigger；不得修改历史 migration；
- 旧数据库没有 Checkpoint 时需要显式恢复差异：`runs.last_checkpoint_id` 为空的运行不得自动
  当作无损恢复，必须按 W6 或 `BLOCKED` 处理；
- Checkpoint/Recovery schema 使用版本号和严格读取；未知版本失败关闭；
- P0 默认保留全部 Checkpoint 和恢复记录，不自动清理；清理属于未来需要单独审计的运维能力；
- 新 Sandbox 快照接口是向后兼容增加；不改变现有 `capture_baseline()` 的干净工作区语义；
- Gate `reenter_prepared` 是新增可信入口，不改变现有 `invoke()` 的拒绝链；
- 任何公共 API 或错误码变化必须同步 `docs/decisions.md`、`docs/design.md`、下游计划和契约测试。

## 12. 验收场景

所有场景必须映射到自动化测试；崩溃注入不使用真实进程终止、网络、真实模型或用户凭据。

| ID | 场景 | 通过判据 |
| --- | --- | --- |
| WP07-AC-001 | Checkpoint 创建正常路径 | Checkpoint、Run 指针、审计同事务写入，重开后完整读取 |
| WP07-AC-002 | 快照字段完整性 | Run、step、计划、调用、结果、Evidence、Approval、预算、stop reason、审计头和 workspace 摘要均存在 |
| WP07-AC-003 | canonical hash 与确定性 | 等价状态得到相同 canonical 内容；重排无关集合不改变事实语义 |
| WP07-AC-004 | Checkpoint 篡改 | 修改 snapshot/hash/关联/sequence 后读取或恢复失败关闭 |
| WP07-AC-005 | append-only | UPDATE/DELETE Checkpoint 或 recovery record 被拒绝 |
| WP07-AC-006 | 写事务失败 | snapshot、Run 指针或 Audit 任一失败时全部回滚 |
| WP07-AC-007 | W0 无意图 | 恢复返回 `CONTINUE_AGENT`，不直接调用 handler |
| WP07-AC-008 | W1 PREPARED 无审批 | 恢复返回 `REENTER_GATE`，Gate 重检后才能 STARTED |
| WP07-AC-009 | W2 APPROVED + PREPARED | 恢复经 Gate claim、检查和 STARTED，预算不重复扣减 |
| WP07-AC-010 | W3 CLAIMED + PREPARED | `resume_claim` 追加恢复审计，handler 最多执行一次 |
| WP07-AC-011 | W4/W5 STARTED 无结果 | Invocation=UNKNOWN、Run=BLOCKED/UNKNOWN_RESULT，handler 重放次数为 0 |
| WP07-AC-012 | W6 结果已提交、Checkpoint 缺失 | 补建 Checkpoint 后复用结果，不重跑 handler |
| WP07-AC-013 | W7 确定结果和 Checkpoint 存在 | 不重跑 handler，结果与预算跨重开一致 |
| WP07-AC-014 | W8 待审批 | 返回 `WAIT_FOR_APPROVAL`，不执行、不 pending->approved |
| WP07-AC-015 | W9 UNKNOWN 人工复核 | 重复恢复仍不可自动继续，审批保持 CLAIMED |
| WP07-AC-016 | 配置变化 | 返回 `BLOCKED`/配置不匹配，旧审批失效，不调用 handler |
| WP07-AC-017 | 工作区身份/外围变化 | 返回 `BLOCKED/WORKSPACE_CHANGED`，旧审批和可控证据失效 |
| WP07-AC-018 | 文件证据版本变化 | Evidence 失效，continuation 不把它作为当前事实 |
| WP07-AC-019 | 审批过期/撤销/绑定变化 | 旧审批不可恢复，Run 不进入可执行状态 |
| WP07-AC-020 | 预算跨重开 | step/tool/token/wall-time usage 不重置，remaining 计算正确 |
| WP07-AC-021 | 租约并发 | 有效旧租约阻止接管；过期租约可接管；旧 token 不能提交/续租 |
| WP07-AC-022 | Audit chain 损坏 | 恢复失败关闭，不补写可执行状态 |
| WP07-AC-023 | continuation 有界确定 | 同样输入逐字节一致，不含 memory/隐藏推理/完整 Audit/secret，超限拒绝 |
| WP07-AC-024 | 人工复核入口 | 展示恢复原因和关联事实，但不产生 handler 授权或自动重试 |
| WP07-AC-025 | 离线重复性 | 两次隔离运行结果一致，无网络、真实模型、用户凭据或 skip 替代 |

## 13. 完成定义

WP-07 只有在以下条件全部满足时才可标记 `DONE`：

1. 第 14 节决策全部关闭，`spec.md` 提升为 `frozen`，`plan.md` 对应同一冻结版本；
2. Checkpoint 快照、hash、migration、append-only、事务和重开路径有自动化证据；
3. W0 至 W11 恢复窗口均有确定性测试，STARTED/UNKNOWN 的 handler 重放次数为 0；
4. PREPARED/CLAIMED 恢复重新经过 Gate，不能绕过权限、参数、预算或审批；
5. 旧证据、旧审批、配置/工作区变化和 Audit 损坏均失败关闭；
6. continuation context 有界、确定，不含旧 Agent memory、隐藏推理、完整 Audit 或秘密；
7. 跨 SQLite 重开后预算、审批和已执行步骤不重置；
8. 目标 pytest、全量 pytest、Ruff lint、Ruff format check 通过；
9. 文档、配置、错误码、migration 和实现契约一致；
10. `acceptance-report.md` 根据实际命令和证据标记 `accepted`，未实现能力没有被写成完成。

## 14. 已冻结决策

2026-11-06 经需求方确认，第 14 节原五项开放决策按推荐方案关闭，并登记为 D-53 至 D-57：

| 原问题 | 决策 | 冻结结论 | 影响 |
| --- | --- | --- | --- |
| WP07-OPEN-001 | D-53 Gate 恢复入口 | 新增 `Gate.reenter_prepared`，复用现有门禁；CLAIMED 先 `resume_claim`，不直接执行 handler | PREPARED/W3 恢复有唯一安全入口，不 monkey patch |
| WP07-OPEN-002 | D-54 未知/阻塞边界 | 同一 Run 不自动恢复；人工核对后启动新 Run | UNKNOWN 和 BLOCKED 保持不可逆安全边界 |
| WP07-OPEN-003 | D-55 W6 差异归属 | 只有可归因于已提交确定结果/Evidence 的内部差异可补建 Checkpoint | 合法写入恢复不误阻塞，外部/不可解释变化仍失败关闭 |
| WP07-OPEN-004 | D-56 Evidence 失效 | 受租约保护的单向 `valid -> false` 并使用 `EVIDENCE_INVALIDATED` 审计 | 旧证据成为持久事实，不被 continuation/Completion 误用 |
| WP07-OPEN-005 | D-57 上限与保留 | snapshot 1 MiB、context/render 64 KiB，P0 不自动清理 | 有界存储与 prompt；超限失败关闭 |
| 实现兼容修订 | D-58 待审批边界 | 追加 `APPROVAL_REQUESTED`，与 `APPROVAL_SETTLED` 区分 | PENDING 审批也能形成 C1 安全点 |

本规格已冻结。施工中如需改变以上行为，必须先提升规格版本并完成新一轮评审。
