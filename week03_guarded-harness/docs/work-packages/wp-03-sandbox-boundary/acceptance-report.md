# WP-03 Sandbox 边界验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-03 Sandbox 边界 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-05 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / done |
| 仓库基线 | `805e7f00ed19dff246d956cd11828234f3f63869` + WP-03 工作区改动 |
| 基线工作区 | 验收前存在 WP-03 未提交改动；本报告记录当前工作树证据 |
| 验收环境 | macOS Darwin 22.6.0 x86_64；Python 3.13.5；pip 25.1.1；git 2.39.2 |

本报告以实际复跑的 Sandbox 路径、文件 I/O、Git 基线、命令和进程测试为证据。WP-03 完成只表示
Sandbox 能力可被下游复用，不表示 Gate、审批、真实仓库工具、恢复或 Completion 已实现。

## 2. 验收范围

### 2.1 已验收能力

- `workspace_root`、`git_root` 和 `workspace_prefix` 的唯一、真实路径绑定；
- `..`、绝对越界、兄弟前缀、Windows/UNC/反斜杠、大小写敏感规则和符号链接逃逸拒绝；
- 平台敏感读、`.git`/依赖目录禁写、任务 `deny_read`/`deny_write` 和窄范围 `allow_write`；
- 10 MiB 有界整文件读取、目录/特殊文件拒绝和读取后路径复核；
- 同目录临时文件、`fsync`、原子创建/替换、预期摘要、权限保留和父目录策略；
- 工作区状态、diff、外围 Git-visible 脏路径、HEAD、index 和空仓库 `UNBORN` 基线；
- 外围保护开关的 `ENABLED/DISABLED` 明确语义和状态目录精确排除；
- 受信 `command_id` 注册、固定可执行 allowlist 和 shell/包装器拒绝；
- 固定 `cwd`、最小环境、凭据隔离、1 MiB head/tail 输出和截断标记；
- Linux/macOS POSIX 进程组、超时、取消、TERM/KILL、未知结果分类和无自动重试；
- 拒绝不触达目标文件、外部路径或 Popen 的副作用证据。

### 2.2 非目标与下游责任

- WP-04：Gate 固定检查链、权限、风险、审批、重复和预算；
- WP-05：只读文件、搜索、目录和 Git handler；
- WP-06：写入、patch 和 `run_test` handler；
- WP-07：Checkpoint、恢复判定和旧证据失效；
- WP-08/WP-09：代码理解、测试修复 Completion；
- WP-10/WP-11：Agent、Harness、CLI 和跨进程恢复；
- WP-12：两类任务端到端、全局故障矩阵和最终交付。

WP-03 不提供 OS 级文件系统或网络隔离。测试命令只允许在可信仓库中执行。

## 3. 交付物

| 交付物 | 位置 | 结果 |
| --- | --- | --- |
| Sandbox 路径、安全 I/O、Git、命令和进程实现 | `src/guarded_harness/sandbox.py` | PASS |
| Sandbox 公共错误码 | `src/guarded_harness/domain.py` | PASS |
| 包根公共导出 | `src/guarded_harness/__init__.py` | PASS |
| WP-03 安全边界测试 | `tests/test_sandbox.py` | PASS |
| 规格和已批准计划 | `docs/work-packages/wp-03-sandbox-boundary/spec.md`、`plan.md` | PASS |
| 设计、决策、任务拆解和索引 | `docs/design.md`、`decisions.md`、`task-breakdown.md`、索引文件 | PASS |

## 4. 实际执行证据

所有命令在仓库根目录执行，使用项目 `.venv`。

| 检查 | 命令 | 实际结果 |
| --- | --- | --- |
| Editable 安装 | `python -m pip install -e '.[dev]'` | PASS；成功构建并安装 editable `guarded-harness==0.1.0` |
| WP-03 目标测试 | `python -m pytest tests/test_sandbox.py -q` | `52 passed in 17.57s` |
| 全量测试 | `python -m pytest -q` | `145 passed in 18.68s` |
| Lint | `python -m ruff check .` | PASS；`All checks passed!` |
| 格式 | `python -m ruff format --check .` | PASS；`37 files already formatted` |
| 依赖一致性 | `python -m pip check` | PASS；`No broken requirements found.` |
| 文档空白检查 | `git diff --check` | PASS |

测试无失败、error、skip 或 xfail。目标测试不访问真实模型、网络或用户凭据。

## 5. 规格场景映射

