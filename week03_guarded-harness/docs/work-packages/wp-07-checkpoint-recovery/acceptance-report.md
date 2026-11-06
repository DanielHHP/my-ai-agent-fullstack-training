# WP-07 Checkpoint 与恢复工作包验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-07 Checkpoint 与恢复 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-06 |
| 对应规格 | [spec.md](spec.md) v1.1.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.1.0 / done |
| 代码基线 | `471863f86970772a8000a5725e0cc5ef733cb8dc` + 本工作包未提交改动 |
| 工作区状态 | 包含预期源码、测试和文档改动；无未解释临时文件 |
| 操作系统 | Darwin 22.6.0 x86_64 |
| Python | 3.13.5（项目 `.venv`） |
| 关键依赖 | `smolagents==1.26.0`、`jsonschema==4.26.0`、`pydantic==2.14.1`、`PyYAML==6.0.3`、`pytest==9.1.1`、`ruff==0.17.3` |

本报告只汇总 WP-07 实际交付与运行证据，不重新定义规格。WP-07 已完成数据库层 Checkpoint、
恢复规划、旧证据失效、Gate PREPARED 重入和 continuation context；Agent/Harness、CLI
`resume` 与真实操作系统进程恢复仍属于 WP-10/WP-11。

## 2. 验收范围

已验收能力：

- 版本化 Checkpoint 快照、canonical 哈希、追加式 SQLite 存储和重开读取；
- Run、step、计划、Invocation、Evidence、Approval、预算、stop reason、审计头和 workspace 摘要；
- `APPROVAL_REQUESTED` 待审批安全点和 `APPROVAL_SETTLED` 已落定安全点；
- W0-W11 恢复窗口、稳定恢复动作、租约接管和跨数据库重开；
- PREPARED 调用经 `Gate.reenter_prepared` 重新门禁；
- CLAIMED/PREPARED 通过 `resume_claim` 恢复，不重复调用 handler 或 ToolCall 预算；
- STARTED 无确定结果进入 `UNKNOWN_RESULT/BLOCKED`，禁止自动重放；
- 结果已提交但 Checkpoint 缺失时补建 Checkpoint，不重复执行 handler；
- 配置、内部/外围工作区、文件 Evidence 和 Audit 链变化时失败关闭；
- Evidence 单向 `true -> false` 失效与 `EVIDENCE_INVALIDATED` 审计；
- 有界、确定、脱敏的 continuation context 与 renderer；
- WP-07 domain、migration v5、公共导出、测试和安全/决策文档。

非目标：

- 不创建 Agent、Fake Model 运行闭环或 Harness 编排；
- 不实现 CLI `resume`、审批命令、等待轮询或日志 UI；
- 不执行 WP-08/WP-09 最终验收；
- 不运行真实模型 Provider 或网络请求；
- 不实现容器、虚拟机、分布式租约或多仓库恢复。

## 3. 交付物

| 交付物 | 结果 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/checkpoint.py` | PASS | Checkpoint、恢复规划、continuation、恢复记录 |
| `src/guarded_harness/domain.py` | PASS | WP-07 严格模型、D-58 边界、RecoveryId、错误码 |
| `src/guarded_harness/storage.py` | PASS | migration v5、checkpoints/recovery_records、append-only trigger |
| `src/guarded_harness/state_store.py` | PASS | Checkpoint 指针和 Evidence 单向失效 |
| `src/guarded_harness/sandbox.py` | PASS | 不强制干净的受控工作区快照与比较 |
| `src/guarded_harness/gate.py` | PASS | `reenter_prepared`、CLAIMED/APPROVED 恢复、预算与结果保护 |
| `src/guarded_harness/audit.py` | PASS | `EVIDENCE_INVALIDATED`、`RECOVERY_RECORDED` 和 audit head 读取 |
| `src/guarded_harness/__init__.py` | PASS | WP-07 公共契约导出 |
| `tests/test_state_checkpoint.py` | PASS | Checkpoint、migration、恢复矩阵、证据、租约、continuation |
| `tests/test_gate.py` | PASS | PREPARED/APPROVED/CLAIMED/STARTED 重入与不重放 |
| `tests/test_sandbox.py` | PASS | 预期脏工作区 Checkpoint 快照 |
| `tests/test_domain.py` | PASS | 新公共模型和 ID 契约 |
| `spec.md` / `plan.md` / 本报告 | PASS | 决策、行为、计划和实际证据一致 |

## 4. 执行证据

命令均在仓库根目录、项目 `.venv` 中执行：

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pip install -e '.[dev]'` | PASS；editable 构建与安装成功，依赖已满足 |
| `.venv/bin/python -m pip check` | PASS；`No broken requirements found.` |
| `.venv/bin/python -m pytest tests/test_state_checkpoint.py tests/test_gate.py -q` | PASS；`101 passed in 33.15s` |
| `.venv/bin/python -m pytest -q` | PASS；`249 passed in 61.15s`，0 failed，0 skipped |
| `.venv/bin/python -m ruff check .` | PASS；`All checks passed!` |
| `.venv/bin/python -m ruff format --check .` | PASS；格式检查无差异 |
| `git diff --check` | PASS；无空白错误 |

