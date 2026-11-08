# WP-12 故障加固与交付验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-12 故障加固与交付 |
| 报告状态 | accepted |
| 验收结论 | accepted：WP-08 至 WP-11 已 accepted，WP-12 项目级故障矩阵、E2E 和人工验收全部通过 |
| 报告版本 | 1.1.0 |
| 验收日期 | 2026-11-08 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / done |
| 代码基线 | `5a15b9a3fe75f2abf87c5b1135020776fda43660`；验收后更新工作包状态文档 |
| 工作区状态 | 代码与测试已提交；当前仅有本次验收状态文档更新 |
| 操作系统 | Darwin 22.6.0 x86_64 |
| Python | 3.13.5 |
| pip | 25.1.1 |
| 关键依赖 | `smolagents==1.26.0`、`pydantic==2.14.1`、`jsonschema==4.26.0`、`PyYAML==6.0.3`、`pytest==9.1.1`、`ruff==0.17.3` |

## 2. 验收范围与结论

本报告验收：

- WP-12 文档、正式样例、测试专用故障注入辅助；
- 硬进程退出后的未知结果恢复；
- DB 锁、Checkpoint 损坏、租约冲突和审计失败语义；
- 代码理解与测试修复两类正式样例 E2E；
- 六个必须测试文件、全量测试、Ruff lint、格式和复跑；
- 测试/运维文档、根 README 和需求/验收映射。

本报告仍不验收：

- 真实模型 Provider 或网络能力（D-07，P1）；
- 多仓库、分布式、Web/TUI、隐藏验收或容器级隔离；
- P1 的真实模型质量与跨平台未覆盖范围。

WP-08 至 WP-11 已分别 accepted，项目级依赖闭环完成。

结论为 `accepted`。WP-00 至 WP-11 已全部 accepted，WP-12 实现、自动化证据、故障矩阵、
两类任务 E2E 和 2026-11-08 人工验收均通过，项目 P0 可以标记完成。

## 3. 交付物

| 交付物 | 状态 | 说明 |
| --- | --- | --- |
| [spec.md](spec.md) | PASS | v1.0.0 frozen，含飞书 P0/P1 全部条目和 WP12-AC |
| [plan.md](plan.md) | PASS | v1.0.0 done，含 T00-T13、崩溃矩阵、测试和回滚 |
| `examples/config/code-understanding.yaml` | PASS | 正式加载器校验，相对路径、状态目录在工作区外 |
| `examples/config/test-repair.yaml` | PASS | 正式加载器校验，失败基线到最终通过 |
| `examples/workspaces/code-understanding/` | PASS | 最小只读源码样例 |
| `examples/workspaces/test-repair/` | PASS | 最小失败 pytest 样例，既有测试只读 |
| `tests/fault_injection.py` | PASS | 测试专用故障点、硬退出子进程、DB 锁和最小环境 |
| `tests/test_fault_recovery.py` | PASS | 9 项 WP-12 项目级故障/E2E 测试 |
| [docs/test-plan.md](../../test-plan.md) | PASS | 测试层级、六文件、飞书/故障/证据矩阵 |
| [docs/operations.md](../../operations.md) | PASS | 运行、审批、恢复、状态、日志、退出码和禁止操作 |
| [docs/manual-acceptance-wp08-wp11.md](../../manual-acceptance-wp08-wp11.md) | PASS | WP-08 至 WP-11 环境准备、人工命令、观察项和记录模板 |
| `README.md` | PASS | 入口概览、当前范围、限制和 docs 链接 |
| `docs/README.md`、工作包索引、任务拆解 | PASS | 已同步 WP-12 文档和状态 |

## 4. 执行证据

### 4.1 安装

命令：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

结果：PASS。editable package 构建并安装成功，所有声明依赖已满足。

### 4.2 WP-12 目标测试

命令：

```bash
python -m pytest tests/test_fault_recovery.py -q
```

首次和复跑结果：

```text
9 passed in 39.74s
9 passed in 19.76s
```

结论：PASS，目标测试确定性复跑一致。

### 4.3 全量测试

命令：

```bash
python -m pytest -q
```

结果：

```text
372 passed in 257.41s (0:04:17)
```

结论：PASS。失败 0、错误 0、skip 0。

### 4.4 Lint 与格式

命令：

```bash
python -m ruff check .
python -m ruff format --check .
```

