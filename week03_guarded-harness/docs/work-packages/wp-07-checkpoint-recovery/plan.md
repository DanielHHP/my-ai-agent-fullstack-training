# WP-07 Checkpoint 与恢复工作包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-07 Checkpoint 与恢复 |
| 文档状态 | done |
| 计划版本 | 1.1.0 |
| 创建日期 | 2026-11-06 |
| 批准日期 | 2026-11-06 |
| 对应规格 | [spec.md](spec.md) v1.1.0 / frozen |
| 前置依赖 | WP-01 v0.4.0 accepted；WP-02 v0.2.0 accepted；WP-03 v1.1.0 accepted；WP-06 v1.0.0 accepted |
| 当前结论 | T00 至 T12 已完成，验收报告 accepted |
| 下游影响 | WP-10 Agent/Harness、WP-11 CLI resume、WP-12 故障矩阵和最终交付 |

本计划不重新定义规格。D-53 至 D-58 已确认，`spec.md` 已冻结，本计划已批准。施工中发现快照
schema、恢复动作、Gate 兼容或错误码缺口时，必须停止当前任务，先更新
并评审规格、设计和决策，再继续。

## 2. 计划目标与完成定义

WP-07 完成后必须通过公共接口、临时 SQLite 和临时 Git 工作区证明：

1. 每个声明安全边界都能原子写入带 canonical hash 的完整 Checkpoint；
2. Checkpoint 重开后仍可读取，篡改、schema 不兼容、引用缺失或 Audit 链损坏均失败关闭；
3. W0 至 W11 恢复窗口得到唯一 RecoveryAction；
4. PREPARED 恢复重新经过 Gate，CLAIMED 恢复复用 `resume_claim`，handler 最多执行一次；
5. STARTED 无确定结果只能进入 `UNKNOWN_RESULT/BLOCKED`，handler 重放计数为 0；
6. 确定结果不重跑 handler；结果提交但 Checkpoint 缺失时只补建 Checkpoint；
7. 配置、工作区、外围保护、审批、证据或 schema 变化时阻塞并留下审计；
8. continuation context 有界、确定、脱敏且不包含旧 Agent memory/隐藏推理；
9. 数据库重开和租约接管后预算、审批和步骤状态不重置；
10. 目标测试、全量测试、Ruff lint 和格式检查通过；
11. 形成实际命令与故障证据驱动的 `acceptance-report.md`；
12. 不以 WP-07 文档或测试声称 Agent/CLI 跨进程恢复已经实现。

## 3. 暂定范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── checkpoint.py       # 新增：快照、恢复规划、恢复记录、continuation
├── domain.py           # 扩展：Checkpoint/Recovery/Continuation 契约和错误码
├── storage.py          # 扩展：migration v5、append-only 表和完整性检查
├── state_store.py      # 扩展：最后 Checkpoint 指针、证据单向失效
├── sandbox.py          # 扩展：不强制干净的工作区快照公共入口
├── gate.py             # 扩展：reenter_prepared 薄恢复入口
├── audit.py            # 扩展：恢复和证据失效事件
└── __init__.py         # 扩展：公共导出

tests/
├── test_domain.py            # 扩展：严格 Checkpoint/Recovery/Continuation 模型
├── test_state_checkpoint.py  # 扩展：Checkpoint、迁移、崩溃窗口、continuation
├── test_gate.py              # 扩展：PREPARED 恢复重入和结果复用
└── test_sandbox.py           # 扩展：写入后受控工作区快照和兼容回归

docs/work-packages/wp-07-checkpoint-recovery/
├── spec.md
├── plan.md
└── acceptance-report.md      # 实测完成后创建
```

WP-07 不创建 `harness.py`、`completion.py`、`agent_adapter.py`、`cli.py` 或第二套 Agent Loop。

### 3.2 依赖方向

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

WP-10/WP-11 ────> checkpoint.py + gate.py + storage.py + sandbox.py
```

禁止：

- `checkpoint.py` 导入 `gate.py`、Agent 或任何工具 handler；
- `checkpoint.py` 用裸 SQL 修改 Run、Invocation、Approval 或 Evidence；
- `gate.py` 根据工作包名、任务名或模型文本选择恢复动作；
- 恢复模块复制 Gate 检查链或直接执行 handler；
- 读取 `SQLiteStorage`、`Sandbox` 或 `StateStore` 私有字段完成公共能力；
- 将 Checkpoint/recovery 直接当作跳过审批的授权；
- 在测试中使用真实模型、外部网络或用户凭据。

