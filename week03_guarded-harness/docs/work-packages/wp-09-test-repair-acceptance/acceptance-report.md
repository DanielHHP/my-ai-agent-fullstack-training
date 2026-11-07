# WP-09 测试修复验收工作包验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-09 测试修复验收 |
| 报告状态 | review |
| 报告版本 | 1.1.0 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / in-progress |
| 代码基线 | `618b114`；2026-11-07 WP-12 补测时的工作区改动 |
| 验收日期 | 2026-11-07 |
| Python | 3.13.5（仓库 `.venv`） |
| 平台 | Darwin 22.6.0 x86_64 |

本报告独立记录 WP-09 的基线、最终报告、节点守恒、工作区范围和恢复能力。2026-11-07 已补充
此前缺失的直接自动化场景，`WP09-AC-001` 至 `WP09-AC-032` 现有自动化证据全部通过。报告仍为
`review`，因为测试修复的断言弱化判断和最终独立复核需要人工完成。

## 2. 实现范围

已实现并可通过公共接口使用：

- `pytest_junit_xml` 严格配置和可信命令适配；
- JUnit XML 有界解析、UTF-8 校验、DOCTYPE/ENTITY 拒绝、节点键和状态归一；
- Gate `invoke_test_verification` 内部测试执行入口；
- 基线 `RUNNING`、最终 `VERIFYING` 状态约束；
- 测试报告、执行、节点和启动 WorkspaceBaseline 的 append-only SQLite 持久化；
- 基线失败节点记录和全绿基线拒绝；
- 最终测试复跑、退出码、报告完整性和节点守恒；
- 基线节点丢失、失败保留、新增失败、新增 skip 的判定；
- `allow_write`、已有测试文件修改/删除、未跟踪文件和测试前后工作区稳定检查；
- H2 陈旧测试报告 Evidence 单向失效；
- 跨 SQLite reopen 复用基线；
- 内部测试工具不进入模型 snapshot；
- Completion 结果、Evidence 和 Audit 事务提交；
- 最终测试修复验收同时复核工作区身份和仓库外围保护变化，避免外部改动被忽略。

主要文件：

- `src/guarded_harness/test_reports.py`
- `src/guarded_harness/completion.py`
- `src/guarded_harness/gate.py`
- `src/guarded_harness/state_store.py`
- `src/guarded_harness/storage.py`
- `tests/test_completion.py`
- `tests/test_state_checkpoint.py`
- `tests/test_tools.py`

## 3. 实际命令与结果

目标测试：

```text
python -m pytest tests/test_completion.py -q \
  -W error::pytest.PytestUnraisableExceptionWarning
```

结果：`62 passed in 131.88s (0:02:11)`。该文件同时包含 WP-08 代码理解测试。

相关契约与基础设施：

```text
python -m pytest tests/test_config.py tests/test_gate.py \
  tests/test_state_checkpoint.py tests/test_audit.py -q \
  -W error::pytest.PytestUnraisableExceptionWarning
```

结果：`136 passed in 38.43s`。

全量回归：

```text
python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning
```

结果：`372 passed in 257.41s`。

静态检查：

```text
python -m ruff check .
python -m ruff format --check .
```

结果：lint 通过；`105 files already formatted`。所有命令无关键 skip。

## 4. 验收场景映射

