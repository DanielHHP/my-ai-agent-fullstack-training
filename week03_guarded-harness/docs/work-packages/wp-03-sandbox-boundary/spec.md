# WP-03 Sandbox 边界规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-03 Sandbox 边界 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 冻结日期 | 2026-11-05 |
| 依赖 | WP-00 v1.2.0（已冻结）；WP-01 v0.3.0、WP-02 v0.2.0 已验收，但本包不直接依赖其业务接口 |
| 下游工作包 | WP-04、WP-05、WP-06、WP-07、WP-08、WP-09、WP-12 |
| 对应需求 | `REQ-SANDBOX-001` 至 `REQ-SANDBOX-006`、`REQ-GATE-002`、`REQ-AUDIT-003`、`REQ-CHECKPOINT-004`、`REQ-TASK-001` |
| 对应设计 | `design.md` 第 5.1、6.1、7.1、8、9.3、11.1、13、14 节 |
| 已确认决策 | D-02、D-05、D-12、D-16、D-21、D-22、D-29 至 D-36 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌表 revision 11 |

本文是 WP-03 已冻结的行为与验收契约，不代表 Sandbox 已实现。`frozen` 表示规格决策已经关闭，
实现是否完成仍由自动化测试、lint、格式检查和验收报告决定。若本文与 `requirements.md`、
`design.md` 或 `decisions.md` 冲突，以后三者为准；本规格不重新定义 Gate、审批、持久化、恢复或
Completion 的跨工作包行为。

## 2. 目标

WP-03 必须交付一个可被 Gate 和后续真实工具复用、但不是第二套 Agent 或 Tool 运行时的 Sandbox
能力闭环：

1. 将一次 Run 固定到唯一的规范化 `workspace_root`，并识别其所在 `git_root` 与
   `workspace_prefix`；
2. 对读写路径执行真实落点校验，并通过 Sandbox 的安全 I/O 原语完成最终文件访问，拦截工作区
   逃逸、敏感文件、受保护写入和符号链接绕过；
3. 捕获工作区初始 Git 状态、仓库外围 Git-visible 脏路径、HEAD 和 index 基线，并能检测变化；
4. 只执行由可信任务配置或固定 allowlist 注册的 argv，固定 `cwd`，使用 `shell=False` 和显式最小
   环境；
5. 用独立进程组执行子进程，支持超时/取消后的进程组终止、输出上限和结构化结果；
6. 把拒绝、工作区变化、超时、终止失败和结果不确定映射为稳定错误或状态，供 Gate、State 和
   Completion 使用；
7. 用离线、确定、可重复的测试证明拒绝发生在真实 I/O 或进程启动之前。

WP-03 提供的是一组安全边界事实。是否放行工具、是否写审计、Run 进入哪个状态，仍由 WP-04 的
Gate 和后续 Harness 决定。

## 3. 范围与非目标

### 3.1 范围内

- `src/guarded_harness/sandbox.py`；
- `domain.py` 和包根新增的 Sandbox 公共类型、稳定错误码与导出；
- 工作区/Git 身份解析、路径规范化、真实落点判定和路径策略匹配；
- 有界整文件安全读取和原子整文件写入；
- 平台级敏感路径规则，以及任务级 `deny_read`、`deny_write`、`allow_write` 的执行语义；
- 工作区 status/diff、仓库外围脏路径、HEAD 和 index 基线；
- 受信命令注册、可执行文件检查、最小环境变量构造；
- 子进程组、超时、取消、输出采集上限、截断和结果确定性；
- `tests/test_sandbox.py` 和必要的通用测试 Fixture 扩展；
- WP-03 文档、索引、下游交接和最终验收报告。

### 3.2 非目标

- 不实现 Gate 固定检查顺序、权限判定、审批、重复调用或预算，这些属于 WP-04；
- 不实现具体 `read_file`、`search_code`、`write_file`、`apply_patch`、`git_status` 或 `run_test`
  handler，这些属于 WP-05/WP-06；本包只提供它们必须复用的安全 I/O 原语；
- 不写 State Store、Audit、Approval 或 Checkpoint，也不选择 Run 状态；
- 不实现 Completion 策略、测试报告解析或旧证据失效判定；
- 不实现 CLI、审批等待、Agent 或 `smolagents` 适配；
- 不承诺容器级、虚拟机级或操作系统级文件系统隔离；
- 不承诺保护工作区外被 `.gitignore` 忽略的文件，也不递归扫描整个文件系统；
- 不提供后台常驻进程、交互式 TTY、包安装、任意 shell、网络代理或命令字符串执行；
- 不通过提示词、工具描述或模型自述替代路径与进程检查。

### 3.3 安全边界声明

WP-03 对文件路径和命令启动参数实施进程内治理，但受限命令的实际子进程仍以当前操作系统用户权限
运行，可以执行目标仓库中的代码。P0 的额外保护只覆盖工作区外 Git-visible 变化、固定 `cwd`、
最小环境、进程组终止和输出边界；不得宣称具备文件系统级沙箱、网络隔离或对恶意并发文件系统
修改的绝对防护。若目标仓库或其测试代码本身不被信任，需要另立操作系统级隔离工作包。

