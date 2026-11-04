# WP-02 审批生命周期验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-02 审批生命周期 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-04 |
| 对应规格 | [spec.md](spec.md) v0.2.0 / frozen |
| 对应计划 | [plan.md](plan.md) v0.2.0 / done |
| 仓库基线 | `da1adba157ece847d4a53a0819a73da1c6be608f` + WP-02 工作区改动 |
| 基线工作区 | 验收前存在 WP-02 未提交改动；报告如实记录当前工作树 |
| 验收环境 | macOS Darwin x86_64；Python 3.13.5；pip 25.1.1 |

本报告以实际测试、SQLite migration/约束、故障注入和命令输出为证据。它只验收 WP-02 的审批生命
周期，不把审批 API 的存在解释为 Gate、真实写入、CLI 或 Checkpoint 已完成。

## 2. 验收范围

### 2.1 已验收能力

- Approval schema v2、审批请求/角色/Channel 公共契约；
- `approver_roles`、`ttl_seconds` 配置校验和配置指纹；
- canonical args digest、完整 binding digest 和有界 JSON 校验；
- SQLite migration v4、审批表约束、绑定不可变与历史不可删除；
- 创建、查询、批准、拒绝、过期、撤销、claim、resume_claim 和 consume；
- ToolInvocation 审批关联、RunSnapshot 审批投影和跨连接重开；
- 审批与审计同事务提交，以及审批写/审计失败回滚；
- 并发 claim 单一赢家、跨上下文绑定失败和终态重放拒绝；
- 确定 `SUCCEEDED/FAILED/TIMED_OUT` 消费审批，`UNKNOWN_RESULT` 保持不可重放；
- 拒绝/过期与 Run `STOPPED` 及新增 StopReason 的受控同事务编排；
- disabled/fake Channel 的失败关闭边界和审批 API 无 handler/文件副作用。

### 2.2 非目标与下游责任

- WP-03：真实路径、Git、命令和进程 Sandbox；
- WP-04：Gate 固定检查链、真实 handler 执行和重复调用门禁；
- WP-06：真实写入、patch、测试进程、超时和 UNKNOWN_RESULT 执行；
- WP-07：Checkpoint 快照、跨进程恢复动作和 continuation context；
- WP-10：`ToolCallingAgent` 适配和 Harness 编排；
- WP-11：CLI 审批、resume 和等待模式；
- WP-12：全局故障矩阵和两类任务端到端验收。

## 3. 交付物

| 交付物 | 位置 | 结果 |
| --- | --- | --- |
| 审批生命周期 | `src/guarded_harness/approval.py` | PASS |
| 公共契约和错误码 | `src/guarded_harness/domain.py`、`src/guarded_harness/__init__.py` | PASS |
| 审批配置 | `src/guarded_harness/config.py`、配置 Fixture | PASS |
| 审计事件 | `src/guarded_harness/audit.py` | PASS |
| migration v4 与完整性检查 | `src/guarded_harness/storage.py` | PASS |
| RunSnapshot 审批投影 | `src/guarded_harness/state_store.py`、`domain.py` | PASS |
| 审批、并发、恢复和安全测试 | `tests/test_state_checkpoint.py` | PASS |
| 契约测试 | `tests/test_domain.py`、`tests/test_config.py` | PASS |
| 规格、计划和本报告 | `docs/work-packages/wp-02-approval-lifecycle/` | PASS |

## 4. 实际执行证据

所有命令在工作仓库根目录执行，使用项目 `.venv`。

| 检查 | 命令 | 实际结果 |
| --- | --- | --- |
| Editable 安装 | `python -m pip install -e '.[dev]'` | PASS；首次沙箱内因构建依赖网络受限失败，获得网络授权后成功 |
| WP-02 目标测试 | `python -m pytest tests/test_state_checkpoint.py -q` | `46 passed in 2.68s` |
| 全量测试 | `python -m pytest -q` | `93 passed in 3.06s` |
| Lint | `python -m ruff check .` | PASS；`All checks passed!` |
| 格式 | `python -m ruff format --check .` | PASS；`31 files already formatted` |
| 依赖一致性 | `python -m pip check` | PASS；`No broken requirements found.` |