## 4. 详细设计

### 4.1 Domain 契约

新增严格、冻结、版本化模型：

- `CheckpointBoundaryKind`、`CheckpointBoundary`；
- `Checkpoint`、`CheckpointVerification`；
- `RecoveryAction`、`RecoveryStatus`、`RecoveryPlan`、`RecoveryRecord`；
- `ContinuationStep`、`ContinuationEvidence`、`ContinuationInvocation`、
  `ContinuationApproval`、`ContinuationContext`；
- WP-07 稳定错误码；
- `IdFactory.new_recovery_id()`（若恢复记录需要独立 ID）。

所有模型：

- `extra="forbid"`、严格类型、显式 schema version；
- 集合按稳定键排序，时间使用 UTC 微秒和 `Z`；
- 路径使用规范化 workspace-relative POSIX；
- 字符串、集合数量、JSON 深度和总大小有硬上限；
- 不包含 `object`、handler、模型对象或未声明字段。

`CheckpointId` 已存在；若新增 RecoveryId，必须使用固定前缀和 32 位小写 UUIDv4，不能复用其他
ID 类型表达恢复事实。

### 4.2 SQLite migration v5

建议表结构，最终 SQL 在 T02 冻结：

```sql
CREATE TABLE checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    boundary_kind TEXT NOT NULL,
    boundary_ref TEXT NOT NULL,
    step_id TEXT,
    tool_call_id TEXT,
    approval_id TEXT,
    run_revision INTEGER NOT NULL CHECK (run_revision >= 0),
    last_audit_sequence INTEGER NOT NULL CHECK (last_audit_sequence >= 0),
    last_audit_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, sequence),
    UNIQUE (run_id, boundary_kind, boundary_ref)
);

CREATE TABLE recovery_records (
    recovery_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    checkpoint_id TEXT REFERENCES checkpoints(checkpoint_id),
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    reason_code TEXT,
    invocation_id TEXT,
    approval_id TEXT,
    input_digest TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

两表均增加 `BEFORE UPDATE`、`BEFORE DELETE` trigger；Checkpoint 序号由同一 Run 内最大 sequence
加一，禁止删除或重排。恢复记录使用稳定 idempotency digest，避免重复恢复重复写同一决定。

Migration 只追加：

- v1 至 v4 SQL 与 checksum 不变；
- v5 可在旧数据库上初始化空 checkpoints/recovery_records；
- `verify_integrity()` 的 required tables 增加两表；
- 旧 Checkpoint schema、未知版本和缺少必要索引均失败关闭；
- 不在 migration 中自动生成伪造 Checkpoint。

### 4.3 工作区快照兼容

现有 `Sandbox.capture_baseline()` 在 `require_clean_workspace=true` 时对初始工作区强制干净，不能
用于写入后的每步 Checkpoint。WP-07 增加公共、只读、受策略约束的：

```text
Sandbox.capture_checkpoint_workspace() -> WorkspaceBaseline-like snapshot
```

候选实现：

```text
snapshot = sandbox._capture_baseline(enforce_clean=False)  # 只能由 Sandbox 内部使用
return public frozen workspace snapshot DTO
```

公共 DTO 不暴露私有字段，至少保留 identity、HEAD/UNBORN、index、status/diff、external
protection、external baseline 摘要和 bounded entries。恢复比较复用现有 `compare_workspace`
语义，不复制路径/Git 判定。该接口不修改工作区、不写 Audit、不改变现有基线行为。

### 4.4 Checkpoint 写入

`CheckpointService.create_checkpoint(...)` 候选流程：

```text
validate config/sandbox binding and boundary
  -> storage.transaction(actor=trusted recovery/harness actor)
       verify lease
       read RunSnapshot + audit head
       capture checkpoint workspace
       build snapshot payload
       canonicalize + digest
       idempotency check
       insert Checkpoint
       conditionally update runs.last_checkpoint_id/revision
       append CHECKPOINT_WRITTEN
     commit