测试使用临时 Git 仓库、临时 SQLite、固定时钟、可控故障注入和本地 fake handler，不调用真实模型、
不访问外部网络、不使用用户凭据。

## 5. 规格场景映射

| 场景 | 测试/证据 | 结果 |
| --- | --- | --- |
| WP07-AC-001 Checkpoint 正常路径 | `test_checkpoint_round_trip_hash_and_reopen` | PASS；快照、Run 指针和审计同事务，重开可读 |
| WP07-AC-002 快照字段完整性 | `test_checkpoint_round_trip_hash_and_reopen`、`test_recovery_pending_approval_waits_and_requested_checkpoint_round_trips`、`test_recovery_with_committed_result_and_checkpoint_continues` | PASS；Run/step/plan/call/evidence/approval/budget/workspace 均覆盖 |
| WP07-AC-003 canonical hash | `test_checkpoint_round_trip_hash_and_reopen`、`test_continuation_is_deterministic_and_secret_free` | PASS；重开 digest 一致，context 逐字节稳定 |
| WP07-AC-004 Checkpoint 篡改 | `test_checkpoint_tamper_and_append_only_fail_closed` | PASS；未知 schema/篡改内容失败关闭 |
| WP07-AC-005 append-only | `test_checkpoint_tamper_and_append_only_fail_closed`、`test_recovery_records_are_append_only` | PASS；UPDATE/DELETE trigger 拒绝 |
| WP07-AC-006 写事务失败 | `test_checkpoint_fault_rolls_back_snapshot_pointer_and_audit` | PASS；注入审计失败后全部回滚 |
| WP07-AC-007 W0 无意图 | `test_recovery_no_pending_work_continues_agent` | PASS；`CONTINUE_AGENT`，不调用 handler |
| WP07-AC-008 W1 PREPARED 无审批 | `test_recovery_prepared_reenters_gate_action`、`test_reenter_prepared_revalidates_and_executes_once` | PASS；计划与 Gate 重检均生效 |
| WP07-AC-009 W2 APPROVED + PREPARED | `test_recovery_approved_prepared_requires_gate_reentry`、`test_reenter_approved_prepared_claims_and_records_budget_once` | PASS；claim、预算一次、执行一次 |
| WP07-AC-010 W3 CLAIMED + PREPARED | `test_recovery_claimed_prepared_requires_resume_claim`、`test_reenter_claimed_prepared_resumes_without_double_budget` | PASS；`resume_claim` 审计存在，预算不重复 |
| WP07-AC-011 W4/W5 STARTED 无结果 | `test_recovery_started_marks_unknown_without_replay`、`test_reenter_started_is_rejected_without_replay` | PASS；UNKNOWN/BLOCKED，副作用调用计数不增加 |
| WP07-AC-012 W6 结果已提交、Checkpoint 缺失 | `test_recovery_w6_rebuilds_missing_checkpoint_without_replay` | PASS；补建 sequence 2，handler 不重跑 |
| WP07-AC-013 W7 结果与 Checkpoint 均存在 | `test_recovery_with_committed_result_and_checkpoint_continues` | PASS；复用结果后继续 |
| WP07-AC-014 W8 待审批 | `test_recovery_pending_approval_waits_and_requested_checkpoint_round_trips` | PASS；保持 WAITING，不自动批准 |
| WP07-AC-015 W9 UNKNOWN 人工复核 | `test_recovery_started_marks_unknown_without_replay` 的重复恢复部分 | PASS；重复恢复仍为 manual，不重放 |
| WP07-AC-016 配置变化 | `test_recovery_config_mismatch_blocks_and_invalidates_approval` | PASS；BLOCKED，审批 INVALIDATED |
| WP07-AC-017 工作区/外围变化 | `test_recovery_external_workspace_change_blocks`、`test_recovery_invalidates_stale_file_evidence_and_blocks` | PASS；`BLOCKED/WORKSPACE_CHANGED` |
| WP07-AC-018 文件 Evidence 版本变化 | `test_recovery_invalidates_stale_file_evidence_and_blocks` | PASS；单向失效并写审计 |
| WP07-AC-019 审批过期/撤销/绑定变化 | WP-02 既有审批测试及 `test_recovery_claimed_prepared_requires_resume_claim` | PASS；旧审批不可重新授权 |
| WP07-AC-020 预算跨重开 | `test_recovery_lease_takeover_preserves_budget_after_reopen` | PASS；usage=1，remaining=19 |
| WP07-AC-021 租约并发/接管 | `test_recovery_lease_takeover_preserves_budget_after_reopen`、WP-01 lease 测试 | PASS；旧 token 被 fencing，新 owner 恢复 |
| WP07-AC-022 Audit 链损坏 | `test_recovery_audit_corruption_blocks_before_state_mutation` | PASS；状态保持 RUNNING，返回恢复审计失败 |
| WP07-AC-023 continuation 有界确定 | `test_continuation_is_deterministic_and_secret_free`、`test_checkpoint_and_context_redact_model_generated_text` | PASS；同输入同结果，secret 不出现 |
| WP07-AC-024 人工复核入口 | `test_recovery_started_marks_unknown_without_replay`、`test_recovery_already_terminal_returns_read_only_disposition` | PASS；仅返回事实，无 handler 授权 |
| WP07-AC-025 离线重复性 | 全量 pytest、固定 Clock、临时 SQLite/Git、mock/fake handler | PASS；0 skip，无网络/真实模型 |