测试无失败、error、skip 或 xfail。目标测试和全量测试均不访问真实模型、网络或用户凭据。

## 5. 规格场景映射

| 规格场景 | 结果 | 主要测试证据 |
| --- | --- | --- |
| WP02-AC-001 创建并重开 | PASS | `test_approval_round_trip_snapshot_and_reopen` |
| WP02-AC-002 绑定篡改 | PASS | `test_approval_rejects_binding_mismatch_and_self_approval`、`test_stale_binding_invalidates_and_blocks_run` |
| WP02-AC-003 摘要确定性 | PASS | `test_approval_digest_is_canonical_and_bounded` |
| WP02-AC-004 决定状态机 | PASS | `test_approval_roles_and_happy_path`、`test_resume_claim_and_deterministic_consume` |
| WP02-AC-005 审批角色 | PASS | `test_approval_roles_and_happy_path`、`test_approval_rejects_binding_mismatch_and_self_approval` |
| WP02-AC-006 过期 | PASS | `test_approval_expiry_stops_run_atomically`、`test_expired_approval_cannot_be_approved_or_claimed` |
| WP02-AC-007 并发 claim | PASS | `test_concurrent_claim_has_one_winner` |
| WP02-AC-008 跨上下文 | PASS | `test_stale_binding_invalidates_and_blocks_run`、`test_approval_request_actor_must_match_unit_of_work` |
| WP02-AC-009 确定性 consume | PASS | `test_resume_claim_and_deterministic_consume`、`test_deterministic_failures_consume_approval`、`test_unknown_result_keeps_approval_claimed` |
| WP02-AC-010 撤销 | PASS | `test_stale_binding_invalidates_and_blocks_run`、`test_started_invocation_cannot_be_invalidated` |
| WP02-AC-011 审计链 | PASS | `test_approval_audit_has_required_correlation_and_valid_chain`、`test_resume_claim_and_deterministic_consume` |
| WP02-AC-012 状态/审计原子性 | PASS | `test_approval_fault_rolls_back_status_and_audit` |
| WP02-AC-013 幂等与冲突 | PASS | `test_duplicate_approval_requests_are_idempotent_or_conflict` |
| WP02-AC-014 无执行副作用 | PASS | `test_approval_api_does_not_touch_repository_files` |
| WP02-AC-015 损坏数据 | PASS | `test_corrupt_approval_row_fails_closed`、`test_approval_rows_are_binding_immutable_and_not_deletable` |
| WP02-AC-016 G3/G4 部分证据 | PASS | `test_approval_api_does_not_touch_repository_files`、`test_concurrent_claim_has_one_winner` |
| WP02-AC-017 创建/等待原子性 | PASS | `test_approval_creation_rolls_back_with_run_transition` |
| WP02-AC-018 claim/执行边界 | PASS | `test_resume_claim_and_deterministic_consume`、`test_claim_rolls_back_when_run_transition_fails` |
| WP02-AC-019 claim 崩溃恢复 | PASS | `test_resume_claim_and_deterministic_consume` |
| WP02-AC-020 拒绝/过期收口 | PASS | `test_approval_rejection_stops_run_atomically`、`test_approval_expiry_stops_run_atomically` |
| WP02-AC-021 陈旧绑定收口 | PASS | `test_stale_binding_invalidates_and_blocks_run` |

## 6. 任务卡验收

| 任务 | 状态 | 依据 |
| --- | --- | --- |
| WP02-T01 公共契约与摘要 | DONE | Approval v2、角色/TTL、StopReason、错误码和摘要测试 |
| WP02-T02 migration v4 | DONE | v3→v4 升级、Run rebuild、触发器、外键检查和重开测试 |
| WP02-T03 创建/查询/Snapshot | DONE | create/load/list、Invocation 关联、重开和快照测试 |
| WP02-T04 决定/角色/过期 | DONE | 角色、自批、revision、拒绝和过期测试 |
| WP02-T05 claim/consume/失效 | DONE | 一次性 claim、resume、确定性结果、UNKNOWN 和撤销边界 |
| WP02-T06 并发/事务/故障 | DONE | 双连接并发、审批/审计回滚和 claim/Run 回滚测试 |
| WP02-T07 Channel/无副作用 | DONE | disabled Channel、fake 边界和文件不变测试 |
| WP02-T08 文档与验收 | DONE | spec/plan/索引/本报告和上述复跑证据 |