## 4. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `sandbox.py` | 路径、安全文件 I/O、Git 基线、受信命令、环境和进程边界 | Gate、审批、任务成功判定、具体仓库工具 |
| `domain.py` | 冻结公共错误码、枚举和可序列化结果模型 | 执行 I/O、启动进程或解释审批 |
| `config.py` | 提供已校验的工作区、路径策略和测试命令 | 在调用时重新放宽或覆盖 Sandbox 规则 |
| `storage.py`/`redaction.py` | 可被下游用于持久化和摘要脱敏 | 不进入 Sandbox 安全判定的旁路 |
| `tests/conftest.py` | 临时 Git 工作区、符号链接和确定性测试辅助 | 不隐藏平台差异或自动判定测试成功 |

依赖方向：

```text
domain.py
   ^
   |
sandbox.py ─────> config.py（只读取已解析配置）
   |
   +────────────> __init__.py（公共导出）

tests/test_sandbox.py ─────> sandbox.py + domain.py + config.py
```

`sandbox.py` 不得导入 Gate、Approval、StateStore、Checkpoint、Completion、真实工具或
`smolagents`。它不得根据 `understand`、`fix_tests` 等任务名分支；任务差异只通过已解析配置和
可信命令规格传入。

## 5. 公共接口

以下名称和语义是 WP-03 冻结契约。

### 5.1 构造与路径检查

```text
Sandbox.from_resolved_config(
    config: ResolvedHarnessConfig,
    *,
    command_policy: CommandPolicy,
    clock: Clock,
) -> Sandbox

Sandbox.check_read(path: str) -> SandboxPath
Sandbox.check_write(path: str) -> SandboxPath
Sandbox.revalidate_path(path: SandboxPath) -> SandboxPath
Sandbox.read_file(path: str, *, max_bytes: int = 10_485_760) -> SandboxFile
Sandbox.write_file_atomic(
    path: str,
    data: bytes,
    *,
    mode: SandboxWriteMode,
    expected_content_digest: Sha256Digest | None = None,
    create_parents: bool = False,
) -> SandboxWriteResult
Sandbox.workspace_root: Path
Sandbox.git_root: Path
Sandbox.workspace_prefix: str
```

`check_read`/`check_write` 只供 Gate 做执行前判定并返回规范化路径。所有真实目标仓库文件 I/O
必须使用同一 Sandbox 实例的 `read_file` 或 `write_file_atomic`；handler 不得在检查后自行打开、
覆盖或删除目标路径。拒绝必须抛出 `SandboxViolation`，不得返回可被忽略的 `allowed=False` 后
继续执行，也不得由调用方自行拼接未规范化路径。

`SandboxPath` 至少包含：

```text
schema_version
original: str
absolute_path: str
workspace_relative: str
git_relative: str
access: SandboxAccess
identity_digest: sha256:<64 hex>
```

`absolute_path`、`workspace_relative` 和 `git_relative` 只保存规范化后的字符串，
`workspace_relative` 使用 POSIX `/`，工作区根本身表示为 `"."`。`identity_digest` 绑定规范路径及
检查时的文件类型/身份信息，用于下游写审计和最短复核；它不是内容证据。

### 5.2 安全文件 I/O

`SandboxFile` 至少包含：

```text
schema_version
path: SandboxPath
data: bytes
content_digest: sha256:<64 hex>
byte_count: int
```

`SandboxWriteMode` 至少包含 `CREATE` 和 `REPLACE`。`SandboxWriteResult` 至少包含：

```text
schema_version
path: SandboxPath
mode: SandboxWriteMode
created: bool
previous_content_digest: sha256:<64 hex> | null
content_digest: sha256:<64 hex>
byte_count: int
```

约束：

- 读取和写入的硬上限均为 10 MiB；调用方可以要求更小读取上限，但不能把硬上限调高；
- 读取超过上限返回 `SANDBOX_FILE_TOO_LARGE`，不得返回截断后仍声称完整的源码；
- `CREATE` 要求目标不存在；`REPLACE` 要求目标是工作区内普通文件；
- `expected_content_digest` 非空时，当前内容摘要必须精确匹配，否则返回
  `SANDBOX_WRITE_CONFLICT` 且不替换文件；
- `create_parents=true` 只接受可信工具装配传入；创建出的每一级目录都必须重新通过工作区、平台
  敏感/禁写和任务写策略检查；
- 原子写必须在目标同目录创建排他临时文件，写入并 `fsync` 后原子替换；失败时清理临时文件且
  不得留下部分目标内容；
- 覆盖写入保留既有文件权限；创建写入使用受控 umask 结果，不擅自把源码变成 `0600`；
- 符号链接只在真实目标位于工作区内且通过策略时使用；写入作用于解析后的普通文件目标，不允许
  通过替换链接本身绕过策略；
- 读取/写入仅支持普通文件；目录、FIFO、设备、socket 或其他特殊文件失败关闭。

### 5.3 Git 基线

```text
Sandbox.capture_baseline() -> WorkspaceBaseline
Sandbox.compare_workspace(baseline: WorkspaceBaseline) -> WorkspaceComparison
```

`WorkspaceBaseline` 至少包含：

```text
schema_version
identity: WorkspaceIdentity
head_oid_or_unborn
index_digest
workspace_status_digest
workspace_diff_digest
external_dirty_baseline_digest
external_protection: ENABLED | DISABLED
workspace_entries
external_entries
captured_at
```

