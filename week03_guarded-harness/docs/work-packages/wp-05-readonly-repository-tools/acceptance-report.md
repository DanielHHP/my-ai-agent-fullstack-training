# WP-05 只读仓库能力包验收报告

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-05 只读仓库能力包 |
| 报告状态 | accepted |
| 报告版本 | 1.0.0 |
| 验收日期 | 2026-11-06 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 对应计划 | [plan.md](plan.md) v1.0.0 / done |
| 仓库基线 | `7439bb352d9db4bc0ba4b455b08b9bb87c006704` + WP-03 v1.1/WP-05 工作区改动 |
| 验收环境 | macOS Darwin 22.6.0 x86_64；Python 3.13.5；pip 25.1.1；git 2.39.2 |

本报告以实际运行的文件读取、搜索、目录枚举、Git status/diff、Gate 集成和安全拒绝测试为证据。
WP-05 完成只表示只读仓库工具及读取 Evidence 可供下游使用，不表示写入、测试命令、恢复、Agent、
CLI 或 Completion 已完成。

## 2. 验收范围

### 2.1 已验收能力

- `list_files`、`search_code`、`read_file`、`read_directory`、`git_status`、`git_diff` 六个工具；
- 可信工具工厂注册、稳定模型快照和无 handler/Sandbox/本机路径泄漏；
- 严格 JSON Schema、可信默认参数归一化、重复调用摘要和 Gate 前置拒绝；
- 10 MiB Sandbox 读取上限、UTF-8/二进制判断、行窗口、稳定截断和 next offset；
- 字面量搜索、大小写折叠、scope/glob/context、命中/文件/字节上限；
- 安全目录枚举、依赖/缓存/构建目录跳过、敏感条目不回显；
- 嵌套工作区 Git 路径转换、禁读路径过滤、staged/unstaged diff 和受限 pathspec；
- READ Evidence 的路径、行范围、完整文件摘要、`run_id` 和 `tool_call_id`；
- COMMAND/DIFF Evidence、Gate 脱敏、结构化失败和审计关联；
- 拒绝/失败不产生伪成功、不触达未授权文件、不把 Git 失败报告成空 diff。

### 2.2 非目标与下游责任

- WP-06：写入、patch、受控测试命令、审批后真实副作用；
- WP-07：Checkpoint、恢复、旧证据失效和结果未知处理；
- WP-08/WP-09：代码理解引用验收与测试修复验收；
- WP-10/WP-11：Agent 适配、Harness 主循环、CLI 和跨进程恢复；
- WP-12：两类任务端到端、全局故障矩阵和最终交付。

## 3. 交付物

| 交付物 | 位置 | 结果 |
| --- | --- | --- |
| 只读工具共享契约/辅助 | `src/guarded_harness/tools/common.py` | PASS |
| 文件、搜索和目录工具 | `src/guarded_harness/tools/fs_read.py` | PASS |
| Git status/diff 工具 | `src/guarded_harness/tools/git.py` | PASS |
| 可信工具工厂 | `src/guarded_harness/tools/__init__.py` | PASS |
| 工具稳定错误码 | `src/guarded_harness/domain.py` | PASS |
| Sandbox 安全枚举与受限 pathspec | `src/guarded_harness/sandbox.py` | PASS |
| 公共导出 | `src/guarded_harness/__init__.py` | PASS |
| WP-05 集成测试 | `tests/test_tools.py` | PASS |
| WP-03 v1.1 兼容测试 | `tests/test_sandbox.py` | PASS |
| 决策、设计、规格、计划、索引 | `docs/` 对应文件 | PASS |

## 4. 实际执行证据

所有命令在仓库根目录执行，使用项目 `.venv`。

| 检查 | 命令 | 实际结果 |
| --- | --- | --- |
| Editable 安装 | `python -m pip install -e '.[dev]'` | PASS |
| WP-05 目标测试 | `python -m pytest tests/test_tools.py -q` | `16 passed` |
| WP-03 兼容目标测试 | `python -m pytest tests/test_sandbox.py -q` | `56 passed` |
| 全量测试 | `python -m pytest -q` | `196 passed` |
| Lint | `python -m ruff check .` | PASS；`All checks passed!` |
| 格式 | `python -m ruff format --check .` | PASS；`53 files already formatted` |
| 依赖一致性 | `python -m pip check` | PASS；`No broken requirements found.` |
| 文档空白检查 | `git diff --check` | PASS |

