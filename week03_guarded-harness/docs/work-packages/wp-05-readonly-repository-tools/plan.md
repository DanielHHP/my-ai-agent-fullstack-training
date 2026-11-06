# WP-05 只读仓库能力包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-05 只读仓库能力包 |
| 文档状态 | done |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-06 |
| 批准日期 | 2026-11-06 |
| 完成日期 | 2026-11-06 |
| 对应规格 | `spec.md` v1.0.0 / frozen |
| 前置依赖 | WP-04 v1.0.0 accepted；WP-03 v1.1.0 compatible revision |
| 施工前置 | 已满足：D-45 至 D-47 已确认，WP-03 兼容契约已冻结 |
| 当前结论 | T00 至 T09 已完成，验收报告 accepted |
| 下游影响 | WP-06 写/命令工具、WP-08 代码理解验收、WP-10 Agent 工具快照 |

本计划不重新定义规格。施工中发现行为、接口、错误码或安全边界缺口时，必须停止当前任务，先更新并
评审 `spec.md`；涉及 Sandbox 公共接口时先更新 WP-03 规格和 `decisions.md`。

## 2. 计划目标与完成定义

WP-05 完成后必须通过公共接口和临时 Git 工作区证明：

1. 六个只读工具由可信工厂注册，模型只能通过 Gate 调用；
2. 模型快照稳定且不泄漏 handler、Sandbox、本机路径、argv 或内部命令；
3. 所有工具根 schema 拒绝额外字段，默认参数经过规范化并参与重复调用摘要；
4. 路径、敏感文件、符号链接和目录穿越均由 Sandbox 执行实际 I/O 边界；
5. `read_file` 和 `search_code` 生成可关联路径、完整行范围、完整文件摘要和 `tool_call_id` 的 Evidence；
6. 六个工具均有条数/字节/时间上限，所有截断都可由结构化字段判断；
7. `git_status`/`git_diff` 只执行固定只读命令，受限 pathspec 不修改 index 或工作区；
8. 拒绝测试证明 handler 或 Git 进程未启动，A1 审计字段完整；
9. 目标测试、全量测试、Ruff lint 和格式检查通过；
10. 生成 `acceptance-report.md`，由实际命令、测试和故障证据决定是否 `accepted`。

完成 WP-05 不等于写工具、受控测试命令、Completion 或 Agent 适配完成。

## 3. 文件范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── tools/
│   ├── __init__.py       # 新增：可信只读工具组装/注册
│   ├── common.py         # 新增：规范化、glob、排序、输出/Evidence 辅助
│   ├── fs_read.py        # 新增：list/search/read file/directory handler
│   └── git.py            # 新增：git status/diff handler
├── domain.py             # 扩展：稳定错误码、必要输出/枚举契约
├── sandbox.py            # 兼容扩展：安全枚举、受控 pathspec
├── __init__.py           # 扩展：公共导出和版本
└── _version.py           # 按发布约定提升版本

tests/
├── test_tools.py                  # 新增：六个工具正常/拒绝/边界
├── test_gate.py                   # 扩展：真实只读 handler 经 Gate
├── test_sandbox.py                # 扩展：目录枚举和 pathspec 兼容契约
└── conftest.py                    # 按需扩展临时仓库与故障 fixture

docs/work-packages/wp-05-readonly-repository-tools/
├── spec.md
├── plan.md
└── acceptance-report.md           # 实测后创建
```

WP-05 不创建 `tools/fs_write.py`、`tools/exec.py`、`checkpoint.py`、`completion.py`、`harness.py`、
`agent_adapter.py` 或 `cli.py`。

### 3.2 依赖方向

```text
domain.py
   ^
   |
sandbox.py ─────> config.py
   ^
   |
tools/common.py
   ^          ^
   |          |
fs_read.py   git.py
   \          /
    \        /
     tools/__init__.py ─────> registry.py

