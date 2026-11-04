# WP-01 持久化与审计底座验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-01 持久化与审计底座 |
| 报告状态 | accepted |
| 报告版本 | 1.1.0 |
| 验收日期 | 2026-11-04 |
| 对应规格 | [spec.md](spec.md) v0.3.0 / frozen |
| 对应计划 | [plan.md](plan.md) v0.3.0 / done |
| 代码基线 | `8e61d9d1a37dde82c697f0ca4547e69900d1fcac` |
| 基线工作区 | 代码基线提交时干净，无未提交修改 |
| 验收环境 | macOS Darwin x86_64；Python 3.13.5；pip 25.1.1 |

本报告以自动化测试、数据库约束、故障注入和实际命令输出为证据，不把模型自述或“文件存在”作为
能力完成的判据。

## 2. 验收范围

### 2.1 已验收能力

- SQLite 连接、WAL、外键、busy timeout、只追加 migration 和 checksum；
- 状态目录工作区隔离与 POSIX owner/type/mode 检查；
- Run、RunPlan、Step、ToolInvocation、单生产者 Evidence、预算 ledger 和 Completion 持久化；
- Run/Step/ToolInvocation 合法状态机、乐观 revision 和基础 Run 租约；
- 过期租约接管、续租、释放和旧 token fencing；
- append-only 审计、连续序号、canonical JSON、哈希链、关联查询和篡改检测；
- 统一脱敏、循环引用和有界输出；
- 状态与审计同事务提交/回滚，以及多连接并发追加和写冲突；
- 固定迁移顺序、确定性时钟和故障注入测试。

### 2.2 非目标与下游责任

- WP-02：审批创建、批准、拒绝、claim、consume 和过期；
- WP-03：真实路径、Git、命令、进程和敏感环境变量 Sandbox；
- WP-04：Gate 固定检查顺序和真实执行 handler；
- WP-07：Checkpoint、崩溃恢复动作和 continuation context；
- WP-08/WP-09：两类任务 Completion 策略；
- WP-10/WP-11：Harness 独立心跳驱动、Agent、CLI 和跨进程恢复；
- WP-12：全局故障矩阵和最终交付。

## 3. 交付物

| 交付物 | 位置 | 结果 |
| --- | --- | --- |
| SQLite/migration/事务 | `src/guarded_harness/storage.py` | PASS |
| 业务状态持久化 | `src/guarded_harness/state_store.py` | PASS |
| append-only 审计 | `src/guarded_harness/audit.py` | PASS |
| 脱敏与受限规范化 | `src/guarded_harness/redaction.py` | PASS |
| 公共模型和错误码 | `src/guarded_harness/domain.py`、`__init__.py` | PASS |
| 状态、租约和迁移测试 | `tests/test_state_checkpoint.py` | PASS |
| 审计、事务、并发测试 | `tests/test_audit.py` | PASS |
| 脱敏边界测试 | `tests/test_redaction.py` | PASS |
| 包内规格、计划和本报告 | `docs/work-packages/wp-01-persistence-audit/` | PASS |

## 4. 实际执行证据

所有命令均在仓库根目录执行，使用项目 `.venv`。

| 检查 | 命令 | 实际结果 |
| --- | --- | --- |
| Editable 安装 | `python -m pip install -e '.[dev]'` | PASS |
| WP-01 目标测试 | `python -m pytest tests/test_state_checkpoint.py tests/test_audit.py -q` | `27 passed in 1.55s` |
| 全量测试 | `python -m pytest -q` | `65 passed in 1.76s` |
| Lint | `python -m ruff check .` | PASS；`All checks passed!` |
| 格式 | `python -m ruff format --check .` | `28 files already formatted` |
| 依赖一致性 | `python -m pip check` | `No broken requirements found.` |
| 补丁空白检查 | `git diff --check` | PASS，无输出 |