每个 entry 至少包含 Git status code、规范化仓库相对路径、文件类型、内容摘要或删除哨兵。基线
只保存路径和摘要，不保存敏感文件内容。`WorkspaceComparison` 至少包含 `schema_version`、
`equal`、变化范围（workspace/external/identity）、`external_protection`、稳定原因码、变化路径
的有界集合和比较时间。

`WorkspaceIdentity` 继续使用 WP-00 冻结字段：

```text
git_root, workspace_root, workspace_prefix
baseline_fingerprint, external_dirty_baseline_fingerprint
```

### 5.4 受信命令

```text
CommandPolicy
  - schema_version
  - allowed_executables
  - base_environment_allowlist
  - default_timeout_seconds
  - default_kill_grace_seconds
  - default_stdout_limit_bytes
  - default_stderr_limit_bytes

TrustedCommandSpec
  - schema_version
  - command_id
  - argv: tuple[str, ...]
  - effect_class: READ_ONLY | IDEMPOTENT | NON_IDEMPOTENT
  - timeout_seconds
  - kill_grace_seconds
  - stdout_limit_bytes
  - stderr_limit_bytes

Sandbox.register_trusted_command(spec: TrustedCommandSpec) -> None
Sandbox.execute(command_id: str, cancellation: CancellationToken | None = None)
    -> SandboxProcessResult
```

`register_trusted_command` 只允许 Harness/工具装配层从已校验任务配置或代码内固定 allowlist 调用。
模型请求最多携带 `command_id`；模型不得提交 argv、可执行文件路径、环境变量、工作目录或 shell
片段。未知 `command_id`、未注册命令和随请求覆盖限制均拒绝。

### 5.5 进程结果

`SandboxProcessResult` 至少包含：

```text
schema_version
command_id
status: START_FAILED | SUCCEEDED | FAILED | TIMED_OUT | CANCELLED | UNKNOWN_RESULT
exit_code: int | null
signal: int | null
stdout: str
stderr: str
stdout_truncated: bool
stderr_truncated: bool
timed_out: bool
cancelled: bool
process_group_terminated: bool
duration_ms: int
result_code: ErrorCode | null
```

`status` 表示本次进程边界观察到的事实；它不直接写 Run 或 Invocation 状态。Gate 负责把
`UNKNOWN_RESULT` 映射到业务状态，把确定失败映射到 `FAILED`，把成功交给任务级后续验收。

## 6. 路径边界契约

### 6.1 工作区身份

- `workspace_root` 必须已存在、为目录，并使用 `Path.resolve(strict=True)` 得到真实路径；
- `git_root` 由固定 Git 命令从 `workspace_root` 推导，必须存在、为目录并已真实解析；
- `workspace_root` 必须等于 `git_root` 或位于 `git_root` 内，使用路径关系判断而非字符串前缀；
- `workspace_prefix` 是 `workspace_root.relative_to(git_root).as_posix()`；相等时为 `"."`；
- `workspace_root`、`git_root` 或前缀无法稳定解析时返回 `SANDBOX_GIT_ROOT_INVALID` 或
  `SANDBOX_WORKSPACE_INVALID`，不得退回当前目录或猜测父目录；
- 同一 Sandbox 实例不得切换到另一工作区。

### 6.2 输入规范化

路径输入必须满足：

- 非空、有界，禁止 NUL 和控制字符；
- 禁止反斜杠、Windows drive、UNC 和以 `~` 代替用户目录；
- POSIX 绝对路径允许作为输入，但其真实落点仍必须位于 `workspace_root` 内；
- 相对路径始终相对固定 `workspace_root`，不相对进程当前目录；
- `.` 段可移除；`..` 可以与其它段一起规范化，但任何真实落点越出工作区都拒绝；
- 重复分隔符、NFC/NFD 等价路径和大小写差异必须落到同一平台真实文件，不得靠字符串前缀判断；
- 对尚不存在的目标，先找到最近已存在祖先，对祖先执行真实解析，再安全拼接剩余尾部组件。

### 6.3 固定判定顺序

```text
语法与长度校验
  -> 基于 workspace_root 绝对化
  -> 最近存在祖先真实解析
  -> 工作区包含关系检查
  -> 平台级敏感路径检查
  -> 平台级禁写检查（仅写）
  -> 任务 deny_read / deny_write 检查
  -> 窄范围 allow_write 例外判定（仅写）
  -> 返回规范化 SandboxPath
```

前一阶段失败后立即拒绝，不得继续执行后续 allow 判定。平台级规则不能被任务配置、`allow_write`
或调用参数覆盖。

### 6.4 平台级敏感读规则

以下路径无论任务配置如何都禁止读取，并在写检查时同样拒绝：

- 任一路径段为 `.env` 或以 `.env.` 开头；
- 常见凭据文件：`.netrc`、`.npmrc`、`.pypirc`、`.pgpass`、`.git-credentials`、
  `credentials`、`credentials.json`；
- 私钥/密钥容器：`id_rsa`、`id_ed25519`、`*.pem`、`*.key`、`*.p12`、`*.pfx`、`*.jks`、
  `*.keystore`；
- `.ssh/**`、`.aws/credentials`、`.config/gcloud/**`。

匹配前对 Unicode 做 NFC、对段名做 casefold，并同时检查输入规范化后的工作区相对路径和真实落点
相对路径。这样 `.ENV`、符号链接别名或 `dir/../.env` 都不能绕过。

### 6.5 平台级禁写规则

以下路径无论任务配置如何都禁止写入：