额外覆盖：

- `test_checkpoint_migration_upgrades_v4_and_preserves_runs`：v4 数据库前向升级 v5；
- `test_recovery_multiple_prepared_calls_blocks_as_ambiguous`：W10 冲突状态失败关闭；
- `test_recovery_plan_becomes_stale_after_state_revision_changes`：过期恢复计划不能生成 context；
- `test_approval_migration_upgrades_v3_and_preserves_runs`：v3 数据库可升级到 v5。

## 6. 任务卡映射

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| WP07-T00 | DONE | D-53 至 D-58，spec v1.1.0 frozen，plan v1.1.0 approved |
| WP07-T01 | DONE | Checkpoint/Recovery/Continuation 模型、RecoveryId、错误码 |
| WP07-T02 | DONE | migration v5、append-only trigger、integrity required tables |
| WP07-T03 | DONE | Sandbox 写入后工作区快照与比较 |
| WP07-T04 | DONE | canonical hash、幂等 Checkpoint、查询/重开 |
| WP07-T05 | DONE | Run pointer、Evidence 单向失效和审计 |
| WP07-T06 | DONE | W0-W11 恢复规划 |
| WP07-T07 | DONE | STARTED -> UNKNOWN、BLOCKED、审批失效原子收口 |
| WP07-T08 | DONE | W6 Checkpoint 补建 |
| WP07-T09 | DONE | Gate `reenter_prepared`、claim 恢复、预算一次 |
| WP07-T10 | DONE | bounded continuation context 与 renderer |
| WP07-T11 | DONE | 101 项 state/gate 目标测试、249 项全量测试 |
| WP07-T12 | DONE | 本报告 accepted，索引和下游交接已更新 |

