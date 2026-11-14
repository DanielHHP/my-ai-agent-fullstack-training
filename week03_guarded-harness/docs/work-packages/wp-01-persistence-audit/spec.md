# WP-01 持久化与审计底座规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-01 持久化与审计底座 |
| 文档状态 | frozen |
| 规格版本 | 0.5.0 |
| 冻结日期 | 2026-11-14（D-106 Harness 心跳失败码修订） |
| 依赖 | WP-00 v1.3.0 |
| 下游工作包 | WP-02、WP-04、WP-07、WP-08、WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-GATE-002`、`REQ-STATE-001` 至 `REQ-STATE-003`、`REQ-AUDIT-001` 至 `REQ-AUDIT-004`、`REQ-CHECKPOINT-003`，以及第 8 节非功能要求 |
| 对应设计 | `design.md` 第 4、5、9、10、13、14 节 |
| 已确认决策 | D-09、D-11、D-12、D-14、D-16 至 D-21 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌表 revision 11 |

本文是 WP-01 已冻结的行为与验收契约。状态为 `frozen` 不表示 SQLite、StateStore、Audit 或
Redaction 已经实现；实现是否完成仍由测试、lint、格式检查和验收证据决定。第 13 节的决策已经
关闭，施工必须依赖 WP-00 v1.1.0 及以上配置契约。

文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-01 必须提供一个可独立验证、可被后续 Approval、Gate、Checkpoint、Harness 和 Completion
共同复用的持久化底座：

1. 一个版本化 SQLite 数据库安全保存 Run、计划、步骤、工具调用、预算、证据和 Completion；
2. 关键状态转换采用条件更新和合法状态机，非法转换、旧版本覆盖和失效租约失败关闭；
3. 跨进程 Run 租约能阻止并发写者同时拥有同一 Run；
4. 审计是每个 Run 内连续编号、带哈希链的 append-only 事实账本；
5. 业务状态与审计通过同一个写事务提交或共同回滚，不产生“状态成功、审计丢失”的缺口；
6. 所有写入状态、审计和错误对象的数据先经过统一、确定性、有界和循环安全的脱敏；
7. 数据库关闭并重开后能恢复完整的 WP-01 业务状态和审计链；
8. 默认测试离线、确定、可重复，并覆盖正常、拒绝和基础设施故障路径。

## 3. 范围与非目标

### 3.1 范围内

- `src/guarded_harness/storage.py`：SQLite 生命周期、migration、事务、锁、约束和稳定错误映射；
- `src/guarded_harness/state_store.py`：Run、计划、步骤、工具调用、预算、证据和 Completion；
- `src/guarded_harness/audit.py`：审计事件、连续序号、canonical JSON、哈希链、查询和校验；
- `src/guarded_harness/redaction.py`：敏感键、敏感值、凭据形态、循环、深度、节点和输出上限；
- `tests/test_state_checkpoint.py`：WP-01 状态持久化和租约部分；
- `tests/test_audit.py`：审计 append-only、原子性、脱敏、关联和并发部分；
- 必要的额外脱敏/存储单元测试；
- WP-01 error code、公共模型导出、包内文档和索引同步。

### 3.2 非目标

- 不实现 Gate 检查顺序、工具 handler、权限、风险、重复调用或预算门禁，这些属于 WP-04；
- 不实现审批创建、批准、拒绝、claim、consume、过期和撤销，这些属于 WP-02；
- 不实现 Checkpoint 快照、恢复动作、工作区变化判定和 Agent continuation，这些属于 WP-07；
- 不实现 Sandbox 的真实路径、命令、进程和敏感环境变量隔离，这些属于 WP-03/WP-06；
- 不实现 Completion 的两类任务验收策略，这些属于 WP-08/WP-09；
- 不接入 `ToolCallingAgent`、Fake Model、CLI 或审批等待；
- 不因为“数据库能打开”就宣称 Gate、恢复或最终验收已完成。

## 4. 飞书核心验收映射

飞书表格的 WP-01 相关条目如下。表中“本包完成”只表示 WP-01 应交付的持久化或审计事实，不表示
整条验收已由 WP-01 完成。

| 飞书编号 | 优先级 | 飞书通过标准 | WP-01 责任 | 完成工作包 |
| --- | --- | --- | --- | --- |
| A1 | P0 | 被拒绝调用的事件含 `rule`、`decision`、`actor`、`toolCallId` | 审计信封与 schema 必须保留 `rule_id`、`decision`、`actor`、`tool_call_id`；用合成拒绝事件验证可写入并重读 | WP-01 提供字段，WP-04 产生真实 Gate 拒绝 |
| A2 | P1 | 完整任务能关联 Gate、Sandbox、State、Approval 和执行结果 | 稳定保留 `run_id`、`step_id`、`tool_call_id`、`approval_id`，并提供按关联 ID 稳定查询 | WP-12 端到端关联 |
| H3 | P0 | 达到最大轮数或预算时以明确 `StopReason` 停止，不伪装成功 | 原子保存预算上限与已用量；禁止已提交 usage 超过上限；验证 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED` 可持久化且重开不变 | WP-01 提供持久化事实，WP-04/WP-10 执行预算门禁与停止 |
| C1 | P0 | 每个安全步骤后保存包含 Run、step、计划、证据、预算和待审批动作的 Checkpoint | WP-01 提供 Run、计划、步骤、证据、预算和工具调用状态的完整快照基础 | WP-07 生成并验收 Checkpoint |
| C2 | P0 | 写入完成后结果未保存时恢复，标记 `UNKNOWN_RESULT` 且不重放 | 持久化 `STARTED` 与 `UNKNOWN_RESULT` 状态并支持重开读取，不设计自动重放 | WP-06/WP-07 |
| C3 | P0 | 换进程恢复后找回进度，已完成写入不重复，计数不清零 | 预算、步骤、工具调用和审计跨连接可读且不重置；写更新必须绑定有效租约 | WP-07/WP-11 |
| C4 | P0 | 恢复时工作区变化使旧证据或旧审批失效并停止 | 只提供带工作区指纹的事实记录，不执行恢复判定 | WP-07 |
| T1 | P0 | 所有 P0 场景有确定性、可重复测试 | WP-01 的原子性、状态机、租约、脱敏、append-only 和重开测试必须确定且无跳过替代 | WP-01 子集，WP-12 全局 |

