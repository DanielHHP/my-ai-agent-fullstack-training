# WP-02 审批生命周期规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-02 审批生命周期 |
| 文档状态 | frozen |
| 规格版本 | 0.2.0 |
| 冻结日期 | 2026-11-04 |
| 依赖 | WP-00 v1.2.0（Approval/config 修订）、WP-01 v0.3.0；WP-01 已验收，WP-00 v1.2 由本包 T01 实现 |
| 下游工作包 | WP-04、WP-06、WP-07、WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-GATE-004`、`REQ-STATE-001`、`REQ-STATE-003`、`REQ-AUDIT-001`、`REQ-AUDIT-002`、`REQ-CHECKPOINT-002`、`REQ-CHECKPOINT-003` |
| 对应设计 | `design.md` 第 5.3、6.3、7.3、9.2、9.3、10、13 节 |
| 已确认决策 | D-01、D-11、D-12、D-14、D-16、D-19、D-20、D-22 至 D-28 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌表 revision 11 |

本文是 WP-02 的已冻结行为与验收契约。第 14 节记录经需求方确认的决策，不表示相关能力已经
实现；实现是否完成仍由自动化测试、故障证据、lint、格式检查和验收报告决定。

文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-02 必须交付一个与工具执行解耦、可跨进程持久化、可由 Gate 安全消费的审批生命周期：

1. 审批请求由程序创建，并绑定 Run、工具调用、任务、工作区、工具名、规范化参数摘要和配置版本；
2. `PENDING`、`APPROVED`、`CLAIMED`、`CONSUMED`、`REJECTED`、`EXPIRED`、`INVALIDATED`
   之间只有明确、单向的状态转换；
3. 批准、拒绝、认领、消费、过期和撤销都通过带 revision 的条件更新完成，并与审计事件同事务提交；
4. 同一审批最多被一个执行意图 claim；第二个并发或后续 claim 必须失败；
5. 参数、工作区、任务、Run、配置或有效期不匹配时，旧审批不得继续产生执行授权；
6. 审批 API 自身不执行 handler、文件写入、进程启动或测试命令；
7. 所有失败默认关闭，并返回稳定、结构化、有界且不含敏感值的错误；
8. 为 WP-04/WP-06 提供窄执行接口，为 WP-07 提供可判断旧审批是否仍有效的持久化事实。

## 3. 范围与非目标

### 3.1 范围内

- `src/guarded_harness/approval.py`；
- 审批相关的公共值对象、命令、稳定错误、参数摘要和绑定摘要；
- SQLite migration v4 中的审批表、约束、索引和只读保护；
- `ApprovalStore` 对创建、查询、批准、拒绝、过期、撤销、claim 和 consume 的事务化操作；
- `RunSnapshot` 的审批投影和 `ToolInvocation.approval_id` 关联；
- 审批审计事件和与 `run_id`、`tool_call_id`、`approval_id` 的关联；
- `tests/test_state_checkpoint.py` 中的审批状态、并发、重开和故障测试；
- `ApprovalChannel` 最小协议和本地 SQLite 实现边界；
- WP-02 文档、索引和最终验收报告。

### 3.2 非目标

- 不实现 Gate 的权限、风险、审批、重复和预算固定检查链，这些属于 WP-04；
- 不实现 CLI `approvals` 命令和等待模式，这些属于 WP-11；
- 不执行 handler、写入仓库文件、启动测试命令或调用模型；
- 不实现 Sandbox 的真实路径和命令边界，这些属于 WP-03/WP-06；
- 不实现 Checkpoint 快照、恢复动作、continuation prompt 或工作区差异判定，这些属于 WP-07；
- 不实现外部审批系统、网络审批、用户认证或真实模型运行；
- 不改变 `smolagents` 核心，也不以审批日志替代 Gate 执行前复核。

## 4. 飞书核心验收映射

下表中的“WP-02 责任”只表示审批生命周期能够提供的事实。整条端到端场景由后续工作包闭合。

| 飞书编号 | 优先级 | 飞书通过标准 | WP-02 责任 | 完成工作包 |
| --- | --- | --- | --- | --- |
| G3 | P0 | 未审批写操作进入 `WAITING_APPROVAL`，目标文件不变 | 创建绑定后的 `PENDING` 审批；审批 API 不触达 handler | WP-04/WP-06 验证 Gate、Run 状态和文件不变 |
| G4 | P0 | 审批后同一调用第一次只执行一次，第二次拒绝 | 原子 claim 只能成功一次；重复 claim/consume 返回稳定错误 | WP-04/WP-06/WP-11 验证真实执行只发生一次 |
| C1 | P0 | 每个安全步骤后保存包含待审批动作的 Checkpoint | 提供完整、可重开读取的审批对象和 Run 快照投影 | WP-07 生成 Checkpoint |
| C4 | P0 | 恢复时工作区变化使旧证据或旧审批失效 | 提供绑定摘要、失效操作和严格复核接口 | WP-07 执行恢复校验与停止 |
| A2 | P1 | 完整任务能关联 Gate、Sandbox、State、Approval 和执行结果 | 每个审批事件带 `run_id`、`tool_call_id`、`approval_id` | WP-12 端到端关联 |
| T1 | P0 | 所有 P0 场景有确定性、可重复测试 | WP-02 的过期、并发、重放和故障测试必须离线、确定性通过 | WP-12 全局验收 |

WP-02 不得用上述映射声称 G3/G4/C4 已端到端完成。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `approval.py` | 审批值对象、绑定摘要、状态机、条件更新和审计编排 | Gate 策略、handler 执行、CLI、恢复动作 |
| `storage.py` | 追加 migration v4；提供共享 SQLite UnitOfWork | 解释审批决定或选择恢复动作 |
| `state_store.py` | 读取 Run/ToolInvocation；在 RunSnapshot 中投影审批 | 创建或消费审批 |
| `audit.py` | 写入审批生命周期事实并参与同事务回滚 | 作为审批业务状态的唯一来源 |
| `domain.py` | 冻结审批公共字段、枚举和稳定错误 | 生成默认 TTL、判断审批角色或执行 handler |

依赖方向：

```text
domain.py
   ^
   |