## 7. 安全与故障矩阵

| 边界 | 验证 |
| --- | --- |
| 参数或工具绑定不匹配 | `APPROVAL_BINDING_MISMATCH`，不执行 handler |
| 工作区/配置指纹变化 | claim 拒绝；旧审批可 `INVALIDATED`，Run 可 `BLOCKED` |
| 过期 | `EXPIRED` 持久化并写审计；拒绝/过期可原子停止 Run |
| 并发 claim | 两个连接仅一个 winner；另一方 `APPROVAL_ALREADY_CLAIMED` |
| 已消费/终态重放 | claim/create 重放拒绝，不生成新执行意图 |
| `UNKNOWN_RESULT` | 保持 `CLAIMED`，consume/resume_claim 均拒绝 |
| 审批写后审计失败 | UnitOfWork 回滚审批 revision 和审计 |
| Run 状态更新失败 | 审批创建/claim 随同一事务回滚 |
| SQL 修改审批绑定 | `APPROVAL_BINDING_IMMUTABLE` trigger 拒绝 |
| SQL 删除审批历史 | `APPROVAL_HISTORY_APPEND_ONLY` trigger 拒绝 |
| 损坏角色 JSON | 读取失败关闭为 `APPROVAL_INVALID` |
| Channel 不可用 | `APPROVAL_CHANNEL_UNAVAILABLE`，不执行 handler |
| 目标仓库文件 | 审批 API 全程不改变文件 |

## 8. 飞书核心验收映射

| 飞书编号 | WP-02 证据 | 状态 | 下游 |
| --- | --- | --- | --- |
| G3 未审批写入 | PENDING 创建无文件副作用；Run 等待状态由调用方同事务编排 | 部分 PASS | WP-04/WP-06 负责 Gate 与真实文件不变 |
| G4 审批后一次性 | 批准后 claim 只有一个赢家，终态重放拒绝 | 部分 PASS | WP-04/WP-06/WP-11 负责真实 handler 一次执行 |
| C1 Checkpoint 待审批动作 | RunSnapshot 提供完整审批投影 | 基础 PASS | WP-07 生成 Checkpoint |
| C4 工作区变化 | 绑定摘要、失效和 Run BLOCKED 路径 | 部分 PASS | WP-07 恢复判定 |
| A2 全链路关联 | 审批事件含 run/tool/approval/actor/rule | PASS | WP-12 端到端 |
| T1 确定性测试 | 46 项目标测试和 93 项全量测试通过 | PASS | WP-12 全局矩阵 |

## 9. 限制与剩余风险

1. Gate、真实 handler、CLI 和 Checkpoint 尚未实现，因此 G3/G4/C4 只能标记为审批层部分证据。
2. 本地 actor 是可信但未认证身份；P0 不提供密码学身份认证，外部 Channel 未启用。
3. `ApprovalStore` 负责审批状态，拒绝/过期与 Run 停止需要 Gate/Harness 在同一 UnitOfWork
   中编排；WP-04/WP-07 必须再次验证，不能只依赖审批表。
4. 参数摘要使用未脱敏规范化参数的 SHA-256；对于低熵秘密仍存在持有本地数据库后的离线猜测风险。
5. 当前只在 Python 3.13.5/macOS x86_64 运行；Python 3.10–3.12 和 Windows 运行时未独立验证。
6. 未实现外部审批网络、真实模型 Provider 或端到端任务完成验收。

## 10. 验收结论

**WP-02 审批生命周期：ACCEPTED。**

冻结规格中的 21 个验收场景、T01-T08、目标测试、全量测试、Ruff、格式和依赖检查均有实际复跑
证据。WP-04、WP-06、WP-07 和 WP-11 可以在 D-23 至 D-28 的审批契约上继续施工。