飞书其余 G/S/H1/H2/T2 条目分别由 WP-04、WP-03/WP-06、WP-08/WP-09、WP-10/WP-12 负责。
WP-01 不得用本表声称这些下游能力已经实现。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `storage.py` | 路径、连接、PRAGMA、migration、事务、约束、SQLite 异常映射 | Gate 规则、审批决定、恢复动作 |
| `state_store.py` | 严格业务状态、条件更新、状态机、租约、快照读取 | 解释模型对话、替代 append-only 审计 |
| `audit.py` | 稳定事件信封、序号、canonical JSON、哈希链、查询和完整性校验 | 保存业务当前状态、修改既有事件 |
| `redaction.py` | 递归脱敏、脱敏计数、循环与有限深度/节点/输出裁剪 | 访问仓库、读取真实环境、生成安全结论 |

依赖方向为：

```text
domain.py
   ^
   |
redaction.py      storage.py
                  /        \
          state_store.py   audit.py
                  \        /
                 unit of work
```

`redaction.py` 和 `storage.py` 不得导入 Gate、Sandbox、Approval、Checkpoint、Completion 或
`smolagents`。`state_store.py` 与 `audit.py` 共享事务能力，但不得把 Audit 数据当作业务状态，
也不得通过更新审计行“回写”业务事实。

## 6. 公共接口

下列名称与语义是冻结契约。最终实现可使用内部辅助类型，但调用方必须能通过包根或模块公共
入口使用这些能力。

### 6.1 SQLiteStorage 与 UnitOfWork

```text
SQLiteStorage(
    database_path: Path,
    workspace_root: Path,
    clock: Clock,
    busy_timeout_ms: int = 5000,
)

SQLiteStorage.initialize() -> None
SQLiteStorage.transaction(*, actor: str) -> ContextManager[StorageUnitOfWork]
SQLiteStorage.schema_version() -> int
SQLiteStorage.verify_integrity() -> IntegrityReport

StorageUnitOfWork.state -> StateStore
StorageUnitOfWork.audit -> AuditStore
```

契约：

- `initialize()` 创建尚不存在的安全父目录、打开数据库、应用待执行 migration 并校验 schema；
- `transaction()` 在进入上下文时执行 `BEGIN IMMEDIATE`；正常退出提交，抛出异常回滚；
- 一个 `StorageUnitOfWork` 只在上下文存活期间有效，退出后继续使用必须拒绝；
- `StateStore` 的可变操作和 `AuditStore.append()` 只能通过活动 UnitOfWork 执行；
- 公共状态写入自动追加对应的必要审计事实，不能在成功路径上只提交状态而不追加审计；
- 调用方可在同一 UnitOfWork 追加 Gate、Approval、Checkpoint 等额外事实，但这些事实仍与其他
  写入共同提交或回滚；
- 未捕获的 SQLite、序列化、校验或审计错误均回滚整个 UnitOfWork；
- 不在失败后自动重试事务。调用方必须重新读取状态、重新通过 Gate 后才能决定是否再次提交。

`transaction()` 不提供静默嵌套事务。需要组合业务动作时，复用同一个 UnitOfWork；在活动
UnitOfWork 内再次开启写事务必须返回 `STATE_TRANSACTION_FAILED`。

### 6.2 StateStore

公共操作：