测试无失败、error、skip 或 xfail；不访问真实模型、网络或用户凭据。

## 5. 规格场景映射

| 规格场景 | 结果 | 主要测试证据 |
| --- | --- | --- |
| WP05-AC-001 工厂与快照 | PASS | `test_readonly_factory_registers_stable_snapshot` |
| WP05-AC-002 严格 schema | PASS | `test_strict_tool_schemas_reject_before_handler` |
| WP05-AC-003 路径逃逸与 A1 | PASS | `test_readonly_path_escape_denied_before_handler` |
| WP05-AC-004 敏感/软链接遍历 | PASS | `test_traversal_omits_symlink_and_sensitive_targets`、Sandbox v1.1 目录测试 |
| WP05-AC-005 三次重复读取 | PASS | `test_third_equivalent_read_is_rejected` |
| WP05-AC-006 分页读取 | PASS | `test_read_file_paginates_with_versioned_evidence` |
| WP05-AC-007 空文件/字节上限 | PASS | `test_read_and_search_bounds_are_explicit` |
| WP05-AC-008 二进制/非法 UTF-8/大文件 | PASS | `test_read_errors_and_redaction_are_structured`、`test_oversized_and_non_utf8_files_fail_closed` |
| WP05-AC-009 字面量搜索 | PASS | `test_literal_search_scope_glob_case_and_context`、`test_default_arguments_have_one_canonical_digest` |
| WP05-AC-010 搜索截断 | PASS | `test_read_and_search_bounds_are_explicit` |
| WP05-AC-011 搜索 Evidence | PASS | `test_literal_search_scope_glob_case_and_context` |
| WP05-AC-012 文件版本变化 | PASS | `test_file_change_produces_new_content_digest` |
| WP05-AC-013 列表/目录边界 | PASS | `test_listing_is_sorted_and_bounded` |
| WP05-AC-014 Git status | PASS | `test_git_status_filters_denied_paths` |
| WP05-AC-015 Git diff/pathspec | PASS | `test_git_diff_pathspec_and_staged_modes` |
| WP05-AC-016 Git 失败 | PASS | `test_git_failures_are_not_reported_as_empty` |
| WP05-AC-017 脱敏与有界输出 | PASS | `test_read_errors_and_redaction_are_structured` |
| WP05-AC-018 结果持久化故障 | PASS | `test_result_audit_failure_leaves_invocation_started`（WP-04 Gate） |

## 6. 任务卡验收

| 任务 | 状态 | 依据 |
| --- | --- | --- |
| WP05-T00 决策与兼容冻结 | DONE | D-45 至 D-47、WP-03 v1.1 规格/实现/测试 |
| WP05-T01 契约与纯辅助 | DONE | 严格 schema、错误码、glob、digest、Evidence 辅助 |
| WP05-T02 `read_file` | DONE | 行窗口、摘要、边界和错误测试 |
| WP05-T03 目录/list | DONE | Sandbox 枚举、稳定排序、limit/omission |
| WP05-T04 `search_code` | DONE | 字面量、大小写、context、预算、Evidence |
| WP05-T05 `git_status` | DONE | 状态解析、禁读过滤、失败和 index 不变 |
| WP05-T06 `git_diff` | DONE | 根/文件 scope、staged、pathspec、禁读过滤 |
| WP05-T07 工厂与 Gate | DONE | 六工具真实 handler 经 Gate，拒绝前置 |
| WP05-T08 全量验证 | DONE | 196 项测试、Ruff、格式、pip check |
| WP05-T09 报告与交接 | DONE | spec/plan/decision/design/index/report 同步 |

## 7. 安全与故障矩阵

