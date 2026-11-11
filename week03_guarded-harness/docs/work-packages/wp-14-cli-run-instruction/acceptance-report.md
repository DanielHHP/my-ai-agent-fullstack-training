# WP-14 CLI Run 指令输入验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-14 CLI Run 指令输入 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-11 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / done |

## 2. 依赖与结论

- WP-10、WP-11、WP-13 均已 accepted；
- 实装未修改 `smolagents` 或现有 Gate、Sandbox、Approval、Completion 路径；
- 目标测试、全量测试、Ruff lint、格式检查、入口烟测和 diff 检查均通过；
- 本报告结论为 `accepted`。

## 3. 交付物

| 交付物 | 状态 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/domain.py` | PASS | `RunRequest.instruction`、空白/长度校验和 START-only 语义 |
| `src/guarded_harness/cli.py` | PASS | `run --instruction TEXT`、CLI 输入校验和 RunRequest 接线 |
| `src/guarded_harness/harness.py` | PASS | 有效任务描述选择、`Run.task_description` 持久化和恢复使用 |
| `tests/test_cli.py` | PASS | CLI 替换、默认描述、边界输入和命令面隔离 |
| `tests/test_harness_loop.py` | PASS | 领域约束、初始任务、审批后恢复和配置指纹 |
| `docs/decisions.md` | PASS | D-101 |
| `docs/design.md`、`docs/operations.md` | PASS | 外部行为、权限边界和操作命令 |
| `docs/test-plan.md`、工作包索引和任务拆解 | PASS | WP-14 规划和验收入口 |

## 4. 执行证据

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pytest tests/test_harness_loop.py -q -W error::pytest.PytestUnraisableExceptionWarning` | PASS；`27 passed in 49.25s` |
| `.venv/bin/python -m pytest tests/test_cli.py -q` | PASS；`31 passed in 55.62s` |
| `.venv/bin/python -m pytest -q -W error::pytest.PytestUnraisableExceptionWarning` | PASS；`402 passed, 1 skipped in 261.17s` |
| `.venv/bin/python -m ruff check .` | PASS |
| `.venv/bin/python -m ruff format --check .` | PASS；`116 files already formatted` |
| `.venv/bin/guarded-harness run --help` | PASS；显示 `--instruction TEXT` |
| `.venv/bin/python -m guarded_harness run --help` | PASS；与 console script 一致 |
| `git diff --check` | PASS；无空白错误 |

全量测试唯一 skip 为既有的显式 opt-in live model smoke，不属于 WP-14，也不在普通离线门禁中运行。

## 5. 行为证据

- `run --instruction TEXT` 时，Fake Model 首次收到的任务包含 CLI 文本，不包含 YAML
  `task.description`；
- 不提供指令时，首次任务继续包含 YAML `task.description`；
- Run 创建后的 `task_description` 与有效 CLI 指令完全一致；
- 进入 `WAITING_APPROVAL` 后批准并 resume，Fake Model 收到的 continuation 继续包含持久化指令；
- `resume`、`approvals` 和 `inspect` 携带 `--instruction` 均在业务副作用前返回 CLI usage error；
- 纯空白、NUL 和 4001 字符输入返回 CLI usage error，且不创建状态数据库；
- 使用 CLI 指令启动后修改 YAML 描述，resume 仍因配置指纹变化失败，模型调用次数为 0；
- CLI 指令不进入配置指纹，也不改变 YAML 的工具、路径、预算、审批或 Completion 配置。

## 6. 规格场景映射

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| WP14-AC-001 | PASS | `test_run_instruction_replaces_and_persists_config_description` |
| WP14-AC-002 | PASS | CLI/Harness 测试断言 YAML 描述未出现在模型消息中 |
| WP14-AC-003 | PASS | `test_run_without_instruction_uses_config_description` |
| WP14-AC-004 | PASS | 状态快照和 SQLite `runs.task_description` 断言 |
| WP14-AC-005 | PASS | `test_resume_uses_persisted_run_instruction_after_approval` |
| WP14-AC-006 | PASS | `test_run_request_instruction_is_start_only_and_bounded`、CLI usage test |
| WP14-AC-007 | PASS | `test_instruction_validation_and_resume_rejection` |
| WP14-AC-008 | PASS | 指令仅进入任务文本；权限、路径、审批和验收仍由 YAML 驱动 |
| WP14-AC-009 | PASS | `test_resume_rejects_config_change_even_when_cli_instruction_was_used` |
| WP14-AC-010 | PASS | CLI parser 拒绝 approvals/inspect 的 `--instruction`，不创建数据库或模型 |
| WP14-AC-011 | PASS | 目标/全量测试、Ruff、格式和 diff 检查 |

## 7. 限制与剩余风险

- 指令会持久化为 Run 任务描述，因此不得包含密钥；CLI argv 也可能进入 shell 历史；
- 不支持 system prompt 覆盖、`RESUME` 指令修正、交互式多轮输入或从文件/环境隐式加载；
- 指令与 YAML 验收或权限冲突时不会扩大权限，只会在 Gate 或 Completion 阶段失败；
- 本工作包不改变 SQLite migration、Audit schema、模型 Provider 或 `smolagents` 版本。