```text
create_run(run: Run, owner_id, lease_policy: LeasePolicy) -> RunLease
get_run(run_id: RunId) -> Run
get_run_snapshot(run_id: RunId) -> RunSnapshot

put_plan(run_id, expected_revision, plan: RunPlan, lease_token) -> RunPlan
create_step(step: Step, expected_run_revision, lease_token) -> Step
transition_step(step_id, expected_status, expected_revision, target,
                ended_at, lease_token) -> Step

create_invocation(invocation: ToolInvocation,
                  expected_run_revision, lease_token) -> ToolInvocation
transition_invocation(run_id, tool_call_id, expected_status,
                      expected_revision, target, result_summary,
                      error_code, lease_token) -> ToolInvocation

add_evidence(evidence: Evidence, lease_token) -> Evidence
record_budget_delta(command: BudgetDeltaCommand, lease_token) -> BudgetUsage
record_completion(check: CompletionCheck,
                  expected_run_revision, lease_token) -> CompletionCheck

transition_run(run_id, expected_status, expected_revision, target,
               stop_reason, ended_at, lease_token) -> Run

acquire_lease(run_id, owner_id, ttl_seconds) -> RunLease
renew_lease(lease, ttl_seconds) -> RunLease
release_lease(lease) -> None

get_run_revision(run_id) -> int
get_invocation_revision(run_id, tool_call_id) -> int
verify_lease(run_id, lease_token) -> None
```

所有读取结果必须重新经过冻结的 Pydantic 模型校验。数据库中的类型、枚举、时间、ID、JSON、
状态组合或引用不合法时，不得返回部分对象，必须失败关闭。

v0.4.0 增加的三个只读接口供 WP-04 在不读取私有字段、不执行直接 SQL 的前提下组合条件更新：
revision getter 返回当前版本，`verify_lease` 只验证当前未过期 owner token，不续期、不修改状态。

`RunSnapshot` 在 WP-01 至少包含：

```text
run: Run
plan: RunPlan | null
steps: tuple[Step, ...]
invocations: tuple[ToolInvocation, ...]
evidence: tuple[Evidence, ...]
completion: CompletionCheck | null
read_at: UTC datetime
```

快照必须在一个一致读取事务中取得。审批和 Checkpoint 字段由 WP-02、WP-07 在其 schema 版本中
扩展；WP-01 不得返回伪造的空审批或空 Checkpoint 并将其解释为“没有待处理动作”。

### 6.3 RunLease

```text
RunLease(
    run_id,
    owner_id,
    token,
    acquired_at,
    heartbeat_at,
    expires_at,
)
```

- `owner_id` 标识运行实例，`token` 是不可预测的持有者凭证；
- 数据库只保存 `token` 的 SHA-256 摘要，不保存原值；
- `acquire_lease` 在 Run 没有未过期租约时成功；已有有效租约时返回 `STATE_LEASE_HELD`；
- 过期租约可以被新的 owner/token 接管，接管与审计必须在同一事务提交；
- `renew_lease` 只允许原 token 在租约未过期时续期；
- `release_lease` 只允许原 token；释放后旧 token 立即失效；
- 任何 Run 级可变状态提交都必须验证有效租约；公共 `create_run` 原子写入 Run、获取租约并返回
  该租约，不暴露无租约 Run；
- 租约判断使用注入的 `Clock`，测试不使用 `sleep` 或墙钟；
- 租约仅为“同一 Run 单写者”协作机制，不替代 SQLite 唯一约束、条件更新或审计事务。

### 6.4 BudgetDeltaCommand

```text
BudgetDeltaCommand(
    run_id,
    source_kind,
    source_id,
    category,
    expected_run_revision,
    delta,
)
```

- `source_kind` 至少支持 `STEP`、`TOOL_CALL`、`HARNESS`，`source_id` 是稳定、长度受限的关联 ID；
- `category` 固定为 `STEPS`、`TOOL_CALLS`、`TOKEN_COST`、`WALL_TIME`；
- 每次命令只更新一个 category，`delta` 不得为负；
- operation key 由 `run_id + source_kind + source_id + category` 使用固定域分隔哈希生成；
- 重复应用同一 key 且 delta 相同，返回已经提交的 usage，不重复累加；
- 同一 key 携带不同 delta 返回 `STATE_CONFLICT`；
- 每个成功增量在 `budget_ledger` 留下事实，并与 Run usage 和 `BUDGET_UPDATED` 审计原子提交；
- token/成本在 SQLite 中以规范十进制文本保存，不转换为二进制浮点数；
- 任一维度超过上限时整个更新失败，usage 保持不变，返回 `STATE_BUDGET_EXCEEDED`；
- 上限相等是允许的；“达到上限”后的停止由 Gate/Harness 决定，StateStore 不得把预算耗尽伪装为
  `COMPLETED` 或 `ACCEPTED`。

### 6.5 AuditStore

公共操作：