目标测试和全量测试均无失败、error、skip 或 xfail。并发测试使用独立 SQLite 连接，不依赖真实网络、
模型、用户凭据或 sleep。

## 5. 规格场景映射

| 规格场景 | 结果 | 主要证据 |
| --- | --- | --- |
| WP01-AC-001 初始化 | PASS | WAL、外键、busy timeout、migration 和强制表检查 |
| WP01-AC-002 重复初始化/checksum | PASS | 幂等 migration、版本过高、低版本元数据和 checksum 拒绝 |
| WP01-AC-003 重开读取 | PASS | Run/Plan/Step/Tool/Evidence/Completion close/reopen |
| WP01-AC-004 Run 状态机 | PASS | 合法 RUNNING 转换；非法 NEW→STOPPED 拒绝且状态不变 |
| WP01-AC-005 Step/Tool 状态机 | PASS | 非法 Step 跳转、终态回退、非幂等 timeout→UNKNOWN 约束 |
| WP01-AC-006 乐观并发 | PASS | 并行写者同一旧 revision，单一成功、另一得到 `STATE_CONFLICT` |
| WP01-AC-007 预算/H3 | PASS | 四维 Decimal 累计、幂等 replay、异载荷冲突、越界拒绝、ledger 汇总 |
| WP01-AC-008 租约 | PASS | 续租延长期限、有效租约阻止第二 owner、过期接管、旧 token fencing |
| WP01-AC-009 审计链 | PASS | 连续 sequence、prev/event hash 重算、确定性 event ID |
| WP01-AC-010 append-only | PASS | UPDATE/DELETE trigger；DROP trigger 后被 `verify_chain` 检出 |
| WP01-AC-011 事务回滚 | PASS | 审计失败回滚 Run/Lease；状态失败回滚先写审计；commit 前故障全部回滚 |
| WP01-AC-012 A1/A2 关联 | PASS | rule、decision、actor、tool_call_id、approval_id schema 与查询 |
| WP01-AC-013 脱敏 | PASS | 敏感键、显式值、Bearer/JWT/PEM/常见 token、状态和审计集成 |
| WP01-AC-014 有界/循环 | PASS | 深度、节点、行、字节、循环和 unsupported object |
| WP01-AC-015 并发追加 | PASS | 三连接并发 30 条事件，sequence 1..32 连续且 chain 有效 |
| WP01-AC-016 故障确定性 | PASS | 固定 Clock、显式 barrier、注入点和调用计数，无睡眠 |
| WP01-AC-017 职责边界 | PASS | 未创建 Approval/Checkpoint 生命周期 API 或 Gate handler |
| WP01-AC-018 RunPlan | PASS | null/非空、连续 ordinal、32 KiB、幂等 no-op 和 revision |
| WP01-AC-019 目录/权限 | PASS | 配置层工作区隔离；存储层复验、owner/type/mode 失败关闭 |
| WP01-AC-020 Evidence 单一生产者 | PASS | junction 删除；派生投影顺序稳定；跨调用隔离；Completion-only 和冲突迁移拒绝 |

## 6. 任务卡验收

| 任务 | 状态 | 依据 |
| --- | --- | --- |
| WP01-T01 SQLite/migration | DONE | migration v1/v2、PRAGMA、checksum、回滚、路径和权限测试 |
| WP01-T02 Redaction | DONE | 敏感值、常见凭据、循环和有界裁剪测试 |
| WP01-T03 Run/Plan/Step | DONE | 事务、状态机、revision、快照和重开测试 |
| WP01-T04 Tool/Evidence/Budget/Completion | DONE | 生命周期、单生产者 Evidence、四维预算、Completion 和重开测试 |
| WP01-T05 Lease | DONE | 续租、过期接管、释放、旧 token 和心跳策略测试 |
| WP01-T06 Audit | DONE | 自动领域事件、哈希链、查询、trigger 和脱敏测试 |
| WP01-T07 Atomicity/Concurrency | DONE | 双向回滚、commit 故障、并发追加和并行 revision 测试 |
| WP01-T08 Docs/Acceptance | DONE | spec/plan、索引、报告和实际复跑证据同步 |

