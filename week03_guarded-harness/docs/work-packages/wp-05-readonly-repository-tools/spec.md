# WP-05 只读仓库能力包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-05 只读仓库能力包 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-06 |
| 冻结日期 | 2026-11-06 |
| 依赖 | WP-04 v1.0.0（已验收）；WP-03 v1.1.0（兼容修订冻结） |
| 下游工作包 | WP-06、WP-08、WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-GATE-001` 至 `REQ-GATE-005`、`REQ-SANDBOX-001`、`REQ-SANDBOX-002`、`REQ-SANDBOX-006`、`REQ-AUDIT-001` 至 `REQ-AUDIT-004`、`REQ-TASK-001`、`REQ-TASK-002` |
| 对应设计 | `design.md` 第 5、6.1、7、8、9、10、11.1、11.2、13 节 |
| 已确认决策 | D-02、D-04、D-14、D-22、D-29、D-30、D-33、D-37、D-38、D-40 至 D-47 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |
| 原文件任务 | P3-04、P3-06、P3-08 前半、P3-10 前半 |

本文是 WP-05 已冻结的行为与验收规格；实现与实测结论见
[acceptance-report.md](acceptance-report.md)。D-45 至 D-47 已关闭第 12 节的兼容决策。文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-05 必须把真实、只读的仓库访问能力接入 WP-04 Gate，并形成可由 WP-08 消费的事实证据：

1. 可信装配代码只向 Registry 注册 `list_files`、`search_code`、`read_file`、`read_directory`、
   `git_status` 和 `git_diff`；
2. 模型只能提交工具名和严格 schema 内的参数，所有候选调用重新经过 Gate；
3. 模型可见快照只包含稳定 `ToolSpec`，不包含 handler、Sandbox 实例、真实路径或内部命令；
4. 所有文件边界、敏感文件、符号链接和真实 I/O 继续由 Sandbox 负责，handler 不重新实现路径规则；
5. 所有目录扫描、搜索和 Git 输出都有确定顺序、条数/字节上限以及显式截断事实；
6. `read_file` 和 `search_code` 为实际返回给模型的源码范围生成读取证据，包含规范化路径、完整行范围、
   文件内容版本摘要和权威 `tool_call_id`；
7. Git 工具只执行固定、只读命令，支持受众受限的 `git_diff` pathspec，不修改 index、refs、工作区或
   Git 配置；
8. 参数错误、路径逃逸、敏感文件、二进制/大文件、命令失败和输出截断均以结构化 observation 返回，
   不伪造成功结果；
9. 测试离线、确定、可重复，并证明拒绝发生在 handler、文件读取或 Git 进程启动之前。

WP-05 完成后仍不负责写文件、patch、测试命令、Checkpoint、恢复、Agent 适配或最终任务验收。

## 3. 范围与非目标

### 3.1 范围内

- `src/guarded_harness/tools/fs_read.py`；
- `src/guarded_harness/tools/git.py`；
- `src/guarded_harness/tools/common.py`（如共享契约和确定性辅助确有必要）；
- `src/guarded_harness/tools/__init__.py` 的只读工具可信装配；
- 只读工具输入/输出契约、稳定错误码和公共导出；
- 支持目录枚举与受限 pathspec 所需的 WP-03 兼容扩展；
- `tests/test_tools.py`，以及 `tests/test_gate.py`、`tests/test_sandbox.py` 的集成扩展；
- WP-05 文档、工作包索引和后续验收报告。

### 3.2 非目标

- 不实现 `write_file`、`apply_patch`、`run_test` 或任意命令工具；
- 不实现 Completion 的文档结构、引用解析、diff 验收或测试修复策略；
- 不实现 Checkpoint、continuation、Run 恢复或旧证据失效编排；
- 不构造 `ToolCallingAgent`，不修改或 monkey patch `smolagents`；
- 不新增模型可指定 argv、cwd、环境变量、超时、shell、可执行文件或 Git revision 的能力；
- 不实现正则搜索引擎、索引服务、LSP、语言解析器或语义代码搜索；
- 不承诺扫描 Git-ignored、依赖目录、`.git/`、状态数据库或任务禁读路径中的内容；
- 不提供操作系统级文件系统隔离，也不承诺识别允许读取文件内部任意形式的凭据；
- 不把工具文本输出本身当作最终验收证据。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。下表只列 WP-05 直接负责或提供
基础事实的条目；跨工作包条目仍由最终归属工作包关闭。

| 编号 | 优先级 | 飞书通过标准 | WP-05 责任 | 最终归属 |
| --- | --- | --- | --- | --- |
| G1 | P0 | 参数缺字段或类型错误时返回明确错误，handler 调用次数为 0 | 直接：六个工具的严格 schema 和 Gate 前拒绝集成测试 | WP-04 / WP-05 / WP-12 |
| G2 | P0 | 连续三次相同工具和参数，第三次在执行前拦截，handler 只执行两次 | 直接：真实只读 handler 集成 Gate 重复限制 | WP-04 / WP-05 / WP-12 |
| S1 | P0 | `artifacts/../../.env` 路径逃逸必须由 Sandbox 拒绝，handler 不执行 | 直接：文件工具把路径声明为受治理 path argument | WP-03 / WP-04 / WP-05 / WP-12 |
| S2 | P1 | 软链接逃逸或敏感目录访问在真实落点校验后拒绝并留痕 | 直接：直接路径和递归遍历都不得绕过 Sandbox | WP-03 / WP-05 / WP-12 |
| A1 | P0 | 被拒绝调用审计含 `rule`、`decision`、`actor`、`toolCallId` | 直接：集成测试通过 Gate 审计检查真实只读工具拒绝 | WP-04 / WP-05 / WP-12 |
| A2 | P1 | 完整任务可关联 Gate、Sandbox、State、Approval 和执行结果 | 支撑：读取 Evidence 通过 `run_id`、`tool_call_id` 关联 | WP-08 / WP-12 |
| H2 | P0 | 测试通过后又修改源码，旧测试证据失效 | 支撑：WP-05 为每个读取范围绑定完整文件内容摘要 | WP-07 / WP-09 / WP-12 |
| T1 | P0 | 所有 P0 场景有确定性测试且可重复通过 | 直接：WP-05 场景全部离线自动化 | WP-05 / WP-12 |

“核心验收”的交付物表要求所有 Tool Call 只有 Gate 一个入口。WP-05 不得因工具声明为只读而提供
handler 直达公开入口；Completion、测试或调用方只能通过 Gate 执行这些 handler。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `tools/__init__.py` | 可信组装只读工具定义并注册到 WP-04 Registry | 不执行未授权调用，不决定模型可见性 |
| `tools/fs_read.py` | 文件、搜索与目录只读 handler | Gate 检查、路径重写、写操作、Completion |
| `tools/git.py` | 固定只读 Git 命令的 handler | 任意 Git 命令、写 index/refs、审批 |
| `tools/common.py` | 严格输入归一化、稳定排序、输出/证据构造辅助 | 文件系统或进程访问 |
| `registry.py` | 接收可信 `ToolDefinition` 并生成模型可见快照 | 不执行工具 |
| `gate.py` | 固定检查链、预算、重复、执行和 Evidence 持久化 | 不解释工具业务输出 |
| `sandbox.py` | 路径、安全 I/O、目录枚举、可信命令和进程边界 | 不选择工具或构造模型请求 |
| `domain.py` | 新稳定错误码、必要公共输出类型和安全枚举 | 不执行 handler |

依赖方向：

```text
domain.py
   ^
   |