Gate ─────> registry.py + sandbox.py + public domain
tests ────> public registration/factory + Gate + Sandbox
```

禁止：

- `tools/*` 导入 `gate.py` 并自行调用 `Gate.invoke()`；
- `tools/*` 导入 State/Audit/Approval/Checkpoint；
- `git.py` 自行调用 `subprocess`；
- `fs_read.py` 直接读取目标路径或递归遍历目标目录；
- 在 `tools/*` 内根据任务名选择路径/权限策略；
- 使用模型参数构造 Git revision、额外 argv、环境变量或输出路径。

## 4. 详细设计

### 4.1 装配与注册

`register_readonly_tools(registry, sandbox)` 的顺序：

```text
validate registry/sandbox public types
  -> build fs_read definitions (sorted by tool name)
  -> build git definitions (sorted by tool name)
  -> validate all schemas and handler callables through ToolDefinition
  -> check duplicate names before mutating registry
  -> register all definitions
  -> return registered names in stable order
```

为保证“整批装配不留下部分注册”，建议先构造 `tuple[ToolDefinition,...]`，检查与 Registry 的冲突后
再逐个注册。Registry 当前在名称冲突时抛错；工厂不得通过吞掉异常来宣称部分成功。

### 4.2 可信 Git 命令注册

工具工厂只注册固定的只读命令，例如：

```text
readonly_git_status
  git --no-optional-locks --no-pager --literal-pathspecs status --porcelain=v1 -z --untracked-files=all

readonly_git_diff_names
  git --no-optional-locks --no-pager --literal-pathspecs diff --no-ext-diff --no-textconv --no-color --name-only -z -- .

readonly_git_diff_names_staged
  git --no-optional-locks --no-pager --literal-pathspecs diff --no-ext-diff --no-textconv --no-color --cached --name-only -z -- .

readonly_git_diff
  git --no-optional-locks --no-pager --literal-pathspecs diff --no-ext-diff --no-textconv --no-color --full-index --

readonly_git_diff_staged
  git --no-optional-locks --no-pager --literal-pathspecs diff --no-ext-diff --no-textconv --no-color --full-index --cached --
```

命令 ID 为内部固定值，不进入 ToolSpec。按 D-47，最后两条命令以 `--` 结束并声明
`allow_pathspecs=true`；实际 argv 为固定 argv 后追加经过 Sandbox 再次复核的路径。`--no-optional-locks`
防止只读查询刷新 index，`--literal-pathspecs` 防止合法文件名中的 glob 字符被解释为 pathspec magic。
如果无法获得允许路径集合，`git_diff` 必须返回失败而不是运行无过滤的全局 diff。

可信命令注册与六个 ToolDefinition 的构造属于同一装配步骤。测试必须断言：

- command ID 重复但 argv 不同时失败关闭；
- 模型无法通过参数改变 command ID、加入 revision 或追加 `--exec-path`/`--upload-pack`；
- Git 子进程使用 Sandbox 的最小环境和固定 cwd。

### 4.3 共享辅助

`common.py` 只放不执行 I/O 的纯函数和契约：

- 默认参数归一化；
- 工作区相对 POSIX glob 校验与确定性匹配；
- 路径范围/前缀比较；
- 有界 UTF-8 解码、行切分、单行 clipping；
- 稳定 JSON canonical digest；
- `HandlerResult` 的稳定失败/成功构造；
- EvidenceDraft 去重和按稳定顺序输出。

不得把 Sandbox 路径校验隐藏在 `common.py`，否则容易绕过 Gate 或复制安全规则。

### 4.4 `read_file`

```text
args = Gate-normalized {path, offset, limit}
file = sandbox.read_file(path, max_bytes=10 MiB)
reject NUL / invalid UTF-8
lines = splitlines(file.data decoded)
resolve offset/limit and output byte budget
render line-numbered content at complete line boundaries
evidence = READ(path, start_line, end_line, file.content_digest)
return structured ReadFileOutput + HandlerResult(SUCCEEDED, evidence)
```

边界：

- `offset` 超出非空文件时 `TOOL_RANGE_OUT_OF_BOUNDS`；
- 空文件 `offset=1` 返回完整空结果；
- 输出字节预算按 UTF-8 编码后计算，给 JSON envelope 留固定余量；
- 至少返回一行（若存在）以避免永久无法前进；超长单行采用固定字符上限并标记 clipping；
- 不把输入文件全量塞入 `summary`；`output` 只保存结构化结果；
- 输出和 evidence 的行范围必须来自同一渲染结果，禁止先构造完整 Evidence 再自行裁剪可见内容。

### 4.5 `search_code`

```text
scope = sandbox.check_read(path)  # 已由 Gate 做一次，Sandbox helper 仍按公共接口复核
files = enumerate files from sandbox file walk under scope
sort files by workspace-relative path
apply fixed glob
for each candidate:
  skip dependency/protected/denied/binary/oversized files
  read full text through sandbox.read_file
  find literal matches line by line
  for each accepted hit:
    render requested context
    add hit and contiguous EvidenceDraft range
  stop at hit/file/byte budget
return structured SearchOutput and EvidenceDrafts
```

默认搜索实现只用线性字面量匹配（`str.find`/等价），不把模型输入交给 `re`。大小写折叠使用固定 Unicode
规则，测试必须覆盖 ASCII、中文和大小写等价输入。扫描顺序、命中顺序和上下文范围在所有平台上稳定。

已达到命中上限时不得继续读取更多文件来“确认没有更多”，直接返回 `has_more=true`。文件或字节扫描
预算耗尽时返回 `scan_limit_reached=true`；两个截断原因可同时存在。

### 4.6 `list_files` 与 `read_directory`

`list_files`：

- 从 Sandbox 安全文件枚举获取候选，不直接调用 `Path.rglob`；
- 以工作区相对路径匹配 `pattern`，按路径排序；
- 只返回普通文件；依赖目录和敏感路径不进入结果；
- `limit` 控制返回条数，扫描预算控制总工作量；
- 不生成内容 Evidence。

`read_directory`：

- 调用 `Sandbox.list_directory`，只读取下一层；
- 目录项在前、文件项在后，同类按名称和 Unicode 码点稳定排序；
- 不跟随目录 symlink；对 symlink 目标保留/省略按 SPEC 6.6；
- 聚合 `omitted_count`，不返回被拒名称；
- 不生成内容 Evidence。

### 4.7 Git status/diff

`git_status`：

```text
process = sandbox.execute("readonly_git_status")
require SUCCEEDED and exit_code == 0
parse NUL-delimited porcelain records
normalize rename/copy records
filter each path through Sandbox.read policy
sort by path
return GitStatusOutput + COMMAND evidence digest
```

空 stdout 仅在确定成功、无解析错误且没有省略项时映射为 `clean=true`。解析失败返回
`TOOL_GIT_OUTPUT_INVALID`，不能返回空列表。

`git_diff`：

```text
scope = Gate-normalized path
name_process = fixed read-only diff --name-only command, staged or unstaged
parse and filter changed paths under scope through Sandbox read policy
if no visible path: return successful empty diff without invoking patch command
patch_process = sandbox.execute(diff command_id, pathspecs=validated visible paths)
verify success, bound/parse/filter output
normalize paths while preserving patch semantics
return GitDiffOutput + DIFF evidence digest
```

实现必须使用 D-47 的受控 pathspec。若该上游接口在运行期拒绝路径，工具返回结构化失败，不能回退为
未过滤的全局 patch 或由 handler 自行拼接 argv。

### 4.8 Evidence 与输出边界

- 成功 `read_file`：一个 READ draft，范围为实际返回行；
- 成功 `search_code`：按文件/连续上下文范围生成 READ draft，稳定去重；
- 成功 `git_status`：一个 COMMAND draft，摘要覆盖过滤后的结构化输出；
- 成功 `git_diff`：一个 DIFF draft，摘要覆盖过滤后 patch；
- Evidence 不含源码正文，不保存完整 argv 或本机绝对路径；
- handler 构造输出后自检不超过 ToolSpec 输出上限；若超限在 handler 内缩小结果并显式标记，不能依赖
  Gate 生成不可解释 preview 后仍返回 SUCCEEDED；
- 所有输出在返回前由 Gate 再次脱敏，工具不得用“已脱敏”为由降低上限。

## 5. 施工任务卡

| 任务 | 内容 | 对应规格 | 依赖 | 完成条件 |
| --- | --- | --- | --- | --- |
| WP05-T00 | 关闭 D-45 至 D-47，冻结 WP-03 兼容修订 | `spec.md` 10、12 | 已完成 | 决策写入 docs；spec 升版冻结 |
| WP05-T01 | 新增只读工具契约、错误码和纯辅助 | WP05-AC-001/002/006/009 | T00 | domain/contract 测试通过，无 I/O |
| WP05-T02 | 实现 `read_file` | WP05-AC-002/006/007/008/011/012 | T01 | 正常、边界、Evidence 测试通过 |
| WP05-T03 | 实现 `list_files`/`read_directory` | WP05-AC-013 | T01 + Sandbox 枚举 | 顺序、过滤、limit、symlink 测试通过 |
| WP05-T04 | 实现 `search_code` | WP05-AC-009/010/011/012 | T01 + T02 + Sandbox 枚举 | 字面量、大小写、context、预算测试通过 |
| WP05-T05 | 实现 `git_status` | WP05-AC-014/016/017 | T01 | 状态解析、敏感过滤、脱敏测试通过 |
| WP05-T06 | 实现 `git_diff` 与受限 pathspec | WP05-AC-015/016/017 | T01 + Sandbox pathspec | staged/unstaged/scope/禁读过滤测试通过 |
| WP05-T07 | 可信工厂注册、快照和 Gate 集成 | WP05-AC-001/002/003/004/005/018 | T02 至 T06 | 六工具均通过 Gate；拒绝不触达 handler |
| WP05-T08 | 全量边界、故障与回归验证 | 全部 AC | T07 | 目标/全量测试、Ruff、格式通过 |
| WP05-T09 | 编写 acceptance-report、索引和下游交接 | 全部 AC | T08 | 实际证据完整，报告可评审 |

### 5.1 T00 施工前置

必须产出：

1. `decisions.md` 对搜索语义、安全目录枚举、受限 pathspec 的最终结论；
2. WP-03 兼容规格版本、错误码和公共类型；
3. 本文对应 `spec.md` 版本与 open question 状态更新；
4. 如果兼容方案改变，先同步 WP-04 ToolSpec/Path 参数约束，不得在 handler 中补偿。

### 5.2 T01 契约与纯辅助

优先完成：

- `ToolName` 不需要新增枚举值，现有字符串约束已允许六个名称；
- 新 `ErrorCode` 与包根导出；
- 纯 glob/行窗口/digest/Evidence 去重辅助；
- 默认参数 normalizer；
- 严格输出模型或固定的 canonical dict builder。

本任务不允许读取真实仓库或启动进程。

### 5.3 T02 至 T06 独立增量

每个增量先加对应失败测试，再实现 handler。建议提交粒度：

```text
feat(wp-05): add bounded file read tool
feat(wp-05): add safe repository listing and literal search
feat(wp-05): add read-only git status and diff tools
test(wp-05): cover read evidence and denied paths
```

各 handler 可以共享纯辅助，但每个工具必须有独立 schema、输出和拒绝测试。

### 5.4 T07 工厂与 Gate 集成

测试不得直接调用 handler 作为“集成通过”的证据。至少建立一个真实临时 Git 工作区 fixture：

```text
workspace/
├── src/login.py
├── tests/test_login.py
├── README.md
├── .env
├── outside -> /tmp/.../outside
└── artifacts/...
```

通过 `ToolRegistry + PolicyEngine + Gate + Sandbox + SQLiteStorage` 调用六个工具，核对 Observation、
Invocation、Evidence 和 Audit 的关联。

## 6. 测试计划

### 6.1 单元测试

| 范围 | 主要用例 | 断言 |
| --- | --- | --- |
| schema/normalizer | 缺字段、额外字段、布尔冒充整数、默认值 | 前后摘要稳定，失败在 handler 前 |
| glob/路径 | `*`/`**`、`..`、绝对路径、反斜杠、隐藏/依赖目录 | 匹配确定且不越界 |
| 行窗口 | UTF-8、空文件、末行、超长行、offset/limit | `start/end/next_offset/complete` 正确 |
| 输出/预算 | 字节上限、命中上限、扫描上限 | 显式 truncated 原因，不声称完整 |
| Evidence | 范围去重、摘要、稳定排序 | 与返回源码版本一致 |
| Git 解析 | `-z` 状态、rename/copy、空输出、坏 NUL、diff 空输出 | 不得把坏结果当成功 |

### 6.2 安全测试

| 场景 | 通过条件 |
| --- | --- |
| `artifacts/../../.env` | Gate/Sandbox 拒绝，读取计数 0，A1 审计完整 |
| 工作区外 symlink | 直接读取拒绝；递归扫描不返回目标内容 |
| `.env`/key/credential | 名称和内容不进入模型 observation |
| 未注册工具/不可见工具 | Gate 拒绝前 handler 计数 0 |
| 参数注入字段 | schema 拒绝，不能改写 argv/cwd/env |
| Git revision/选项注入 | schema 或 Sandbox 拒绝，进程启动 0 |
| Git 禁读路径 diff | patch/status 均不包含其内容 |
| 输出含 token | Gate observation 脱敏；Evidence 不保存正文 |
| 审计/结果写失败 | 不返回伪成功，不产生伪 Evidence |

### 6.3 集成测试

使用临时 Git 工作区和 Fake Clock/FaultInjector：

1. 初始化干净仓库，提交源码；
2. 通过 Gate 读取/搜索文件，核对 SQLite Evidence；
3. 修改文件后再次读取，证明 digest 变化；
4. 添加 staged/unstaged 修改，核对 `git_status`/`git_diff`；
5. 加入许可路径外修改和敏感文件，证明输出被过滤；
6. 重复同一只读调用三次，证明第三次在 handler 前拦截；
7. 注入 audit/result 写失败，证明失败关闭；
8. 关闭并重开 SQLite，核对 Evidence、Invocation 和 Audit 仍可关联。

## 7. 规格场景到测试映射

| 规格场景 | 测试文件 | 建议测试函数 | 主要证据 |
| --- | --- | --- | --- |
| WP05-AC-001 | `tests/test_tools.py` | `test_readonly_factory_registers_stable_snapshot` | 快照字段与排序 |
| WP05-AC-002 | `tests/test_tools.py` | `test_strict_tool_schemas_reject_before_handler` | handler counter=0 |
| WP05-AC-003 | `tests/test_gate.py` | `test_readonly_path_escape_denied_before_handler` | A1 审计、read counter=0 |
| WP05-AC-004 | `tests/test_tools.py` | `test_traversal_omits_symlink_and_sensitive_targets` | 输出无敏感名/内容 |
| WP05-AC-005 | `tests/test_gate.py` | `test_third_equivalent_read_is_rejected` | Invocation 数与 handler count |
| WP05-AC-006 | `tests/test_tools.py` | `test_read_file_paginates_with_versioned_evidence` | 行范围、digest、Evidence |
| WP05-AC-007 | `tests/test_tools.py` | `test_read_file_line_and_byte_boundaries` | 边界字段 |
| WP05-AC-008 | `tests/test_tools.py` | `test_read_file_rejects_non_text_and_oversized_files` | 稳定错误码 |
| WP05-AC-009 | `tests/test_tools.py` | `test_literal_search_scope_glob_case_and_context` | 顺序与内容 |
| WP05-AC-010 | `tests/test_tools.py` | `test_search_reports_all_truncation_reasons` | 截断字段 |
| WP05-AC-011 | `tests/test_tools.py` | `test_search_evidence_matches_each_context_version` | Evidence 行范围/digest |
| WP05-AC-012 | `tests/test_tools.py` | `test_file_change_produces_new_content_digest` | 两版摘要 |
| WP05-AC-013 | `tests/test_tools.py` | `test_listing_is_sorted_and_bounded` | 目录/文件顺序 |
| WP05-AC-014 | `tests/test_tools.py` | `test_git_status_filters_denied_paths` | 状态输出 |
| WP05-AC-015 | `tests/test_tools.py` | `test_git_diff_pathspec_and_staged_modes` | patch、index 不变 |
| WP05-AC-016 | `tests/test_tools.py` | `test_git_failures_are_not_reported_as_empty` | 错误码 |
| WP05-AC-017 | `tests/test_tools.py` | `test_read_errors_and_redaction_are_structured` | 脱敏后的 observation 与原始摘要 |
| WP05-AC-018 | `tests/test_gate.py` | `test_result_audit_failure_leaves_invocation_started` | 故障注入后不伪造成功 |

## 8. 推荐施工顺序与可回滚增量

```text
WP05-T00 规格/上游兼容评审
  -> WP05-T01 公共契约与纯辅助
  -> WP05-T02 read_file
  -> WP05-T03 listing/directory
  -> WP05-T04 search_code
  -> WP05-T05 git_status
  -> WP05-T06 git_diff
  -> WP05-T07 factory + Gate 集成
  -> WP05-T08 全量验证
  -> WP05-T09 验收报告
```

每个工具应能独立回滚：

- 删除对应 ToolDefinition 注册不会改变 Gate 或已有写工具；
- Evidence/错误码 schema 一旦对外发布，不允许用不兼容字段覆盖；
- 若 Sandbox 兼容修订失败，保持 WP-03 v1.0.0 与 WP-04 v1.0.0 不动，WP-05 停留在设计阶段。

## 9. 风险、限制与人工处理

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 模型正则造成 CPU 耗尽 | 阻塞只读请求 | P0 只支持字面量；未来引入有硬边界的引擎 |
| 目录 symlink 循环/逃逸 | 读取工作区外内容 | Sandbox 枚举不跟随目录链接，逐项真实落点校验 |
| Git diff 带出禁读文件 | 敏感内容泄漏 | 先过滤可见 changed paths，再用受控 pathspec；失败关闭 |
| 输出截断被误当完整 | 模型错误引用/遗漏 | 所有结果带 `truncated/has_more/complete` 并测试 |
| 文件在读取后改变 | 旧引用失真 | Evidence 绑定完整文件摘要；WP-08 后续比较版本 |
| P0 10 MiB 文件上限 | 大生成文件不可读 | 明确失败并提供路径/大小诊断，不静默截断 |
| 非 UTF-8 源码名/内容 | 输出不稳定或序列化失败 | 内容返回编码错误；路径无法稳定表示时聚合跳过 |
| Gate 只读串行 | 搜索/读取性能较低 | 保持安全默认，后续另行评审有界并发 |

人工处理入口：

- Sandbox 路径/目录策略变化：停止 WP-05，重新冻结 WP-03/WP-05；
- Git 输出无法可靠解析：保留失败 observation 和审计，诊断仓库状态，不使用未经验证的空结果；
- Evidence 摘要与磁盘当前版本不一致：交给 WP-08/WP-09 判定旧证据失效，WP-05 不自行覆盖历史事实；
- 发现允许文件内嵌凭据：按事件流程处理，不能通过修改模型提示掩盖。

## 10. 下游交接

WP-05 验收通过后向 WP-06/WP-08/WP-10 交付：

- 六个稳定工具名、schema、输出 envelope 和错误码；
- `register_readonly_tools` 装配契约；
- Sandbox 目录枚举与受限 pathspec 兼容接口；
- READ Evidence 的路径/行范围/版本摘要语义；
- 截断与失败字段定义；
- 目标测试、全量测试、Ruff 和格式检查证据；
- 已知限制，尤其是字面量搜索、10 MiB 上限和嵌入凭据识别边界。

WP-06 可以在同一 `tools/__init__.py` 追加写工具，但不得改变只读工具名称、风险、默认参数摘要或
Evidence 语义；WP-08 不得把 `COMMAND`/`DIFF` Evidence 当作代码行引用证据。

## 11. 实际完成情况

| 项目 | 状态 |
| --- | --- |
| 规格 | `spec.md` v1.0.0 / frozen |
| 计划 | `plan.md` v1.0.0 / done |
| 决策 | D-45 至 D-47 已确认并同步 |
| 只读工具 | 六个工具、可信工厂和 Git 命令 allowlist 已实现 |
| 测试 | WP-05 目标 `16 passed`；WP-03 目标 `56 passed`；全量 `196 passed` |
| 验收报告 | [acceptance-report.md](acceptance-report.md) v1.0.0 / accepted |

WP-05 已完成。Write/patch、受控测试命令、Checkpoint、Agent 和 Completion 仍由后续工作包负责。