## 7. 安全与故障矩阵

| 边界 | 验证 |
| --- | --- |
| migration 损坏/版本不一致 | 打开和迁移均失败关闭 |
| 状态目录在 workspace 内 | 配置与存储层均拒绝，不创建数据库 |
| POSIX 权限过宽 | 拒绝，且不静默修改已有状态目录 |
| 非法状态转换 | 拒绝且事务回滚 |
| 过期/错误/被接管 token | 写、续租和释放均拒绝 |
| 状态成功、审计失败 | Run/Lease/Plan/State 全部回滚 |
| 审计已写、状态失败 | 审计随事务回滚 |
| commit 前进程退出 | 状态、审计和关联行一起回滚 |
| 并发审计写入 | sequence 连续、event ID 唯一、chain 有效 |
| 审计直接篡改 | trigger 拒绝；绕过 trigger 后链校验失败 |
| Evidence 双重归属 | junction 表已删除；Evidence 唯一生产者，Invocation evidence_ids 为派生投影 |
| secret 泄漏 | 参数、结果、错误摘要和审计摘要统一脱敏 |

## 8. 飞书核心验收映射

| 飞书编号 | WP-01 证据 | 状态 | 下游 |
| --- | --- | --- | --- |
| A1 | Gate 拒绝事件 schema、rule/decision/actor/tool_call_id 查询和脱敏 | WP-01 部分 PASS | WP-04 产生真实 Gate 拒绝 |
| A2 | Run/Step/Tool/Evidence/Approval 关联键和稳定查询 | 契约 PASS | WP-12 端到端 |
| H3 | 四维预算持久化、越界拒绝、`STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED` | 持久化 PASS | WP-04/WP-10 执行门禁 |
| C1 | Run、计划、Step、Evidence、预算和 Tool 状态一致性快照 | 基础 PASS | WP-07 Checkpoint |
| C2 | STARTED/UNKNOWN_RESULT 持久化和非幂等 timeout 禁止普通超时 | 持久化 PASS | WP-06/WP-07 |
| C3 | 跨连接重开、预算不重置、租约单写者 | 基础 PASS | WP-07/WP-11 |
| C4 | 工作区指纹可持久化；旧证据/审批失效判定未实现 | 部分 | WP-07 |
| T1 | WP-01 目标与全量测试确定性通过 | PASS | WP-12 全局 |

## 9. 限制与剩余风险

1. 当前只在 Python 3.13.5/macOS x86_64 运行；Python 3.10-3.12 和 Windows runtime 未单独验证。
2. Windows ACL 未实现等价强隔离；POSIX owner/mode 是当前已验收边界。
3. 并发测试覆盖独立连接和线程，不是 WP-07 要求的完整崩溃恢复矩阵。
4. WP-01 只提供租约原语；Harness 独立心跳、handler 取消和 `STARTED` 恢复动作由 WP-10/WP-07
   实现。
5. 同一 `git_root` 内、workspace 外的状态目录排除属于 WP-03；本包只保证目录位于 workspace 外。
6. Gate、Sandbox、真实工具、审批消费、Checkpoint、Completion 策略和 Agent Loop 不在本包。
7. Evidence 只支持一个生产 ToolCall；Completion 可以复用 Evidence ID，但同一工具结果需要跨调用
   共享时，应由后续需求显式增加带关系类型的模型，不能复用已删除的通用 junction。

## 10. 验收结论

**WP-01 持久化与审计底座：ACCEPTED。**

冻结规格中的 WP-01 场景、T01-T08、目标测试、全量测试、lint、格式和依赖检查均有实际复跑证据。
WP-02 可以在本报告、StateStore UnitOfWork、migration 和 AuditStore 契约上继续施工。
