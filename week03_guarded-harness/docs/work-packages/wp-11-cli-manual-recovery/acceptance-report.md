# WP-11 CLI 与人工恢复验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-11 CLI 与人工恢复 |
| 报告状态 | review |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-07 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.1.0 / done |
| 代码基线 | `3ee4c63` + 本工作包未提交工作区改动 |
| 操作系统 | Darwin 22.6.0 x86_64 |
| Python | 3.13.5 |
| pip | 25.1.1 |
| `smolagents` | 1.26.0 |
| pytest | 9.1.1 |
| Ruff | 0.17.3 |
| guarded-harness | 0.1.0 |

报告生成前工作区只包含 WP-11 实现、测试和文档改动；没有生成运行产物，也没有提交或改写
append-only 业务数据。

## 2. 验收范围

本报告验收：

- `guarded-harness` 与 `python -m guarded_harness` 共用的 `main`；
- `run`、`resume`、`approvals list/approve/reject` 和 `inspect`；
- 默认审批安全退出与 `--wait-for-approval SECONDS`；
- 审批控制面、只读事实视图和稳定退出码；
- 新进程审批后恢复；
- 稳定错误码、JSON envelope 和脱敏/有界输出。

不包含：

- 真实模型 Provider、网络 smoke test 或模型质量评估；
- Web/HTTP 审批控制面；
- 项目级两类任务故障矩阵和最终交付文档，这些属于 WP-12；
- 将 `UNKNOWN_RESULT` 或 `BLOCKED` 人工改写为成功。

## 3. 依赖状态与结论边界

- WP-07 已 accepted；
- WP-10 当前为 `VERIFYING`，其验收报告仍为 `review`；
- 需求方明确要求在 WP-10 review 状态下并行开始 WP-11 施工；这是本报告的已记录施工偏差；
- WP-11 自身实现和命令级证据已通过，但按完成定义不能在上游未 accepted 时转为 `accepted`；
- 本报告结论为 `review`，建议在 WP-10 上游报告转 accepted 后复核并转 accepted。

## 4. 交付物

| 交付物 | 状态 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/cli.py` | PASS | 严格 parser、`main`、命令派发、退出码、输出和 wait 编排 |
| `src/guarded_harness/__main__.py` | PASS | module 入口调用同一个 `main` |
| `src/guarded_harness/control.py` | PASS | 审批控制服务与只读 inspect 投影 |
| `src/guarded_harness/approval.py` | PASS | 增加无 lease 的 locate 和有界跨 Run list，不物化过期 |
| `src/guarded_harness/domain.py` | PASS | 增加 `CLI_*` 稳定错误码 |
| `pyproject.toml` | PASS | 注册 `guarded-harness = guarded_harness.cli:main` |
| `tests/test_cli.py` | PASS | 21 项 CLI、审批、等待、恢复、inspect 和退出码测试 |
| `tests/cli_model_runner.py` | PASS | 跨进程测试注入 Fake Model 的真实 `main` runner |
| `docs/decisions.md` | PASS | 登记 D-78 至 D-83 |
| `docs/design.md` | PASS | 增加 CLI 与人工恢复设计 12.5 |
| WP-11 `spec.md` / `plan.md` | PASS | 规格冻结、计划批准和执行状态更新 |

## 5. 执行证据

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pip install -e '.[dev]'` | PASS；editable 安装成功，console script 已注册 |
| `.venv/bin/python -m pytest tests/test_cli.py -q` | PASS；`21 passed in 40.02s` |
| `.venv/bin/python -m pytest -q` | PASS；`314 passed in 158.44s` |
| `.venv/bin/python -m ruff check .` | PASS；无 lint 错误 |
| `.venv/bin/python -m ruff format --check .` | PASS；`89 files already formatted` |
| `.venv/bin/guarded-harness --help` | PASS；退出码 0 |
| `.venv/bin/python -m guarded_harness --help` | PASS；退出码 0，与 console 同一 usage |
| `git diff --check` | PASS；无空白错误 |

全量测试没有失败或 skip。以上结果来自最终工作区复跑，未使用计划中的预期值替代。

## 6. 行为证据

### 6.1 入口

- console script 与 module 入口均调用 `guarded_harness.cli.main`；
- 导入 `guarded_harness` 不启动模型、数据库迁移或仓库操作；
- 缺 `--config`、未知 flag、非法 wait/limit 在业务副作用前返回退出码 2；
- 缺模型 factory 的 `run/resume` 返回 `CLI_MODEL_UNAVAILABLE`，退出码 6，目标文件不变；
- `approvals` 和 `inspect` 不要求模型 factory。

### 6.2 审批安全退出

实际测试证明：