结果：

```text
All checks passed!
105 files already formatted
```

结论：PASS。

### 4.5 工作区差异

命令：

```bash
git diff --check
```

结果：PASS，无空白错误。

## 5. 六个必须测试文件

| 文件 | pytest 收集数 | 结果 | 关键覆盖 |
| --- | ---: | --- | --- |
| `tests/test_gate.py` | 32 | PASS | schema/权限/风险/审批/重复/预算/零 handler/审计 |
| `tests/test_sandbox.py` | 58 | PASS | 路径、敏感文件、命令、环境、超时和进程组 |
| `tests/test_state_checkpoint.py` | 71 | PASS | 状态、预算、审批、租约、Checkpoint 和恢复 |
| `tests/test_audit.py` | 8 | PASS | append-only、决策链、脱敏和事务失败 |
| `tests/test_harness_loop.py` | 23 | PASS | Fake Model、Agent、审批、预算、两类任务 |
| `tests/test_completion.py` | 62 | PASS | 代码理解/测试修复、结构/Evidence 矩阵、旧证据、diff、节点守恒和并发 |
| `tests/test_cli.py` | 26 | PASS | 入口、审批、并发 resume、inspect、审计链、脱敏和 UNVERIFIABLE |
| `tests/test_fault_recovery.py` | 9 | PASS | 两类 E2E、硬退出、W6、DB 锁、Checkpoint 和租约 |

收集数来自：

```bash
python -m pytest --collect-only -q \
  tests/test_gate.py tests/test_sandbox.py tests/test_state_checkpoint.py \
  tests/test_audit.py tests/test_harness_loop.py tests/test_completion.py
```

参数化用例使 pytest 总收集数为 372，高于静态 `def test_` 数量；六份文件均实际参与全量通过。

## 6. 飞书核心验收映射

| 编号 | 优先级 | 结果 | 证据 |
| --- | --- | --- | --- |
| G1 | P0 | PASS | `test_schema_injection_is_denied_before_invocation`、`test_schema_rejections_have_zero_handler_calls` |
| G2 | P0 | PASS | `test_third_equivalent_read_is_denied_after_two_handler_calls`、`test_g2_third_equivalent_read_is_denied` |
| G3 | P0 | PASS | `test_code_understanding_waits_then_resumes_and_accepts`、`test_run_defaults_to_waiting_and_does_not_write` |
| G4 | P0 | PASS | `test_completed_write_replay_is_suppressed_after_resume`、`test_new_process_resume_executes_once_after_console_approval` |
| S1 | P0 | PASS | `test_agent_path_escape_is_denied_before_invocation`、Sandbox 路径穿越测试 |
| S2 | P1 | PASS | `test_symlink_escape_and_internal_resolution`、`test_write_symlink_escape_is_denied_and_audited` |
| S3 | P0 | PASS | Gate 命令 schema 注入拒绝、`test_run_test_*` command/process 测试 |
| S4 | P1 | PASS | `test_timeout_kills_process_group`、`test_cancellation_kills_process_group` |
| C1 | P0 | PASS | Checkpoint round-trip 与 `RUN_STARTED/TOOL_*/APPROVAL_*/COMPLETION_RECORDED` 测试 |
| C2 | P0 | PASS | `test_hard_exit_after_side_effect_recovers_unknown_without_replay` |
| C3 | P0 | PASS | `test_new_process_resume_executes_once_after_console_approval`、恢复预算测试 |
| C4 | P0 | PASS | 工作区变化后 `BLOCKED`、旧 Evidence/审批失效测试 |
| A1 | P0 | PASS | `test_denial_audit_contains_required_a1_fields`、`test_inspect_exposes_denied_call_audit_without_raw_arguments` |
| A2 | P1 | PASS | 两类 E2E 快照可关联 Run/ToolCall/Approval/Checkpoint/Evidence；inspect 关联测试 |
| H1 | P0 | PASS | `test_final_answer_without_deliverable_is_rejected` |
| H2 | P0 | PASS | `test_stale_source_evidence_is_rejected`、测试后工作区变化失效测试 |
| H3 | P0 | PASS | step/token/wall-time 预算测试；CLI 退出码映射 |
| T1 | P0 | PASS | 372 项全量测试通过，失败/错误/skip 为 0 |
| T2 | P1 | NOT_RUN | D-07 明确真实模型不属于 P0；未使用 Fake Model 冒充 |