approval.py ----> storage.py ----> state_store.py
   |                                  |
   +------------> audit.py <----------+
```

`approval.py` 只能通过活动 `StorageUnitOfWork` 访问 State、Audit 和审批表，不得自行打开数据库连接，
不得导入 Gate、Sandbox、Completion 或 `smolagents`。

## 6. 公共接口

以下名称和语义是冻结的 WP-02 契约。

### 6.1 审批请求

`ApprovalRequest` 是 Gate 创建审批时提交的不可变输入：

```text
ApprovalRequest(
    run_id,
    tool_call_id,
    normalized_args,
    requested_by,
    allowed_approver_roles,
    ttl_seconds=900,
)
```

约束如下：

- `normalized_args` 必须是已完成工具 schema、语义和路径规范化的 JSON 兼容对象；
- `requested_by` 是可信调用上下文中的请求 actor，不接受模型任意覆盖；
- `allowed_approver_roles` 必须非空、去重、每项长度受限，并以稳定顺序保存；
- `ttl_seconds` 默认 900，允许 `1..86400`；Gate 从可信任务配置读取，不能采用模型参数或未经
  校验的 CLI 覆盖；
- ApprovalStore 从数据库 Run 读取 `task_id`、`task_kind`、工作区身份和配置指纹，调用方不能传入并
  覆盖这些安全字段；
- 请求中的 `tool_call_id`、工具名和参数摘要必须与已持久化的 `ToolInvocation` 一致。

### 6.2 Approval 与绑定摘要

`Approval` 使用 `schema_version=2`，至少包含：

```text
schema_version, approval_id, run_id, tool_call_id
task_id, task_kind
git_root, workspace_root, workspace_prefix
tool_name, canonical_args_digest, binding_digest, config_fingerprint
requested_by, allowed_approver_roles
status, revision
requested_at, expires_at
decided_at, decided_by
claimed_at, execution_intent_id
consumed_at, consumption_code
```

其中：

- `canonical_args_digest` 只绑定工具名和规范化参数；
- `binding_digest` 绑定第 7 节定义的全部审批上下文；
- `binding_digest` 使用与 `canonical_args_digest` 相同的 domain-separated canonical JSON 规则；
- `Claimed/CONSUMED` 状态必须有 `claimed_at` 和 `execution_intent_id`；
- `APPROVED/REJECTED/CLAIMED/CONSUMED` 状态必须有 `decided_at` 和可信 `decided_by`；
- `CONSUMED` 状态必须有 `consumed_at`；确定性失败可使用结果码写入 `consumption_code`；
- `EXPIRED/INVALIDATED` 不得伪造批准人或批准时间；
- 审批对象是不可变快照，任何更新返回新 revision 的对象。

不采用修改现有 `schema_version=1` 行并静默兼容的做法。升级方式见第 12 节。

### 6.3 审批 Store

`ApprovalStore` 必须绑定一个活动 `StorageUnitOfWork`，公共操作至少包括：

```text
create(request: ApprovalRequest, lease_token) -> Approval
load(approval_id, lease_token) -> Approval
list_for_run(
    run_id,
    statuses=(),
    after_created_at=None,
    limit=100,
    lease_token=...,
) -> tuple[Approval, ...]