- 任一路径段为 `.git`；
- 依赖/虚拟环境目录：`.venv`、`venv`、`node_modules`、`site-packages`、`__pypackages__`、
  `.tox`、`.nox`；
- 平台敏感读规则覆盖的文件；
- 解析后的目标不是普通文件或目录、或写入父级不安全时；
- 目标在检查后被替换为指向工作区外或敏感位置的符号链接时。

### 6.6 任务级规则与 `allow_write`

- `deny_read` 同时阻止读取和写入；命中后先于禁写例外拒绝；
- `deny_write` 只阻止写；
- `allow_write` 只可绕过任务级 `deny_write`，不得绕过平台级敏感、平台级禁写、工作区包含关系
  或 Gate 的任务级交付物限制；
- 匹配对象是规范化后的工作区相对 POSIX 路径；
- pattern 使用 WP-00 已校验的 POSIX glob 语法，`**` 为跨目录匹配，`*` 和 `?` 只匹配单段；
- 对不存在的写入目标也必须判断其规范化后的预期相对路径，而不是只判断最近存在祖先；
- 工作区内指向工作区内的既有符号链接可以解析到真实目标；最终指向工作区外、敏感路径或受保护
  目录的符号链接一律拒绝。

### 6.7 检查后变化

`check_read`/`check_write` 只用于 Gate 预检，不能单独授权 handler 自行 I/O。`read_file` 与
`write_file_atomic` 必须在打开或替换前重新校验目标、父目录身份和策略。若目标类型、真实路径、
父目录身份或敏感/禁写匹配发生变化，必须失败关闭。

Sandbox 不承诺抵御拥有本机同级权限的攻击者持续替换目录的 TOCTOU 攻击；该限制必须在下游审计
和验收报告明确记录。原子写必须先完成全部校验，再使用同目录排他临时文件，失败时不得留下部分
目标内容。

## 7. Git 基线与变化检测

### 7.1 捕获范围

`capture_baseline` 至少记录：

- `git_root`、`workspace_root`、`workspace_prefix`；
- 当前 HEAD；空仓库使用稳定的 `UNBORN` 哨兵，不伪造 commit ID；
- 规范化 index 摘要；
- `workspace_root` 内所有 Git-visible 修改、删除、重命名和未跟踪文件；
- `protect_other_git_changes=true` 时，`git_root` 内、`workspace_root` 外已有 Git-visible
  脏路径及其内容摘要；
- 每个条目的 status code、文件类型和规范化仓库相对路径；
- 工作区 status 摘要、仓库外围保护基线摘要和总工作区指纹。

若 `state_dir` 位于同一 `git_root`，只排除该精确目录，不得排除其宽松字符串前缀。`.gitignore`
忽略且不在 Git-visible 范围内的外部文件不进入基线，作为已知剩余风险。FIFO、设备、socket 等
特殊文件不支持；`require_clean_workspace=false` 时若初始 entries 含特殊文件，基线失败关闭。

### 7.2 Git 命令安全

Git 身份、status、diff 和 index 采集只能使用代码内生 argv 与 `shell=False`，不得接受模型拼接。
环境必须禁用交互提示、分页、系统/全局配置、外部 diff 和 textconv，并固定 `cwd` 到可信目录。
Git 不可执行、返回非零、输出无法解析或路径身份不一致时失败关闭。

### 7.3 初始干净要求

- `workspace.require_clean_workspace=true` 时，工作区内出现任何 Git-visible 变化即返回
  `SANDBOX_WORKSPACE_DIRTY`，不启动 Agent 或验收命令；
- `false` 时允许建立脏基线，但必须明确记录初始变化；该配置不能关闭平台敏感路径和路径边界；
- `false` 时记录工作区初始 entries 的完整路径、status、类型和内容摘要，后续任何偏离该精确
  基线的情况均视为工作区变化；
- `workspace.protect_other_git_changes=true` 时，仓库外围基线文件的新增、删除、内容变化或
  状态变化均产生 `SANDBOX_EXTERNAL_WORKSPACE_CHANGED`；
- `workspace.protect_other_git_changes=false` 时仍记录 `git_root`、HEAD 和 index，但跳过外围
  脏路径的采集与比较；外围指纹使用域分隔的“未监控”哨兵，比较结果必须明确标记
  `external_protection=DISABLED`，不能声称外围无变化；
- git 身份、HEAD、index、工作区前缀或预期工作区资源发生变化时分别产生稳定错误。

### 7.4 比较语义

`compare_workspace` 重新采集同一范围并使用规范化摘要比较：

- 新路径、消失路径、内容摘要变化、文件类型变化均视为变化；
- 外围保护开启时，已有外围脏文件改动视为变化，不能因 baseline 本来脏而忽略；
- 外围保护关闭时，`external_protection=DISABLED` 且不返回外围变化结论；
- 忽略列表、状态渲染差异、当前时间和本机路径不得影响等价结果；
- 返回的变化路径按仓库相对 POSIX 路径稳定排序，并做数量与字符长度上限；
- 不把变化文件内容、密钥或二进制全文写入结果、异常或日志；
- 无法重新读取 Git 状态时返回失败关闭结果，不得判定“无变化”。

## 8. 命令与环境契约

### 8.1 命令来源