| 规格场景 | 结果 | 主要测试证据 |
| --- | --- | --- |
| WP03-AC-001 工作区/Git 身份 | PASS | `test_workspace_and_git_identity` |
| WP03-AC-002 路径语法 | PASS | `test_path_syntax_boundaries` |
| WP03-AC-003 穿越/绝对越界 | PASS | `test_traversal_absolute_and_sibling_escape_are_rejected`、`test_rejection_has_no_external_side_effect` |
| WP03-AC-004 兄弟前缀 | PASS | `test_traversal_absolute_and_sibling_escape_are_rejected` |
| WP03-AC-005 符号链接逃逸 | PASS | `test_symlink_escape_and_internal_resolution`、`test_revalidation_detects_symlink_retarget` |
| WP03-AC-006 工作区内软链接 | PASS | `test_symlink_escape_and_internal_resolution` |
| WP03-AC-007 敏感文件 | PASS | `test_platform_sensitive_and_deny_write_rules` |
| WP03-AC-008 平台禁写 | PASS | `test_platform_sensitive_and_deny_write_rules` |
| WP03-AC-009 任务禁止/例外 | PASS | `test_task_deny_read_write_and_allow_exception` |
| WP03-AC-010 检查后变化 | PASS | `test_revalidation_detects_symlink_retarget` |
| WP03-AC-011 初始工作区干净 | PASS | `test_dirty_workspace_and_dirty_baseline_semantics` |
| WP03-AC-012 外围脏基线 | PASS | `test_external_dirty_baseline_detects_change` |
| WP03-AC-013 外围新增/删除 | PASS | `test_external_new_and_deleted_paths_are_detected` |
| WP03-AC-014 HEAD/index/身份 | PASS | `test_head_and_index_changes_are_identity_changes` |
| WP03-AC-015 Git-ignored 风险 | PASS | `test_ignored_external_file_is_not_claimed_as_protected` |
| WP03-AC-016 受信命令 | PASS | `test_task_repair_acceptance_command_is_registered`、`test_command_registry_rejects_untrusted_executables` |
| WP03-AC-017 cwd/环境 | PASS | `test_fixed_cwd_and_minimal_environment` |
| WP03-AC-018 shell 字面量 | PASS | `test_shell_metacharacters_remain_literal_arguments` |
| WP03-AC-019 输出上限 | PASS | `test_stdout_and_stderr_are_bounded` |
| WP03-AC-020 超时进程组 | PASS | `test_timeout_kills_process_group` |
| WP03-AC-021 用户取消 | PASS | `test_cancellation_kills_process_group` |
| WP03-AC-022 结果确定性 | PASS | `test_process_result_classification_matrix`、`test_start_failure_is_structured` |
| WP03-AC-023 无自动重试 | PASS | `test_sandbox_never_auto_retries` |
| WP03-AC-024 损坏/异常 | PASS | `test_non_git_workspace_fails_closed`、`test_malformed_git_status_fails_closed` |
| WP03-AC-025 拒绝无真实副作用 | PASS | `test_rejection_has_no_external_side_effect`、`test_atomic_write_injected_failure_leaves_no_partial_target` |
| WP03-AC-026 飞书 S1-S4 部分证据 | PASS | `test_feishu_s1_s4_partial_mapping` |
| WP03-AC-027 离线确定性 | PASS | WP-03 全量目标测试；无网络/模型/凭据依赖 |
| WP03-AC-028 安全读取 | PASS | `test_read_file_is_bounded_and_regular_only` |
| WP03-AC-029 创建/替换/摘要 | PASS | `test_atomic_write_create_replace_and_expected_digest` |
| WP03-AC-030 原子失败/权限 | PASS | `test_atomic_write_create_replace_and_expected_digest`、`test_atomic_write_injected_failure_leaves_no_partial_target` |
| WP03-AC-031 父目录策略 | PASS | `test_create_requires_absent_target_and_parent_policy`、`test_create_parents_obeys_deny_and_allow_exception` |
| WP03-AC-032 平台能力 | PASS | `test_unsupported_platform_fails_before_popen` |
| WP03-AC-033 外围保护关闭 | PASS | `test_disabled_external_protection_is_explicit` |
| WP03-AC-034 空仓库/特殊类型 | PASS | `test_unborn_repository_uses_stable_sentinel`、`test_symlink_gitlink_and_special_file_baseline_edges` |

## 6. 任务卡验收

| 任务 | 状态 | 依据 |
| --- | --- | --- |
| WP03-T01 公共类型与错误码 | DONE | 包根导出、公开枚举/结果类型和 Sandbox 错误码契约通过 |
| WP03-T02 工作区身份与路径边界 | DONE | 真实根、前缀、穿越、前缀误判和软链接测试通过 |
| WP03-T03 路径策略与平台规则 | DONE | 敏感、平台禁写、任务 deny/allow 与 revalidation 测试通过 |
| WP03-T04 安全文件 I/O | DONE | 10 MiB 读取、CREATE/REPLACE、摘要冲突、原子故障和权限测试通过 |
| WP03-T05 Git 基线与变化比较 | DONE | 干净/脏/外围/ignored/UNBORN/gitlink/状态目录测试通过 |
| WP03-T06 受信命令与最小环境 | DONE | command_id、可执行 allowlist、cwd、env 和 shell 字面量测试通过 |
| WP03-T07 进程执行与输出 | DONE | 输出截断、超时、取消、进程组终止、启动失败和分类矩阵通过 |
| WP03-T08 安全回归 | DONE | 副作用为 0、无自动重试、损坏 Git 和平台失败关闭通过 |
| WP03-T09 文档与验收 | DONE | spec/plan/decision/design/index/report 同步，验收命令复跑通过 |

