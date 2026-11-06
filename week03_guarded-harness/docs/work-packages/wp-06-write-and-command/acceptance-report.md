# WP-06 写入与命令能力包验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-06 写入与命令能力包 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-06 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / approved |
| 代码基线 | `e5a6177a53d3c32eb7d9e595886693f8b10dd939` |
| 工作区状态 | 包含本工作包预期源码、测试和文档改动；无未解释临时文件 |
| 操作系统 | Darwin 22.6.0 x86_64 |
| Python | 3.13.5（项目 `.venv`） |
| 关键依赖 | `smolagents==1.26.0`、`jsonschema==4.26.0`、`pydantic==2.14.1`、`PyYAML==6.0.3`、`pytest==9.1.1`、`ruff==0.17.3` |

本报告只汇总实际实现与运行证据，不重新定义规格。WP-06 已完成真实文件写入、单文件 patch 和受控
测试命令接入，但测试修复最终验收、Checkpoint 恢复、Agent 和 CLI 仍属于后续工作包。

## 2. 验收范围

已验收能力：

- 可信注册 `write_file`、`apply_patch` 和测试修复任务使用的 `run_test`；
- 严格 schema、任务写范围、Sandbox 路径边界和敏感/平台保护；
- 写入审批、参数绑定、一次性执行和 replay suppression；
- 单文件 CREATE/REPLACE 原子写和 no-op 语义；
- 单文件精确文本 patch 的唯一匹配、顺序应用和全有或全无语义；
- 固定 `acceptance_test` 命令、POSIX 进程组超时/取消和 `UNKNOWN_RESULT`；
- 写入/命令 Evidence、脱敏、审计关联和结果持久化失败关闭；
- 公开注册入口、错误码和 WP-06 文档同步。

非目标：

- 多文件 patch、统一 diff patch、二进制 patch；
- 模型指定 argv、cwd、env、shell、可执行文件、timeout 或 test file；
- 测试基线、测试节点比较、测试报告解析和最终测试修复判定；
- Checkpoint、崩溃恢复动作选择、continuation prompt；
- Agent 装配、CLI 审批命令和跨进程 resume；
- OS 容器级文件系统/网络隔离。

## 3. 交付物

| 交付物 | 结果 | 说明 |
| --- | --- | --- |
| `src/guarded_harness/tools/fs_write.py` | PASS | `write_file` 与 `apply_patch` handler/normalizer |
| `src/guarded_harness/tools/exec.py` | PASS | 固定 `acceptance_test` 命令与结果映射 |
| `src/guarded_harness/tools/__init__.py` | PASS | `register_side_effect_tools` 和严格 ToolSpec |
| `src/guarded_harness/tools/common.py` | PASS | 写范围、schema、输出大小和 digest 辅助 |
| `src/guarded_harness/domain.py` | PASS | WP-06 稳定错误码 |
| `src/guarded_harness/gate.py` | PASS | 可信 normalizer 的结构化语义错误映射 |
| `src/guarded_harness/__init__.py` | PASS | 公开导出 `register_side_effect_tools` |
| `tests/test_tools.py` | PASS | 39 项工具/审批/命令/故障测试（含原有只读回归） |
| `tests/test_gate.py` | PASS | 真实写工具经 Gate 的审批与 replay 测试 |
| `tests/test_sandbox.py` | PASS | 配置命令注册和真实进程边界扩展 |
| `spec.md` / `plan.md` / 本报告 | PASS | 规格、计划、证据和索引一致 |

## 4. 执行证据

命令均在仓库根目录、项目 `.venv` 中执行：

| 命令 | 结果 |
| --- | --- |
| `.venv/bin/python -m pip install -e '.[dev]'` | PASS；editable 包构建和安装成功，依赖已满足 |
| `.venv/bin/python -m pytest tests/test_tools.py tests/test_gate.py tests/test_sandbox.py -q` | PASS；`124 passed in 39.96s`，0 failed，0 skipped |
| `.venv/bin/python -m pytest -q` | PASS；`221 passed in 33.29s`，0 failed，0 skipped |
| `.venv/bin/python -m ruff check .` | PASS；`All checks passed!` |
| `.venv/bin/python -m ruff format --check .` | PASS；`58 files already formatted` |
| `git diff --check` | PASS；无空白错误 |

测试使用临时 Git 仓库、临时 SQLite、固定时钟、fake fault injector 和本地短生命周期 Python 子进程，
未调用真实模型、未访问外部网络、未使用用户凭据。

## 5. 规格场景映射