- 只执行 `CommandPolicy` 固定 allowlist 中由可信装配层注册的 `TrustedCommandSpec`；
- `argv` 必须是非空字符串元组，第一项必须解析到允许的可执行文件；
- 模型只提交 `command_id`；不得提交或覆盖 argv、可执行文件、环境变量、`cwd`、超时、输出限制
  或重试策略；
- 明确拒绝 `sh`、`bash`、`zsh`、`cmd`、`powershell`、`env`、`xargs` 等 shell/包装器，除非未来
  单独立项并完成安全评审；
- 可信 argv 元素中即使包含 `;`、`|`、`>`、`$()` 等字符，也只作为普通参数，由 `shell=False`
  直接传递，不进行 shell 拼接、变量展开、通配符展开、管道、重定向或命令替换；
- 命令不可用、可执行文件不在 allowlist、argv 含 NUL 或规格非法时在 `Popen` 前拒绝；
- 同一 Run 只能执行已由任务配置和工具策略授权的 `command_id`；Sandbox 注册表本身不是执行授权。

### 8.2 工作目录与环境

- `cwd` 固定为规范化 `workspace_root`，不继承调用进程当前目录；
- 子进程环境从空白映射构造，不直接复制 `os.environ`；
- 固定注入受控 `PATH`、`LANG=C.UTF-8`、`LC_ALL=C.UTF-8`、`TZ=UTC`；
- `HOME` 和 `TMPDIR` 指向本次执行新建的私有临时目录，目录位于工作区外并使用 `0700`；
- 固定注入 `PYTHONDONTWRITEBYTECODE=1`、`PYTHONNOUSERSITE=1`、`PYTHONHASHSEED=0`；
- Git 子进程固定注入 `GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_GLOBAL=/dev/null`、
  `GIT_TERMINAL_PROMPT=0`、`GIT_PAGER=cat`、`PAGER=cat`；
- P0 不允许任务配置追加任何环境变量；
- 不继承父进程的代理、凭据、Python、pytest 或云配置变量；
- `PATH` 只由已解析解释器所在目录、固定 Git 所在目录和固定系统目录构造，不直接采用宿主
  `PATH` 字符串；
- 环境变量名、值、来源和清理行为必须由测试锁定，不能依赖宿主偶然配置。

### 8.3 输出

- stdout/stderr 分开、并发采集，避免管道互堵；
- 每个流分别以 1,048,576 字节为硬上限；保留头部和尾部，中间丢弃并在唯一位置插入稳定截断标记；
- 超限后继续消费管道但丢弃超出内容，避免子进程因管道阻塞；
- 进入返回值前移除凭据/令牌形态并应用字符上限；
- 截断不得伪造完整输出或成功结论；退出码仍按真实进程事实记录，但依赖完整报告的测试证据无效；
- 不把二进制内容直接转成无界字符串；使用有界、可诊断的解码策略；
- 结果至少包含长度/截断标记、退出码或信号、耗时、是否超时和是否取消。

## 9. 进程与结果语义

### 9.1 启动

- 使用 `subprocess.Popen(..., shell=False, stdin=DEVNULL, cwd=workspace_root, env=minimal_env)`；
- POSIX 使用独立 session/进程组启动，保存进程组 ID；
- 启动失败返回 `START_FAILED`，此时可以确定子进程未运行；
- 启动成功后，Sandbox 不自动重试任何命令。

### 9.2 正常结束

- exit code 0 且未超时/取消：`SUCCEEDED`；
- 非零 exit code、被信号终止或 exec 后失败：`FAILED`；
- 正常结束的 `FAILED` 是确定失败，可由 Gate 记录并返回模型，不自动重试；
- 输出截断本身不改变退出码事实，但下游不得把缺失报告当作完整证据。

### 9.3 超时与取消

触发 `timeout_seconds` 或取消令牌时：

1. 标记 `timed_out` 或 `cancelled`；
2. 向整个进程组发送可捕获终止信号；
3. 在 `kill_grace_seconds` 内等待；
4. 仍未退出则向整个进程组强制终止；
5. 等待/确认进程组不再可观察；
6. 读取已采集的有界输出并返回结构化结果。

仅停止父进程、关闭管道或本地停止等待不满足本规格。若终止失败或无法确认，`status` 必须为
`UNKNOWN_RESULT`，不得自动重试。

### 9.4 结果确定性

| 场景 | 结果 | 是否可自动重试 |
| --- | --- | --- |
| Popen 前拒绝或启动失败 | `START_FAILED`/拒绝错误 | 不自动重试 |
| exit code 0 | `SUCCEEDED` | 由下游决定是否继续 |
| 确定非零退出 | `FAILED` | 不自动重试 |
| 只读命令超时且进程组已终止 | `TIMED_OUT` | 可在明确只读策略下由 WP-04 决定 |
| 幂等命令超时且结果可安全重放 | `TIMED_OUT` | 只允许重新经过 Gate 和审批 |
| 非幂等命令超时/取消 | `UNKNOWN_RESULT` | 禁止 |
| 终止失败或结果无法确认 | `UNKNOWN_RESULT` | 禁止 |

Sandbox 不自行执行重试。任何重试都必须由 WP-04 根据工具元数据、前次结果和 Gate 重新判定。

## 10. 错误契约

WP-03 计划追加以下稳定 `ErrorCode`：

