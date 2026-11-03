# WP-01 持久化与审计底座实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-01 持久化与审计底座 |
| 文档状态 | approved |
| 计划版本 | 0.2.1 |
| 修订日期 | 2026-11-03 |
| 对应规格 | `spec.md` v0.2.1 / frozen |
| 前置依赖 | WP-00 v1.1.0，已验收 |
| 施工前置 | 已满足；D-16 至 D-21 已写入决策基线 |
| 当前结论 | T01 SQLite/migration 与 T02 统一脱敏已完成并通过目标及全量验证 |

本计划只安排 WP-01 的施工，不重新定义 `spec.md`。规格变化时先更新并评审 `spec.md`，再同步本计划。

## 2. 设计摘要

WP-01 使用一个 SQLite 文件承载 State、Audit 和后续共享事务。每个写操作创建独立连接并执行
`BEGIN IMMEDIATE`，避免依赖单个长期连接或进程内锁。业务行用 revision 做乐观并发控制，Run 用
数据库租约做跨进程单写者控制，审计用 Run 内连续序号和哈希链证明顺序与完整性。

建议模块关系：

```text
domain.py / config.py（WP-00）
        |
        +--> redaction.py ----+
        |                     |
        +--> storage.py ------+--> UnitOfWork
                              |      +--> StateStore
                              |      +--> AuditStore
                              |
                         audit.py
```

详细不变量：

- StateStore 公共写操作必须运行在活动 UnitOfWork 中；
- 写事务采用 `BEGIN IMMEDIATE`，成功退出统一 commit，任意异常统一 rollback；
- Run 公共创建接口原子完成业务行、租约和审计；此后所有 Run 级可变数据必须匹配有效租约；
- StateStore 写操作自动追加必要审计，额外 Gate/Approval/Checkpoint 事实由调用方在同一
  UnitOfWork 追加；
- 审计 sequence 在写锁内读取当前最大值加一；
- migration 和 schema checksum 失败关闭；
- 所有外部内容先脱敏、再裁剪、再 canonical JSON、最后哈希或落盘；
- 读取时重新构造冻结模型，不使用裸字典继续传播。

## 3. 详细数据设计

### 3.1 Migration v1

建议 migration 序号与内容：

1. bootstrap `schema_migrations(version, checksum, applied_at)`；
2. 创建 WP-01 业务表、外键、唯一约束、CHECK 和索引；
3. 创建 `audit_events` UPDATE/DELETE 拒绝触发器；
4. 写入 migration version 与 checksum；
5. 读取 `PRAGMA user_version` 作为快速 schema 标记，并与 `schema_migrations` 最大值交叉校验。

连接启动设置：

```text
PRAGMA foreign_keys = ON
PRAGMA journal_mode = WAL
PRAGMA busy_timeout = 5000
PRAGMA synchronous = FULL
PRAGMA trusted_schema = OFF
```

如果环境不支持 WAL 或关键 PRAGMA 无法生效，初始化失败，不降级为 journal 模式静默继续。

### 3.2 表与索引

| 表 | 主键/唯一约束 | 关键索引 |
| --- | --- | --- |
| `schema_migrations` | `version` | 无 |
| `runs` | `run_id`；`trace_id UNIQUE` | `(status, updated_at)`、`(task_id)` |
| `run_plans` | `run_id` | 无 |
| `steps` | `step_id`；`(run_id, ordinal) UNIQUE` | `(run_id, ordinal)` |
| `tool_calls` | `(run_id, tool_call_id)` | `(run_id, step_id)`、`(run_id, status)` |
| `evidence` | `evidence_id` | `(run_id, kind)`、`(run_id, tool_call_id)` |
| `tool_call_evidence` | `(run_id, tool_call_id, evidence_id)` | `(run_id, evidence_id)` |
| `completion_results` | `run_id` | `(acceptance_status)` |
| `completion_items` | `(run_id, item_id)` | `(run_id, passed)` |
| `budget_ledger` | `(run_id, source_kind, source_id, category)` | `(run_id, created_at)` |
| `run_leases` | `run_id` | `(expires_at)` |
| `audit_events` | `event_id`；`(run_id, sequence)` | `(run_id, sequence)`、`(run_id, tool_call_id, sequence)`、`(run_id, approval_id, sequence)` |

