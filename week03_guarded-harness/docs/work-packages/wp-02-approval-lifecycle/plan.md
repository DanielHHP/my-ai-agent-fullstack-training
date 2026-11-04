# WP-02 审批生命周期实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-02 审批生命周期 |
| 文档状态 | done |
| 计划版本 | 0.2.0 |
| 批准日期 | 2026-11-04 |
| 对应规格 | `spec.md` v0.2.0 / frozen |
| 前置依赖 | WP-00 v1.2.0、WP-01 v0.3.0；WP-00 v1.2 由本包 T01 实现 |
| 施工前置 | 已满足；D-23 至 D-28 已确认 |
| 当前结论 | T01 至 T08 已完成，验收报告 accepted |

本计划只安排 WP-02 施工，不重新定义规格。若实现中发现规格缺口，应停止施工、更新并评审
`spec.md`，再同步本计划。跨工作包公共模型变化还必须先修订 WP-00 契约。

## 2. 计划目标与完成定义

WP-02 完成后必须能够只依赖公共接口和临时 SQLite/Git Fixture 独立证明：

1. 审批完整绑定 Run、ToolInvocation、任务、工作区、配置、工具和规范化参数摘要；
2. 相同输入产生稳定摘要，任意绑定字段变化都会被拒绝；
3. 批准者角色、有效期和 revision 在每次决定/claim 时重新校验；
4. 并发 claim 只有一个赢家，后续 claim 和 consume 不能重放；
5. 只有 ToolInvocation 的确定终态能 consume，`UNKNOWN_RESULT` 保持不可执行；
6. 审批、ToolInvocation 关联和审计事件共同提交或回滚；
7. 关闭并重开数据库后状态、revision、时间和关联完整；
8. 审批 API 不存在调用 handler、修改目标文件或启动进程的路径；
9. 所有测试离线、确定、可重复，无 skip 或真实网络/模型依赖；
10. 生成 `acceptance-report.md`，以实际测试和故障注入证据决定是否 accepted。

工作包完成不覆盖 Gate、真实写入、CLI 等待和 Checkpoint。G3/G4/C4 只能在后续工作包中端到端
关闭。

## 3. 文件范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── approval.py          # 新增：审批值对象、摘要、状态机、Store、Channel
├── domain.py            # 扩展：Approval v2、请求/命令、错误码
├── config.py             # 扩展：approver_roles、ttl_seconds 与语义校验
├── audit.py             # 扩展：APPROVAL_INVALIDATED 事件
├── storage.py           # 扩展：migration v4、完整性必需表
├── state_store.py       # 扩展：RunSnapshot.approvals、审批关联读取
└── __init__.py          # 扩展：公共导出

tests/
├── test_config.py            # 扩展：角色、TTL、跨字段和指纹契约
└── test_state_checkpoint.py  # 扩展：WP-02 审批生命周期、并发和故障测试

docs/work-packages/wp-02-approval-lifecycle/
├── spec.md
├── plan.md
└── acceptance-report.md      # 施工完成并实测后创建
```

不在 WP-02 创建 `gate.py`、`cli.py`、`checkpoint.py`、真实工具 handler 或外部审批服务。

### 3.2 依赖方向

```text
domain.py
   ^
   |
config.py
   |
approval.py ─────> storage.py
   │                  │
   ├────> audit.py <──┤
   │                  │
   └────> state_store.py