```text
append(draft: AuditEventDraft) -> AuditEventRecord
get(run_id, sequence) -> AuditEventRecord | null
list_run_events(run_id, after_sequence=0, limit=100) -> tuple[AuditEventRecord, ...]
list_tool_events(run_id, tool_call_id, limit=1000) -> tuple[AuditEventRecord, ...]
list_approval_events(run_id, approval_id, limit=1000) -> tuple[AuditEventRecord, ...]
verify_chain(run_id) -> AuditChainReport
```

`AuditEventDraft` 至少包含：

```text
run_id, event_type, phase, status, decision, code
step_id, tool_call_id, approval_id
actor, tool_name, rule_id, workspace_fingerprint
input_summary, output_summary, evidence_ref
occurred_at
```

约束：

- `status` 必填，固定支持 `PENDING`、`ALLOWED`、`DENIED`、`STARTED`、`SUCCEEDED`、
  `FAILED`、`TIMED_OUT`、`UNKNOWN_RESULT`、`STOPPED`、`BLOCKED`；
- `code` 在 `DENIED`、`FAILED`、`TIMED_OUT`、`UNKNOWN_RESULT` 时必填，成功事件可为 `null`；
- `tool_call_id`、`approval_id`、`rule_id` 允许为 `null`，但一旦提供必须保持原始关联值；
- `decision` 至少支持 `ALLOW`、`DENY`、`NOT_APPLICABLE`；
- 审计记录返回稳定 `event_id`、`sequence`、`prev_hash` 和 `event_hash`；
- 查询按 `sequence` 升序稳定返回，`limit` 必须位于 `1..1000`；
- `verify_chain` 重新计算每个事件的哈希并检查序号连续、前序哈希匹配和事件模型有效性；
- 不以模型可读文本替代结构化字段，也不记录完整 Prompt、旧 Agent Memory 或隐藏推理。

### 6.6 Redactor

```text
RedactionLimits(
    max_output_bytes: int = 65536,
    max_lines: int = 512,
    max_depth: int = 16,
    max_nodes: int = 10000,
)

Redactor(sensitive_keys=(), sensitive_values=(), limits=RedactionLimits())
Redactor.redact(value) -> RedactionResult
RedactionResult.value
RedactionResult.truncated
RedactionResult.redaction_count
```

`value` 必须转换为 JSON 兼容值。实现对映射、序列、字符串、数字、布尔、`None` 和循环引用给出
确定性结果；不支持的对象类型不得被 `repr()` 后写入状态或审计。

## 7. 行为、状态与不变量

### 7.1 数据库初始化与 migration

- 数据库路径由必填的 `ResolvedHarnessConfig.state_dir` 与固定文件名 `harness.sqlite3` 组成；
- `state_dir` 必须位于规范化 `workspace_root` 外；初始化时重新验证真实路径、文件类型和包含关系；
- 父目录创建、数据库创建、WAL、外键、busy timeout 或 migration 任一步失败均失败关闭；
- 每个连接设置 `PRAGMA foreign_keys=ON`；数据库设置 WAL 与有界的 busy timeout；
- migration 以只追加、固定版本序列执行，每个版本有稳定 checksum；
- 已应用 migration 的 checksum 与代码不一致、数据库版本高于代码支持版本或 migration 中途失败时
  拒绝打开/继续，不自动降级或忽略错误；
- 多个进程同时初始化时，migration 必须由 SQLite 写锁串行化，只应用一次；
- `schema_migrations` 自身是唯一允许在 bootstrap 中条件创建的基础表；
- WP-01 的 migration 不创建或决定审批、Checkpoint、Gate 规则表；这些由所属下游工作包以新版本
  追加；
- 已发布或已被测试使用的 migration 不得修改；变更使用新 migration。

### 7.2 Run 状态机

合法 Run 转换固定为：

```text
NEW                 -> RUNNING
RUNNING             -> WAITING_APPROVAL | VERIFYING | STOPPED | FAILED | BLOCKED
WAITING_APPROVAL    -> RUNNING | STOPPED | FAILED | BLOCKED
VERIFYING           -> COMPLETED | FAILED | BLOCKED
```

`COMPLETED`、`STOPPED`、`FAILED`、`BLOCKED` 均为 WP-01 终态，不允许原位恢复到 `RUNNING`。
恢复新执行实例必须由 WP-07/WP-11 决定是继续同一 Run 的允许路径还是新建 Run；WP-01 不提供任意
“重置终态”后门。

跨字段不变量：

- `RUNNING`、`WAITING_APPROVAL`、`VERIFYING` 不要求 `ended_at`；终态必须写入 `ended_at`；
- `STOPPED`、`FAILED`、`BLOCKED` 必须有稳定 `stop_reason`；
- 预算耗尽只允许持久化为
  `status=STOPPED`、`stop_reason=BUDGET_EXHAUSTED`、`acceptance_status=NOT_EVALUATED`；