approve(approval_id, expected_revision, approver, lease_token) -> Approval
reject(approval_id, expected_revision, approver, reason_code, lease_token) -> Approval
expire_due(run_id, lease_token) -> tuple[Approval, ...]
invalidate(approval_id, expected_revision, reason_code, lease_token) -> Approval

claim(approval_id, expected_revision, execution_intent_id, lease_token) -> Approval
resume_claim(approval_id, expected_revision, execution_intent_id, lease_token) -> Approval
consume(
    approval_id,
    expected_revision,
    execution_intent_id,
    result_status,
    result_code,
    lease_token,
) -> Approval
```

`Approver` 至少包含 `actor` 和 `roles`。`result_status` 只接受
`SUCCEEDED`、`FAILED`、`TIMED_OUT`；`UNKNOWN_RESULT` 不能走 consume。

所有修改操作必须：

- 在 `BEGIN IMMEDIATE` UnitOfWork 中执行；
- 校验 Run 租约、审批 revision 和当前状态；
- 使用条件 UPDATE/INSERT，并断言恰好一行发生变化；
- 在同一事务追加审批审计事件；
- 审计或业务写入任一失败时整体回滚；
- 不自动重试事务或审批操作。

### 6.4 ApprovalChannel

保留一个最小、与持久化无关的通道接口：

```text
ApprovalChannel.name: str
ApprovalChannel.submit(approval: Approval) -> None
ApprovalChannel.poll(approval_id) -> ChannelDecision | None
```

`ChannelDecision` 只表达外部通道观察到的事件，不是授权事实。通道返回的批准/拒绝仍必须通过
`ApprovalStore.approve/reject` 重新校验角色、actor、有效期和 revision。通道不可用时失败关闭。
WP-02 只交付本地 SQLite 语义和 disabled/fake 通道，不实现 HTTP、网络等待或 CLI；外部通道只有
在补充可信身份适配后才能启用。

### 6.5 StateStore 与 RunSnapshot 扩展

`RunSnapshot` 必须增加以下只读投影：

```text
approvals: tuple[Approval, ...]
```

读取顺序固定为 `requested_at, approval_id`。快照必须在当前 UnitOfWork 中读取 Run、工具调用和
审批，不得返回半绑定对象。

创建审批时，若 ToolInvocation 尚未关联审批，必须在同一事务设置其 `approval_id`；同一
`(run_id, tool_call_id)` 只能有一张审批单。该关联必须与审批行和审计事件共同提交或回滚。

## 7. 绑定与规范化

### 7.1 参数摘要

`canonical_args_digest` 的计算输入固定为：

```json
{
  "schema": "guarded-harness/approval-args/v1",
  "tool_name": "<normalized-tool-name>",
  "normalized_args": {}
}
```

规则：

- 只使用 Gate 已校验、已规范化的参数，不使用未校验的模型原始参数；
- JSON 编码为 UTF-8、`ensure_ascii=false`、排序键、固定分隔符、禁止 NaN/Infinity；
- 对象键按 Unicode 码点排序，数组顺序保持不变；
- 布尔、整数、小数、字符串和 `null` 保持 JSON 类型边界；
- 不支持的对象、非 JSON 值、重复规范化键或超限输入直接拒绝；
- 摘要输出格式为 `sha256:<64 lowercase hex>`；
- 输入仅用于计算摘要和有界脱敏审计，不把完整敏感参数写入审批表。

路径参数必须在进入 WP-02 前由 Gate/Sandbox 完成规范化。WP-02 不把 `a/../b` 和 `b` 自行视为
相同参数，除非 Gate 已明确规范化并给出同一规范化对象。

### 7.2 绑定摘要

`binding_digest` 的 canonical payload 至少包含：

```text
run_id, tool_call_id, task_id, task_kind
git_root, workspace_root, workspace_prefix
tool_name, canonical_args_digest, config_fingerprint
requested_by, sorted(allowed_approver_roles)
requested_at, expires_at
```

任何一项变化都产生不同摘要。审批创建后，该摘要和所有绑定字段均为不可变事实；若实际上下文不再
匹配，只能将仍可失效的审批转为 `INVALIDATED`，不得原地改写绑定。

## 8. 行为与状态机

### 8.1 合法状态转换

```text
PENDING -> APPROVED
PENDING -> REJECTED
PENDING -> EXPIRED
PENDING -> INVALIDATED

