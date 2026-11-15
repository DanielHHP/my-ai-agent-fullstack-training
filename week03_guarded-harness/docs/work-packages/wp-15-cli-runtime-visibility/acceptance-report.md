# WP-15 CLI 运行可观测性验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-15 CLI 运行可观测性 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-15 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / done |

## 2. 依赖与结论

- WP-10、WP-11、WP-14 均已 accepted；
- 实装未修改 `smolagents`、Gate、Sandbox、ApprovalStore、Completion 或 SQLite schema；
- Run 创建通知使用 Harness 可选只读观察器，不参与授权、执行或验收；
- CLI 进度、审批提示、纯文本最终回答和 JSON 输出隔离均有自动化测试；
- 目标测试、全量测试、Ruff lint、格式检查和 diff 检查通过；
- 本报告结论为 `accepted`。

## 3. 交付物

| 交付物 | 状态 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/harness.py` | PASS | 新增可选 `RunCreatedEvent`/`RunEventSink`，在 Run 启动后、模型调用前通知 |
| `src/guarded_harness/cli.py` | PASS | stderr 运行进度、审批去重提示、纯文本最终回答和终端控制字符清理 |
| `src/guarded_harness/__init__.py` | PASS | 导出观察事件和 sink 类型 |
| `tests/test_harness_loop.py` | PASS | 事件顺序、持久化状态和观察器故障隔离 |
| `tests/test_cli.py` | PASS | JSON 隔离、run 等待提示、审批字段和纯文本最终回答 |
| `docs/` | PASS | WP-15 规格/计划/报告、D-111、设计、运维和测试计划同步 |

## 4. 执行证据

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_cli.py -q -W error::pytest.PytestUnraisableExceptionWarning` | PASS；`34 passed in 42.64s` |
| `.venv/bin/python -m pytest tests/test_harness_loop.py -q -W error::pytest.PytestUnraisableExceptionWarning` | PASS；`34 passed in 50.59s` |
| `.venv/bin/python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning` | PASS；`433 passed, 1 skipped in 274.53s` |
| `.venv/bin/python -m ruff check .` | PASS |
| `.venv/bin/python -m ruff format --check .` | PASS；`121 files already formatted` |
| `git diff --check` | PASS；无空白错误 |

全量测试唯一 skip 为既有的显式 opt-in live model smoke，不属于 WP-15。一次全量运行中既有的
`test_timeout_kills_process_group` 因 0.2 秒超时早于子进程写 PID 文件出现偶发失败；该测试单独
复跑通过，随后无并发负载的全量回归全部通过。该现象属于既有 Sandbox 短超时测试的时序敏感性，
与本工作包改动无关。

## 5. 行为证据

- Run 创建观察器触发时，数据库中 Run 已为 `RUNNING`，模型调用数仍为 0；
- 观察事件只包含 `run_id`/`trace_id`，并与最终 `RunResult` 完全一致；
- 观察器抛出 `OSError` 时，Run 仍完成程序化验收流程，终态不受展示故障影响；
- `run --json` 的 stdout 可在 stderr 存在进度输出时完整解析为单个 JSON envelope；
- `run --wait-for-approval` 显示 Run、审批、工具、调用、状态、有效期和角色摘要；
- 同一审批在等待轮询中只输出一次，审批状态和超时语义未改变；
- 默认文本模式保留多行最终回答，不产生 JSON `\\n` 字符转义；
- 最终回答来自已持久化的脱敏值，密钥值不会出现在文本输出，ANSI escape 被移除；
- 等待审批且无候选答案时不输出空 `final_answer` 区块。

## 6. 规格场景映射

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| WP15-AC-001 | PASS | `test_run_created_event_observes_persisted_run_before_model_call` |
| WP15-AC-002 | PASS | `test_run_created_progress_keeps_json_stdout_parseable`、文本最终回答测试 |
| WP15-AC-003 | PASS | `test_run_created_progress_keeps_json_stdout_parseable` |
| WP15-AC-004 | PASS | `test_run_wait_for_approval_prints_notice_without_empty_final_block` |
| WP15-AC-005 | PASS | `test_wait_mode_approves_during_poll_and_resumes_once` 的提示计数断言 |
| WP15-AC-006 | PASS | `test_text_mode_renders_final_answer_plainly_and_safely` |
| WP15-AC-007 | PASS | 持久化脱敏、ANSI 移除和无 JSON 换行转义断言 |
| WP15-AC-008 | PASS | run 等待测试断言不存在空 `final_answer` 区块 |
| WP15-AC-009 | PASS | `test_run_created_event_failure_does_not_change_governance_result` |
| WP15-AC-010 | PASS | 目标/全量 pytest、Ruff、格式和 diff 检查 |

## 7. 限制与剩余风险

- `--json` 模式仍将候选答案放在 JSON `data` 中；需要自然阅读时使用默认文本模式；
- 审批提示有意不展示原始工具参数，因此异常排查仍应使用 `inspect`；
- 观察器故障以固定非敏感日志记录，不承诺终端写入一定成功；
- 不展示流式 token、隐藏推理、颜色、TUI 或完整模型响应；
- 本工作包不改变 SQLite migration、Audit schema、退出码、审批授权或程序验收事实。