| AC | 场景 | 现有证据 | 结论 |
| --- | --- | --- | --- |
| WP09-AC-001 | 失败基线修复后全部通过 | `test_test_repair_baseline_and_final_report_are_bound` | PASS |
| WP09-AC-002 | 未捕获基线 | `test_missing_baseline_never_reaches_final_test` | PASS |
| WP09-AC-003 | 基线损坏、跨 Run、Evidence 失效 | `test_test_baseline_failure_modes_fail_closed`、reopen 基线测试 | PASS |
| WP09-AC-004 | 最终命令/配置指纹不一致 | `test_test_baseline_command_binding_mismatch_is_rejected` | PASS |
| WP09-AC-005 | 测试命令启动失败 | `test_test_command_start_failure_prevents_agent_and_completion` | PASS |
| WP09-AC-006 | 超时、取消、结果未知不重放 | `test_test_command_timeout_is_unknown_and_not_retried` + `tests/test_tools.py` 取消测试 | PASS |
| WP09-AC-007 | 失败但报告完整的基线 | 合法测试修复用例 | PASS |
| WP09-AC-008 | 报告缺失、空、超限或节点超限 | `test_junit_report_size_and_node_limits_fail_closed`、既有空/计数错误测试 | PASS |
| WP09-AC-009 | DOCTYPE/ENTITY/歧义 XML | `test_parse_junit_report_preserves_nodes_and_rejects_unsafe_xml` | PASS |
| WP09-AC-010 | 基线执行前后工作区变化 | `test_baseline_side_effect_stops_before_agent` | PASS |
| WP09-AC-011 | 最终退出码非零 | `test_unrepaired_failure_remains_rejected`，最终仍失败 | PASS |
| WP09-AC-012 | exit 0 但报告缺失/为空 | `test_final_exit_zero_with_empty_report_is_unverifiable` | PASS |
| WP09-AC-013 | 基线节点消失 | `test_removing_a_baseline_test_node_is_rejected` | PASS |
| WP09-AC-014 | 基线失败仍保留 | `test_unrepaired_failure_remains_rejected` | PASS |
| WP09-AC-015 | 最终新增测试失败 | `test_new_failing_generated_test_is_rejected` | PASS |
| WP09-AC-016 | 原执行节点变为 skip/xfail | `test_weakening_existing_test_into_skip_is_rejected` | PASS |
| WP09-AC-017 | 基线已有且最终保持 skip | `test_existing_skip_may_remain_while_failure_is_repaired` | PASS |
| WP09-AC-018 | 参数化节点减少或 ID 改变 | `test_parameterized_node_reduction_is_rejected` | PASS |
| WP09-AC-019 | 基线测试文件 allow_write 外修改 | `test_weakening_existing_test_into_skip_is_rejected`、`test_removing_a_baseline_test_node_is_rejected` | PASS |
| WP09-AC-020 | 测试文件删除、重命名、符号链接替换 | `test_existing_test_file_rename_is_rejected`、`test_existing_test_file_symlink_replacement_is_rejected` | PASS |
| WP09-AC-021 | 允许目录新增测试并全通过 | `test_allowed_generated_test_is_part_of_superset` | PASS |
| WP09-AC-022 | `src/**` 修改允许 | 合法测试修复用例 | PASS |
| WP09-AC-023 | 未命中 allow_write 的未跟踪文件 | `test_unexpected_untracked_file_is_out_of_scope` | PASS |
| WP09-AC-024 | HEAD/index/身份/外围变化 | `test_external_change_before_test_repair_completion_is_rejected` + 身份/外围底层测试 | PASS |
| WP09-AC-025 | 测试命令运行中修改工作区 | `test_test_command_that_changes_workspace_is_rejected` | PASS |
| WP09-AC-026 | 最终测试后、Completion 前源码变化 | `test_workspace_change_before_completion_commit_invalidates_report` | PASS |
| WP09-AC-027 | stdout/stderr 截断但报告完整 | `test_truncated_command_output_does_not_invalidate_complete_junit_report` | PASS |
| WP09-AC-028 | 模型 `run_test`/自述不影响验收 | `test_model_claims_do_not_override_program_test_verdict` + 内部工具隔离测试 | PASS |
| WP09-AC-029 | 非 VERIFYING 或预算停止 | WP-10 预算停止闭环覆盖不调用 Completion | PASS（跨包） |
| WP09-AC-030 | State/Audit/Completion 写入故障 | `test_test_repair_completion_transaction_failure_rolls_back` | PASS |
| WP09-AC-031 | 内部测试工具不可见/不可调用 | `test_internal_verifier_tools_are_not_model_visible` | PASS |
| WP09-AC-032 | 重复/并发验收不重复执行 | `test_concurrent_test_repair_completion_executes_final_test_once` | PASS |

## 5. 飞书核心验收映射

| 编号 | 现有证据 | 结论 |
| --- | --- | --- |
| H1 | 内部测试事实决定验收，模型自述只作为候选文本 | PASS（WP-08/WP-10 联动） |
| H2 | 最终报告后源码变化使 Evidence 失效并拒绝 | PASS |
| H3 | 预算停止不验收，保持 NOT_EVALUATED | PASS（WP-10） |
| A2 | 基线、最终执行、节点、Evidence、Completion、Audit 可通过关联 ID 读取 | PASS（核心路径） |
| T1 | 目标与全量测试确定性通过 | PASS |

## 6. 未闭合项

当前没有自动化 AC 缺口。剩余为：

1. 既有测试的断言语义是否被弱化，仍需按
   [手动验收操作说明](../../manual-acceptance-wp08-wp11.md) 人工审查 diff；
2. 独立复核人尚未确认补测和报告映射；
3. 自然语言断言弱化属于已声明 P0 机械检测限制。

## 7. 结论

WP-09 冻结规格的 32 个 AC 现有自动化证据全部通过，失败基线、最终 JUnit、节点守恒、
工作区范围、H2 Evidence 失效和并发验收均有直接测试。报告保持 `review`，待人工测试质量审查
和独立复核完成后可升级为 `accepted`。