## 7. 安全与故障矩阵

| 边界 | 实际证据 |
| --- | --- |
| 路径穿越/绝对越界 | 稳定错误码，外部文件摘要不变 |
| 软链接逃逸 | 真实落点越界拒绝；检查后重定向得到 `SANDBOX_PATH_CHANGED` |
| 敏感文件 | `.ENV`、密钥、凭据路径拒绝且不读取内容 |
| `.git`/依赖写入 | 平台规则先于任务例外拒绝 |
| 超限读取/写入 | 10 MiB 上限失败关闭，不返回部分内容 |
| 摘要冲突 | 目标文件字节不变 |
| 原子写故障 | 无部分目标，临时文件和未提交父目录被清理 |
| 外围保护关闭 | 明确返回 `external_protection=DISABLED`，不伪造“无变化” |
| 未注册/shell 命令 | Popen 前拒绝，目标 marker 不存在 |
| 宿主凭据环境 | 子进程环境仅包含固定 allowlist，凭据原值未出现 |
| 输出超限 | stdout/stderr 分别截断，进程退出事实保留 |
| 超时/取消 | POSIX 进程组和后代真实终止 |
| 非幂等超时 | `UNKNOWN_RESULT`，Sandbox 不自动重试 |
| 不支持平台 | 构造阶段 `SANDBOX_UNSUPPORTED_PLATFORM`，Popen 未执行 |
| Git 不可用/损坏 | 非 Git 工作区和 malformed status 失败关闭 |

## 8. 飞书核心验收映射

| 飞书编号 | WP-03 证据 | 状态 | 下游 |
| --- | --- | --- | --- |
| S1 路径逃逸 | Sandbox 对 `artifacts/../../.env` 拒绝，外部文件不变 | 直接 PASS | WP-04/WP-05 认证 handler 调用为 0 |
| S2 软链接/敏感目录 | 真实落点判定、敏感规则和结构化拒绝 | 直接 PASS | WP-04 记录审计 |
| S3 配置外命令 | 未注册 command_id 和不可信 executable 在 Popen 前拒绝 | 直接 PASS | WP-06 接入 `run_test` |
| S4 超时/取消 | 父和后代进程组终止，结果结构化 | 直接 PASS | WP-06/WP-11 映射业务终态 |
| C4 工作区变化 | 工作区、外围、HEAD/index/身份比较 | 基础 PASS | WP-07 失效证据并停止恢复 |
| A2 全链路关联 | 结果含 command_id、路径、错误码和摘要 | 基础 PASS | WP-01/WP-04 建立完整审计链 |
| T1 确定性测试 | 52 项目标测试、145 项全量测试通过 | PASS | WP-12 全局收口 |
| T2 真实模型任务 | 未执行 | N/A（P1） | WP-12/P1 |

## 9. 限制与剩余风险

1. 测试命令和仓库代码按可信但可能出错处理；没有 OS 级文件系统和网络隔离。
2. 工作区外 `.gitignore` 忽略文件不进入 Git 基线，不能被宣称为受保护。
3. 被 Git 优化成目录的未跟踪特殊结构可能不在 baseline status 中出现；工具读取该路径时仍会因
   特殊文件类型失败关闭，但基线阶段不能保证枚举所有 ignored/special 内容。
4. 文件路径检查与最终 I/O 之间仍有协作式 TOCTOU 窗口；安全 I/O 使用 canonical path、
   `O_NOFOLLOW`、identity 复核和原子替换降低风险，但不抵御拥有同级权限的攻击者持续替换目录。
5. 进程执行仅在 macOS/Python 3.13 实测；Linux 使用同一 POSIX 机制但未在独立运行时复验，
   Windows 明确失败关闭。
6. 输出被截断时退出码事实仍有效，但依赖完整输出的测试报告或证据必须由 WP-09 拒绝。
7. 本包不记录业务 Audit、不更新 State/Approval，也不提供 Completion 结论。
8. 当前验收基于未提交的 WP-03 工作区改动；提交后应以最终提交哈希补充发布基线。

## 10. 验收结论

**WP-03 Sandbox 边界：ACCEPTED。**

冻结规格中的 34 个场景、T01-T09、目标测试、全量测试、Ruff、格式、依赖和文档检查均有实际
复跑证据。WP-04、WP-05、WP-06 和 WP-07 可以在 D-29 至 D-36 的 Sandbox 契约上继续施工。