sandbox.py ─────> config.py
   ^
   |
tools/* ─────────> registry.py（只构造 ToolDefinition）
   |
Gate ───────────> registry.py + policy.py + sandbox.py + state/audit/approval

tests/test_tools.py ─────> tools/* + Gate + Sandbox + public domain
```

禁止：

- `tools/*` 直接写 StateStore、Audit、Approval 或 Checkpoint；
- `tools/*` 使用 `open()`、`Path.read_bytes()`、`Path.rglob()`、`os.walk()` 读取目标仓库；
- handler 根据任务 ID 或任务名称散落 `understand`、`fix_tests` 分支；
- 使用模型提供的字符串拼接 shell 命令、Git revision、`--exec-path` 或 `--upload-pack`；
- 把沙箱绝对路径、可执行文件路径、命令 argv 模板或 handler 放入模型快照；
- 通过日志、错误或 Evidence 存储文件原文或敏感环境变量。

## 6. 公共接口

### 6.1 可信注册入口

```text
register_readonly_tools(
    registry: ToolRegistry,
    sandbox: Sandbox,
) -> tuple[ToolName, ...]
```

行为：

- 只允许可信 Harness/测试装配代码调用；
- 从只读模块构造六个 `ToolDefinition`，按工具名稳定排序后逐一注册；
- 任一定义 schema 无效、名称冲突或 handler 装配失败时整批失败关闭，不留下部分注册状态；
- 返回实际注册名，便于装配测试核对；
- 不创建 Policy 或 Run；工具是否对模型可见仍由已校验任务配置和 `PolicyEngine` 决定。

handler 不新增绕过 Gate 的公共执行方法。测试可直接测试纯辅助函数，但真实工具行为必须至少通过一次
`Gate.invoke()` 验证。

### 6.2 工具元数据

六个工具均声明：

- `risk=READ`；
- `required_permissions={ToolPermission.READ}`；
- `approval_requirement=NEVER`；
- `idempotent=True`；
- `retry_policy=SAFE_READ_ONLY`；
- `concurrency_class=SERIAL`，直到 Gate 实现有上限的只读并发；
- `output_limit_bytes <= 65_536`。

| 工具 | 用途 | 幂等只读命令/操作 | 建议超时 |
| --- | --- | --- | --- |
| `list_files` | 按固定 glob 递归列出文件 | Sandbox 安全枚举 | 8 秒 |
| `search_code` | 在文本文件中做字面量搜索 | Sandbox 安全枚举 + 有界读取 | 15 秒 |
| `read_file` | 按行窗口读取 UTF-8 文本 | `Sandbox.read_file` 后切行 | 5 秒 |
| `read_directory` | 列出目录下一层条目 | Sandbox 安全目录枚举 | 5 秒 |
| `git_status` | 查看工作区状态 | 固定 `git status` 只读命令 | 10 秒 |
| `git_diff` | 查看工作区或 index 差异 | 固定 `git diff` 只读命令 | 15 秒 |

超时值是 Gate 的协作 deadline 和验收上限。Git 进程的真实停止由 Sandbox 进程组边界负责；进程内搜索
必须通过文件数、总字节数和单次条目上限主动有界。

### 6.3 输入 schema 与规范化

所有根 schema 必须同时满足：

- `type="object"`；
- `additionalProperties=false`；
- 不包含模型可控制的 `argv`、`cwd`、`env`、`shell`、`command_id`、`timeout`、`revision` 或
  `executable` 字段；
- 字符串拒绝 NUL 和未声明控制字符；
- 整数使用严格整数，不接受布尔值或数字字符串。

受治理路径字段统一命名为 `path`，并声明在 `ToolSpec.path_arguments` 中。为保证 Gate 当前公共契约
可直接处理，`path` 在所有带路径的工具中必填；工作区根使用 `"."`，不使用“缺失字段代表根目录”。

| 工具 | 模型输入 | 可信归一化默认值 |
| --- | --- | --- |
| `list_files` | `path`, `pattern`, `limit?` | `limit=200` |
| `search_code` | `path`, `pattern`, `glob?`, `ignore_case?`, `context?`, `limit?` | `glob=null`, `ignore_case=false`, `context=0`, `limit=50` |
| `read_file` | `path`, `offset?`, `limit?` | `offset=1`, `limit=2000` |
| `read_directory` | `path`, `limit?` | `limit=200` |
| `git_status` | 空对象 | 无 |
| `git_diff` | `path`, `staged?` | `staged=false` |

可信 normalizer 必须在 Gate schema 校验之后、Semantic 阶段补默认值并再次校验，使省略默认值和显式
传入默认值得到相同规范化参数摘要，不能借此绕过重复调用限制。

固定边界：

- `path`：1 至 4,096 字符，工作区相对 POSIX 路径或 `"."`；
- `pattern`：1 至 512 字符；
- `glob`：1 至 512 字符，工作区相对 POSIX glob，不得为绝对路径、含 `..`、反斜杠或 NUL；
- `limit`：`list_files`/`read_directory` 为 1 至 500，`search_code` 为 1 至 200，
  `read_file` 为 1 至 2,000；
- `context`：0 至 10；
- `offset`：大于等于 1。

P0 `search_code.pattern` 采用字面量语义，不解释正则元字符，支持 `ignore_case`。D-45 已确认不在
P0 开放模型正则；实现不得在未重新评审时使用 Python `re` 解释模型输入。

### 6.4 工具输出信封

六个工具都返回 JSON object，至少包含：

```text
schema_version: 1
tool: ToolName
```

各工具附加字段如下。输出是模型 observation，不是最终验收事实；Completion 必须依据 Evidence、
工作区和命令报告，不得从自然语言输出推断成功。

```text
FileListOutput
  pattern, scope, entries: [workspace-relative path],
  returned_count, scanned_count, has_more, scan_limit_reached

DirectoryOutput
  scope, entries: [
    {name, path, entry_type: DIRECTORY|FILE|SYMLINK|OTHER, size_bytes: int|null}
  ],
  visible_count, omitted_count, returned_count, has_more

SearchOutput
  pattern, scope, glob, ignore_case,
  matches: [
    {
      path, line_number, line_text,
      context_start_line, context_end_line,
      context: [{line_number, text}],
      content_digest
    }
  ],
  returned_matches, files_scanned, bytes_scanned,
  has_more, hit_limit_reached, scan_limit_reached,
  skipped_binary_files, skipped_large_files, skipped_denied_entries

ReadFileOutput
  path, total_lines, start_line, end_line,
  content, line_numbered_content: bool=true,
  content_digest, byte_count,
  complete, next_offset, truncated, clipped_line_count

GitStatusOutput
  clean, entries: [
    {status, path, original_path: string|null}
  ],
  returned_count, omitted_count, truncated

GitDiffOutput
  staged, scope, patch, empty,
  content_digest, truncated
```

约束：

- 结构化路径字段使用规范化工作区相对 POSIX 路径，不以 `/` 或本机工作区绝对路径对外暴露；
  `git_diff.patch` 内部可保留标准 Git patch 的 `a/`/`b/` 文本语法，但不得被解释为工作区路径；
- 数组顺序稳定：文件按路径、行匹配按路径和行号、目录项先目录后文件再按名称；
- `has_more=true` 或 `truncated=true` 必须带稳定原因，不能把部分结果描述为完整结果；
- 输出正文再经过 Gate 统一脱敏和有界处理；工具自身也要预留 envelope 开销，避免把结构化结果退化成
  不可解释的字符串 preview；
- `GitStatusOutput.clean=true` 只在本次可见路径集合没有状态条目且无过滤/截断时成立；
- `GitDiffOutput.empty=true` 只在命令成功且可见 pathspec 集合内没有差异时成立。

### 6.5 `read_file` 行语义

- 文件按 UTF-8 严格解码；含 NUL 的内容按二进制拒绝，非法 UTF-8 返回
  `TOOL_TEXT_ENCODING_INVALID`；
- 行从 1 开始，使用 Python `str.splitlines()` 语义；空文件为 0 行；
- `offset` 指向首行时，`start_line=offset`；空文件且 `offset=1` 返回空的完整读取结果；
- `end_line` 表示实际完整纳入本次返回的最后一行，不表示文件总行数；
- `next_offset` 仅在后文还有未返回行时非空；
- 单行过长时最多保留固定前缀并设置 `clipped_line_count`，但不得在源码中间伪造换行；行号和完整文件
  摘要仍对应真实文件；
- 输出达到字节上限时必须在行边界结束并返回 `next_offset`；
- 只有当 `complete=true` 时才能在后续叙述中声称已经读取至文件末尾。

### 6.6 搜索与目录边界

- `search_code` 只搜索工作区内的普通文本文件；二进制、超过 10 MiB 的文件和 Sandbox 拒绝的条目跳过；
- `list_files`/`read_directory` 最多检查 20,000 个目录项，超出时返回显式扫描截断，不继续无界遍历；
- 固定扫描预算为最多 5,000 个文件、64 MiB 文件内容，达到任一上限即停止并返回显式截断事实；
- 搜索命中上限为 200；达到上限时 `has_more=true` 且结果不得声称没有更多命中；
- `glob` 对完整工作区相对路径匹配，固定规则为 `*` 不跨 `/`、`?` 匹配一个非 `/` 字符、`**` 可跨
  `/`；空 glob 和非法 glob 在 handler 或可信 normalizer 中失败关闭；
- `list_files` 只返回文件，不返回目录；使用与搜索相同的确定性路径集合；
- `read_directory` 只列 `path` 的下一层，不递归，不跟随目录符号链接；指向工作区内的文件符号链接可
  作为 `SYMLINK` 条目出现，指向工作区外或敏感目标的条目必须省略并计入 `omitted_count`；
- 目录扫描不进入 `.git/`、`.venv/`、`venv/`、`node_modules/`、`site-packages/`、
  `__pypackages__/`、`.tox/`、`.nox/`；忽略隐藏文件不扩展到其它任意目录；
- 被 Sandbox 拒绝的条目数量可以聚合报告，但不得在 observation、错误或审计中回显其敏感名称或内容。

### 6.7 Git 工具

允许的 Git 身份和参数来自可信装配，模型不可覆盖：

- 禁用 pager、颜色、外部 diff、textconv、签名与交互提示；
- 固定 `cwd=workspace_root`、最短环境、POSIX 进程组和 Sandbox 输出上限；
- 固定启用 Git 的 `--no-optional-locks` 与 `--literal-pathspecs` 全局选项，避免只读查询刷新 index，并
  禁止模型路径被解释为 Git pathspec magic；
- `git_diff` 只比较工作区与 index，或 `staged=true` 时比较 index 与 HEAD；不得接受任意 revision；
- pathspec 只能来自 Gate 已规范化的 `path`，并由 Sandbox 在启动前再次校验包含关系与读权限；
- 所有 Git 输出先解析/过滤，再进入模型；任务禁读路径下的状态项和 diff 不得出现在 observation；
- `git_status` 与 `git_diff` 不得修改 index、refs、工作区、ignore 规则或 Git 配置；
- 非零退出、启动失败、超时或输出无法可靠解析时返回结构化失败，不能把空输出当作成功。

Git pathspec 的最小安全形态见第 10 节。P0 不接受模型传入多个 pathspec、排除模式、revision 范围或
`:(attr)` 等 Git pathspec magic。

### 6.8 Evidence

只有实际读取操作产生 durable Evidence：

| 来源 | kind | path | line range | content digest | command_id |
| --- | --- | --- | --- | --- | --- |
| `read_file` | `READ` | 规范化文件路径 | 实际返回给模型的首末行 | 完整文件内容 SHA-256 | null |
| `search_code` | `READ` | 命中文件 | 实际返回的含上下文连续范围 | 完整文件内容 SHA-256 | null |
| `git_status` | `COMMAND` | null | null | 有界、过滤后结构化结果的规范化 SHA-256 | 固定只读 command ID |
| `git_diff` | `DIFF` | null | null | 有界、过滤后 patch 的规范化 SHA-256 | 固定只读 command ID |

`list_files`、`read_directory` 和零命中的 `search_code` 不创建内容 Evidence；其事实由 ToolInvocation
与 append-only Audit 记录，避免把目录枚举或不存在的源码范围伪装成“已读文件”。后续 WP-08 只能把
`kind=READ` 且路径、行范围和摘要匹配的 Evidence 用作代码引用依据。

Evidence 由 Gate 在结果事务中创建并分配 `evidence_id`、`run_id` 和 `tool_call_id`；handler 只提交
`EvidenceDraft`。同一返回值中不得生成内容不同但范围重复的草稿。

## 7. 固定处理链

```text
模型候选 ToolCall
  -> Gate visibility
  -> Gate schema
  -> 可信 normalizer 补默认值并语义校验
  -> Gate 规范化 path 并调用 Sandbox.check_read
  -> Gate permission/risk/approval/repeat/budget
  -> Gate 持久化 PREPARED/STARTED 与审计
  -> handler
       -> Sandbox 安全枚举/读取/受信 Git 命令
       -> 确定性过滤、上限和 EvidenceDraft
  -> Gate 结果事务：Evidence + Invocation + 审计
  -> 有界、脱敏 ToolObservation
```

handler 不得再次信任模型原始参数。它只消费 Gate 已规范化的参数和可信 `ToolExecutionContext`；
对遍历中新发现的每个路径仍必须通过 Sandbox 公共接口重新校验。

## 8. 状态、不变量与失败语义

- WP05 不新增 Run 或 Invocation 状态机，只使用 WP-04 已冻结状态。
- 成功只读调用最终为 Invocation `SUCCEEDED`、Observation `ALLOWED`。
- 确定失败为 Invocation `FAILED`、Observation `FAILED`，且错误码稳定。
- 只读操作没有外部写副作用，但结果落盘失败时仍按 Gate 规则失败关闭，不返回伪造成功。
- 同一只读工具、规范参数最多执行两次；第三次在 handler 前拒绝。
- 模型不可见工具、参数错误、路径越界和敏感路径拒绝都不创建 ToolInvocation，只写 Gate 拒绝审计。
- 文件在检查与读取之间改变时使用 `SANDBOX_PATH_CHANGED` 失败；不得使用旧内容或转换后的近似内容。
- 文件内容摘要必须基于 Sandbox 实际完整读取的字节，而不是模型可见截断文本。
- 所有限制和截断标志必须可序列化、稳定，并由测试断言；不得依赖日志文案判断完整性。

## 9. 错误契约

新增错误码在 WP-05 规格冻结时进入 `domain.ErrorCode` 并导出。既有 Sandbox/Gate 错误优先复用。

| 错误码 | 触发条件 | handler 是否执行 | 可重试 |
| --- | --- | --- | --- |
| `GATE_INVALID_ARGUMENT` | schema 缺字段、类型错误、额外字段 | 否 | 修正参数后新调用 |
| `GATE_SEMANTIC_INVALID` | glob/path 语义非法、normalizer 失败 | 否 | 修正参数后新调用 |
| `SANDBOX_PATH_*` | 越界、敏感、禁读、身份变化 | 否；遍历期间命中时只跳过该条目 | 人工修正后新调用 |
| `SANDBOX_FILE_TOO_LARGE` | 目标文件超过 10 MiB | 是，Sandbox 在真实内容返回前拒绝 | 否 |
| `TOOL_BINARY_FILE` | 内容含 NUL，按二进制处理 | 是，只完成类型判定 | 改用非文本工具或换目标 |
| `TOOL_TEXT_ENCODING_INVALID` | 非 UTF-8 源码文本 | 是，解码失败 | 否 |
| `TOOL_RANGE_OUT_OF_BOUNDS` | `offset` 大于非空文件行数 | 是，未返回源码内容 | 修正行号 |
| `TOOL_PATH_TYPE_MISMATCH` | 文件工具收到目录或目录工具收到非目录 | 是，未读取内容 | 修正路径 |
| `TOOL_GIT_FAILED` | 固定 Git 命令确定非零退出 | 是 | 依赖失败原因与幂等条件 |
| `TOOL_GIT_TIMEOUT` | 只读 Git 命令超时且进程组已终止 | 是 | 可在重新过 Gate 后重试 |
| `TOOL_GIT_OUTPUT_INVALID` | Git NUL/状态/diff 输出无法可靠解析 | 是 | 否，需诊断环境或仓库 |
| `TOOL_SCAN_LIMIT_EXCEEDED` | 扫描预算耗尽且无法返回值得信任的部分结果 | 是 | 可缩小 path 后重试 |

失败 observation 至少保留原 `tool_call_id`、拒绝/执行阶段、稳定错误码、有界原因和
`retryable`。错误文本不得包含绝对路径、文件内容、Git 凭据、完整 argv 或环境变量值。

## 10. WP-03 兼容修订

以下缺口已由 D-46/D-47 关闭，并由 WP-03 v1.1.0 提供向后兼容公共接口；WP-05 只消费这些方法。

### 10.1 安全目录枚举

handler 不能直接对目标仓库执行 `os.scandir()`、`os.walk()` 或 `Path.rglob()`，否则会绕过 Sandbox
路径落点校验和目录类型边界。冻结接口为：

```text
Sandbox.list_directory(
    path: str,
    *,
    max_entries: int,
) -> SandboxDirectoryListing

Sandbox.walk_readable_files(
    path: str,
    *,
    max_files: int,
    max_bytes: int,
    max_entries: int,
) -> SandboxFileWalk
```

`list_directory` 必须重新校验起始目录、拒绝特殊文件和目录逃逸、不跟随目录符号链接，并返回有界、
稳定排序的条目。`walk_readable_files` 必须把每个候选文件重新送入读策略，并为跳过项提供聚合计数，
不返回敏感名称。具体公共类型和错误码由 WP-03 兼容规格冻结。

### 10.2 受限 Git pathspec

`git_diff` 需要对用户选择的子路径进行真实 Git pathspec 过滤，同时不能把模型字符串变成任意 argv。
可信命令增加窄参数槽：

```text
TrustedCommandSpec.allow_pathspecs: bool = false

Sandbox.execute(
    command_id: str,
    *,
    pathspecs: tuple[SandboxPath, ...] = (),
    cancellation: CancellationToken | None = None,
) -> SandboxProcessResult
```

约束：

- 仅当 `allow_pathspecs=true` 时接受非空 pathspec；
- 每个 pathspec 必须由当前 Sandbox 产生为 READ 权限的 `SandboxPath`，启动前再次复核；
- pathspec 只能作为 `--` 后的独立 argv 元素，不解析 shell、glob magic 或 revision；
- 设置数量上限，禁止空字符串、NUL 和模型自定义排除规则；
- 不改变已冻结的固定命令行为；旧调用方不传 pathspec 时语义不变。

如果该修订不获批准，则 P0 只能取消 `git_diff.path`，并在任何 diff 输出中可靠过滤禁读路径；不能
保留路径参数却由 handler 自行拼接命令。

## 11. 验收场景

每个场景最终必须映射到自动化测试。`WP05-AC-001` 至 `WP05-AC-018` 是 WP-05 冻结和验收的最小集合。

| ID | 场景 | 预期 |
| --- | --- | --- |
| WP05-AC-001 | 可信装配注册六个工具并生成模型快照 | 名称稳定有序；快照无 handler、绝对路径或 argv |
| WP05-AC-002 | 缺字段、错误类型、额外字段送入 `read_file`/`search_code` | Gate schema 拒绝；handler 调用次数 0 |
| WP05-AC-003 | `artifacts/../../.env` 送入任一路径工具 | Sandbox/Gate 拒绝；真实读取次数 0；拒绝审计含 A1 字段 |
| WP05-AC-004 | 工作区外符号链接、敏感文件和依赖目录访问 | 直接调用被拒；递归扫描跳过且不泄露名称/内容 |
| WP05-AC-005 | 连续三次相同 `read_file` 调用 | 前两次最多进入 handler 两次；第三次在 handler 前拒绝 |
| WP05-AC-006 | 分页读取 UTF-8 文件 | 行号、offset、next_offset、完整文件和内容摘要一致 |
| WP05-AC-007 | 空文件、行尾、超长单行和输出字节上限 | 边界稳定，不标称已读未返回行 |
| WP05-AC-008 | 二进制、非法 UTF-8、大文件和目录作为 `read_file` | 返回稳定结构化失败，不返回伪造源码 |
| WP05-AC-009 | 字面量搜索的大小写、scope、glob、context 和排序 | 命中稳定；默认参数摘要与显式默认值一致 |
| WP05-AC-010 | 搜索达到命中/文件/字节上限 | `has_more` 或 `scan_limit_reached` 明确，不声称完整 |
| WP05-AC-011 | 搜索产生多个上下文范围 | 每条 READ Evidence 的路径、范围和摘要匹配实际文件版本 |
| WP05-AC-012 | 文件读取完成后内容改变 | 新读取摘要不同；旧 Evidence 保留旧版本事实，WP-08/09 可据此失效 |
| WP05-AC-013 | `list_files` 和 `read_directory` 稳定排序与截断 | 文件/目录类型、顺序、limit 和 omitted 计数正确 |
| WP05-AC-014 | `git_status` 在干净、修改、删除、重命名和禁读路径场景 | 只显示允许路径，状态可解析，禁读路径不泄漏 |
| WP05-AC-015 | `git_diff` 根、单文件、子目录、unstaged/staged | pathspec 生效；不包含禁读路径；index 与工作区不变 |
| WP05-AC-016 | Git 非零退出、超时和不可解析输出 | 稳定失败；不伪造“没有差异”或“工作区干净” |
| WP05-AC-017 | 观察输出含测试密钥或超长内容 | Gate observation 已脱敏、有界，Evidence 摘要仍可复核 |
| WP05-AC-018 | Gate 结果/审计写入失败 | 不返回成功 observation，不产生内容 Evidence 或伪造调用终态 |

## 12. 已关闭决策

2026-11-06 经需求方确认，三项兼容决策已登记为 D-45 至 D-47：

| 原问题 | 冻结结论 | 实现约束 |
| --- | --- | --- |
| 搜索是否支持模型正则 | D-45：P0 只支持字面量和大小写折叠 | 不使用 Python `re` 解释模型输入 |
| Sandbox 是否提供目录枚举 | D-46：提供 `list_directory`/`walk_readable_files` | handler 不直接访问目标目录或递归调用文件系统 API |
| Git diff 是否支持受限 pathspec | D-47：增加受控 pathspec 槽 | 只追加已复核 `SandboxPath`，固定 `--`、`--no-optional-locks` 和 `--literal-pathspecs` |

本规格无阻塞性未决问题。若后续改变上述选择，必须先修订本规格、WP-03 兼容规格和相关契约测试。