## 7. 安全与故障矩阵

| 注入/边界 | 证据 | 结论 |
| --- | --- | --- |
| v3/v4 数据库升级 v5 | migration 测试 | 历史 Run 保留，旧 migration checksum 不变 |
| Checkpoint 内容/关联/schema 篡改 | tamper 测试 | 失败关闭，不把损坏记录当不存在 |
| UPDATE/DELETE Checkpoint/recovery | append-only 测试 | SQLite trigger 拒绝 |
| Checkpoint 审计写失败 | fault rollback 测试 | Snapshot、Run pointer、Audit 全部回滚 |
| PREPARED 直接执行风险 | Gate reentry 测试 | 必须重新经过 schema/权限/risk/repeat/budget |
| CLAIMED 重复执行风险 | 双预算/审批测试 | `resume_claim`，不重复预算，结果只一次 |
| STARTED 未知副作用 | Recovery/Gate 测试 | UNKNOWN + BLOCKED，不重放 |
| 结果已提交但无 Checkpoint | W6 测试 | 补建索引，不重复 handler |
| 配置/外围变化 | blocked 测试 | 审批失效，`WORKSPACE_CHANGED` |
| 文件内容漂移 | Evidence 测试 | 旧 Evidence 单向失效并审计 |
| Audit 链损坏 | corruption 测试 | 恢复失败关闭，不改 Run |
| 租约过期 | takeover 测试 | 新 owner 可恢复，旧 token fencing |
| 模型生成敏感文本 | redaction 测试 | Checkpoint/context 中不出现 secret |
| stale recovery plan | revision 测试 | 不能生成 continuation，必须重新规划 |

## 8. 飞书“核心验收”映射

| 编号 | 本包结论 | 证据/边界 |
| --- | --- | --- |
| C1 | PASS | 每类安全边界的 Checkpoint schema、原子写入、审计和重开均有测试；Agent 调用点由 WP-10 接线 |
| C2 | PASS | STARTED 无确定结果原子进入 UNKNOWN/BLOCKED，恢复与 Gate 测试均证明不重放 |
| C3 | PASS（WP-07 范围） | SQLite 重开、租约接管、结果复用和预算不重置已验证；真实 CLI 新进程由 WP-11 收口 |
| C4 | PASS | 配置、内部/外围工作区、Evidence 和审批变化均阻塞或失效 |
| A2 | PASS（恢复链支撑） | recovery record、Checkpoint、Gate、State、Approval、Evidence 通过稳定 ID 关联 |
| H2 | PASS（证据失效部分） | 源码版本变化使旧文件 Evidence 失效；最终测试报告判定仍由 WP-09 负责 |
| T1 | PASS（本包） | 249 项全量测试，0 failed，0 skipped，离线可重复 |

## 9. 限制与剩余风险

- WP-07 提供 Checkpoint/恢复 API，但还不自动在每个 Agent 安全边界调用；WP-10 必须按设计调用并
  补充真实运行链测试。
- 跨 SQLite 重开和租约接管在本包使用新 storage/service 实例验证；真正的新操作系统进程、CLI
  `resume` 和审批等待属于 WP-11。
- W6 的差异归属允许已提交 COMMAND/TEST_REPORT Evidence 解释工作区变化。最终 diff 仍需 WP-09
  的测试修复验收和 WP-12 故障矩阵复核。
- Checkpoint 保留全部历史，P0 不提供自动清理；数据库容量增长由后续运维策略处理。
- `UNKNOWN_RESULT`/`BLOCKED` 不允许同一 Run 自动恢复；人工核对后应启动新 Run。
- Sandbox 仍不提供 OS 级文件系统或网络隔离，测试命令只可用于可信仓库。

## 10. 验收结论

**ACCEPTED。**

WP-07 的冻结规格场景均有自动化证据，101 项 state/gate 目标测试和 249 项全量测试通过，Ruff
lint、格式检查和依赖检查通过。工作包达到 DONE 条件，可以进入 WP-08 验收引擎与代码理解工作。