```text
SANDBOX_WORKSPACE_INVALID
SANDBOX_GIT_UNAVAILABLE
SANDBOX_GIT_ROOT_INVALID
SANDBOX_BASELINE_INVALID
SANDBOX_WORKSPACE_DIRTY
SANDBOX_WORKSPACE_CHANGED
SANDBOX_EXTERNAL_WORKSPACE_CHANGED
SANDBOX_IDENTITY_CHANGED
SANDBOX_UNSUPPORTED_PLATFORM

SANDBOX_PATH_INVALID
SANDBOX_PATH_OUT_OF_WORKSPACE
SANDBOX_PATH_SENSITIVE
SANDBOX_PATH_DENY_READ
SANDBOX_PATH_DENY_WRITE
SANDBOX_PATH_PROTECTED
SANDBOX_PATH_CHANGED
SANDBOX_UNSUPPORTED_FILE_TYPE
SANDBOX_FILE_TOO_LARGE
SANDBOX_WRITE_CONFLICT
SANDBOX_IO_ERROR

SANDBOX_COMMAND_UNKNOWN
SANDBOX_COMMAND_INVALID
SANDBOX_EXECUTABLE_DENIED
SANDBOX_SHELL_DENIED
SANDBOX_ENVIRONMENT_INVALID
SANDBOX_COMMAND_START_FAILED
SANDBOX_COMMAND_TIMEOUT
SANDBOX_COMMAND_CANCELLED
SANDBOX_PROCESS_TERMINATION_FAILED
SANDBOX_RESULT_UNKNOWN
```

`SandboxViolation` 至少包含 `code`、`operation`、有界 `message`、规范化相对路径（适用时）和
`retryable=false`。拒绝消息不得包含敏感文件内容、完整环境变量值、底层命令输出或未脱敏 argv。
输入语法错误与平台策略拒绝必须使用不同错误码，以便 Gate 和审计选择稳定 `rule_id`。

## 11. 不变量

| ID | 不变量 |
| --- | --- |
| WP03-INV-001 | 一个 Sandbox 实例只绑定一个已解析 `workspace_root`、一个 `git_root` 和一个前缀。 |
| WP03-INV-002 | 路径包含关系使用真实路径关系判断，不使用字符串前缀。 |
| WP03-INV-003 | 平台敏感/禁写规则不能被任务 `allow_write` 覆盖。 |
| WP03-INV-004 | 拒绝发生在真实 handler、I/O 或进程启动之前；拒绝不得伪造成功。 |
| WP03-INV-005 | 子进程 `cwd` 固定为 `workspace_root`，argv 不经过 shell。 |
| WP03-INV-006 | 子进程环境从显式 allowlist 构造，不继承凭据类变量。 |
| WP03-INV-007 | 超时/取消必须终止进程组；无法确认时结果不得为成功。 |
| WP03-INV-008 | 非幂等超时、取消或终止失败不自动重试，映射为 `UNKNOWN_RESULT`。 |
| WP03-INV-009 | stdout/stderr 有界，截断必须显式标记。 |
| WP03-INV-010 | Git 基线只保存安全摘要和状态，不保存敏感文件全文。 |
| WP03-INV-011 | Git 或文件系统基础设施不可用时失败关闭，不得降级为无边界执行。 |
| WP03-INV-012 | Sandbox 不直接改变 Run、Invocation、Approval、State 或 Audit 业务事实。 |
| WP03-INV-013 | 真实目标仓库文件 I/O 只能通过 Sandbox 安全 I/O 原语，读取/写入硬上限均为 10 MiB。 |
| WP03-INV-014 | 原子写失败不得留下部分目标内容；预期摘要不匹配时不得替换既有文件。 |
| WP03-INV-015 | 不支持可靠 POSIX 进程组的平台不得进入命令执行路径。 |
| WP03-INV-016 | 外围保护关闭必须报告 `DISABLED`，不得伪装成“无变化”。 |
| WP03-INV-017 | 空仓库使用 `UNBORN`；特殊文件失败关闭，不被当作普通内容读取。 |

## 12. 验收场景

所有场景必须映射到自动化测试；平台无法提供的符号链接或进程组能力不得用 skip 作为最终 P0
证据，最终验收环境必须支持声明的边界。