所有表使用显式 `NOT NULL`、CHECK、外键和 `ON UPDATE RESTRICT ON DELETE RESTRICT`。JSON 字段保存
canonical JSON 文本；时间保存 `YYYY-MM-DDTHH:MM:SS.ffffffZ`；金额保存规范化 Decimal 文本。

### 3.3 Run 行

建议 `runs` 将预算上限与 usage 展平为独立列，避免从嵌套 JSON 做条件更新：

```text
run_id, trace_id, task_id, task_kind, task_description,
config_fingerprint, workspace_json,
max_steps, max_tool_calls, max_token_cost, max_wall_time_seconds,
used_steps, used_tool_calls, used_token_cost, used_wall_time_seconds,
status, stop_reason, acceptance_status,
current_step_id, last_checkpoint_id, resumed_from_checkpoint_id,
candidate_final_answer, schema_version, revision,
created_at, updated_at, started_at, ended_at
```

写入和读取时组合/拆解冻结 `Run`、`RunBudget`、`BudgetUsage` 和 `WorkspaceIdentity`，不做宽松
类型转换。

### 3.4 审计行

```text
event_id, schema_version, run_id, sequence, occurred_at,
event_type, phase, status, decision, code,
step_id, tool_call_id, approval_id,
actor, tool_name, rule_id, workspace_fingerprint,
input_summary_json, output_summary_json, evidence_ref,
prev_hash, event_hash
```

建议 `event_id` 不依赖随机工厂，而是确定性生成：

```text
audit_<run_id without "run_">_<sequence:020d>
```

这样同一 Run 的重载、并发追加和测试都使用同一稳定身份规则，也不需要改变 WP-00 的 `IdFactory`。

### 3.5 budgets

`budget_ledger` 保存每次唯一增量：

```text
run_id, source_kind, source_id, category, delta, created_at
```

应用流程：

1. 校验 lease、expected revision、source/category 和 delta 非负；
2. 由 `run_id + source_kind + source_id + category` 确定性生成 operation key；
3. 查询同一个操作 key；载荷相同则返回当前 usage，载荷不同则返回 `STATE_CONFLICT`；
4. 用 Decimal 计算候选 usage；超过上限则返回 `STATE_BUDGET_EXCEEDED`，不写 ledger；
5. 插入 ledger、条件更新 runs usage/revision、追加 `BUDGET_UPDATED`；
6. 提交后返回新 usage。

该流程消除“更新已提交但调用方没收到响应后重试”导致的预算双计。

### 3.6 租约

`run_leases` 建议字段：

```text
run_id, owner_id, token_digest, acquired_at, heartbeat_at, expires_at
```

- token 由 32 字节随机数生成并只在返回对象中出现；
- 数据库保存带固定域分隔的 SHA-256 digest；
- 获取、续期、释放均在 `BEGIN IMMEDIATE` 中条件更新；
- 获取过期租约视为 takeover，旧 token 因摘要不匹配立即失效；
- lease 事件与 `run_leases` 更新在同一事务写入 Audit；
- 过期清理采用“下一次操作时条件接管”，不运行隐藏后台线程。

## 4. 事务与调用链

### 4.1 UnitOfWork 生命周期

推荐实现形态：

```python
with storage.transaction(actor="harness") as uow:
    run = uow.state.get_run(run_id)
    uow.state.record_budget_delta(command, lease_token=lease.token)
    uow.audit.append(gate_decision)
```

内部行为：

```text
enter:
  assert not already in transaction
  connect with isolation_level=None
  configure connection PRAGMAs
  BEGIN IMMEDIATE
  build StateStore/AuditStore bound to transaction

success:
  COMMIT
  mark UnitOfWork closed

exception:
  ROLLBACK if active
  mark UnitOfWork closed
  map only recognized storage/constraint errors to stable errors
  re-raise
```

不允许在活动 UnitOfWork 内再次 `transaction()`。读取快照可使用独立只读连接并显式 `BEGIN`，在
同一读事务中读取 Run 及所有 WP-01 子对象。

### 4.2 StateStore 写链路

```text
validate uow active
-> check lease and revision
-> validate state transition / invariant
-> execute parameterized UPDATE/INSERT with conditions
-> assert exactly one row changed
-> append required audit event
-> return only after UoW commit
```

如果审计追加失败，UPDATE/INSERT 已写但未 commit，UnitOfWork 回滚。如果状态约束失败，调用方已经
追加的审计也随 UnitOfWork 回滚。