- `COMPLETED` 表示验收流程已执行，必须有终态 acceptance；它本身不等于 `ACCEPTED`；
- `acceptance_status` 一旦进入 `ACCEPTED`、`REJECTED` 或 `UNVERIFIABLE`，不得在原 Run 中改变；
- 已提交 usage 的任一维度不得超过对应上限；
- `updated_at` 不得早于 `created_at`，结束时间不得早于开始时间。

### 7.3 Step 状态机

```text
PLANNED -> RUNNING
RUNNING -> COMPLETED | FAILED
```

`COMPLETED` 和 `FAILED` 是终态。同一 Run 内 `(run_id, ordinal)` 唯一；步骤摘要必须在落盘前
脱敏和有界。删除或复用已结束 step 的序号不允许。

### 7.4 ToolInvocation 状态机

```text
PREPARED -> STARTED
STARTED  -> SUCCEEDED | FAILED | TIMED_OUT | UNKNOWN_RESULT
```

`SUCCEEDED`、`FAILED`、`TIMED_OUT`、`UNKNOWN_RESULT` 是终态。WP-01 不重放终态调用；重试必须以
新的调用事实进入 Gate。非幂等副作用无法确认结果时，调用方只能进入 `UNKNOWN_RESULT`，不得记录
为成功，也不得自动重设为 `PREPARED`。

同一 Run 内 `tool_call_id` 必须唯一。若上游提供重复 ID，`create_invocation` 返回
`STATE_CONFLICT`，不得覆盖已有调用。

### 7.5 RunPlan

- 首次设置前 `RunPlan` 为 `null`；一旦设置必须至少包含一个 PlanItem；
- PlanItem 使用从 0 开始连续且唯一的 ordinal；内容只包含公开计划摘要；
- 最多 50 项、单项最多 1,000 字符、整计划最多 32 KiB；
- 内容相同的幂等提交不增加 revision、不新增审计；内容变化必须增加 revision 并追加完整新计划；
- PlanItem 不保存隐藏推理或 Step 执行状态，执行进度通过 Step 表达。

### 7.6 乐观并发与租约

- `runs`、`run_plans`、`steps`、`tool_calls` 和 `completion_results` 都有从零开始的整数
  `revision`；
- 条件更新同时匹配主键、旧状态、旧 revision 和必需租约；
- revision 不匹配返回 `STATE_CONFLICT`，旧状态不匹配但目标不在合法转换中返回
  `STATE_INVALID_TRANSITION`；
- 返回成功只能发生在事务提交成功之后；
- 任何状态更新都不能依赖进程内 `Lock`、字典缓存或线程局部变量作为唯一互斥手段；
- 数据库忙超时、锁超时和连接失败不得降级为放行或静默重试。

Run 创建和租约获取在同一事务完成。`LeasePolicy` 默认 TTL 30 秒、允许 10 至 300 秒；Harness
以 `min(10, TTL/3)` 秒独立心跳。第一次续租失败进入租约风险并立即重试，连续第二次失败请求取消
handler；普通续租不写业务审计。旧 token 在接管后不能续租或提交状态。

`Evidence.tool_call_id` 是唯一生产该证据的工具调用，可以为 `null`；ToolInvocation 的
`evidence_ids` 是按生产调用查询得到的只读投影。证据必须在调用进入终态前写入，终态调用的证据
投影不得再变化。Completion 可以消费 Evidence ID，但不得修改证据的生产归属。

### 7.7 审计 append-only 与哈希链

- 每个 Run 的 `sequence` 从 `1` 开始连续递增，`(run_id, sequence)` 唯一；
- `prev_hash` 等于前一事件 `event_hash`；首事件的 `prev_hash` 为
  `sha256:` 后接 64 个零；
- `event_id` 由 Run 身份和 sequence 确定性生成，格式固定为
  `audit_<run_id 去掉 run_ 前缀>_<sequence:020d>`；
- `event_hash` 使用 `audit_schema=1` 域分隔，并对包含 `prev_hash`、不含 `event_hash` 的完整事件
  信封执行与 WP-00 一致的 canonical JSON 后再 SHA-256；
- `occurred_at` 来自注入的 `Clock`，按 D-11 序列化；
- `input_summary`、`output_summary`、`actor` 等可包含外部内容的字段必须先脱敏再参与哈希；
- 插入前验证 Run 存在；事件写入失败时整个 UnitOfWork 回滚；
- 数据库触发器拒绝 `UPDATE` 和 `DELETE` 已有审计行；
- 即使触发器被异常绕过，读取完整链时也必须通过哈希和连续性校验发现篡改。

### 7.8 状态与审计的固定处理顺序

单次 StateStore 写操作在同一个 UnitOfWork 内按以下逻辑处理：

1. 验证 UnitOfWork 活动、参数模型、工作区 Run 和租约；
2. 读取并校验当前状态和 revision；
3. 验证目标状态转换与跨字段不变量；
4. 写入业务状态和必要的关联行；
5. 生成对应审计事件并追加到审计链；
6. 提交事务；
7. 仅在第 6 步成功后向调用方返回成功对象。