| 场景 | 测试 | 结果 |
| --- | --- | --- |
| WP06-AC-001 | `tests/test_tools.py::test_side_effect_factory_registers_stable_snapshot` | PASS；三个工具稳定排序，快照无 handler/路径/argv |
| WP06-AC-002 | `tests/test_tools.py::test_side_effect_schemas_reject_before_handler` | PASS；缺字段、错误类型、额外字段均为 0 Invocation |
| WP06-AC-003 | `tests/test_tools.py::test_write_scope_path_escape_and_platform_denials`、`test_write_symlink_escape_is_denied_and_audited` | PASS；穿越和符号链接逃逸在 I/O 前拒绝并审计 |
| WP06-AC-004 | `tests/test_tools.py::test_write_scope_path_escape_and_platform_denials` | PASS；`.git` 与受保护测试文件拒绝 |
| WP06-AC-005 | `tests/test_tools.py::test_write_scope_path_escape_and_platform_denials` | PASS；代码理解非交付物返回 `GATE_WRITE_NOT_ALLOWED` |
| WP06-AC-006 | `tests/test_tools.py::test_write_scope_path_escape_and_platform_denials` | PASS；测试修复 `allow_write` 外路径不变 |
| WP06-AC-007 | `tests/test_tools.py::test_unapproved_write_waits_without_side_effect` | PASS；`WAITING_APPROVAL` 时目标字节不变 |
| WP06-AC-008 | `tests/test_tools.py::test_unapproved_patch_waits_without_side_effect` | PASS；patch 等待时目标不变 |
| WP06-AC-009 | `tests/test_tools.py::test_approved_write_is_suppressed_after_external_change`、`tests/test_gate.py::test_real_write_tool_is_approval_gated_once` | PASS；只执行一次，外部变化不被重放覆盖 |
| WP06-AC-010 | `tests/test_tools.py::test_changed_write_content_invalidates_existing_approval` | PASS；同调用 ID 内容变化被拒绝，文件不变 |
| WP06-AC-011 | `tests/test_tools.py::test_write_file_create_replace_and_noop` | PASS；CREATE、摘要、DIFF Evidence 正确 |
| WP06-AC-012 | `tests/test_tools.py::test_write_file_create_replace_and_noop`、`test_result_persistence_failure_never_claims_success` | PASS；REPLACE 原子提交，结果失败不伪造成功 |
| WP06-AC-013 | `tests/test_tools.py::test_write_rejects_oversize_and_special_targets` | PASS；超限、目录和非 UTF-8 目标拒绝 |
| WP06-AC-014 | `tests/test_tools.py::test_patch_empty_and_noop_rejected_before_handler` | PASS；空编辑 schema 拒绝，no-op 在 handler 前拒绝 |
| WP06-AC-015 | `tests/test_tools.py::test_patch_context_missing_and_ambiguous` | PASS；缺失上下文返回稳定错误且不写 |
| WP06-AC-016 | `tests/test_tools.py::test_patch_context_missing_and_ambiguous` | PASS；歧义上下文返回稳定错误且不写 |
| WP06-AC-017 | `tests/test_tools.py::test_patch_later_failure_does_not_commit_earlier_edits` | PASS；后段失败时前段也不落盘 |
| WP06-AC-018 | `tests/test_tools.py::test_patch_sequential_edits_and_versioned_evidence` | PASS；顺序编辑正确并生成新版本 DIFF Evidence |
| WP06-AC-019 | `tests/test_tools.py::test_run_test_rejects_command_injection_before_popen` | PASS；配置外命令与注入字段被 schema 拒绝，marker 不存在 |
| WP06-AC-020 | `tests/test_tools.py::test_run_test_reports_success_and_nonzero_exit` | PASS；退出 0 产生 COMMAND Evidence |
| WP06-AC-021 | `tests/test_tools.py::test_run_test_reports_success_and_nonzero_exit` | PASS；退出 3 为确定失败，保留退出码和输出 |
| WP06-AC-022 | `tests/test_tools.py::test_run_test_timeout_is_unknown_and_not_retried` | PASS；进程组终止、UNKNOWN、启动计数保持 1 |
| WP06-AC-023 | `tests/test_tools.py::test_run_test_cancellation_maps_unknown`、`tests/test_sandbox.py::test_cancellation_kills_process_group` | PASS；取消终止进程组并映射 UNKNOWN |
| WP06-AC-024 | `tests/test_tools.py::test_command_output_is_bounded_and_redacted` | PASS；截断显式，密钥不进入 observation/Audit，无完整 Evidence |
| WP06-AC-025 | `tests/test_tools.py::test_result_persistence_failure_never_claims_success` | PASS；文件已改变但结果写入失败时不返回成功，Invocation 停在 STARTED |
| WP06-AC-026 | `tests/test_tools.py::test_later_file_change_invalidates_old_write_evidence` | PASS；当前内容摘要与旧 Evidence 不同 |
| WP06-AC-027 | `tests/test_tools.py::test_parallel_writes_are_serialized` | PASS；并发等价写只产生一次物理写入 |
| WP06-AC-028 | `tests/test_tools.py::test_write_command_suite_is_offline_and_repeatable` | PASS；网络入口被禁用，两次同内容写入摘要一致 |

## 6. 任务卡映射

