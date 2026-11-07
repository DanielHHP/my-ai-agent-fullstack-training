# WP-08 验收引擎与代码理解验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-08 验收引擎与代码理解 |
| 报告状态 | review |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / in-progress |
| 代码基线 | `618b114` |
| 验收日期 | 2026-11-07 |
| Python | 3.13.5（仓库 `.venv`） |
| 平台 | Darwin 22.6.0 x86_64 |

本报告独立记录 WP-08 的实现、测试和未闭合验收项。报告不把全量测试通过等同于
`WP08-AC-001` 至 `WP08-AC-024` 全部通过；当前存在直接场景覆盖缺口，因此状态为 `review`，
工作包不能标记为 `DONE`。

## 2. 实现范围

已实现并可通过公共接口使用：

- `CompletionChecker.evaluate_code_understanding`；
- 严格 Markdown“事实 / 推断 / 代码依据”解析、证据定义和引用图；
- 同一 Run、同一路径、同一内容摘要的 Agent `READ` Evidence 校验；
- 连续分页读取范围并集覆盖；
- 交付物相对启动基线必须变化；
- 交付物、源码、测试、未跟踪文件、HEAD/index、工作区身份和外围 Git 变化检查；
- 陈旧 Evidence 单向失效；
- Gate `invoke_internal` 固定只读控制面和独立内部 Registry；
- Completion-only `DOCUMENT` / `DIFF` Evidence；
- 逐项 `COMPLETION_CHECKED` Audit、最终 `CompletionCheck` 和事务写入；
- 模型完成声明的保守诊断，且不将自述作为验收事实；
- `CompletionCheck` 可跨 SQLite reopen 读取。

主要文件：

- `src/guarded_harness/completion.py`
- `src/guarded_harness/gate.py`
- `src/guarded_harness/registry.py`
- `src/guarded_harness/state_store.py`
- `src/guarded_harness/audit.py`
- `tests/test_completion.py`

## 3. 实际命令与结果

目标测试：

```text
python -m pytest tests/test_completion.py -q \
  -W error::pytest.PytestUnraisableExceptionWarning
```

结果：`18 passed in 58.65s`。该文件同时包含 WP-09 测试，不能把 18 项全部归因于 WP-08。

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
| WP08-AC-001 | 合法文档和引用 | `test_valid_code_understanding_is_accepted` | PASS |
| WP08-AC-002 | 交付物不存在 | `test_missing_deliverable_and_unsupported_claim_are_rejected` | PASS |
| WP08-AC-003 | 空、二进制、超上限交付物 | 无直接自动化用例 | GAP |
| WP08-AC-004 | 交付物路径绝对/`..`/符号链接逃逸 | 配置层路径测试存在；Completion 交付物读取链无直接用例 | PARTIAL |
| WP08-AC-005 | 缺少必需 Markdown 区 | 解析实现存在；无直接表驱动测试 | GAP |
| WP08-AC-006 | 重复区、空事实、非列表事实 | 解析实现存在；无直接测试 | GAP |
| WP08-AC-007 | 未引用、未定义、重复编号 | 引用图实现存在；无直接测试 | GAP |
| WP08-AC-008 | 证据路径越界、行号不存在、区间逆序 | 路径/行语义有底层测试，但无 Completion 引用定义矩阵 | PARTIAL |
| WP08-AC-009 | 只覆盖部分引用范围 | 并集函数已实现；无部分覆盖拒绝测试 | GAP |
| WP08-AC-010 | 跨 Run、失效、非 READ Evidence | 实现有 producer/validity 过滤；无直接 Completion 测试 | GAP |
| WP08-AC-011 | Completion 自产 Evidence 冒充 Agent 读取 | 实现按 Agent producer 过滤；无直接测试 | GAP |
| WP08-AC-012 | 源码读取后变化 | `test_stale_source_evidence_is_rejected` | PASS |
| WP08-AC-013 | 分页 Evidence 并集 | 实现存在；无两段分页成功测试 | GAP |
| WP08-AC-014 | 越界修改源码/测试/配置 | 有陈旧源码和测试修复越界证据；无代码理解任务专测 | PARTIAL |
| WP08-AC-015 | 新增未声明文件 | 无代码理解任务专测 | GAP |
| WP08-AC-016 | HEAD/index/身份变化 | Sandbox 底层有身份测试；Completion 无端到端用例 | PARTIAL |
| WP08-AC-017 | 外围保护开启且外部变化 | Sandbox 有外围测试；Completion 无端到端用例 | PARTIAL |
| WP08-AC-018 | 外围保护关闭的未监控语义 | 实现存在；无 Completion 直接测试 | GAP |
| WP08-AC-019 | 模型完成声明无证据 | `test_missing_deliverable_and_unsupported_claim_are_rejected` | PASS |
| WP08-AC-020 | 无模型声明但程序事实通过 | 合法验收 fixture 的 candidate 为 `null`，仍 ACCEPTED | PASS |
| WP08-AC-021 | 非 VERIFYING 或预算停止 | Harness 预算停止测试覆盖“不调用 Completion”；无直接 Completion 状态矩阵 | PARTIAL |
| WP08-AC-022 | State/Audit 写入故障 | 审计和状态事务有底层故障测试；无代码理解 Completion 故障用例 | PARTIAL |
| WP08-AC-023 | 内部工具模型可见/可调用 | `test_internal_verifier_tools_are_not_model_visible` | PASS |
| WP08-AC-024 | 重复/并发 Completion | 无 WP-08 代码理解重复或并发专测 | GAP |

## 5. 飞书核心验收映射

| 编号 | 现有证据 | 结论 |
| --- | --- | --- |
| H1 | 缺失交付物 + 模型自述被拒绝 | PASS |
| H2 | 源码变化使 READ Evidence 陈旧并拒绝 | PASS |
| H3 | WP-10 预算停止不进入 Completion | PASS（跨包） |
| A2 | Completion、Gate、Evidence、Audit 关联在有效和拒绝路径可读取 | PASS（核心路径） |
| T1 | 目标与全量测试确定性通过 | PASS |

## 6. 未闭合项

以下项阻止 WP-08 标记 `accepted`：

1. 缺少 WP08-AC-003、005、006、007、009、010、011、013、015、018、024 的直接自动化测试；
2. WP08-AC-004、008、014、016、017、021、022 只有实现或底层证据，缺少 Completion 端到端断言；
3. 规格要求每个 AC 都有自动化映射，当前测试函数命名和计划映射也未完全同步；
4. 计划中的目标测试和全量数字来自实现早期，需按报告证据更新；
5. 语义解释正确性仍不在 P0 机械验收能力内。

## 7. 结论

WP-08 核心实现和合法/主要拒绝路径可运行，H1/H2 的程序化边界成立，全量回归稳定。但冻结规格的
24 个 AC 尚未全部具备直接自动化证据，因此本报告不能标记 `accepted`，WP-08 继续保持
`IN_PROGRESS`。完成上述缺失测试并复验后，可将本报告升级为 `accepted`。