```

实现注意：

- `RunSnapshot`、audit head、workspace capture 和 insert 的相对时序必须保证事实一致；若文件系统
  扫描不能置于 SQLite 事务内，读取后再比较 run revision/审计头/workspace digest；
- 任何一步失败都回滚整个 UnitOfWork；
- 同一边界重复调用返回原 Checkpoint，不重复写审计；
- 同一个 `(run_id,boundary_kind,boundary_ref)` 但不同 digest 返回 conflict；
- `run.last_checkpoint_id` 使用条件更新，旧 revision 不得覆盖新 Checkpoint；
- snapshot 内的 audit head 是该 Checkpoint 事件之前的最后一个事件，Checkpoint 事件自身不递归
  写入 snapshot；恢复可要求该事件紧随其后或由 Checkpoint 记录关联。

### 4.5 恢复规划

`prepare_recovery` 候选流程：

```text
transaction A (read + validations):
  verify lease/schema/support/audit chain
  load latest checkpoint and verify hash/links
  inspect Run/plan/steps/invocations/evidence/approvals/budget
  compare config + workspace identity/HEAD/index/external baseline
  materialize due approvals
  detect crash window
  compute evidence validity

transaction B (only if required):
  mark STARTED invocation UNKNOWN_RESULT
  transition Run BLOCKED/UNKNOWN_RESULT or WORKSPACE_CHANGED
  invalidate eligible approvals/evidence
  append recovery facts and audit

transaction C (only if required):
  rebuild missing W6 checkpoint

build deterministic bounded continuation context
  -> return RecoveryPlan
```

因为跨事务状态可能变化，B/C 必须以 Run revision、Invocation status/revision、审批 revision 和
audit head 的条件更新提交。任一步冲突时重新读取；不得盲目重试 handler。

### 4.6 崩溃窗口决策

固定判定顺序与规格第 8 节一致：

| 优先级 | 事实 | 候选动作 |
| --- | --- | --- |
| 1 | Checkpoint/DB/Audit schema 损坏 | `MANUAL_REVIEW` + BLOCKED |
| 2 | 配置/工作区身份/外围基线变化 | `MANUAL_REVIEW` + BLOCKED |
| 3 | STARTED 或无确定结果 | `MARK_UNKNOWN_RESULT` |
| 4 | 冲突的多个执行意图 | `MANUAL_REVIEW` + BLOCKED |
| 5 | CLAIMED + PREPARED | `RESUME_CLAIM_THEN_REENTER_GATE` |
| 6 | APPROVED/PREPARED | `REENTER_GATE` |
| 7 | PENDING 审批 | `WAIT_FOR_APPROVAL` |
| 8 | 确定结果无 Checkpoint | `REBUILD_CHECKPOINT_THEN_CONTINUE` |
| 9 | 确定结果有 Checkpoint | `CONTINUE_AGENT`，context 含持久化结果 |
| 10 | 无未决调用 | `CONTINUE_AGENT` |

决策函数只使用已持久化的枚举、状态、摘要和校验结果；不得使用模型文本、文件名猜测或工作区
mtime 作为唯一判据。

### 4.7 W6 Checkpoint 重建

W6 必须先完成：

- Invocation 终态和结果一致性；
- Approval 消费/CLAIMED 状态一致性；
- Audit 链、Run revision 和 Evidence 完整性；
- config、identity、HEAD/index、外部基线和证据版本校验；
- 不存在 STARTED/UNKNOWN/冲突调用；
- 当前差异可归属到已提交的确定结果或没有差异。

补建 Checkpoint 的 boundary 使用：

```text
kind = TOOL_SUCCEEDED / TOOL_FAILED / TOOL_TIMED_OUT
ref = tool_call_id
```

如果同一工具边界已有 Checkpoint 但内容不同，停止并 `BLOCKED`，不得覆盖。

### 4.8 Gate 恢复入口

`Gate.reenter_prepared` 的候选流程：

```text
verify service-trusted caller + lease
  -> load RunSnapshot/Invocation by run_id/tool_call_id
  -> require PREPARED
  -> load ToolDefinition by persisted tool_name
  -> validate persisted normalized args against current schema/config
  -> re-run visibility, semantic, permission, risk, approval, repeat and budget checks
  -> if approval CLAIMED:
        resume_claim in same UnitOfWork
     if approval APPROVED:
        claim as normal Gate path
  -> transition PREPARED -> STARTED in separate UnitOfWork
  -> execute persisted handler exactly once
  -> persist result/evidence/approval consumption as WP-06