### 4.3 审计追加链路

```text
validate draft
-> verify run exists and unit-of-work active
-> redact summary/actor fields
-> read chain head under BEGIN IMMEDIATE
-> allocate prev_hash and sequence
-> compute deterministic event_id
-> canonicalize and hash event body
-> INSERT audit event
-> return record after commit
```

不得先在事务外缓存 sequence，也不得使用时间排序代替 sequence。

## 5. 状态机实现矩阵

| 聚合 | 合法转换 | 终态 | 额外条件 |
| --- | --- | --- | --- |
| Run | NEW→RUNNING；RUNNING→WAITING_APPROVAL/VERIFYING/STOPPED/FAILED/BLOCKED；WAITING_APPROVAL→RUNNING/STOPPED/FAILED/BLOCKED；VERIFYING→COMPLETED/FAILED/BLOCKED | COMPLETED/STOPPED/FAILED/BLOCKED | 终态 `ended_at`；停止/失败/阻塞 `stop_reason`；预算耗尽固定三元组 |
| Step | PLANNED→RUNNING；RUNNING→COMPLETED/FAILED | COMPLETED/FAILED | `(run_id, ordinal)` 唯一；结束时间不早于开始 |
| ToolInvocation | PREPARED→STARTED；STARTED→SUCCEEDED/FAILED/TIMED_OUT/UNKNOWN_RESULT | 后四者 | `(run_id, tool_call_id)` 唯一；无终态重置 |
| Acceptance | NOT_EVALUATED→ACCEPTED/REJECTED/UNVERIFIABLE | 后三者 | 原 Run 内不可再次改变 |

实现使用一个集中状态转换表及一个跨字段不变量校验函数，不在各 SQL 调用点复制规则。

## 6. 施工任务卡

任务默认串行。每张任务卡先写测试，再实现，再运行目标测试。公共字段、表、错误码变化时同一增量
更新契约测试和文档。

### WP01-T01 SQLite 与 migration 基线

| 项目 | 内容 |
| --- | --- |
| 目标 | 建立安全打开、PRAGMA、只追加 migration、checksum 校验和连接生命周期 |
| 前置 | spec v0.2.1 已冻结；WP-00 v1.1.0 已重新验收 |
| 涉及文件 | `storage.py`、`__init__.py`、`tests/test_state_checkpoint.py` |
| 公共/schema 变化 | 新增 `SQLiteStorage`、`IntegrityReport`、`STATE_*` 子集；migration v1 bootstrap |
| 正常路径 | 新目录创建 DB；重复初始化幂等；重开可读 schema version |
| 拒绝/故障 | 数据库是目录、版本过高、checksum 不符、WAL/PRAGMA 失败、migration 中途失败 |
| 对应规格 | WP01-AC-001、WP01-AC-002、WP01-AC-016、WP01-AC-017 |
| 完成条件 | 目标测试通过；不产生审批/Checkpoint 业务表；失败无半初始化 schema |

### WP01-T02 统一脱敏与有界转换

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现确定性 Redactor，供状态、审计和错误摘要复用 |
| 前置 | T01 |
| 涉及文件 | `redaction.py`、`audit.py`/`state_store.py` 的接入点、`tests/test_audit.py`，可增加 `tests/test_redaction.py` |
| 公共变化 | `Redactor`、`RedactionLimits`、`RedactionResult`、`REDACTION_*` |
| 正常路径 | 嵌套映射/序列、敏感键、Bearer、JWT、PEM、常见 token、显式 sensitive value |
| 拒绝/故障 | 不支持对象、循环引用、深度/节点/字节/行超限、失败关闭 |
| 对应规格 | WP01-AC-013、WP01-AC-014 |
| 完成条件 | secret 不在返回值和异常中出现；裁剪确定性；无 `repr()` 泄漏 |

### WP01-T03 Run、计划与 Step 持久化

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 runs/run_plans/steps schema、模型往返、条件更新和状态机 |
| 前置 | T01、T02 |
| 涉及文件 | `state_store.py`、`storage.py`、`domain.py` 必要兼容扩展、`tests/test_state_checkpoint.py` |
| 公共/schema 变化 | `RunPlan`、`PlanItem`、`RunSnapshot`、StateStore 读取/转换操作 |
| 正常路径 | 创建 Run/计划、合法状态转换、重开读取完整对象 |
| 拒绝/故障 | 非法转换、旧 revision、重复/跳号 ordinal、跨 Run 引用、计划超限、非法跨字段组合 |
| 对应规格 | WP01-AC-003、WP01-AC-004、WP01-AC-006、WP01-AC-018 |
| 完成条件 | 状态机集中表驱动；读取重新 Pydantic 校验；非法路径状态不变 |