第 1 至 5 步任一步失败都必须回滚，不得提交部分状态或单独审计。拒绝事件本身如果是调用方显式
追加的 Gate 事实，可按 AuditStore 的 append 契约提交；拒绝不能携带伪造成功结果。

业务状态自动事件名固定为：

| 状态变化 | 审计事件 |
| --- | --- |
| 创建 Run / NEW → RUNNING | `RUN_CREATED` / `RUN_STARTED` |
| RUNNING → WAITING_APPROVAL / VERIFYING | `RUN_WAITING_APPROVAL` / `RUN_VERIFYING` |
| → COMPLETED / STOPPED / FAILED / BLOCKED | `RUN_COMPLETED` / `RUN_STOPPED` / `RUN_FAILED` / `RUN_BLOCKED` |
| Step 状态变化 | `STEP_PLANNED` / `STEP_STARTED` / `STEP_COMPLETED` / `STEP_FAILED` |
| Invocation 状态变化 | `TOOL_PREPARED` / `TOOL_STARTED` / `TOOL_SUCCEEDED` / `TOOL_FAILED` / `TOOL_TIMED_OUT` / `TOOL_UNKNOWN_RESULT` |
| 计划、预算、证据、Completion | `RUN_PLAN_UPDATED` / `BUDGET_UPDATED` / `EVIDENCE_RECORDED` / `COMPLETION_RECORDED` |
| 租约获取、接管、释放 | `RUN_LEASE_ACQUIRED` / `RUN_LEASE_TAKEOVER` / `RUN_LEASE_RELEASED` |

自动事件类型由 StateStore 决定，调用方只能提供可信 actor、trace 和必要摘要，不能覆盖事件名。

## 8. 安全与故障语义

### 8.1 脱敏

以下内容不得以原值进入状态、审计或稳定错误信息：

- 键名大小写不敏感命中 `token`、`secret`、`password`、`api_key`、`authorization`、`cookie`、
  `private_key`、`credential` 的映射值；
- 调用方显式提供的敏感环境变量值或其它 secret 值；
- `Authorization: Bearer ...`、JWT、PEM 私钥、常见 `sk-`、GitHub token 和 AWS access key 形态；
- 任务配置声明的额外敏感字段。

脱敏必须在裁剪和哈希之前完成。命中值整体替换为固定 marker，不得保留可复原的前缀/后缀。
短路、深度超限、节点超限和输出字节超限使用固定 marker 表示；循环引用不得导致异常、递归爆炸或
不确定输出。脱敏失败时，状态和审计写入必须失败关闭。

### 8.2 有界性

默认输出上限为 64 KiB、512 行、深度 16 和 10,000 个节点。裁剪后必须带确定性截断 marker 和
`truncated=true`。错误消息、审计摘要和状态摘要分别受其模型长度和 Redactor 上限共同约束，先达到
的限制生效。

### 8.3 失败关闭

- migration、审计、状态、序列化、脱敏或租约不可验证时，不返回成功；
- 审计写失败时，业务状态不得提交；
- 状态写失败时，审计不得单独提交；
- 数据库锁冲突不自动重试 Commit；调用方可从新事务重新评估；
- 不吞掉异常，不使用宽泛 `except Exception: pass`；
- 对外错误只包含稳定错误码、操作类别和有界脱敏说明，不包含 SQL、数据库积压路径、参数原值、
  环境变量值或凭据片段。

### 8.4 文件与权限

数据库文件不得位于 `.git/`、依赖目录、敏感路径或目标工作区。路径合法性由 WP-00 配置校验
提供，WP-01 在打开前重新验证文件类型。POSIX 要求状态目录、数据库、WAL/SHM 由当前有效用户
拥有且类型安全；新建目录使用 `0700`，数据库及 WAL/SHM 使用 `0600`。已有路径权限不安全或
无法收紧时失败关闭，不提供生产旁路。Windows ACL 不作为本包强隔离能力宣称。

状态数据库位于 `workspace_root` 外但同一 `git_root` 内时，WP-03 只能对该精确 harness 状态目录
做外围脏路径排除；该排除不得扩大为任意前缀。

## 9. 数据 schema 与持久化格式

WP-01 migration v1 建立以下表和约束；v2 追加复合外键所需的 Step 唯一索引。后续修正只通过新
migration 追加：

