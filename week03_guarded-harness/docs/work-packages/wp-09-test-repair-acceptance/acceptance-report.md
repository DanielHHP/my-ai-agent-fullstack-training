# WP-09 测试修复验收工作包验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-09 测试修复验收 |
| 报告状态 | review |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / in-progress |
| 代码基线 | `618b114` |
| 验收日期 | 2026-11-07 |
| Python | 3.13.5（仓库 `.venv`） |
| 平台 | Darwin 22.6.0 x86_64 |

本报告独立记录 WP-09 的基线、最终报告、节点守恒、工作区范围和恢复能力。报告不把全量测试通过
等同于 `WP09-AC-001` 至 `WP09-AC-032` 全部通过；当前存在直接场景覆盖缺口，因此状态为
`review`，工作包不能标记为 `DONE`。

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
- Completion 结果、Evidence 和 Audit 事务提交。

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

结果：`18 passed in 58.65s`。该文件同时包含 WP-08 代码理解测试。

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

结果：`293 passed in 141.10s`。

静态检查：

```text
python -m ruff check .
python -m ruff format --check .
```

结果：lint 通过；`79 files already formatted`。所有命令无关键 skip。

## 4. 验收场景映射

| AC | 场景 | 现有证据 | 结论 |
| --- | --- | --- | --- |
| WP09-AC-001 | 失败基线修复后全部通过 | `test_test_repair_baseline_and_final_report_are_bound` | PASS |
| WP09-AC-002 | 未捕获基线 | `test_missing_baseline_never_reaches_final_test` | PASS |
| WP09-AC-003 | 基线损坏、跨 Run、Evidence 失效 | 无直接 Completion 测试 | GAP |
| WP09-AC-004 | 最终命令/配置指纹不一致 | 配置契约有测试；无最终验收专测 | PARTIAL |
| WP09-AC-005 | 测试命令启动失败 | 无基线/最终 Completion 专测 | GAP |
| WP09-AC-006 | 超时、取消、结果未知不重放 | `tests/test_tools.py` 有命令层测试；无 Completion 两阶段专测 | PARTIAL |
| WP09-AC-007 | 失败但报告完整的基线 | 合法测试修复用例 | PASS |
| WP09-AC-008 | 报告缺失、空、超限或节点超限 | 空报告和计数错误有测试；8 MiB/100k 上限无专测 | PARTIAL |
| WP09-AC-009 | DOCTYPE/ENTITY/歧义 XML | `test_parse_junit_report_preserves_nodes_and_rejects_unsafe_xml` | PASS |
| WP09-AC-010 | 基线执行前后工作区变化 | `test_baseline_side_effect_stops_before_agent` | PASS |
| WP09-AC-011 | 最终退出码非零 | `test_unrepaired_failure_remains_rejected`，最终仍失败 | PASS |
| WP09-AC-012 | exit 0 但报告缺失/为空 | 无直接集成用例 | GAP |
| WP09-AC-013 | 基线节点消失 | `test_removing_a_baseline_test_node_is_rejected` | PASS |
| WP09-AC-014 | 基线失败仍保留 | `test_unrepaired_failure_remains_rejected` | PASS |
| WP09-AC-015 | 最终新增测试失败 | 无直接用例 | GAP |
| WP09-AC-016 | 原执行节点变为 skip/xfail | `test_weakening_existing_test_into_skip_is_rejected` | PASS |
| WP09-AC-017 | 基线已有且最终保持 skip | 无直接用例 | GAP |
| WP09-AC-018 | 参数化节点减少或 ID 改变 | 无直接用例 | GAP |
| WP09-AC-019 | 基线测试文件 allow_write 外修改 | skip 修改用例会同时触发 `test_files_scope`；无独立专测 | PARTIAL |
| WP09-AC-020 | 测试文件删除、重命名、符号链接替换 | 删除节点用例覆盖删除；重命名和符号链接无专测 | PARTIAL |
| WP09-AC-021 | 允许目录新增测试并全通过 | 无直接生成测试用例 | GAP |
| WP09-AC-022 | `src/**` 修改允许 | 合法测试修复用例 | PASS |
| WP09-AC-023 | 未命中 allow_write 的未跟踪文件 | `test_unexpected_untracked_file_is_out_of_scope` | PASS |
| WP09-AC-024 | HEAD/index/身份/外围变化 | Sandbox 有底层测试；Completion 无端到端专测 | PARTIAL |
| WP09-AC-025 | 测试命令运行中修改工作区 | `test_test_command_that_changes_workspace_is_rejected` | PASS |
| WP09-AC-026 | 最终测试后、Completion 前源码变化 | `test_workspace_change_before_completion_commit_invalidates_report` | PASS |
| WP09-AC-027 | stdout/stderr 截断但报告完整 | 无 Completion 专测 | GAP |
| WP09-AC-028 | 模型 `run_test`/自述不影响验收 | 内部工具隔离有测试；模型自述由 WP-10 H1 覆盖 | PARTIAL |
| WP09-AC-029 | 非 VERIFYING 或预算停止 | WP-10 预算停止闭环覆盖不调用 Completion | PASS（跨包） |
| WP09-AC-030 | State/Audit/Completion 写入故障 | 底层事务有测试；无测试修复 Completion 专测 | PARTIAL |
| WP09-AC-031 | 内部测试工具不可见/不可调用 | `test_internal_verifier_tools_are_not_model_visible` | PASS |
| WP09-AC-032 | 重复/并发验收不重复执行 | 有基线跨 reopen 和 append-only；无并发最终验收专测 | PARTIAL |

## 5. 飞书核心验收映射

| 编号 | 现有证据 | 结论 |
| --- | --- | --- |
| H1 | 内部测试事实决定验收，模型自述只作为候选文本 | PASS（WP-08/WP-10 联动） |
| H2 | 最终报告后源码变化使 Evidence 失效并拒绝 | PASS |
| H3 | 预算停止不验收，保持 NOT_EVALUATED | PASS（WP-10） |
| A2 | 基线、最终执行、节点、Evidence、Completion、Audit 可通过关联 ID 读取 | PASS（核心路径） |
| T1 | 目标与全量测试确定性通过 | PASS |

## 6. 未闭合项

以下项阻止 WP-09 标记 `accepted`：

1. 缺少 WP09-AC-003、005、012、015、017、018、021、027 的直接自动化测试；
2. WP09-AC-004、006、008、019、020、024、028、030、032 只有部分底层或间接证据；
3. 测试文件重命名、符号链接替换、完整报告缺失、生成测试允许和并发最终验收需要直接场景；
4. 规格要求每个 AC 均映射到自动化测试，当前计划映射和真实测试名称尚未完全同步；
5. 断言语义弱化仍无法由机械节点守恒完全证明，属于已声明 P0 限制。

## 7. 结论

WP-09 已形成失败基线、修复、最终 JUnit 验收、节点守恒、H2 Evidence 失效和跨 reopen 基线的
有效核心闭环，主要 happy path 与多项关键拒绝路径通过。冻结规格的 32 个 AC 尚未全部具备直接
自动化证据，因此本报告不能标记 `accepted`，WP-09 继续保持 `IN_PROGRESS`。完成缺失测试并复验
后，可将本报告升级为 `accepted`。