### WP01-T04 工具调用、证据、预算与 Completion

| 项目 | 内容 |
| --- | --- |
| 目标 | 持久化 ToolInvocation、Evidence、Completion 和幂等预算 ledger |
| 前置 | T03 |
| 涉及文件 | `state_store.py`、`storage.py`、`domain.py`、`tests/test_state_checkpoint.py` |
| 公共/schema 变化 | `BudgetDeltaCommand`；tool/evidence/completion/ledger 表；`STATE_BUDGET_EXCEEDED` |
| 正常路径 | 调用状态转换、证据关联、Completion 往返；Decimal usage 原子累计 |
| 拒绝/故障 | 重复 `tool_call_id`、终态回退、超预算、负增量、同 key 不同 delta、非法证据关联 |
| 对应规格 | WP01-AC-003、WP01-AC-005、WP01-AC-007、WP01-AC-012 |
| 完成条件 | H3 固定三元组可持久化；source/category 幂等不重复计数；usage 与 ledger 汇总一致；禁止预算超限提交 |

### WP01-T05 Run 租约

| 项目 | 内容 |
| --- | --- |
| 目标 | 用 SQLite 条件更新实现跨连接/跨进程单写者租约 |
| 前置 | T03 |
| 涉及文件 | `state_store.py`、`storage.py`、`tests/test_state_checkpoint.py` |
| 公共/schema 变化 | `LeasePolicy`、`RunLease`、原子 create_run、acquire/renew/release、`STATE_LEASE_*`、run_leases 表 |
| 正常路径 | 创建 Run 时原子获取租约；续期、释放；过期后新 owner 接管 |
| 拒绝/故障 | 有效租约被抢占、旧 token 续期/释放、过期 token 写状态、连续心跳失败、时钟回退 |
| 对应规格 | WP01-AC-008、WP01-AC-006、WP01-AC-016 |
| 完成条件 | 固定 Clock 驱动全部测试；不使用 sleep；无内存锁作为安全边界 |

### WP01-T06 Append-only 审计与查询

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现审计信封、序号、canonical hash chain、查询、触发器和链校验 |
| 前置 | T01、T02、T03 |
| 涉及文件 | `audit.py`、`storage.py`、`domain.py`、`tests/test_audit.py` |
| 公共/schema 变化 | `AuditEventType`、`AuditDecision`、`AuditEventDraft`、`AuditEventRecord`、`AuditChainReport`、`AUDIT_*`、audit_events |
| 正常路径 | 追加、稳定列表、按 tool/approval 关联、verify chain |
| 拒绝/故障 | 更新/删除触发器、非法序号、链断裂、关联 Run 缺失、limit 越界 |
| 对应规格 | WP01-AC-009、WP01-AC-010、WP01-AC-012 |
| 完成条件 | 首事件 genesis、事件哈希重算一致；查询顺序不依赖时间或 rowid |

### WP01-T07 UnitOfWork 原子性、并发与故障注入

| 项目 | 内容 |
| --- | --- |
| 目标 | 把 State/Audit 写入绑定同一事务，并证明失败回滚和并发不覆盖/不重号 |
| 前置 | T03 至 T06 |
| 涉及文件 | `storage.py`、`state_store.py`、`audit.py`、`tests/test_audit.py`、`tests/test_state_checkpoint.py` |
| 公共/schema 变化 | `StorageUnitOfWork`、fault point 名称和稳定错误映射 |
| 正常路径 | 状态+审计共同提交；Gate 追加事件与状态共同提交 |
| 拒绝/故障 | 审计失败回滚状态；状态失败回滚审计；嵌套事务拒绝；并发 revision 冲突；并发 seq 唯一 |
| 对应规格 | WP01-AC-006、WP01-AC-011、WP01-AC-015、WP01-AC-016 |
| 完成条件 | 故障注入计数可断言；错误关闭无自动重试；事务退出后对象不可复用 |

### WP01-T08 文档、索引与验收准备