```

必须避免：

- 从模型请求重新读取/覆盖规范参数；
- 对已记录的 ToolCall 预算再次 `record_budget_delta`；
- 在 STARTED 提交失败时调用 handler；
- 对 `STARTED/UNKNOWN_RESULT/CONSUMED` 恢复 claim；
- 对没有 PREPARED invocation 的审批伪造执行意图。

建议将现有 Gate 的“preflight/异常到 observation”抽取为内部可信 helper，仅重组流程，不复制
固定检查顺序。必须补公开 API 兼容测试和 `docs/decisions.md` 升级说明。

### 4.9 Continuation context

数据组装顺序：

1. 原任务、task kind、description、config fingerprint；
2. Run status/stop reason/acceptance status；
3. plan 与最近 10 个完成/失败步骤；
4. 仍有效证据及版本摘要；
5. 待执行 invocation/审批；
6. 预算上限、使用量和剩余量；
7. 禁止动作和安全提示；
8. canonical context digest。

渲染使用固定模板和固定字段顺序。内容超限时：

- 先裁剪最早的公开步骤摘要；
- 保留安全相关事实、待审批/待执行动作、预算和禁止动作；
- 不截断关键规则使其含义变化；
- 仍超限则返回 `RECOVERY_CONTEXT_TOO_LARGE` 并阻塞，不生成不确定 prompt。

### 4.10 租约与并发

- `checkpoint.py` 不自行启动隐藏心跳线程；租约由 WP-10/WP-11 负责续期，WP-07 验证 token；
- 恢复前必须取得有效新租约；旧租约未过期时返回 `STATE_LEASE_HELD`；
- 过期租约接管和旧的 fencing token 失效由 WP-01 负责，WP-07 只消费公开接口；
- 同一 Run 的 Checkpoint、恢复记录和状态更新默认串行；
- 两个进程竞争恢复时，只有一个能在有效租约/revision 上提交；
- 恢复流程不得把“本地未等待到进程”误判成外部副作用没有发生。

## 5. 暂定任务卡

| 任务 | 实现内容 | 依赖 | 对应规格 | 主要测试 | 完成条件 |
| --- | --- | --- | --- | --- | --- |
| WP07-T00（已完成） | 关闭 WP07-OPEN-001 至 005，冻结 spec，批准 plan，登记决策 | 无 | 第 14 节 | 文档评审 | D-53 至 D-57 已登记，规格与计划已冻结/批准 |
| WP07-T01（已完成） | 新增 Checkpoint/Recovery/Continuation domain 模型、ID 和错误码 | T00 | 6.1 至 6.7、10 | `test_domain.py` / `test_state_checkpoint.py` | 严格模型 round-trip，额外字段失败 |
| WP07-T02（已完成） | 增加 migration v5、append-only trigger、完整性检查 | T01 | 11 | `test_state_checkpoint.py` migration 组 | v1-v4 保留，v3/v4 可升级 v5 |
| WP07-T03（已完成） | 增加 Sandbox 受控不强制干净工作区快照接口 | T01 | 6.4、7.1、4.3 | `test_sandbox.py` | 写入后快照可用，原初始基线语义不变 |
| WP07-T04（已完成） | 实现 Checkpoint 构建、canonical hash、幂等写入和查询 | T02、T03 | 6.2 至 6.5、7.1 | `test_state_checkpoint.py` checkpoint 组 | 原子提交、篡改/重复/超限拒绝 |
| WP07-T05（已完成） | 实现 StateStore 最后 Checkpoint 指针和 Evidence 单向失效 | T04 | 7.5、9 | state/evidence 组 | 租约、revision、审计约束正确 |
| WP07-T06（已完成） | 实现恢复校验和 W0 至 W11 规划器 | T05 | 7.3、8、9 | recovery matrix 组 | 每个窗口唯一动作 |
| WP07-T07（已完成） | 实现 STARTED -> UNKNOWN、BLOCKED、审批/证据失效原子收口 | T06 | 8、9、10 | recovery failure 组 | handler 重放为 0，状态和审计不矛盾 |
| WP07-T08（已完成） | 实现 W6 受控 Checkpoint 补建 | T06、T07 | 8.1 | W6 组 | 只补建、不重跑，冲突时阻塞 |
| WP07-T09（已完成） | 实现 Gate `reenter_prepared` | T05 | 6.8、7.4 | `test_gate.py` recovery 组 | PREPARED 重检、claim 恢复、预算不重复 |
| WP07-T10（已完成） | 实现 bounded continuation context 和 renderer | T06 | 6.7、7.4、4.9 | context 组 | 确定、有界、无 memory/secret |
| WP07-T11（已完成） | 增加跨连接/租约/并发/故障矩阵和回归测试 | T07 至 T10 | 6、8、12 | 全量目标测试 | WP07-AC-001 至 025 全部覆盖 |
| WP07-T12（已完成） | 文档、索引、全量测试、Ruff、格式和 acceptance report | T11 | 13 | 全量命令 | 报告 accepted，证据可复跑 |

## 6. T00 决策冻结记录

2026-11-06 已全部完成：

1. 确认 PREPARED 恢复是否新增 Gate 公共入口；
2. 确认 UNKNOWN/BLOCKED 是否禁止同一 Run 自动恢复；
3. 确认 W6 工作区差异的“可解释”规则；
4. 确认 Evidence 是否写回单向 validity；
5. 确认 Checkpoint 1 MiB、continuation 64 KiB 和 P0 全量保留；
6. D-53 至 D-57 已登记到 `docs/decisions.md`；
7. 已同步更新 `design.md` 的恢复动作、持久化 schema、Gate 恢复边界和 Evidence 失效语义；
8. `spec.md` 已提升为 v1.1.0 / frozen，本计划已提升为 v1.1.0 / approved；D-58 追加待审批边界。

## 7. 测试计划

### 7.1 测试基础设施

- 复用 WP-01 的临时 SQLite、固定 Clock、fault injector 和临时 Git 工作区；
- 提供可重开 storage 的 helper，记录数据库路径和 state dir；
- 提供可控 lease clock，用 advance 模拟过期和接管，不 sleep；
- 提供 handler 计数器和 write marker，证明恢复不重放；
- 提供可控 workspace 文件写入、HEAD/index/外部脏文件变化和 symlink 场景；
- 提供可注入崩溃点：`checkpoint:before-insert`、`checkpoint:before-run-update`、
  `checkpoint:before-audit`、`recovery:before-unknown`、`recovery:before-block`；
- 所有模型调用使用 fake/mock，所有命令使用本地短生命周期测试进程或 fake。

### 7.2 Checkpoint/迁移测试

- 从空库初始化 v5；从 v1、v3、v4 fixture 升级并保留已有 Run/Audit；
- Checkpoint 字段完整、sequence 连续、stable sort、hash round-trip；
- 重复等价边界返回原记录；冲突 digest 拒绝；
- snapshot/行索引/关联字段篡改检测；
- UPDATE/DELETE trigger 拒绝；
- Run 指针与 Checkpoint/审计同事务提交或回滚；
- 超过 1 MiB、未知 schema、缺关联、Audit 链损坏失败关闭；
- 数据库重开、WAL、foreign keys、quick check 正确。

### 7.3 工作区/证据测试

- 干净工作区捕获初始基线；
- 合法成功写入后捕获 Checkpoint workspace，不触发“初始脏工作区”拒绝；
- 工作区文件变化使旧 READ/DIFF Evidence 失效；
- 文件不存在、不可读或版本变化时不纳入 continuation 的 valid evidence；
- 外围保护启用/关闭的明确状态；
- HEAD、index、workspace identity/prefix 变化阻止自动恢复；
- 旧审批在绑定变化/证据失效/工作区变化时无法恢复；
- W6 的可解释差异能补建 Checkpoint，不可解释差异阻塞。

### 7.4 崩溃窗口测试

使用固定 fixture 构造：

- W0：无 Invocation；
- W1：PREPARED 无审批；
- W2：APPROVED + PREPARED；
- W3：CLAIMED + PREPARED；
- W4/W5：STARTED 无结果；
- W6：终态已提交但无 Checkpoint；
- W7：终态和 Checkpoint 均已提交；
- W8：PENDING 审批；
- W9：UNKNOWN 已持久化；
- W10：多个冲突调用；
- W11：配置/工作区/Audit/schema 变化。

每个窗口断言：

- RecoveryAction/Status 唯一且稳定；
- Run/Invocation/Approval 状态符合矩阵；
- handler 调用次数符合期望；
- 预算 usage 不重复；
- Audit 和 recovery record 的关联完整；
- 相同输入重复调用不产生第二次副作用。

### 7.5 Gate 恢复测试

- `PREPARED` 无审批经 `reenter_prepared` 重新 schema/权限/路径检查；
- `APPROVED + PREPARED` claim 后只执行一次；
- `CLAIMED + PREPARED` 只追加 `APPROVAL_CLAIM_RECOVERED`，不重复 budget；
- `STARTED/UNKNOWN/终态` 拒绝恢复执行；
- 变更工具名/参数/工作区导致绑定失败；
- Gate 重入不暴露给模型，不改变正常 `invoke()` 行为；
- STARTED 写失败时 handler 计数为 0；
- handler 结果写入失败时保留 STARTED/UNKNOWN 并可恢复为人工阻塞。

### 7.6 Continuation 测试

- 固定输入得到 stable text 与 digest；
- 包含任务、配置、plan、最近步骤、预算、有效证据、待审批/待执行调用；
- 不包含旧 memory、hidden reasoning、完整 Audit 事件流、租约/审批 token、secret；
- 超过步骤/单项/总字节限制时有界裁剪或失败关闭；
- 文件版本变化后旧事实不出现在可引用区域；
- 恢复上下文中的禁止动作能阻止“模型声称完成”或“已执行写入重试”。

### 7.7 租约与并发测试

- 有效租约阻止第二个 owner；
- 过期租约可接管，旧 token 续租/状态写入均失败；
- Checkpoint 并发重复边界只生成一条；
- 两个恢复规划者只有当前 revision 条件更新成功；
- 数据库重开后 step/tool/token/wall usage 和审批消费状态不变；
- 审计顺序随事务提交，不因线程竞争出现重复序号。

## 8. 规格场景到测试映射

| 规格场景 | 测试文件/函数候选 | 证据 |
| --- | --- | --- |
| WP07-AC-001 | `test_state_checkpoint.py::test_checkpoint_atomic_round_trip` | Checkpoint + Run pointer + Audit |
| WP07-AC-002 | `test_checkpoint_contains_required_snapshot_facts` | Snapshot field assertions |
| WP07-AC-003 | `test_checkpoint_hash_is_canonical_and_stable` | Two equivalent snapshots |
| WP07-AC-004 | `test_checkpoint_tamper_and_unknown_schema_fail_closed` | Tamper markers |
| WP07-AC-005 | `test_checkpoint_and_recovery_tables_are_append_only` | trigger errors |
| WP07-AC-006 | `test_checkpoint_fault_rolls_back_all_rows` | fault injection |
| WP07-AC-007 | `test_recovery_w0_continues_without_handler` | invocation count 0 |
| WP07-AC-008 | `test_recovery_prepared_reenters_gate` | Gate observation |
| WP07-AC-009 | `test_recovery_approved_prepared_claims_once` | approval revision |
| WP07-AC-010 | `test_recovery_claimed_prepared_resumes_once` | resume audit |
| WP07-AC-011 | `test_recovery_started_becomes_unknown_without_replay` | marker unchanged, count 0 |
| WP07-AC-012 | `test_recovery_rebuilds_missing_checkpoint_without_handler` | checkpoint added |
| WP07-AC-013 | `test_recovery_uses_committed_result_after_reopen` | handler count unchanged |
| WP07-AC-014 | `test_recovery_pending_approval_waits` | approval PENDING |
| WP07-AC-015 | `test_recovery_unknown_stays_manual_after_retry` | no replay |
| WP07-AC-016 | `test_recovery_config_mismatch_blocks_and_invalidates` | mismatch reason |
| WP07-AC-017 | `test_recovery_workspace_change_blocks` | changed path summary |
| WP07-AC-018 | `test_recovery_invalidates_stale_file_evidence` | evidence validity |
| WP07-AC-019 | `test_recovery_rejects_expired_or_rebound_approval` | approval status |
| WP07-AC-020 | `test_recovery_preserves_budget_across_reopen` | budget values |
| WP07-AC-021 | `test_recovery_lease_takeover_fences_old_token` | lease token failure |
| WP07-AC-022 | `test_recovery_blocks_on_audit_chain_corruption` | audit error |
| WP07-AC-023 | `test_continuation_context_is_bounded_and_secret_free` | canonical text |
| WP07-AC-024 | `test_manual_review_exposes_no_execution_authorization` | plan fields |
| WP07-AC-025 | `test_checkpoint_recovery_suite_is_offline_and_repeatable` | no network/skip |

最终测试函数名可在 T00 冻结后调整，但 spec 场景 ID、覆盖关系和证据必须保留。

## 9. 施工顺序与增量

```text
T00 decisions/spec freeze
  -> T01 domain contracts
  -> T02 schema/migration
  -> T03 workspace snapshot
  -> T04 Checkpoint service
  -> T05 state/evidence extensions
  -> T06 recovery planner
  -> T07 unknown/block atomic closure
  -> T08 W6 rebuild
  -> T09 Gate reenter_prepared
  -> T10 continuation
  -> T11 fault/concurrency/regression
  -> T12 acceptance report