| 表 | 关键内容 | 关键约束 |
| --- | --- | --- |
| `schema_migrations` | version、checksum、applied_at | version 主键；checksum 固定 |
| `runs` | Run 标量、预算上限/usage、状态、验收、关联 ID、时间、revision | run_id 主键；trace_id 唯一；状态与预算 CHECK |
| `run_plans` | run_id、revision、计划项 canonical JSON、更新时间 | 每个 Run 至多一个当前计划；Run 外键 |
| `steps` | Step 字段、revision | step_id 主键；`(run_id, ordinal)` 唯一；Run 外键 |
| `tool_calls` | ToolInvocation 字段、revision | `(run_id, tool_call_id)` 主键；step/Run 外键 |
| `evidence` | Evidence 字段 | evidence_id 主键；Run 与可选 tool_call 外键 |
| `completion_results` | CompletionCheck 字段、revision | run_id 主键；Run 外键 |
| `completion_items` | CompletionItem 字段和有序 evidence 关联 | `(run_id, item_id)` 唯一；Run 外键 |
| `budget_ledger` | source_kind、source_id、category、增量、创建时间 | `(run_id, source_kind, source_id, category)` 唯一；不可公开修改/删除 |
| `run_leases` | owner、token digest、时间 | run_id 主键；Run 外键 |
| `audit_events` | 信封、sequence、summary JSON、哈希 | `(run_id, sequence)` 唯一；event_id 唯一；Run 外键 |

所有 JSON 字段保存 canonical JSON；所有时间保存 D-11 UTC 微秒格式；所有 ID、枚举、路径和摘要
在写入和读取时均重新校验。运行时表只使用参数化 SQL，不拼接模型提供值。

`run_plans` 保存当前计划的 canonical PlanItem 数组，通过 revision 条件更新；历史计划由审计事实
保存，不在 RunPlan 中复制完整版本历史。Run usage 必须等于 `budget_ledger` 汇总，完整性检查和
RunSnapshot 读取都要验证该关系。

## 10. 错误契约

WP-01 计划在 `ErrorCode` 中只追加以下稳定值，并用契约测试锁定：

```text
STATE_DATABASE_UNAVAILABLE
STATE_MIGRATION_FAILED
STATE_SCHEMA_UNSUPPORTED
STATE_TRANSACTION_FAILED
STATE_NOT_FOUND
STATE_CONFLICT
STATE_INVALID_TRANSITION
STATE_INVARIANT_VIOLATION
STATE_LEASE_INVALID
STATE_LEASE_EXPIRED
STATE_LEASE_HELD
STATE_LEASE_HEARTBEAT_FAILED
STATE_BUDGET_EXCEEDED
STATE_INTEGRITY_ERROR

AUDIT_WRITE_FAILED
AUDIT_READ_FAILED
AUDIT_APPEND_ONLY
AUDIT_SEQUENCE_CONFLICT
AUDIT_CHAIN_INVALID
AUDIT_EVENT_INVALID

REDACTION_INVALID_INPUT
REDACTION_FAILED
```

| 错误 | 触发条件 | 重试 |
| --- | --- | --- |
| `STATE_SCHEMA_UNSUPPORTED` | 数据库版本高于代码或 migration checksum 不符 | 否，需人工迁移/修复 |
| `STATE_CONFLICT` | revision、唯一键或预期业务版本不匹配 | 可重新读取后重新决策，不自动重试 |
| `STATE_INVALID_TRANSITION` | 状态转换不在冻结状态机中 | 否 |
| `STATE_LEASE_HELD` | 同一 Run 存在未过期租约 | 可等待或查询持有者，不自动抢锁 |
| `STATE_LEASE_EXPIRED` | 续期/改写时租约已过期 | 需重新获取租约和重新通过 Gate |
| `STATE_LEASE_HEARTBEAT_FAILED` | Harness 连续两次续租失败 | 否，停止 Agent 后续动作并进入恢复判定 |
| `STATE_BUDGET_EXCEEDED` | 增量会使任一 usage 超过上限 | 否，进入停止流程 |
| `AUDIT_APPEND_ONLY` | 直接更新/删除审计事件 | 否 |
| `AUDIT_CHAIN_INVALID` | 序号、前序哈希或事件哈希不匹配 | 否，需人工审计 |
| `REDACTION_FAILED` | 脱敏无法得到可靠、有界结果 | 否，当前写入失败关闭 |

异常对象至少提供 `code`、`operation`、有界 `message` 和 `retryable`。错误不得泄露参数值或底层
SQL 文本。

## 11. 验收场景

以下编号是 WP-01 的稳定验收契约：

