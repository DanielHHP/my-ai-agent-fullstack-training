# WP-08 验收引擎与代码理解验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-08 验收引擎与代码理解 |
| 报告状态 | review |
| 报告版本 | 1.1.0 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / in-progress |
| 代码基线 | `618b114`；2026-11-07 WP-12 补测时的工作区改动 |
| 验收日期 | 2026-11-07 |
| Python | 3.13.5（仓库 `.venv`） |
| 平台 | Darwin 22.6.0 x86_64 |

本报告独立记录 WP-08 的实现和测试。2026-11-07 已补充此前缺失的直接自动化场景，
`WP08-AC-001` 至 `WP08-AC-024` 现有自动化证据全部通过。报告仍为 `review`，因为代码解释的
业务语义仍需要人工抽查，且最终独立复核尚未完成。

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
- `CompletionCheck` 可跨 SQLite reopen 读取；
- 重复调用已持久化 Completion 时直接返回原结论，不再生成不同 `checked_at` 并触发内容冲突。

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

结果：`62 passed in 131.88s (0:02:11)`。该文件同时包含 WP-09 测试，不能把 62 项全部归因于
WP-08。

相关契约与基础设施：

```text
python -m pytest tests/test_config.py tests/test_gate.py \
  tests/test_state_checkpoint.py tests/test_audit.py -q \
  -W error::pytest.PytestUnraisableExceptionWarning
```

结果：WP-08/WP-09 相关目标集合与 WP-10/WP-11 联合复验通过。

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
| WP08-AC-001 | 合法文档和引用 | `test_valid_code_understanding_is_accepted` | PASS |
| WP08-AC-002 | 交付物不存在 | `test_missing_deliverable_and_unsupported_claim_are_rejected` | PASS |
| WP08-AC-003 | 空、二进制、超上限交付物 | `test_code_understanding_deliverable_content_rejections` | PASS |
| WP08-AC-004 | 交付物路径绝对/`..`/符号链接逃逸 | `test_absolute_deliverable_and_pattern_are_rejected`、`test_symlink_escape_is_rejected`、`test_rejection_has_no_side_effects` | PASS |
| WP08-AC-005 | 缺少必需 Markdown 区 | `test_code_understanding_markdown_structure_rejections` | PASS |
| WP08-AC-006 | 重复区、空事实、非列表事实 | `test_code_understanding_markdown_structure_rejections` | PASS |
| WP08-AC-007 | 未引用、未定义、重复编号 | `test_code_understanding_citation_graph_rejections` | PASS |
| WP08-AC-008 | 证据路径越界、行号不存在、区间逆序 | `test_code_understanding_citation_graph_rejections` | PASS |
| WP08-AC-009 | 只覆盖部分引用范围 | `test_code_understanding_partial_evidence_is_rejected` | PASS |
| WP08-AC-010 | 跨 Run、失效、非 READ Evidence | `test_evidence_from_another_run_cannot_prove_agent_read`、`test_non_read_evidence_cannot_prove_agent_read`、`test_invalidated_read_evidence_is_rejected` | PASS |
| WP08-AC-011 | Completion 自产 Evidence 冒充 Agent 读取 | `test_completion_self_read_cannot_prove_agent_read` | PASS |
| WP08-AC-012 | 源码读取后变化 | `test_stale_source_evidence_is_rejected` | PASS |
| WP08-AC-013 | 分页 Evidence 并集 | `test_adjacent_paginated_reads_form_complete_evidence` | PASS |
| WP08-AC-014 | 越界修改源码/测试/配置 | `test_code_understanding_source_change_is_rejected` | PASS |
| WP08-AC-015 | 新增未声明文件 | `test_code_understanding_undeclared_file_is_rejected` | PASS |
| WP08-AC-016 | HEAD/index/身份变化 | `test_code_understanding_index_change_is_rejected` | PASS |
| WP08-AC-017 | 外围保护开启且外部变化 | `test_code_understanding_external_change_is_rejected` | PASS |
| WP08-AC-018 | 外围保护关闭的未监控语义 | `test_code_understanding_disabled_external_protection_is_explicit` | PASS |
| WP08-AC-019 | 模型完成声明无证据 | `test_missing_deliverable_and_unsupported_claim_are_rejected` | PASS |
| WP08-AC-020 | 无模型声明但程序事实通过 | 合法验收 fixture 的 candidate 为 `null`，仍 ACCEPTED | PASS |
| WP08-AC-021 | 非 VERIFYING 或预算停止 | `test_code_understanding_requires_verifying_state` + Harness 预算测试 | PASS |
| WP08-AC-022 | State/Audit 写入故障 | `test_code_understanding_completion_audit_failure_rolls_back` | PASS |
| WP08-AC-023 | 内部工具模型可见/可调用 | `test_internal_verifier_tools_are_not_model_visible` | PASS |
| WP08-AC-024 | 重复/并发 Completion | `test_code_understanding_completion_is_idempotent`、`test_code_understanding_concurrent_completion_is_stable` | PASS |

## 5. 飞书核心验收映射

| 编号 | 现有证据 | 结论 |
| --- | --- | --- |
| H1 | 缺失交付物 + 模型自述被拒绝 | PASS |
| H2 | 源码变化使 READ Evidence 陈旧并拒绝 | PASS |
| H3 | WP-10 预算停止不进入 Completion | PASS（跨包） |
| A2 | Completion、Gate、Evidence、Audit 关联在有效和拒绝路径可读取 | PASS（核心路径） |
| T1 | 目标与全量测试确定性通过 | PASS |

## 6. 未闭合项

当前没有自动化 AC 缺口。剩余为：

1. 代码解释的业务语义、表达清晰度和完整性需要按
   [手动验收操作说明](../../manual-acceptance-wp08-wp11.md) 人工抽查；
2. 独立复核人尚未确认补测和报告映射；
3. 语义解释正确性仍不在 P0 机械自动证明范围。

## 7. 结论

WP-08 冻结规格的 24 个 AC 现有自动化证据全部通过，H1/H2 程序化边界成立，全量回归稳定。
报告保持 `review`，待人工语义抽查和独立复核完成后可升级为 `accepted`。