## 7. WP12-AC 验收

| 场景 | 结果 | 证据/说明 |
| --- | --- | --- |
| WP12-AC-001 依赖准入 | PASS | WP-08 至 WP-11 均 accepted，索引为 DONE |
| WP12-AC-002 必须文件 | PASS | 六份文件存在；全量收集/通过 372 项 |
| WP12-AC-003 全量基线 | PASS | 372 passed |
| WP12-AC-004 lint/格式 | PASS | Ruff check/format 通过 |
| WP12-AC-005 确定性复跑 | PASS | WP-12 目标 9 项两次一致，39.74s / 19.76s |
| WP12-AC-006 样例加载 | PASS | `test_formal_examples_load_with_strict_production_loader` |
| WP12-AC-007 证据映射 | PASS | 本文第 6-8 节、test-plan 和 P6 任务映射 |
| WP12-AC-008 文档一致 | PASS | README/docs 已同步；仍受上游状态限制 |
| WP12-AC-010 G1 | PASS | Gate/Agent schema 注入零 handler |
| WP12-AC-011 G2 | PASS | 第三次等价只读调用拒绝，handler 总数为 2 |
| WP12-AC-012 G3 | PASS | 代码理解/测试修复样例审批前文件不变 |
| WP12-AC-013 G4 | PASS | resume 只执行一次，重复 resume 文件 mtime/内容不变 |
| WP12-AC-014 S1 | PASS | 路径逃逸在 handler 前拒绝，审计存在 |
| WP12-AC-015 S3 | PASS | 命令只来自可信 command_id/配置，注入失败关闭 |
| WP12-AC-016 C1 | PASS | Checkpoint 快照字段、boundary 和 round-trip 测试 |
| WP12-AC-017 C2 | PASS | 文件写入后子进程 `exit 91`；恢复 `BLOCKED/UNKNOWN_RESULT`，模型 0 调用 |
| WP12-AC-018 C3 | PASS | 跨进程 resume、预算/计数保留、无重复写 |
| WP12-AC-019 C4 | PASS | 工作区变化恢复阻塞，旧证据/审批失效 |
| WP12-AC-020 A1 | PASS | rule/decision/actor/tool_call_id 关联测试 |
| WP12-AC-021 H1 | PASS | 完成声明无交付物时 `REJECTED` |
| WP12-AC-022 H2 | PASS | 当前工作区版本变化使旧测试/源码证据失效 |
| WP12-AC-023 H3 | PASS | 预算耗尽 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED` |
| WP12-AC-024 T1 | PASS | 372 项全量通过 |
| WP12-AC-025 S2 | PASS/P1 | Sandbox 与工具 symlink/敏感路径测试 |
| WP12-AC-026 S4 | PASS/P1 | timeout/cancel 进程组终止测试；平台限 Linux/macOS |
| WP12-AC-027 A2 | PASS/P1 | E2E 快照与 inspect 关联字段 |
| WP12-AC-028 T2 | NOT_RUN/P1 | 无真实 Provider |
| WP12-AC-030 允许前审计失败 | PASS | allow/deny/result audit failure 测试 |
| WP12-AC-031 副作用后结果未提交 | PASS | `test_hard_exit_after_side_effect_recovers_unknown_without_replay` |
| WP12-AC-032 DB 锁 | PASS | `test_database_write_lock_fails_closed_without_partial_state` |
| WP12-AC-033 Checkpoint 损坏 | PASS | `test_checkpoint_tampering_is_detected_after_successful_run` |
| WP12-AC-034 租约冲突 | PASS | `test_lease_conflict_fences_old_token` 与既有 lease takeover 测试 |
| WP12-AC-035 代码理解 E2E | PASS | `test_code_understanding_formal_sample_waits_then_accepts` |
| WP12-AC-036 测试修复 E2E | PASS | `test_test_repair_formal_sample_baseline_patch_and_accepts` |
| WP12-AC-037 审批后跨进程 | PASS | `test_new_process_resume_executes_once_after_console_approval` |
| WP12-AC-038 已确定写不重复 | PASS | replay suppression 与 mtime 断言 |
| WP12-AC-039 预算不清零 | PASS | `test_recovery_lease_takeover_preserves_budget_after_reopen` |
| WP12-AC-040 无故障旁路 | PASS | `test_production_cli_rejects_unknown_fault_flags` |

## 8. 任务卡完成情况

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| WP12-T00 基线门禁 | PASS | 安装/全量/lint/格式和上游 accepted 条件均满足 |
| WP12-T01 规格与计划 | PASS | D-84 至 D-86；spec frozen、plan done |
| WP12-T02 故障辅助层 | PASS | `tests/fault_injection.py`、硬退出和 DB 锁辅助 |
| WP12-T03 崩溃恢复矩阵 | PASS | 9 项 WP-12 目标测试覆盖硬退出、恢复、锁、损坏和租约 |
| WP12-T04 审计/Checkpoint 故障 | PASS | 新测试与既有 Audit/Checkpoint 测试全量通过 |
| WP12-T05 安全边界复核 | PASS | Gate/Sandbox/Tools 全量通过，P1 证据汇总 |
| WP12-T06 正式样例 | PASS | 两份 YAML 与两个最小工作区 |
| WP12-T07 代码理解 E2E | PASS | 审批前无文件、审批后一次写入、Completion accepted |
| WP12-T08 测试修复 E2E | PASS | 失败基线、patch、最终 JUnit、节点守恒和 accepted |
| WP12-T09 飞书全矩阵 | PASS | 第 6-7 节映射 |
| WP12-T10 文档与入口 | PASS | test-plan、operations、README 和索引 |
| WP12-T11 全量质量门禁 | PASS | 372 passed、Ruff 通过、目标复跑 |
| WP12-T12 验收报告 | PASS | 本报告实际生成并升级为 accepted |
| WP12-T13 封版交接 | PASS | WP-08 至 WP-11 accepted，索引和工作包状态已同步为 DONE |

## 9. 故障矩阵证据

| 注入/场景 | 结果 | 关键断言 |
| --- | --- | --- |
| Handler 写入后、Gate 记录结果前子进程硬退出 | PASS | 子进程退出 91；文件已写；Invocation 为 STARTED；恢复后 UNKNOWN/BLOCKED；模型 0 调用；文件无第二次写入 |
| SQLite 第二连接持有 `BEGIN IMMEDIATE` | PASS | 20 ms busy timeout 内失败；`STATE_TRANSACTION_FAILED` 可重试；schema 未变化 |
| Checkpoint snapshot 篡改 | PASS | 哈希校验失败，`CheckpointServiceError`，不返回恢复成功 |
| 有效租约第二 owner | PASS | `STATE_LEASE_HELD`；过期接管后旧 token `STATE_LEASE_INVALID` |
| 允许前/结果后审计失败 | PASS | allow 失败 handler 0；结果失败不生成成功终态 |
| 未知结果重复 resume | PASS | `manual_review/UNKNOWN_RESULT`，无自动重放 |

## 10. 限制与剩余风险

1. WP-08 至 WP-11 已 accepted，项目级依赖不再阻塞。
2. T2 未执行，P0 不声明真实模型质量、网络可靠性或 Provider 配置能力。
3. 进程执行只支持 Linux/macOS POSIX 进程组；Windows 和不支持可靠进程组终止的平台失败关闭。
4. Sandbox 不是容器/虚拟机级隔离；Git-ignored 外围文件不在仓库保护基线。
5. 自然语言语义正确性不在 P0 自动证明范围；代码理解只证明引用可追溯，测试修复仍存在自然语言断言
   弱化的残余风险。
6. 当前验收在 macOS Darwin 22.6.0 上执行，未单独声明 Linux 运行时复验。

## 11. 封版确认

1. WP-08、WP-09 报告已 accepted；
2. WP-10、WP-11 报告已 accepted；
3. WP-00 至 WP-11 索引状态为 DONE；
4. WP-12 安装、全量测试、Ruff lint、格式和目标复跑证据已保留；
5. 2026-11-08 人工验收结果已由需求方确认；
6. 本报告状态已从 `blocked` 提升为 `accepted`，工作包索引同步为 DONE。

## 12. 实际结论

WP-12 要求中的本地实现、正式样例、故障矩阵、两类任务 E2E、文档、质量门禁和人工验收已经完成
并通过实测。当前交付状态为：

```text
WP-12 implementation: PASS
WP-12 package acceptance: ACCEPTED
Project P0 completion: COMPLETE
```