APPROVED -> CLAIMED
APPROVED -> EXPIRED
APPROVED -> INVALIDATED

CLAIMED -> CONSUMED
CLAIMED -> INVALIDATED    # 仅当关联 ToolInvocation 仍为 PREPARED
```

终态为 `CONSUMED`、`REJECTED`、`EXPIRED`、`INVALIDATED`。终态不得回到任何更早状态。
`CLAIMED` 是占用态，不是执行完成态。

### 8.2 创建

1. 校验活动 UnitOfWork、Run 租约、请求字段和角色列表；
2. 读取 Run 和 ToolInvocation，要求同 Run、同一工具、同一参数摘要且 invocation 为 `PREPARED`；
3. 按 `(run_id, tool_call_id)` 查询已有审批：
   - `requested_by`、角色集合、工具名、参数摘要和有效 TTL 都相同，且状态为
     `PENDING/APPROVED` 时，返回原单，不生成新时间、不重复写审计；
   - 任一会改变绑定的字段不同，返回 `APPROVAL_CONFLICT`；
   - 已进入 `CLAIMED` 或终态时返回 `APPROVAL_REPLAYED`；
4. 不存在同调用审批时，用注入 Clock 生成 `requested_at/expires_at`；
5. 计算参数摘要和绑定摘要；
6. 插入 `PENDING` 审批并设置 ToolInvocation 的 `approval_id`；
7. Gate 在同一 UnitOfWork 将 Run 从 `RUNNING` 转为 `WAITING_APPROVAL`；
8. 追加 `APPROVAL_REQUESTED` 和 Run 等待审计事件；
9. 共同提交后返回完整 Approval。

创建审批不调用 handler，也不改变目标文件。审批、ToolInvocation 关联、Run 等待状态和审计必须
共同提交或回滚，不允许只完成其中一部分。

### 8.3 批准与拒绝

- 只允许 `PENDING`；
- 先检查系统时间，已到期时原子转为 `EXPIRED` 并拒绝本次决定；
- 审批 actor 必须来自可信调用上下文，且交易 actor 必须与审批 actor 一致；
- actor 的至少一个角色必须与 `allowed_approver_roles` 相交；
- `requested_by == decided_by` 一律拒绝；
- 批准写入 `APPROVED/decided_at/decided_by`，拒绝写入
  `REJECTED/decided_at/decided_by`；
- 两者都追加 `APPROVAL_DECIDED`，拒绝使用 `DENY` decision 和稳定错误码；
- `APPROVED` 只是可 claim 的前提，不能替代 Gate 重新校验，也不执行 handler。

批准时 Run 保持 `WAITING_APPROVAL`。拒绝时 Gate/Harness 必须在同一 UnitOfWork 将 Run 转为
`STOPPED/APPROVAL_REJECTED/NOT_EVALUATED`；拒绝后模型不能继续当前 Run 的工具调用。

### 8.4 Claim

1. 校验活动租约、expected revision 和审批仍为 `APPROVED`；
2. 校验当前时间早于 `expires_at`；
3. 重新读取 Run 和 ToolInvocation，重新计算并比较绑定摘要、工具名和参数摘要；
4. 要求 ToolInvocation 仍为 `PREPARED`，并使用 `execution_intent_id=tool_call_id`；
5. 条件更新 `APPROVED -> CLAIMED`，写入 `claimed_at`、`execution_intent_id` 和 revision；
6. Gate 在同一 UnitOfWork 将 Run 从 `WAITING_APPROVAL` 转为 `RUNNING`；
7. 在同一事务提交预执行意图和 `APPROVAL_CLAIMED` 审计；
8. 只向获胜调用返回 `CLAIMED`。

第二个 claim、错误 revision、过期、绑定变化或非 `PREPARED` invocation 均失败。失败不得修改审批、
invocation、Run 或审计中的既有事实。

claim 成功后，Gate 在另一个事务将 Invocation 从 `PREPARED` 转为 `STARTED`，提交后才调用
handler。claim 与 STARTED 之间发生崩溃时，新进程可执行显式 `resume_claim`：

- 必须重新取得有效 Run 租约并全量校验绑定、工作区和配置；
- 只允许原 `execution_intent_id=tool_call_id` 且 Invocation 仍为 `PREPARED`；
- 不重置审批状态或 claim 时间，只追加 `APPROVAL_CLAIM_RECOVERED` 审计；
- Invocation 已 `STARTED`、`UNKNOWN_RESULT` 或终态时禁止 resume；
- resume 后仍须由 Gate 写 `STARTED`，之后才允许触达 handler。

### 8.5 Consume

- 只允许 `CLAIMED`；
- `execution_intent_id` 必须与 claim 时完全一致；
- 关联 ToolInvocation 必须已有确定终态 `SUCCEEDED`、`FAILED` 或 `TIMED_OUT`；
- 条件更新为 `CONSUMED` 并写入 `consumed_at`、结果码和 revision；
- 追加 `APPROVAL_CONSUMED` 审计；
- `STARTED` 或无确定结果的 invocation 不得 consume；
- `UNKNOWN_RESULT` 保持审批为 `CLAIMED`，不自动过期、不重新开放、不允许第二次 claim；
- consume API 不执行 handler。

### 8.6 过期、撤销与陈旧绑定

- `PENDING` 和 `APPROVED` 在 `Clock.now() >= expires_at` 时视为过期；
- 授权相关读取和所有变更操作必须先使到期审批持久化为 `EXPIRED` 并写审计；
- 审批过期的同一 UnitOfWork 必须由 Gate/Harness 将 Run 转为
  `STOPPED/APPROVAL_EXPIRED/NOT_EVALUATED`；
- `CLAIMED` 不因墙钟过期自动释放，避免执行中授权在副作用附近突然消失；
- 显式撤销只允许 `PENDING`、`APPROVED`，以及关联 invocation 仍为 `PREPARED` 的 `CLAIMED`；
- 已有 `STARTED`、`UNKNOWN_RESULT` 或确定终态的 invocation 不得撤销审批；
- 工作区、任务、配置指纹、Run、工具名或参数摘要变化时，claim 返回
  `APPROVAL_BINDING_MISMATCH`；恢复阶段必须将仍可失效的审批标记为 `INVALIDATED`，由
  Gate/Harness 将 Run 转为 `BLOCKED/WORKSPACE_CHANGED`，人工处理完成后启动新 Run；
- 任何过期或撤销都不执行 handler，也不把 Run 伪装为成功。

## 9. 不变量

1. 一个 `(run_id, tool_call_id)` 最多对应一张审批单；
2. 一张审批单只属于一个 Run 和一个 ToolInvocation；
3. 同一审批最多一次从 `APPROVED -> CLAIMED`；
4. 只有同一 `execution_intent_id` 能在 claim 后 consume；
5. `CONSUMED`、`REJECTED`、`EXPIRED`、`INVALIDATED` 不可逆；
6. `expires_at` 严格晚于 `requested_at`；
7. 审批绑定字段在创建后不可修改；
8. 每次状态变化 revision 恰好增加 1；
9. 审批表、ToolInvocation 关联和审计事件共同提交或共同回滚；
10. 审批 API 不持有、不调用 handler；
11. 模型不能设置 `requested_by`、approver roles、决定、status、revision 或 execution intent；
12. 任何无法验证的审批状态失败关闭，不把损坏行降级成 `PENDING`。

## 10. 审计契约

WP-02 至少产生以下审计事件：

| 事件 | status | decision | code |
| --- | --- | --- | --- |
| `APPROVAL_REQUESTED` | `PENDING` | `NOT_APPLICABLE` | `APPROVAL_REQUIRED` |
| `APPROVAL_DECIDED`（批准） | `SUCCEEDED` | `ALLOW` | `null` |
| `APPROVAL_DECIDED`（拒绝） | `DENIED` | `DENY` | `APPROVAL_REJECTED` |
| `APPROVAL_EXPIRED` | `DENIED` | `DENY` | `APPROVAL_EXPIRED` |
| `APPROVAL_INVALIDATED` | `DENIED` | `DENY` | 调用方提供的稳定原因 |
| `APPROVAL_CLAIMED` | `STARTED` | `NOT_APPLICABLE` | `null` |
| `APPROVAL_CLAIM_RECOVERED` | `STARTED` | `NOT_APPLICABLE` | `null` |
| `APPROVAL_CONSUMED` | `SUCCEEDED` | `NOT_APPLICABLE` | 可空结果码 |

每个事件至少关联 `run_id`、`tool_call_id`、`approval_id`、`actor`、`tool_name`、审批绑定摘要和
工作区指纹。`input_summary/output_summary` 只能保存脱敏、有界摘要。审计事件必须能按
`approval_id` 稳定按 sequence 重放。

## 11. 错误契约

WP-02 计划追加以下稳定 `ErrorCode`：

```text
APPROVAL_NOT_FOUND
APPROVAL_REQUIRED
APPROVAL_BINDING_MISMATCH
APPROVAL_INVALID
APPROVAL_EXPIRED
APPROVAL_REJECTED
APPROVAL_NOT_EXECUTABLE
APPROVAL_ALREADY_CLAIMED
APPROVAL_REPLAYED
APPROVAL_CONFLICT
APPROVAL_APPROVER_DENIED
APPROVAL_SELF_APPROVAL_DENIED
APPROVAL_CHANNEL_UNAVAILABLE
APPROVAL_STATE_INVALID
```

| 错误 | 触发条件 | 重试 |
| --- | --- | --- |
| `APPROVAL_NOT_FOUND` | 指定 approval 不存在 | 否，需重新提案/审批 |
| `APPROVAL_REQUIRED` | 当前工具调用没有有效审批请求 | 否，由 Gate 返回等待/拒绝 |
| `APPROVAL_BINDING_MISMATCH` | Run、任务、工作区、配置、工具或参数摘要不匹配 | 否，必须重新审批 |
| `APPROVAL_INVALID` | 字段、角色、哈希或持久化组合非法 | 否 |
| `APPROVAL_EXPIRED` | 有效期已过 | 否，必须重新审批 |
| `APPROVAL_REJECTED` | 审批已明确拒绝 | 否 |
| `APPROVAL_NOT_EXECUTABLE` | 当前状态不允许该操作 | 否 |
| `APPROVAL_ALREADY_CLAIMED` | 另一执行意图已 claim | 否 |
| `APPROVAL_REPLAYED` | claim/consume 或创建重放已终态审批 | 否 |
| `APPROVAL_CONFLICT` | 同一工具调用出现不同审批绑定或 revision 冲突 | 否，需重新读取状态 |
| `APPROVAL_APPROVER_DENIED` | actor 不具备允许角色 | 否 |
| `APPROVAL_SELF_APPROVAL_DENIED` | 请求者试图自批，且策略不允许 | 否 |
| `APPROVAL_CHANNEL_UNAVAILABLE` | 审批通道不可用或返回损坏结果 | 否，当前操作失败关闭 |
| `APPROVAL_STATE_INVALID` | 非法状态转换或字段组合 | 否 |

错误对象至少包含 `code`、`operation`、有界 `message` 和 `retryable`。错误文本不得包含参数
原值、敏感摘要内容或底层 SQL。

## 12. 兼容与迁移

- WP-01 migration v1-v3 已冻结，WP-02 只能追加 migration v4；
- migration v4 创建审批表和索引，并更新 schema version 与完整性检查中的必需表集合；
- 现有 WP-01 数据库没有审批行时可直接升级；升级失败必须整体回滚，不留下半张表；
- 审批表不得删除历史行；终态通过状态和审计保留；
- `Approval` 已按 D-23 升级为 schema v2；旧 schema v1 行只允许由 migration 或兼容读取器显式
  升级，不允许静默缺省字段；
- WP-00 v1.2.0 的 Approval v2、`ApprovalConfig.approver_roles/ttl_seconds` 和新增 StopReason
  由 WP-02 T01 实现；实现前现有 v1.1.0 代码不视为满足该修订；
- 未知审批 schema version、非法枚举、非法时间组合或缺失 ToolInvocation 外键时失败关闭；
- WP-02 代码可回滚时，数据库 v4 表保持只读；若已存在审批事实，不执行破坏性降级，必须备份后
  使用显式人工迁移或前向修复；
- 下游只能通过 `ApprovalStore` 和冻结的 Approval 模型消费审批，不得直接更新 `approvals` 表。

## 13. 验收场景

以下编号是 WP-02 的稳定验收契约：

| 编号 | 场景 | 通过判据 |
| --- | --- | --- |
| WP02-AC-001 | 创建并重开审批 | PENDING 审批包含 Run、工具调用、任务、工作区、配置、参数摘要和有效期；重开后完全一致 |
| WP02-AC-002 | 绑定篡改 | 工具名、任一路径、task/config、工具调用或参数摘要变化后 claim 返回 `APPROVAL_BINDING_MISMATCH` |
| WP02-AC-003 | 规范化确定性与边界 | 等价键顺序生成相同摘要；数组顺序、值变化、非 JSON 值、超大或非法输入得到稳定结果 |
| WP02-AC-004 | 批准/拒绝状态机 | 只有 PENDING 可决定；拒绝不可逆；错误 revision 和终态重放失败且状态不变 |
| WP02-AC-005 | 审批角色 | 允许角色内的 actor 可批准；无交集角色返回 `APPROVAL_APPROVER_DENIED`；自批返回 `APPROVAL_SELF_APPROVAL_DENIED` |
| WP02-AC-006 | 过期 | 固定 Clock 到达 expires_at 后，load/decide/claim 均可观察并持久化 EXPIRED，handler 计数为 0 |
| WP02-AC-007 | 一次性 claim | 两个连接并发 claim 最多一个成功；第二方得到 `APPROVAL_ALREADY_CLAIMED`，无重复审计或状态覆盖 |
| WP02-AC-008 | 跨运行/跨工作区/跨任务 | approval 不能在另一 Run、工作区或 task/config 下被 claim |
| WP02-AC-009 | 确定性 consume | SUCCEEDED/FAILED/TIMED_OUT 可消费；UNKNOWN_RESULT 或 STARTED 不可 consume，审批保持 CLAIMED |
| WP02-AC-010 | 撤销 | PENDING/APPROVED/PREPARED-CLAIMED 可失效；已 STARTED/UNKNOWN/终态不可撤销 |
| WP02-AC-011 | 审批审计链 | REQUESTED、DECIDED、EXPIRED、INVALIDATED、CLAIMED、CLAIM_RECOVERED、CONSUMED 均含稳定关联字段且可按 ID 重放 |
| WP02-AC-012 | 状态与审计原子性 | 注入审批写后/审计写后故障，审批、invocation 关联和审计共同回滚 |
| WP02-AC-013 | 幂等与冲突 | 相同绑定重复创建不产生第二张单；不同绑定复用同一 tool_call 返回 `APPROVAL_CONFLICT` |
| WP02-AC-014 | 无执行副作用 | 审批创建、批准、拒绝、过期、claim、consume API 均不调用测试 handler，也不改变目标文件 |
| WP02-AC-015 | 损坏数据 | 非法状态组合、未知 schema、缺失 Run/Invocation、角色 JSON 损坏和目标文件不变均失败关闭 |
| WP02-AC-016 | G3/G4 部分证据 | 未批准时目标文件不变；批准后 claim/consume 最多形成一次执行意图，完整 handler 只执行一次由 WP-04/WP-06 验收 |
| WP02-AC-017 | 创建与等待原子性 | 审批、Invocation 关联、Run `RUNNING -> WAITING_APPROVAL` 和审计共同提交或回滚 |
| WP02-AC-018 | claim 与执行边界 | claim、Run `WAITING_APPROVAL -> RUNNING` 和预执行意图共同提交；`STARTED` 在后续事务写入，审批 API 不调用 handler |
| WP02-AC-019 | claim 崩溃恢复 | 新租约下相同 intent 可在 PREPARED 状态 `resume_claim`；STARTED/UNKNOWN/终态拒绝，恢复审计存在 |
| WP02-AC-020 | 拒绝/过期收口 | 人工拒绝或过期与 Run `STOPPED`、对应 StopReason、`NOT_EVALUATED` 和审计原子提交 |
| WP02-AC-021 | 陈旧绑定收口 | 绑定变化时旧审批 `INVALIDATED`、Run `BLOCKED/WORKSPACE_CHANGED`，不允许同 Run 重绑定 |

所有场景必须映射到自动化测试；不能用“模块存在”或人工声明替代。

## 14. 已确认决策

以下决策已由需求方逐轮确认，并登记为 D-23 至 D-28；施工必须遵循，不得在任务卡中重新解释。

| 结论 | 内容 |
| --- | --- |
| Approval 模型 | `Approval` schema v2；完整绑定 ToolCall、任务、工作区、配置、参数和生命周期，带 revision |
| 配置 | `ApprovalConfig.approver_roles` 与 `ttl_seconds` 进入配置指纹；TTL 默认 900、范围 `1..86400` |
| 身份与自批 | actor 本地可信但未认证；必须命中允许角色且禁止自批；`requested_by` 来自 Gate 上下文 |
| Channel | 外部决定不是授权事实；Store 必须重校验；P0 只提供本地 SQLite 和 disabled/fake 通道 |
| 创建边界 | 审批、Invocation 关联、Run 转 `WAITING_APPROVAL` 和审计同事务 |
| claim 边界 | claim、Run 转 `RUNNING` 和预执行意图同事务；`STARTED` 在后续事务写入 |
| 执行身份 | `execution_intent_id` 固定等于 `tool_call_id` |
| 崩溃恢复 | PREPARED 下新租约可显式 `resume_claim`；STARTED 后只能 UNKNOWN，不回退授权 |
| 结果语义 | SUCCEEDED/FAILED/TIMED_OUT 消费；UNKNOWN_RESULT 保持 CLAIMED 且不可重放 |
| 拒绝/过期 | 与 Run STOPPED、`APPROVAL_REJECTED/APPROVAL_EXPIRED`、NOT_EVALUATED 和审计同事务 |
| 陈旧绑定 | 审批 INVALIDATED，Run BLOCKED/WORKSPACE_CHANGED；人工处理完成后启动新 Run |
| 幂等 | 每 `(run_id, tool_call_id)` 一张审批；活动态相同请求返回原单，不同绑定冲突，终态重放拒绝 |
| 摘要 | 脱敏前规范化参数参与 domain-separated SHA-256；只持久化摘要和脱敏审计 |

## 15. 验收命令

规格冻结并完成施工后至少运行：

```bash
python -m pip install -e '.[dev]'
python -m pytest tests/test_state_checkpoint.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

验收报告必须记录实际退出码、测试数量、失败/skip 数、并发 claim 证据、故障回滚证据、迁移重开
证据和代码基线，不得复制本文预期值。