| ID | 场景 | 通过判据 |
| --- | --- | --- |
| WP03-AC-001 | 初始化工作区与 Git 身份 | 嵌套工作区得到真实 `git_root`、`workspace_root` 和 POSIX 前缀；工作区根等于 Git 根时为 `.` |
| WP03-AC-002 | 合法与非法路径语法 | 合法相对/绝对工作区路径规范化；NUL、反斜杠、Windows drive/UNC、超长和控制字符拒绝 |
| WP03-AC-003 | 穿越与绝对越界 | S1 的 `artifacts/../../.env`、`../outside`、`/etc/passwd` 均拒绝，且目标文件未读取 |
| WP03-AC-004 | 前缀误判 | 与工作区同前缀的兄弟目录 `workspace-evil` 不能通过包含关系 |
| WP03-AC-005 | 符号链接逃逸 | 文件/目录软链接指向工作区外时拒绝；不存在尾部经软链接祖先逃逸也拒绝 |
| WP03-AC-006 | 工作区内软链接 | 指向工作区内的既有软链接解析到真实目标；返回规范路径且不越过策略 |
| WP03-AC-007 | 敏感文件读写 | `.env`、`.env.local`、`*.key`、凭据文件的直接、大小写、别名和软链接形式均拒绝 |
| WP03-AC-008 | 平台禁写 | `.git/config` 和依赖目录写入拒绝，即使任务 `allow_write` 命中也不能放行 |
| WP03-AC-009 | 任务禁止与例外 | `deny_read` 拦读写；`deny_write` 拦写；窄范围 `allow_write` 只覆盖对应任务禁写 |
| WP03-AC-010 | 检查后目标变化 | 检查后目标/父级改为越界软链接，最短复核或安全 I/O 路径返回 `SANDBOX_PATH_CHANGED` |
| WP03-AC-011 | 初始工作区干净 | `require_clean_workspace=true` 且工作区有修改时捕获基线失败，不启动 handler/命令 |
| WP03-AC-012 | 仓库外围脏基线 | 工作区外已有脏文件被允许记录；运行前后同内容不改指纹，内容变化被检测 |
| WP03-AC-013 | 仓库外围新增/删除 | 工作区外新增 Git-visible 文件、删除已有文件或改变状态均被检测 |
| WP03-AC-014 | HEAD/index/身份变化 | HEAD、index、Git root、workspace prefix 变化分别返回稳定身份变化错误 |
| WP03-AC-015 | Git-ignored 风险边界 | 工作区外忽略文件不被宣称受保护，文档和验收报告记录剩余风险 |
| WP03-AC-016 | 受信命令白名单 | 注册命令可执行；未知 ID、非 allowlist 可执行文件、shell/包装器、模型 argv/env/cwd 注入均拒绝 |
| WP03-AC-017 | 固定 cwd 与环境 | 子进程报告 cwd 为工作区；注入的 KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL 变量不出现；宿主配置不进入 |
| WP03-AC-018 | shell 特殊字符只作字面量 | 不允许 shell 展开、管道、重定向、命令替换；被拒绝命令的副作用计数为 0 |
| WP03-AC-019 | 输出上限和截断 | 超大 stdout/stderr 被分别限流，结果标记截断，父子进程不因管道阻塞 |
| WP03-AC-020 | 超时终止进程组 | 父命令启动子进程后被杀，父和后代均终止；结果有 `timed_out` 和终止证据 |
| WP03-AC-021 | 用户取消 | 取消令牌触发进程组终止；不继续等待，非幂等结果按规则为 `UNKNOWN_RESULT` |
| WP03-AC-022 | 结果确定性 | 成功、非零失败、只读超时、非幂等超时、启动失败和终止失败映射稳定 |
| WP03-AC-023 | 无重试 | 任意超时、取消、启动失败或未知结果后 Sandbox 不自动重新启动命令 |
| WP03-AC-024 | 数据损坏与异常 | Git 输出损坏、非 Git 目录、不可读元数据或环境构造失败均失败关闭 |
| WP03-AC-025 | 拒绝不触达真实副作用 | 路径/命令拒绝时 handler 计数为 0、目标文件摘要不变、进程注册表未启动 |
| WP03-AC-026 | 飞书 S1-S4 部分证据 | S1/S3 的 P0 拒绝证据和 S2/S4 的 P1 边界证据可关联到 Sandbox 结果 |
| WP03-AC-027 | 离线确定性 | 测试不访问真实模型/网络，不依赖用户凭据；进程/超时测试有界且可重复 |
| WP03-AC-028 | 安全读取边界 | 合法普通文件通过；超过 10 MiB、目录和特殊文件返回稳定错误，且不返回部分源码 |
| WP03-AC-029 | 原子写冲突与创建语义 | CREATE 拒绝既有目标；REPLACE 的 expected digest 不匹配时文件字节不变 |
| WP03-AC-030 | 原子写失败与权限 | 注入写入/替换故障时无部分目标；覆盖保留权限，新建使用受控 umask |
| WP03-AC-031 | 父目录创建策略 | `create_parents=false` 不创建目录；可信 `true` 只创建逐级通过写策略的目录 |
| WP03-AC-032 | 平台能力 | POSIX 平台进入命令执行；模拟不支持平台返回 `SANDBOX_UNSUPPORTED_PLATFORM`，不调用 Popen |
| WP03-AC-033 | 关闭外围保护 | `protect_other_git_changes=false` 记录 Git 身份但明确返回 `external_protection=DISABLED` |
| WP03-AC-034 | 空仓库与特殊类型 | 空仓库使用 `UNBORN`；FIFO/设备/socket 在基线或 I/O 阶段失败关闭 |

## 13. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌表 revision 11。下表只列 WP-03 直接或部分负责
的场景；整条跨包验收的最终归属不得被本包单独宣称完成。