| 边界 | 实际证据 |
| --- | --- |
| schema 缺字段/类型/额外字段 | Gate Schema 拒绝，invocation 数 0 |
| 路径穿越/敏感文件 | Gate Semantic 拒绝，读取与 invocation 均未发生 |
| 工作区外软链接 | 目录枚举聚合省略，不返回名称/内容 |
| 递归目录链接 | Sandbox 不跟随，避免循环和逃逸 |
| 禁读 Git 路径 | status 省略；diff 只传允许 staged/unstaged pathspec |
| Git 选项/revision 注入 | Schema 拒绝；可信命令固定 argv |
| Git 失败/超时/坏输出 | 稳定 `TOOL_GIT_*` 失败，不返回空 diff/clean |
| 嵌套工作区路径 | `--relative` 与 workspace prefix 转换后再次经过 Sandbox |
| index 稳定性 | `--no-optional-locks`；staged diff 前后 index 摘要相同 |
| 二进制/非法 UTF-8/大文件 | 稳定失败，不返回部分源码 |
| 长输出/命中上限 | `truncated`/`has_more`/limit reason 明确 |
| 测试密钥 | Gate observation 脱敏；Evidence 保存摘要而非正文 |
| 文件版本变化 | 两次读取得到不同摘要，Evidence 各绑定原版本 |

## 8. 飞书核心验收映射

| 飞书编号 | WP-05 证据 | 状态 | 下游 |
| --- | --- | --- | --- |
| G1 参数错误 handler 为 0 | 严格 schema + Gate integration | 直接 PASS | WP-12 |
| G2 第三次重复读取拒绝 | 真实 `read_file` 前两次执行、第三次拒绝 | 直接 PASS | WP-12 |
| S1 路径逃逸 | 真实只读工具在 Gate/Sandbox 前拒绝 | 直接 PASS | WP-12 |
| S2 软链接/敏感访问 | 直接拒绝；递归扫描不泄露名称 | 直接 PASS | WP-12 |
| A1 拒绝审计 | Gate 审计含 decision/rule/actor/tool_call_id | 直接 PASS | WP-12 |
| A2 全链路关联 | READ/COMMAND/DIFF Evidence 关联 run/tool call | 基础 PASS | WP-08/WP-12 |
| H2 内容版本 | Evidence 保存完整文件 SHA-256 | 基础 PASS | WP-07/WP-09/WP-12 |
| T1 确定性测试 | 16 项 WP-05 目标测试、196 项全量测试 | PASS | WP-12 |

## 9. 限制与剩余风险

1. P0 搜索只支持字面量和大小写折叠，不支持正则、语言解析或语义索引。
2. 单文件读取/搜索候选受 10 MiB 上限；更大的生成文件返回明确失败。
3. 目录/搜索扫描最多检查 20,000 个条目；单次搜索最多 5,000 个文件、64 MiB 候选内容和 200
   个返回命中。达到上限时不会声称结果完整。
4. 允许读取的文件内部如含未被现有规则识别的凭据，仍可能进入模型；P0 只承诺路径级敏感规则和
   Gate 已知模式脱敏，不承诺内容级 DLP。
5. Git timeout 的实际进程组终止由 WP-03 测试证明；WP-05 对超时结果的结构化映射使用确定性 fake
   process，未等待真实 10/15 秒 Git 超时。
6. 目录枚举和最终文件读取之间仍存在协作式 TOCTOU 窗口；Sandbox 使用 canonical 路径、身份复核
   和稳定排序降低风险，但不抵御同一权限攻击者持续替换目录。
7. 只读工具声明为 `SERIAL`；P0 不开放 Gate 只读并发，避免审批/审计或扫描预算并发复杂度。
8. Git 仅验证 macOS/Git 2.39；Linux 使用同一 POSIX 机制但未在独立运行时复验。

## 10. 验收结论

**WP-05 只读仓库能力包：ACCEPTED。**

冻结规格中的 18 个场景、T00-T09、目标/全量测试、Ruff、格式、依赖和文档检查均有实际证据。
WP-06 可以复用只读工具和 Evidence 契约继续写入/命令能力；WP-08 可以消费 READ Evidence 做代码
理解验收。WP-05 不宣称写操作、测试命令、恢复、Agent 或 Completion 已完成。