__init__.py ─────> 公共导出
```

- `approval.py` 不导入 Gate、Sandbox、Completion 或 `smolagents`；
- 审批写操作必须通过活动 `StorageUnitOfWork`；
- `approval.py` 可以读取 Run 和 ToolInvocation，但不得复制 `state_store.py` 的状态机；
- `state_store.py` 不写审批表，只读取审批投影和读取 `tool_calls.approval_id`；
- `audit.py` 不知道审批业务规则，只接受经过校验的事件 draft。

## 4. 详细设计

### 4.1 公共类型

在 `domain.py` 中新增或扩展：

```text
ApprovalRequest
ApprovalDecision
Approver
ClaimApprovalCommand
ResumeClaimCommand
ConsumeApprovalCommand
InvalidateApprovalCommand
ChannelDecision
ApprovalChannel
```

`Approval` 按 `spec.md` 第 6.2 节升级为 schema v2。可变请求命令与持久化快照分离，Store 只返回
重新校验后的不可变 `Approval`。`ApprovalConfig` 同步增加 `approver_roles` 与 `ttl_seconds`；
`StopReason` 追加 `APPROVAL_REJECTED`、`APPROVAL_EXPIRED`。

新增错误码：

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

新增 `AuditEventType.APPROVAL_INVALIDATED` 和 `APPROVAL_CLAIM_RECOVERED`。拒绝复用现有的
`APPROVAL_DECIDED`，通过 `status=DENIED`、`decision=DENY` 表达。

### 4.2 参数摘要

建议在 `approval.py` 提供内部但契约测试可见的 helper：

```text
canonical_approval_args(tool_name, normalized_args) -> bytes
approval_args_digest(tool_name, normalized_args) -> Sha256Digest
approval_binding_digest(binding_fields) -> Sha256Digest
```

实现规则：

1. 输入先验证为严格 JSON 兼容值；
2. 构造 domain-separated payload；
3. 使用 `json.dumps(allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`；
4. UTF-8 编码后 SHA-256，加 `sha256:` 前缀；
5. 输出固定 64 位小写十六进制摘要；
6. 异常只返回稳定错误码和字段路径，不回显输入值。

摘要必须在 WP-01 状态持久化脱敏之前对 Gate 提供的规范化参数计算；审批表只保存摘要和脱敏审计
摘要，不保存未脱敏参数。恢复时由 Gate 提交当前规范化参数并重新计算摘要。

### 4.3 Migration v4

`storage.py` 当前 schema version 为 3，WP-02 追加 v4，不修改 v1-v3。建议表结构：

```text
approvals (
    approval_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    git_root TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    workspace_prefix TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    canonical_args_digest TEXT NOT NULL,
    binding_digest TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    allowed_approver_roles_json TEXT NOT NULL,
    status TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    claimed_at TEXT,
    execution_intent_id TEXT,
    consumed_at TEXT,
    consumption_code TEXT,
    UNIQUE (run_id, tool_call_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (run_id, tool_call_id)
        REFERENCES tool_calls(run_id, tool_call_id)
)
```

数据库 CHECK 至少覆盖：

- status 为冻结枚举值；
- `expires_at > requested_at`；
- `revision >= 0`；
- APPROVED/CLAIMED/CONSUMED 有 decided 信息；
- CLAIMED/CONSUMED 有 claim 信息；
- CONSUMED 有 `consumed_at`；
- PENDING 不得提前带决定/claim/consume 字段；
- REJECTED/EXPIRED/INVALIDATED 不得带非法 consume 组合。

索引至少包括：

```text
(run_id, status, requested_at)
(status, expires_at)
(execution_intent_id)
```

增加 `approvals_binding_immutable` trigger，阻止 UPDATE 绑定字段；增加 no-delete trigger，阻止删除
审批历史。状态字段由 Store 条件更新，不通过通用 UPDATE 暴露给下游。

`SCHEMA_VERSION` 更新为 4，`verify_integrity()` 的必需表集合和 migration count 同步更新。旧数据库
启动时只增加 approvals 表，不回填 WP-01 数据。

### 4.4 创建与关联

建议调用链：

```text
with storage.transaction(actor=request.requested_by) as uow:
    approval = uow.approvals.create(request, lease_token=lease.token)
    uow.state.transition_run(... target=WAITING_APPROVAL ...)
```

实现步骤：

1. 验证 UoW active、Run 租约、请求字段和 roles；
2. 读取 Run；
3. 读取 `(run_id, tool_call_id)` ToolInvocation；
4. 检查 invocation 为 PREPARED、tool_name 和 args_digest 匹配；
5. 查询同一 `(run_id, tool_call_id)`：
   - 请求 actor、角色、工具、参数摘要和有效 TTL 相同且 PENDING/APPROVED：返回原单，不追加重复审计；
   - 绑定不同：`APPROVAL_CONFLICT`；
   - 已进入 CLAIMED/终态：`APPROVAL_REPLAYED`；
6. 对不存在的调用审批读取/计算 TTL，生成 UTC 时间；
7. 计算 `canonical_args_digest` 和 `binding_digest`；
8. INSERT approval；
9. 条件更新 ToolInvocation 的 `approval_id`；
10. Gate 在同一 UnitOfWork 将 Run 从 RUNNING 转为 WAITING_APPROVAL；
11. 追加 `APPROVAL_REQUESTED` 和 Run 等待审计；
12. UnitOfWork 共同提交。

ToolInvocation 关联更新必须采用 expected revision，不得覆盖 Gate 已推进的状态。若关联失败，整个
事务回滚，不留下孤立审批单。

### 4.5 批准、拒绝与过期

批准/拒绝调用链：

```text
begin immediate
-> 验证租约和 expected revision
-> 读取审批
-> 若到期：PENDING/APPROVED -> EXPIRED + 审计
-> 验证 actor == UoW.actor
-> 验证角色交集且 requested_by != decided_by
-> 条件更新 PENDING -> APPROVED/REJECTED
-> 追加 APPROVAL_DECIDED
-> 若拒绝：Run -> STOPPED/APPROVAL_REJECTED/NOT_EVALUATED
-> commit
```

拒绝必须捕获稳定 `reason_code`，只写入审计的有界摘要和审批结果码，不执行 handler。

`expire_due(run_id)` 扫描该 Run 的 PENDING/APPROVED 到期项，逐项条件更新、追加
`APPROVAL_EXPIRED`，并由 Gate/Harness 在同一 UnitOfWork 将 Run 转为
`STOPPED/APPROVAL_EXPIRED/NOT_EVALUATED`。`load`、`list_for_run`、decide、claim 也必须先处理
目标审批的到期状态，避免调用方只读列表后把已经过期事实用于授权。CLAIMED 不扫描为过期。

### 4.6 Claim 与 consume

Claim 调用链：

```text
begin immediate
-> 验证 Run 租约和审批 revision
-> 读取 Current Run + Approval + ToolInvocation
-> 校验 APPROVED、有效期内、绑定摘要完全一致
-> 校验 invocation PREPARED
-> 条件更新 APPROVED -> CLAIMED
-> 写 claimed_at, execution_intent_id, revision+1
-> Run WAITING_APPROVAL -> RUNNING
-> 写预执行意图并追加 APPROVAL_CLAIMED
-> commit
```

条件更新至少包含：

```sql
UPDATE approvals
SET status = 'CLAIMED', claimed_at = ?, execution_intent_id = ?,
    revision = revision + 1
WHERE approval_id = ? AND run_id = ? AND status = 'APPROVED'
  AND revision = ? AND expires_at > ?
```

`rowcount == 1` 才视为获胜。否则重新读取并映射为 EXPIRED、ALREADY_CLAIMED 或
NOT_EXECUTABLE，不自动重试。

claim 提交后，Gate 在另一个事务执行 Invocation `PREPARED -> STARTED`，提交后才调用 handler。
claim 与 STARTED 之间崩溃时，`resume_claim` 只允许原 `execution_intent_id=tool_call_id`、
Invocation PREPARED 和重新取得租约的新进程继续，追加 `APPROVAL_CLAIM_RECOVERED`，不重置审批
状态，也不调用 handler。

Consume 调用链：

```text
begin immediate
-> 验证 Run 租约和审批 revision
-> 要求 approval CLAIMED 且 execution_intent_id 匹配
-> 读取 ToolInvocation
-> 只接受 SUCCEEDED/FAILED/TIMED_OUT
-> 条件更新 CLAIMED -> CONSUMED
-> 追加 APPROVAL_CONSUMED
-> commit
```

`STARTED`、`PREPARED` 或 `UNKNOWN_RESULT` 均返回 `APPROVAL_NOT_EXECUTABLE`。审批保持 CLAIMED，
防止恢复后再次 claim。

### 4.7 撤销与恢复交接

`invalidate` 可处理：

- PENDING/APPROVED；
- CLAIMED 且关联 invocation 仍为 PREPARED。

不能处理：

- 已 STARTED、TIMED_OUT、UNKNOWN_RESULT 或成功/失败终态；
- 已 REJECTED/EXPIRED/CONSUMED。

撤销与审计由 ApprovalStore 完成；Gate/Harness 在同一 UnitOfWork 将受影响 Run 置为
`BLOCKED/WORKSPACE_CHANGED`。WP-07 在恢复前重新检查配置、工作区、审批绑定和 invocation 状态；
不允许在同一 Run 中自动重绑定，人工处理完成后启动新 Run。

### 4.8 ApprovalChannel

`ApprovalChannel` 只负责提交展示和读取外部决定，不写数据库、不授予权限。建议提供：

- `DisabledApprovalChannel`：始终返回通道不可用；
- 测试 Fake Channel：固定返回批准/拒绝/无决定；
- 本地 SQLite 语义直接由 `ApprovalStore` 提供，未被外部通道包装。

任何 Channel 返回值都进入 `ApprovalStore.approve/reject` 的完整校验。WP-02 不轮询网络，不实现
CLI 等待。

## 5. 施工任务卡

所有任务卡在 `spec.md` 冻结后才可执行。每张卡先写测试，再实现，再运行目标测试。

### WP02-T01 公共契约与摘要

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 Approval v2、审批配置、StopReason、请求/命令、错误码和绑定摘要 |
| 前置 | D-23 至 D-28 已确认 |
| 涉及文件 | `domain.py`、`config.py`、`approval.py`、`__init__.py`、`tests/test_domain.py`、`tests/test_config.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | 合法请求可构造；字段排序稳定；摘要固定 |
| 拒绝路径 | 未声明字段、非法状态组合、非 JSON/NaN/深度或大小超限、非法角色 |
| 对应规格 | WP02-AC-002、WP02-AC-003、WP02-AC-005 |
| 完成条件 | 摘要向量固定；round-trip 稳定；角色/TTL 进入指纹；新增枚举和包根导出契约通过 |

### WP02-T02 Approval migration v4

| 项目 | 内容 |
| --- | --- |
| 目标 | 追加 approvals schema、约束、索引、完整性检查和只读触发器 |
| 前置 | T01 |
| 涉及文件 | `storage.py`、`domain.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | WP-01 v3 数据库升级到 v4；新库一次迁移；重开稳定 |
| 拒绝路径 | 半迁移、checksum 错、非法约束、修改/删除审批行、缺失 ToolInvocation |
| 对应规格 | WP02-AC-001、WP02-AC-015 |
| 完成条件 | migration 只追加；v1-v3 checksum 不变；完整性检查包含 approvals |

### WP02-T03 创建、查询与 Snapshot 投影

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 create/load/list、ToolInvocation 关联和 RunSnapshot.approvals |
| 前置 | T01、T02 |
| 涉及文件 | `approval.py`、`state_store.py`、`audit.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | PENDING 创建、重开、按 ID/Run 查询、快照稳定顺序 |
| 拒绝路径 | invocation 非 PREPARED、工具/摘要不匹配、重复不同绑定、孤立审批 |
| 对应规格 | WP02-AC-001、WP02-AC-008、WP02-AC-013、WP02-AC-017 |
| 完成条件 | 审批、invocation 关联、Run 等待状态和审计同事务；相同绑定幂等，无重复单 |

### WP02-T04 决定、角色与过期

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 approve/reject/expire_due，校验 actor/角色/自批/TTL 并原子联动 Run |
| 前置 | T03；D-23 至 D-28 已确认 |
| 涉及文件 | `approval.py`、`state_store.py`、`audit.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | PENDING->APPROVED/REJECTED；到期项转变为 EXPIRED |
| 拒绝路径 | 角色不足、自批、过期、非 PENDING、错误 actor/revision |
| 对应规格 | WP02-AC-004、WP02-AC-005、WP02-AC-006、WP02-AC-020 |
| 完成条件 | Clock 驱动；无 handler/文件副作用；每次状态变化恰有一条审计 |

### WP02-T05 Claim、consume 与失效

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 claim/resume_claim、确定结果 consume 和受限 invalidate |
| 前置 | T04；D-23 至 D-28 已确认 |
| 涉及文件 | `approval.py`、`state_store.py`、`audit.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | APPROVED->CLAIMED->CONSUMED；PREPARED-CLAIMED 可失效 |
| 拒绝路径 | 二次 claim、跨 intent、STARTED、UNKNOWN_RESULT、终态重放、STARTED 后撤销 |
| 对应规格 | WP02-AC-007、WP02-AC-009、WP02-AC-010、WP02-AC-018、WP02-AC-019、WP02-AC-021 |
| 完成条件 | claim 与 Run/意图同事务；STARTED 后续提交；第二执行者不产生写入或审计 |

### WP02-T06 并发、事务与故障注入

| 项目 | 内容 |
| --- | --- |
| 目标 | 证明多连接并发和审批/审计/关联写入的全有或全无 |
| 前置 | T03 至 T05 |
| 涉及文件 | `approval.py`、`storage.py`、`audit.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | 两连接 claim 只有一胜；不同 Run 独立 |
| 拒绝/故障 | before-insert、before-update、before-audit、audit insert 故障全部回滚 |
| 对应规格 | WP02-AC-007、WP02-AC-011、WP02-AC-012、WP02-AC-015、WP02-AC-017、WP02-AC-020 |
| 完成条件 | 固定 barrier、无 sleep；故障点调用次数可断言；无自动重试 |

### WP02-T07 Channel 与无副作用证明

| 项目 | 内容 |
| --- | --- |
| 目标 | 建立最小 ApprovalChannel 契约、disabled/fake 通道和无执行副作用测试 |
| 前置 | T01、T04 |
| 涉及文件 | `approval.py`、`__init__.py`、`tests/test_state_checkpoint.py` |
| 正常路径 | fake 决定仍经过 Store 校验；disabled 返回稳定不可用 |
| 拒绝路径 | channel 篡改 actor/roles/revision、损坏返回、不可用 |
| 对应规格 | WP02-AC-005、WP02-AC-014、WP02-AC-016 |
| 完成条件 | 测试 handler/file 计数保持 0；外部结果不能直接改状态 |

### WP02-T08 文档、索引与验收准备

| 项目 | 内容 |
| --- | --- |
| 目标 | 同步公共契约、migration、错误码、下游交接和包内索引 |
| 前置 | T01 至 T07 |
| 涉及文件 | `docs/README.md`、`docs/work-packages/index.md`、`docs/task-breakdown.md`、WP-02 文档 |
| 正常路径 | 文档与 schema/API/测试一致，生成下游影响清单 |
| 拒绝路径 | 测试失败、skip、证据不足、待决问题未关闭时不生成 accepted |
| 对应规格 | 第 11 至 15 节 |
| 完成条件 | 目标/全量 pytest、Ruff lint、format、pip check 通过；创建并评审验收报告 |

## 6. 测试计划与场景映射

WP-02 的主要测试放在 `tests/test_state_checkpoint.py`，复用 WP-01 的临时 SQLite、固定 Clock、
ID 和 FaultInjector。

| 规格场景 | 建议测试 | 关键断言 |
| --- | --- | --- |
| WP02-AC-001 | `test_approval_round_trip_and_snapshot` | 字段、revision、关联和快照顺序重开一致 |
| WP02-AC-002 | `test_approval_binding_mismatch_fails_closed` | 每类绑定变化均拒绝，状态不变 |
| WP02-AC-003 | `test_approval_digest_is_canonical_and_bounded` | 固定 digest、键顺序、数组顺序和非法输入 |
| WP02-AC-004 | `test_approval_decision_state_machine` | 合法决定、非法转换和 rev 冲突 |
| WP02-AC-005 | `test_approval_actor_roles_and_self_approval` | 角色交集、actor 绑定、自批策略 |
| WP02-AC-006 | `test_approval_expiry_is_materialized` | 过期持久化、审计、handler 计数 0 |
| WP02-AC-007 | `test_concurrent_claim_has_one_winner` | 两连接 barrier；一次成功，一次 ALREADY_CLAIMED |
| WP02-AC-008 | `test_approval_cannot_cross_context` | 跨 Run/工作区/task/config 全部拒绝 |
| WP02-AC-009 | `test_consume_requires_terminal_invocation` | SUCCEEDED/FAILED/TIMED_OUT 可；UNKNOWN 不可 |
| WP02-AC-010 | `test_approval_invalidation_boundaries` | PREPARED 可失效；STARTED/UNKNOWN/终态不可 |
| WP02-AC-011 | `test_approval_audit_lifecycle` | 事件顺序、关联字段、actor、链完整 |
| WP02-AC-012 | `test_approval_and_audit_roll_back_together` | 每个故障点均无半状态 |
| WP02-AC-013 | `test_duplicate_approval_request_is_idempotent` | 相同绑定返回原单；冲突/终态重放稳定 |
| WP02-AC-014 | `test_approval_api_has_no_handler_side_effect` | 计数器和文件快照不变 |
| WP02-AC-015 | `test_corrupt_or_incomplete_approval_rows_fail_closed` | 非法 JSON、状态组合、外键失败关闭 |
| WP02-AC-016 | `test_g3_g4_partial_lifecycle_evidence` | PENDING 无文件变化，claim/consume 最多一次意图 |
| WP02-AC-017 | `test_approval_request_and_waiting_are_atomic` | 审批、关联、Run WAITING 和审计共同提交 |
| WP02-AC-018 | `test_claim_restores_run_and_precedes_started` | claim/RUNNING/意图同事务，STARTED 后续事务 |
| WP02-AC-019 | `test_resume_claim_only_for_prepared_intent` | PREPARED 可恢复；STARTED/UNKNOWN/终态拒绝 |
| WP02-AC-020 | `test_reject_and_expire_stop_run_atomically` | 审批终态、Run STOPPED、StopReason 和审计一致 |
| WP02-AC-021 | `test_stale_binding_invalidates_and_blocks` | INVALIDATED、BLOCKED/WORKSPACE_CHANGED，无同 Run 重绑定 |

测试要求：

- 所有时间由固定 Clock 推进，不使用 sleep；
- 并发测试使用显式 `threading.Barrier` 或等价确定性同步；
- 每个负向用例验证审批、invocation、Run revision 和审计集合均未意外变化；
- 数据库重开使用新连接，不复用内存对象；
- Fake Channel 不访问网络；
- 不通过直接修改私有字段制造预期结果。

## 7. 施工顺序与可提交增量

在规格冻结后按以下顺序推进：

```text
T01 契约与摘要
  -> T02 migration v4
  -> T03 create/read/snapshot
  -> T04 decide/roles/expiry
  -> T05 claim/consume/invalidate
  -> T06 concurrency/fault
  -> T07 channel/no-side-effect
  -> T08 docs/acceptance
```

建议提交边界：

```text
test(wp-02): define approval binding and digest contracts
feat(wp-02): add approval lifecycle migration
feat(wp-02): persist approval requests and snapshot projection
feat(wp-02): enforce approval decisions and expiry
feat(wp-02): claim and consume approvals atomically
test(wp-02): cover concurrent claim and rollback windows
feat(wp-02): add approval channel boundary
docs(wp-02): accept approval lifecycle
```

每个增量必须同时包含测试和契约文档。T05 完成前不得允许下游 Gate 使用审批 API。

## 8. 安全与故障矩阵

| 边界 | 预期失败点 | 必须留下的证据 |
| --- | --- | --- |
| 参数/工具/任务/工作区变化 | claim 前 binding 比较 | `APPROVAL_BINDING_MISMATCH`，状态不变 |
| 过期后批准/claim | Clock 判定 | EXPIRED 行和审计，handler 0 次 |
| 双执行者 claim | 条件 UPDATE | 一胜一败，单一 audit/execution intent |
| 跨 Run 使用 approval_id | Run/tool 关联校验 | NOT_FOUND 或 BINDING_MISMATCH |
| 重复 consume | CLAIMED + intent 条件 | REPLAYED/NOT_EXECUTABLE，无第二次审计 |
| STARTED 后撤销 | invocation 状态校验 | 拒绝且 approval 保持 CLAIMED |
| UNKNOWN_RESULT | consume 拒绝 | approval 保持 CLAIMED，不允许新 claim |
| 审批写后审计失败 | UnitOfWork 异常 | 审批和 invocation 关联回滚 |
| 审计写后状态失败 | UnitOfWork 异常 | 审计随事务回滚 |
| 数据库重开 | 新连接读取 | revision、status、时间、关联完整 |
| 审批行损坏 | 严格模型和约束 | 不返回部分对象，失败关闭 |
| Channel 不可用/伪造 | Store 重新校验 | 不产生授权，不执行 handler |

## 9. 回滚策略

1. 实现前记录 WP-01 已验收代码基线和数据库 schema v3；
2. migration v4 只允许新增表、索引和触发器，不修改 v1-v3；
3. T02 合并前在临时 v3 数据库上测试升级、失败回滚和重开；
4. 若审批功能尚未产生真实 Run，回退代码可保留空的 v4 表；
5. 若已存在审批事实，禁止直接删除表或删除历史行；先导出/备份数据库，再使用前向 migration 或
   回退到已验证代码基线；
6. 回滚后下游 Gate/CLI 必须停止使用审批 API，不能通过直接 SQL 继续执行；
7. 任何回滚都记录受影响 Run、approval ID、数据库版本和人工处理结果。

## 10. 风险与限制

| 风险/问题 | 影响 | 处理 |
| --- | --- | --- |
| Approval v2 变更 WP-00 冻结契约 | 下游模型和契约测试需要同步升级 | 已按 D-23 修订 WP-00 v1.2；T01 实现 |
| 本地 actor 无密码学认证 | 本机调用者可声明角色 | 已按 D-24 限制范围；外部 Channel 不纳入 P0 |
| CLAIMED 永久悬挂 | UNKNOWN_RESULT 可能留下不可自动恢复审批 | 已按 D-26/D-27 保留人工处理入口 |
| 审批参数摘要与脱敏 | 摘要可被离线猜测低熵秘密 | 摘要 pre-redaction 计算；私密状态库；验收限制明确 |
| 并发 claim 偶发 | 无法证明一次性 | 条件 UPDATE + barrier + 新连接测试 |
| 数据库不可回滚 | 已写入审批事实可能丢失 | 只追加 migration，保留备份和前向修复 |

上述限制不阻塞施工；D-23 至 D-28 已确认，规格和计划均已冻结/批准。

## 11. 下游交接

WP-02 验收后必须向 WP-04、WP-06、WP-07、WP-11 交付：

- Approval v2、ApprovalRequest、Approver、ChannelDecision 和公共错误码；
- canonical args/binding digest 的固定输入和测试向量；
- migration v4、审批表约束和数据库版本；
- create/load/list/approve/reject/expire/invalidate/claim/resume_claim/consume 公共接口；
- ToolInvocation 关联和 RunSnapshot.approvals 读取方式；
- 审批审计事件、事件顺序和关联字段；
- 一次性 claim、确定终态 consume、UNKNOWN_RESULT 不可重放规则；
- 未覆盖能力、限制和下游必须重新执行的 Gate/恢复校验。

WP-04 必须在每次 claim 前重跑 Gate；WP-07 必须重新校验工作区和配置；WP-11 的审批命令只能改变
授权，不能执行 handler。

## 12. 实际完成情况

当前规格、实现、测试和验收报告均已完成。

| 项目 | 状态 |
| --- | --- |
| 规格 | `spec.md` v0.2.0 / frozen |
| 计划 | `plan.md` v0.2.0 / done |
| 公共契约 | D-23 至 D-28 已确认并入 WP-00 v1.2 |
| migration v4 | 已实现，v3→v4 升级、Run rebuild、外键与触发器测试通过 |
| `approval.py` | 已实现并通过目标/全量测试 |
| 测试 | 目标 `46 passed`；全量 `93 passed` |
| 验收报告 | [acceptance-report.md](acceptance-report.md) v1.0.0 / accepted |

工作包已完成。后续 Gate/CLI/Checkpoint 接入仍须遵守冻结的审批契约和失败关闭语义。