| 编号 | 原验收场景 | 原通过标准 | 优先级 | WP-03 责任 | 最终归属 |
| --- | --- | --- | --- | --- | --- |
| S1 | `artifacts/../../.env` 路径逃逸 | Sandbox 拒绝，handler 不执行 | P0 | 直接：真实落点规范化、工作区包含关系和敏感规则；提供结构化拒绝 | WP-03 / WP-04 / WP-05 / WP-12 |
| S2 | 软链接逃逸或敏感目录访问 | 真实落点校验后拒绝，审计留痕 | P1 | 直接：路径与软链接判定；审计事实由 WP-04 记录 | WP-03 / WP-04 / WP-12 |
| S3 | `run_test` 配置外命令 | 命令不在允许范围时不执行 | P0 | 直接：受信命令 ID、argv、可执行文件 allowlist 和启动前拒绝 | WP-03 / WP-04 / WP-06 / WP-12 |
| S4 | 命令超时或用户取消 | 进程组真实终止，不是只停止等待 | P1 | 直接：进程组 TERM/KILL、取消令牌、终止确认和结构化结果 | WP-03 / WP-06 / WP-11 / WP-12 |
| C4 | 恢复时工作区已变化 | 旧证据或旧审批失效，停止并等待核对 | P0 | 部分：提供工作区/外围/身份变化事实；恢复动作归 WP-07 | WP-03 / WP-07 / WP-12 |
| A2 | 查看一次完整任务 | 能关联 Gate、Sandbox、State、Approval 和执行结果 | P1 | 部分：拒绝和进程结果提供稳定错误码、路径和命令关联字段 | WP-01 / WP-04 / WP-12 |
| T1 | 运行完整测试集 | 所有 P0 场景有确定性测试且可重复通过 | P0 | 部分：WP03-AC 全部离线、确定；全局覆盖由 WP-12 收口 | WP-12 |
| T2 | 用真实模型跑通一类任务 | 产物、Diff、测试记录和审计相互印证 | P1 | 不在本包运行真实模型；只提供 Sandbox 证据边界 | P1 / WP-12 |

WP-03 的测试可以证明 Sandbox 在测试 handler、文件访问或进程启动之前拒绝，但 S1/S3 的完整
“handler 调用次数为 0”还必须在 WP-04 Gate 和 WP-05/WP-06 真实工具测试中复验。S2/S4 的 P1
结果不能被宣称为 P0 交付完成。

## 14. 兼容与迁移

- Sandbox 结果模型显式带 `schema_version`；新增字段或语义变化必须提升版本并补契约测试；
- 新错误码只能追加，不能复用旧值表达不同拒绝原因；
- WP-00 的配置 schema、配置指纹和领域模型字段不变；WP-03 不新增任务配置字段；
- POSIX/Linux/macOS 的路径大小写、Unicode 和符号链接行为必须用平台测试记录；不得用 macOS
  行为推断 Linux 强隔离；
- 进程组语义在声明支持的 POSIX 平台验证；不支持安全进程组的平台必须显式失败关闭；
- 已发布的既有 migration 和数据库表不变，WP-03 不新增 migration；
- 下游只能依赖 WP-03 的公共 Sandbox 接口，不得读取私有字段或复制一份路径判定逻辑。

## 15. 完成定义

WP-03 只有在以下条件全部满足时才能标记 `DONE`：

1. 本规格冻结，未决决策全部关闭，`plan.md` 对应冻结版本；
2. 路径、安全 I/O、Git、命令、环境和进程边界均有正常、拒绝和故障测试；
3. S1/S3 的 Sandbox 拒绝发生在真实 I/O/进程启动前，并由副作用计数证明；
4. S2/S4 的 P1 证据按声明环境通过，未通过不得写成已完成；
5. 超时/取消真实终止进程组，无法确认时返回 `UNKNOWN_RESULT`；
6. Git 基线重开/重采集稳定，外围脏文件变化、HEAD/index/身份变化被检测；
7. 安全 I/O 超限/冲突/原子失败、输出上限、截断、环境清理和敏感信息不泄漏测试通过；
8. 目标 pytest、全量 pytest、Ruff lint、Ruff format check 和 `pip check` 通过；
9. 文档与实现一致，`acceptance-report.md` 基于实际命令和证据标记 `accepted`；
10. 没有把下游 Gate、审批、具体工具、恢复或 Completion 能力写成已实现。

## 16. 已冻结决策

2026-11-05 经需求方逐轮确认，WP-03 决策已登记为 D-29 至 D-36。实现必须遵循下表，不得在任务卡
中重新解释。

| 决策 | 冻结结论 | 影响 |
| --- | --- | --- |
| D-29 | 可信但可能出错的仓库；不承诺 OS 级文件系统/网络隔离 | 测试命令只可用于可信仓库，限制必须写入文档和验收报告 |
| D-30 | Sandbox 提供 10 MiB 有界读和原子整文件写，handler 不自行 I/O | WP-05/WP-06 必须复用安全 I/O，写冲突可失败关闭 |
| D-31 | Linux/macOS POSIX 进程组；其他平台命令能力失败关闭 | 构造阶段区分支持能力，不静默只杀父进程 |
| D-32 | 模型只提交 `command_id`，不能提交 argv/env/cwd/限制 | WP-06 `run_test` 使用可信配置命令，未来参数槽需单独评审 |
| D-33 | stdout/stderr 各 1 MiB，头尾保留；截断报告不能作为完整验收证据 | 进程退出码保留，Completion 责任由 WP-09 执行 |
| D-34 | 空白环境 + 固定 allowlist；任务配置不得追加 env | 环境依赖必须进入代码级安全评审，不继承宿主凭据/代理 |
| D-35 | 外围保护关闭时身份仍记录，外围比较明确为 `DISABLED` | 恢复/完成不能把未监控状态报告为无变化 |
| D-36 | 支持 `UNBORN`、精确初始脏基线、symlink/gitlink 摘要；特殊文件失败关闭 | 空仓库和 dirty-allowed 行为可确定测试 |

规格冻结后可以进入施工，但实现是否完成仍由测试、lint、格式和 `acceptance-report.md` 决定。