- `run` 遇到必审 `write_file` 返回 `WAITING_APPROVAL` 和退出码 3；
- 目标文件内容与 mtime 不变；
- Approval 处于 PENDING，Run 处于 WAITING_APPROVAL；
- lease 在 Harness 返回时释放。

### 6.3 审批命令

- approve 只把 PENDING 改为 APPROVED，不执行 handler；
- reject 原子产生 Approval REJECTED、Run `STOPPED/APPROVAL_REJECTED/NOT_EVALUATED`；
- 未持有允许角色、自批和重复 approve 均失败关闭；
- 过期审批首次 action 会先物化 EXPIRED 和 Run STOPPED，然后返回 `APPROVAL_EXPIRED`；
- 查询列表只计算 effective status，不物化过期，也不增加 Audit 事件数。

### 6.4 跨进程恢复

实际采用了三个进程边界：

1. 测试父进程通过 `main` 产生等待审批 Run；
2. 安装后的 console script 执行 approve；
3. 新 Python 子进程通过测试 runner 注入新 Fake Model，调用同一 `main` 执行 resume。

结果：

- 新进程重新加载配置、SQLite、Checkpoint 和审批；
- 写操作只发生一次，交付物通过 Completion；
- 第二次新进程 resume 读取既终态，不再改文件；
- budget、Step 和 ToolCall 计数没有重置。

### 6.5 Wait

- 等待过程中外部 approve 后，当前命令重建 Harness/Agent 并 resume；
- 等待超时返回 WAITING_APPROVAL/退出码 3；
- 超时不自动 approve、reject、过期或写入；
- 等待轮询对 PENDING/APPROVED 使用无副作用读取；只有到期物化需要短租约。

### 6.6 Inspect

- 可查看 Run、budget/usage、step、approval、invocation、completion、recovery 和 Audit head；
- `--tool-call-id` 可展示 Gate 拒绝的 rule、decision、actor、toolCallId；
- `--approval-id` 可展示审批生命周期并标记 Audit 截断；
- 不输出原始工具参数、完整文件内容、完整模型消息或隐藏推理；
- 未知 Run、Audit chain 失败和查询解析错误不会返回伪成功。

## 7. 规格场景映射

| 场景 | 结果 | 主要证据 |
| --- | --- | --- |
| WP11-AC-001 | PASS | `test_module_and_console_entrypoints_start_the_same_main` |
| WP11-AC-002 | PASS | CLI 只构造 Harness 调用；`test_run_without_model_factory_*` |
| WP11-AC-003 | PASS | `test_run_defaults_to_waiting_and_does_not_write` |
| WP11-AC-004 | PASS | `test_approvals_list_is_bounded_and_does_not_execute`，Audit 数量不变 |
| WP11-AC-005 | PASS | `test_approve_then_resume_executes_write_once_and_accepts` |
| WP11-AC-006 | PASS | `test_reject_stops_run_without_executing_handler` |
| WP11-AC-007 | PASS | `test_approval_requires_an_allowed_non_self_role`、`test_self_approval_and_repeated_approval_are_rejected` |
| WP11-AC-008 | PASS | `test_expired_approval_action_materializes_stop_before_failing` + WP-02 binding tests |
| WP11-AC-009 | PASS | `test_new_process_resume_executes_once_after_console_approval` |
| WP11-AC-010 | PASS | 新进程重复 resume 与 mtime 不变 |
| WP11-AC-011 | PASS | `test_resume_without_approval_returns_waiting_without_model_call` |
| WP11-AC-012 | PASS | `test_wait_mode_approves_during_poll_and_resumes_once` |
| WP11-AC-013 | PASS | `test_wait_timeout_keeps_waiting_without_mutating_approval` |
| WP11-AC-014 | PASS | `test_workspace_change_blocks_resume_without_execution` |
| WP11-AC-015 | PASS | WP-10 `test_unknown_result_blocks_resume_without_model_call` + control query status |
| WP11-AC-016 | PASS | CLI exit mapper + WP-10 budget tests；STOPPED 为退出码 4 |
| WP11-AC-017 | PASS | `test_inspect_exposes_denied_call_audit_without_raw_arguments`，REJECTED 为退出码 7 |
| WP11-AC-018 | PASS | `test_exit_code_mapping_covers_all_terminal_results` |
| WP11-AC-019 | PASS | A1 字段由 tool-call Audit 查询验证 |
| WP11-AC-020 | PASS | `test_inspect_can_filter_approval_audit_and_mark_truncation` + inspect projection |
| WP11-AC-021 | PASS | `test_strict_config_failure_keeps_config_code_and_redacts_value`；inspect 不输出原始参数 |
| WP11-AC-022 | PASS | WP-01/WP-02 lease/claim concurrency tests；CLI 复用同一事务/lease 入口 |
| WP11-AC-023 | PASS | `test_inspect_rejects_unknown_run_*` + WP-01 Audit chain tests |
| WP11-AC-024 | PASS | Darwin POSIX 子进程测试；未宣称 Windows 进程组支持 |
| WP11-AC-025 | PASS | 全量测试离线运行，无真实模型、网络或凭据 |