```

每个增量必须：

- 先写失败测试，再实现最小行为；
- 保持旧 WP-01/WP-02/WP-03/WP-04/WP-06 测试全绿；
- 不修改已发布 migration；新 migration 只追加；
- 不以 mock 替代真实 SQLite 事务、真实临时 Git 工作区和真实路径 I/O；
- 记录公共 API、错误码、schema 和下游影响；
- 可单独审查和回滚；回滚不得删除已提交审计或 Checkpoint 事实。

## 10. 风险、阻塞与人工处理

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| PREPARED 无 Gate 入口 | 恢复只能绕过门禁或阻塞 | T00 决定新增薄 Gate API；未批准则 BLOCKED |
| STARTED 无法区分 handler 是否执行 | 潜在重复写/命令 | 统一 UNKNOWN，不自动重放 |
| W6 工作区自然变脏 | 误报 C4 阻塞或误判安全 | 使用可解释结果+证据边界，否则阻塞 |
| 旧 Evidence 仍被引用 | 验收错用旧源码事实 | 单向失效 + continuation 过滤 |
| Checkpoint 写失败 | 状态显示成功但无恢复点 | 整体回滚；不得返回安全边界成功 |
| 租约过期/旧进程心跳 | 两个写者竞争 | 条件更新、fencing token、审计 |
| 大工作区扫描阻塞事务 | 恢复延迟/DB 锁 | 有界快照、事务外扫描前后复核，超限失败关闭 |
| 旧 Agent memory 被恢复 | 不确定行为和隐藏推理泄漏 | 只生成结构化 context，不序列化 Agent |
| 审计链损坏 | 无法证明恢复事实 | BLOCKED，人工核对/前向修复 |

人工处理入口的最小语义：

- 只读查看 run 当前状态、最后 Checkpoint、恢复原因、关联 invocation/approval/evidence；
- 显示 `UNKNOWN_RESULT`、`WORKSPACE_CHANGED`、`BLOCKED` 和审计事实；
- 不提供把 `UNKNOWN_RESULT` 改成 SUCCEEDED、释放 CLAIMED 审批或自动重放 handler 的 API；
- 人工核对后启动新 Run；旧 Run 保留事实，供 WP-11 inspect 和 WP-12 最终记录。

## 11. 回滚计划

- T00 未完成：不改源码、不建 migration，工作包保持 `BLOCKED`；
- T01 后：删除未发布的 domain 扩展并恢复上游导入，不影响已发布状态；
- T02 后：production 不使用未发布 migration；测试数据库可重建，已发布 v1-v4 不变；
- T03 后：移除快照扩展，恢复 Sandbox 原公开接口和测试；
- T04 至 T10 后：保留已发生的 Checkpoint/Audit/Unknown 事实，不回写、不删除；
- 发生真实副作用后回滚只能停止自动恢复并人工核对，不能恢复代码同时重放请求；
- 任何回滚都必须记录受影响 Run、Checkpoint、Approval、数据库版本和人工处理结论。

## 12. 实施约束

D-53 至 D-58 已关闭，WP-07 已进入施工。以下为本计划实施默认值：

- 测试目标集中在 `tests/test_state_checkpoint.py` 和 `tests/test_gate.py`，避免新增未被
  requirements 指定的测试文件替代六个必需文件；必要的模型和 Sandbox 回归分别扩展
  `tests/test_domain.py` 与 `tests/test_sandbox.py`；
- 使用 WP-01 的固定 Clock 和 FaultInjector，不引入 sleep 或后台线程；
- Recovery record 默认在 `prepare_recovery` 成功规划后追加；未知/阻塞状态先于返回结果提交；
- 所有恢复 API 的 actor 必须是可信 system/operator 上下文，不接受模型参数；
- 若实现需要新增 `recovery_id`，先补 `IdFactory` 协议和 WP-00 兼容测试。