| 编号 | 场景 | 通过判据 |
| --- | --- | --- |
| WP01-AC-001 | 初始化新数据库 | 目录/DB 建立，migration 完成，WAL、外键和 busy timeout 生效 |
| WP01-AC-002 | 重复初始化与 checksum 篡改 | 重复初始化不重复迁移；版本不兼容或 checksum 变化失败关闭 |
| WP01-AC-003 | Run/Step/Tool/Evidence/Completion 重开 | 关闭连接、重开数据库后所有冻结字段、状态和 revision 完整一致 |
| WP01-AC-004 | Run 状态机 | 所有合法转换成功；每个非法转换返回 `STATE_INVALID_TRANSITION` 且状态不变 |
| WP01-AC-005 | Step 与 ToolInvocation 状态机 | 合法终态可读；非法回退、终态覆盖和重复 `tool_call_id` 被拒绝 |
| WP01-AC-006 | 乐观并发 | 两个写者用相同旧 revision 更新时最多一个成功，另一个得到 `STATE_CONFLICT` |
| WP01-AC-007 | 预算与 H3 | 按 source/category 幂等累计；同 key 不同 delta 冲突；usage 与 ledger 汇总一致；越界拒绝；预算耗尽三元组重开不变 |
| WP01-AC-008 | Run 租约 | 有效租约阻止第二持有者；过期可接管；旧 token 续期/释放被拒绝 |
| WP01-AC-009 | 审计连续序号与哈希链 | 多次追加得到连续序号和有效 chain；按 order 查询稳定 |
| WP01-AC-010 | 审计 append-only | 直接 SQL UPDATE/DELETE 失败；绕过触发器后由 `verify_chain` 检出 |
| WP01-AC-011 | 状态/审计共同回滚 | 审计失败不留下状态；状态失败不留下审计；成功时两者同时可见 |
| WP01-AC-012 | A1/A2 关联 | 合成拒绝事件含 rule、decision、actor、tool_call_id；关联查询返回完整稳定顺序 |
| WP01-AC-013 | 脱敏 | 敏感键、环境值、Authorization、JWT/PEM/常见 token 形态均不以原值进入 DB 或异常 |
| WP01-AC-014 | 有界与循环 | 深、宽、大、循环输入确定裁剪，不崩溃，不泄露分段 secret |
| WP01-AC-015 | 并发追加 | 多连接并发追加后序号无重复、无丢失，哈希链完整；并发 Run 写不覆盖 |
| WP01-AC-016 | 故障确定性 | 注入点由 Fixture 记录次数；测试不依赖真实网络、模型、凭据或墙钟等待 |
| WP01-AC-017 | 不越权实现 | 不创建审批/Checkpoint 生命周期 API，不宣称 Gate、恢复或最终验收完成 |
| WP01-AC-018 | RunPlan 契约 | null/非空规则、ordinal、大小上限、幂等 no-op、revision 和审计映射正确 |
| WP01-AC-019 | 状态目录隔离与权限 | 工作区内 state_dir 在配置阶段拒绝；WP-01 复验路径；POSIX owner/type/mode 不合规时失败关闭 |
| WP01-AC-020 | Evidence 单一生产者 | 跨 ToolCall 关联被消除；Invocation evidence_ids 为派生投影；Completion-only 证据合法 |

每个场景必须映射到自动化测试；不能以人工检查或“代码存在”替代。

## 12. 兼容与迁移

- WP-00 的领域 JSON `schema_version=1` 不等于 SQLite migration 版本；
- SQLite migration 版本从 `1` 开始，只追加；不可修改已应用 migration 的文本或 checksum；
- 历史行保留自身 `schema_version`，读取时必须显式支持；未知版本失败关闭；
- 新增公共枚举值允许按 D-14 追加并补契约测试；删除、重命名或改变旧语义需要规格版本升级；
- `Approval`、Checkpoint 和 Gate 表由下游工作包追加 migration，不要求修改 WP-01 migration；
- 下游不能复制 `Run`、`ToolInvocation`、`Evidence` 或 `CompletionCheck` 模型形成第二套持久化
  协议；
- 未来 schema 升级必须说明升级窗口、备份/回滚策略、旧 Run 的只读行为和恢复是否允许。

## 13. 已关闭决策

| 原问题 | 最终决策 | 对应决策 |
| --- | --- | --- |
| 状态数据库位置 | `state_dir` 必填且在 `workspace_root` 外；WP-00 配置阶段检查，WP-01 打开时复验 | D-16 |
| 预算幂等 | 使用 source/category 维度 ledger、物化 usage 和汇总一致性检查 | D-17 |
| 计划建模 | 独立 `RunPlan`/`PlanItem` 和单一当前 `run_plans` row，历史由 Audit 保存 | D-18 |
| 租约与心跳 | 创建 Run 与租约原子提交；短 TTL；Harness 独立心跳；token fencing 和 STARTED 防重放 | D-19 |
| 状态变更审计 | 业务状态自动生成固定领域审计，禁止状态-only 成功 | D-20 |
| POSIX 权限 | owner/type/mode 强校验，失败关闭；Windows ACL 记录为限制 | D-21 |

## 14. 验收命令

规格冻结并完成施工后至少运行：

```bash
python -m pip install -e '.[dev]'
python -m pytest tests/test_state_checkpoint.py tests/test_audit.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

验收报告必须记录实际退出码、测试数量、失败/skip 数、数据库 PRAGMA 证据、事务故障证据、
直接 SQL 篡改拒绝证据和代码基线，不得复制本文的预期值。