| 项目 | 内容 |
| --- | --- |
| 目标 | 同步公共契约、error code、migration、任务状态和下游交接 |
| 前置 | T01 至 T07 全部完成 |
| 涉及文件 | `docs/README.md`、`docs/work-packages/index.md`、`docs/task-breakdown.md`、WP-01 spec/plan |
| 正常路径 | 文档与 schema/API 一致；生成实际失败点和下游影响 |
| 拒绝/故障 | 测试失败、skip、证据不足时不生成 accepted 结论 |
| 对应规格 | 第 11、12、13、14 节 |
| 完成条件 | 目标/全量 pytest、Ruff lint、format、pip check 全通过；另建 `acceptance-report.md` 记录实测结果 |

## 7. 测试计划与场景映射

### 7.1 `tests/test_state_checkpoint.py`

| 测试组 | 关键用例 | 覆盖 |
| --- | --- | --- |
| storage_bootstrap | 新 DB、PRAGMA、幂等 migration、版本/checksum 拒绝 | WP01-AC-001/002 |
| state_round_trip | Run/Plan/Step/Invocation/Evidence/Completion close/reopen | WP01-AC-003/005 |
| state_machines | 合法/非法 Run、Step、Invocation、Acceptance 转换 | WP01-AC-004/005 |
| concurrent_updates | 两连接相同 revision；不同 Run 独立更新 | WP01-AC-006/015 |
| budget_ledger | Decimal 精确累计、source/category 幂等、usage 汇总一致、越界、H3 三元组 | WP01-AC-007 |
| leases | acquire/renew/release/takeover/old token | WP01-AC-008 |

### 7.2 `tests/test_audit.py`

| 测试组 | 关键用例 | 覆盖 |
| --- | --- | --- |
| append_and_chain | genesis、连续 sequence、哈希重算、稳定查询 | WP01-AC-009 |
| immutability | 直接 UPDATE/DELETE、绕过后的 verify 报警 | WP01-AC-010 |
| unit_of_work | audit 写失败回滚 state；state 写失败回滚先写 audit | WP01-AC-011 |
| correlation | A1/A2 字段与按 tool/approval 查询 | WP01-AC-012 |
| redaction | 敏感键/值/header/PEM/JWT、裁剪、错误信息 | WP01-AC-013/014 |
| concurrent_append | 多连接并发 append 无重号/丢事件 | WP01-AC-015 |

### 7.3 故障注入点

Fixture 使用 WP-00 的 `FaultInjector`，至少规划：

```text
storage:migration-before-commit
state:before-write
state:after-write
audit:before-insert
audit:after-insert
uow:before-commit
lease:after-read
```

每个点只用于测试，并记录调用次数。不得把 FaultInjector 接入生产安全判定或作为放行开关。

### 7.4 测试独立性

- 每个用例使用 `tmp_path/harness.sqlite3`；
- Clock 使用固定 UTC 序列，租约过期通过推进 Clock 模拟；
- 并发测试使用独立连接/线程，共享显式 barrier，busy timeout 足够但不依赖长等待；
- 不访问网络、真实模型、真实用户目录或系统凭据；
- 不跳过 P0 安全场景；若环境不支持 WAL，测试应失败而不是 skip 后宣称通过。

## 8. 施工顺序与增量

```text
T01 SQLite/migration
  -> T02 Redaction
  -> T03 Run/Plan/Step
  -> T04 Tool/Evidence/Budget/Completion
  -> T05 Lease
  -> T06 Audit
  -> T07 UnitOfWork/并发/故障
  -> T08 文档与验收准备
```

T03 和 T04 的模型测试可以先写，但 migration v1 必须在 T01 后冻结；T06 的审计表可以在 T01 同一
migration 中先建立，但审计 API 到 T06 才完成。不得把整个 schema 分散到多个文件并通过
`CREATE TABLE IF NOT EXISTS` 替代 migration。

建议提交：

```text
feat(wp-01): add versioned SQLite storage foundation
feat(wp-01): add deterministic redaction boundary
feat(wp-01): persist run plan and state transitions
feat(wp-01): persist tool evidence budget and completion
feat(wp-01): add cross-process run leases
feat(wp-01): add append-only hash-chained audit
test(wp-01): prove state audit rollback and concurrency
docs(wp-01): freeze persistence and audit contracts
```

## 9. 回滚策略