## 8. 任务卡映射

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| T00 决策冻结 | DONE | D-78 至 D-83、spec frozen、plan approved |
| T01 parser/output/exit | DONE | `cli.py`、`test_usage_errors_*`、exit mapping |
| T02 main/module/console | DONE | entrypoint test 与 help smoke |
| T03 model factory | DONE | 注入/缺省失败测试；P0 无真实 Provider |
| T04 control services | DONE | `control.py`、`ApprovalStore.locate/list_all` |
| T05 `run` | DONE | waiting、无副作用和正常 Completion 路径 |
| T06 `approvals` | DONE | list/approve/reject、权限、过期和重复 |
| T07 `resume` | DONE | 未批准等待、新进程恢复、replay 抑制 |
| T08 wait | DONE | approve during poll、timeout |
| T09 `inspect` | DONE | A1、approval filter、truncation、未知 Run |
| T10 cross-process | DONE | console approve + module-equivalent runner |
| T11 hardening | DONE | 脱敏、严格 parser、工作区变化、原子过期 |
| T12 report | REVIEW | 本报告等待 WP-10 accepted |

## 9. 飞书核心验收映射

| 编号 | 本包证据 | 责任边界 |
| --- | --- | --- |
| G3 | waiting 安全退出和文件不变 | WP-11 完成 CLI 可见结果；Gate 归属上游 |
| G4 | approve 不执行，新进程 resume 一次，重复不写 | WP-11 完成命令和进程边界 |
| C3 | 新进程恢复、预算/Step 保留、写不重复 | WP-11 完成，底层恢复归 WP-07/WP-10 |
| C4 | 工作区变化 BLOCKED，无强放行入口 | WP-11 完成可见事实 |
| A1 | inspect 展示 rule/decision/actor/toolCallId | WP-11 完成查询视图 |
| H1 | REJECTED 非零退出，模型自述不成功 | WP-11 完成退出码 |
| H3 | STOPPED 非零退出，不调用 Completion 的底层约束由 WP-10 提供 | WP-11 完成退出码 |

项目级 G1/G2、S1-S4、C1/C2、A2、H2、T1/T2 仍由 WP-12 汇总复核。

## 10. 安全与故障矩阵

| 主题 | 证据 |
| --- | --- |
| 严格参数 | unknown/invalid flag、wait、limit、ID、config 全部失败关闭 |
| 模型来源 | argv 不接受 import path；无 factory 不访问仓库副作用 |
| 审批身份 | 显式 actor/role、allowed-role 交集、自批拒绝 |
| 审批一次性 | approve 不改 handler；resume 重新过 Gate；重复 resume 不重写 |
| 过期边界 | list effective-only；action 原子物化 EXPIRED/STOPPED 后失败 |
| 只读查询 | list/inspect 不增加 Audit、不启动模型/handler、不改文件 |
| 状态变化 | workspace changed -> BLOCKED，模型 generate 次数为 0 |
| 输出 | config secret 不进入错误文本；inspect 不输出原始参数 |
| 进程边界 | console 审批 + 新 Python 进程恢复 |
| 查询失败 | 未知 Run、非法 ID、Audit/State 异常不返回成功 |

## 11. 限制与剩余风险

1. P0 标准 console 的 `run/resume` 没有真实 Provider，必须由可信 Python 调用方注入
   `model_factory`；这是 D-78 的预期行为。
2. 跨进程测试通过 `tests/cli_model_runner.py` 注入 Fake Model；它不是生产 Provider。
3. `approvals` 与 `inspect` 打开状态库时仍会执行现有初始化/兼容检查，但不会写业务状态或
   Audit；读取事务当前复用 SQLite immediate transaction，会短暂获取写锁。
4. CLI 没有 Web/HTTP 审批、远程身份认证或实时日志流。
5. WP-10 的上游验收报告仍为 review，是本报告不能转 accepted 的唯一直接阻塞。
6. Windows 进程执行和跨平台终端行为未纳入 P0 验收。

## 12. 验收结论

WP-11 的实现、命令行为、跨进程恢复、审批安全边界、只读 inspect 和最终测试证据均通过。

当前报告状态为 `review`，不标记 `accepted`，原因是 WP-10 accepted 依赖尚未完成。上游验收
转为 accepted 后，本报告可直接进入最终复核，不需要新增 WP-11 功能。