| 任务 | 状态 | 证据 |
| --- | --- | --- |
| WP06-T00 | DONE | D-48 至 D-52、spec v1.0.0、plan v1.0.0 |
| WP06-T01 | DONE | 错误码、严格 schema、写范围与 digest 辅助；AC-002/005/006/014 |
| WP06-T02 | DONE | `write_file` CREATE/REPLACE/no-op；AC-007/009/011/012/013 |
| WP06-T03 | DONE | 单文件 all-or-none patch；AC-008/014 至 018 |
| WP06-T04 | DONE | 固定命令、结果映射、超时和取消；AC-019 至 024 |
| WP06-T05 | DONE | 可信工厂、快照和任务写范围；AC-001 至 006 |
| WP06-T06 | DONE | Gate 真实审批、一次性执行和 replay；AC-007 至 010/027 |
| WP06-T07 | DONE | Evidence、脱敏、故障与审计关联；AC-024 至 026 |
| WP06-T08 | DONE | 124 项目标测试、221 项全量测试、Ruff 和格式通过 |
| WP06-T09 | DONE | 本报告状态 accepted，索引和下游交接已更新 |

## 7. 飞书“核心验收”映射

| 编号 | 本包结论 | 证据/边界 |
| --- | --- | --- |
| G1 | PASS | 三个工具 schema 拒绝均不创建 Invocation |
| G2 | PASS | 写/执行工具最多一次，真实写 replay 不落盘 |
| G3 | PASS | 首次写/patch 进入 `WAITING_APPROVAL`，文件不变 |
| G4 | PASS | 审批后首次写入一次，重放返回原有 Evidence |
| S1 | PASS | 穿越与符号链接逃逸由 Sandbox 在 handler 前拒绝 |
| S3 | PASS | `run_test` 只接受固定 `acceptance_test` |
| S4 | PASS（本包集成范围） | 真实超时终止进程组；生命周期取消接线由 WP-10/WP-11 负责 |
| C2 | PASS（本包事实范围） | 结果持久化失败停在 STARTED，不返回成功；恢复动作由 WP-07 负责 |
| A1 | PASS | 写路径拒绝审计包含 actor、rule、decision、tool_call_id |
| A2 | PASS（支撑） | 写/命令 Evidence 与 Run/Invocation/Approval 可关联；完整任务链由 WP-08/09/12 收口 |
| H2 | PASS（支撑） | 写入产生新内容摘要，旧 Evidence 可识别为过期；最终拒绝由 WP-09 负责 |
| H3 | PASS（支撑） | 所有工具仍经 Gate 预算；Harness StopReason 编排由 WP-10 负责 |
| T1 | PASS | WP-06 目标与全量离线测试全部通过 |

## 8. 安全与故障矩阵

| 注入/边界 | 证据 | 结论 |
| --- | --- | --- |
| 参数缺失、类型错误、额外字段 | AC-002 | DENY before Invocation |
| 路径穿越、绝对越界、符号链接逃逸 | AC-003 | DENY before handler I/O |
| 敏感文件、`.git`、保护测试路径 | AC-004 | Sandbox 平台规则拒绝 |
| 未审批写/patch | AC-007/008 | WAITING，文件不变 |
| 审批参数变化/重放 | AC-009/010 | 旧审批不执行，原副作用不重复 |
| 空/缺失/歧义/no-op patch | AC-014 至 017 | 不写目标 |
| 模型命令注入 | AC-019 | schema 拒绝，无进程启动 |
| 真实超时/取消 | AC-022/023 | 进程组终止，UNKNOWN，不重试 |
| 输出超限/伪密钥 | AC-024 | 有界、截断、脱敏、无完整 Evidence |
| 结果审计失败 | AC-025 | 不返回成功，保留 STARTED 事实 |
| 并发等价写 | AC-027 | Gate 串行，只成功执行一次 |
| 文件版本变化 | AC-026 | 新摘要可见，旧证据不覆盖事实 |

## 9. 限制与剩余风险

- `apply_patch` 只支持单文件精确文本替换；多文件事务尚未提供。
- `run_test` 只运行完整 `acceptance.test_command`，不支持测试文件或节点选择。
- 测试命令按非幂等处理；测试代码仍可能写缓存、报告或工作区文件，最终 diff 必须由 WP-09 检查。
- 当前取消路径验证了 Sandbox 和 `run_test` 结果映射；真实运行生命周期中的取消令牌接线由
  WP-10/WP-11 完成。
- 结果持久化失败时文件可能已经改变，Invocation 保持 `STARTED`。WP-07 必须将其视为未知结果并停止
  自动重放，人工核对前不得继续。
- WP-06 不声称证明测试断言语义未弱化；基线节点、跳过标记、覆盖和报告解析由 WP-09 负责。
- 测试命令以当前用户权限运行，WP-03 的进程内治理不提供容器/虚拟机级隔离或网络隔离。
- 本地审批 actor 沿用 D-24，是可信但未经密码学认证的身份。

## 10. 验收结论

**ACCEPTED。**

WP-06 的冻结规格场景均有自动化证据，目标测试、全量测试、Ruff lint、格式检查和安装命令通过。
工作包达到 `DONE` 条件，可以进入 WP-07 Checkpoint 与恢复。