WP-01 尚未发布生产数据库，但已运行的本地数据库仍按可恢复数据处理：

1. 每个增量独立 revert；
2. schema 变化只通过新增 migration 修正，不修改已应用 migration；
3. 若 migration v1 尚未被任何验收数据库使用，可在开发分支回滚整个 v1；一旦验收报告使用过，只能
   追加修复 migration；
4. 回滚实现时保留数据库文件和审计，不执行 `DROP TABLE`、删除数据库或改写审计；
5. Audit/Redaction 安全语义回滚时，下游不得继续依赖未封版接口；
6. 若已进入 WP-02，先停止下游写入并保存/导出受控数据库副本，再回滚代码；
7. 任何无法确认的 schema 或审计状态按 `blocked` 处理，不自动重建数据库掩盖问题。

## 10. 风险与阻塞

| 风险 | 影响 | 处理 |
| --- | --- | --- |
| Decimal 用 SQLite REAL 存储 | 预算边界和重开结果漂移 | 只用规范十进制文本并在 Python 中精确运算 |
| revision 与 lease 只实现其中一个 | 跨进程仍可能覆盖 | 全部 Run 写必须同时验证 lease、revision 和条件 SQL |
| sequence 在事务外分配 | 并发重号或审计链断裂 | `BEGIN IMMEDIATE` 内读取 head 并插入 |
| 先裁剪后脱敏 | secret 残片可能保留 | 强制先脱敏再规范化/裁剪/哈希 |
| Audit 表无触发器 | 直接 SQL 篡改不可控 | 加 UPDATE/DELETE trigger，并保留 chain verify |
| 审计失败被吞掉 | 状态成功但事实缺失 | UnitOfWork 回滚并返回稳定 `AUDIT_*` |
| SQLite busy 后自动重试写副作用 | 重复执行/双计预算 | 不自动重试；重新读取并重新通过上层门禁 |
| 状态目录进入工作区 | Agent 可读或污染 diff | 配置阶段和 WP-01 真实打开前双重拒绝；同一 Git 根内只做精确排除 |
| Approval/Checkpoint 提前实现 | 跨包职责和 migration 冲突 | WP-01 只提供共享事务基础，不建其生命周期 API |
| 并发测试偶发失败 | 证据不可重复 | 显式 barrier、固定 Clock、独立连接、受控 busy timeout |

当前无规格阻塞项。T01 的唯一前置是 WP-00 v1.1.0 compatibility amendment 完成重新验收。

## 11. 下游交接

WP-01 验收后必须向 WP-02、WP-04、WP-07 交付：

- SQLite migration/checksum/事务和 UnitOfWork 使用方式；
- Run、Plan、Step、ToolInvocation、Evidence、Completion 的持久化与读取接口；
- 预算 ledger、usage 合并规则和 `STATE_BUDGET_EXCEEDED` 语义；
- 租约 acquire/renew/release 与所有 Run 写操作的前置条件；
- AuditEventDraft/Record、事件类型、关联字段和 chain verify；
- Redactor 的默认敏感规则、限制和失败关闭语义；
- 所有 `STATE_*`、`AUDIT_*`、`REDACTION_*` 错误码；
- 已覆盖/未覆盖场景、剩余风险和禁止绕过事项。

WP-02 必须在同一数据库上追加审批 migration；WP-07 必须追加 Checkpoint migration。两者不得修改
WP-01 migration、复制 StateStore 或绕过 UnitOfWork。

## 12. 实际完成情况

当前状态：规格已冻结、计划已批准，WP01-T01 和 T02 已完成，其余任务尚未实现。

| 项目 | 状态 |
| --- | --- |
| `spec.md` | frozen，v0.2.1 |
| `plan.md` | approved，v0.2.1 |
| SQLite/migration | T01 已完成；目标测试 `7 passed`，PRAGMA、checksum、回滚、路径隔离和权限通过 |
| StateStore/租约 | 未实现 |
| Redaction | T02 已完成；目标测试 `13 passed`，敏感值、凭据形态、循环和有界裁剪通过 |
| Audit append-only/哈希链 | 未实现 |
| 测试与验收报告 | 未开始 |

当前全量测试：`44 passed in 0.61s`；Ruff lint、格式检查和 `pip check` 均通过。

只有在 T02 至 T08 实现和实测验收完成后，才能更新本节并生成
`acceptance-report.md`。
