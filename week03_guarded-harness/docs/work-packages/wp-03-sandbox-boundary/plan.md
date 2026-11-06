# WP-03 Sandbox 边界实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-03 Sandbox 边界 |
| 文档状态 | done |
| 计划版本 | 1.1.0 |
| 批准日期 | 2026-11-05 |
| 完成日期 | 2026-11-06 |
| 对应规格 | `spec.md` v1.1.0 / frozen |
| 前置依赖 | WP-00 v1.2.0 已冻结；WP-01/WP-02 已验收但不直接参与本包公共接口 |
| 施工前置 | 已满足：D-29 至 D-36、D-46、D-47 已确认，规格已冻结 |
| 当前结论 | T01 至 T11 已完成，兼容修订验收待复跑 |
| 下游影响 | WP-04 Gate、WP-05 只读工具、WP-06 写入/命令、WP-07 恢复、WP-08/09 Completion |

本计划不重新定义规格。若施工中发现规格冲突，必须停止并先修改、评审 `spec.md`。涉及配置 schema
或跨包安全语义的变化，还必须先更新 `docs/design.md`/`docs/decisions.md` 和 WP-00 契约。

## 2. 计划目标与完成定义

WP-03 完成后必须只依赖公共接口和临时 Git 工作区独立证明：

1. 工作区、Git 根和前缀只解析一次，所有文件路径按真实落点限定在工作区内；
2. `..`、绝对越界、前缀误判、大小写/分隔符绕过和符号链接逃逸均在真实 I/O 前拒绝；
3. 平台敏感规则不能被子任务 allowlist 覆盖，任务禁写例外只作用于任务级规则；
4. 目标仓库最终文件 I/O 只能通过 Sandbox 的 10 MiB 有界读取和原子写入；
5. Git 工作区、仓库外围脏路径、HEAD、index 和身份变化可稳定检测；
6. 只执行可信注册命令，固定 cwd，使用显式最小环境且不继承凭据；
7. stdout/stderr 有界，超时/取消真实终止 POSIX 进程组，不能确认时返回 `UNKNOWN_RESULT`；
8. 拒绝用例证明 handler/文件/进程副作用计数为 0；
9. 所有测试离线、确定、可重复，最终验收报告中不存在关键 skip；
10. 为下游交付稳定 SandboxPath、SandboxFile、SandboxWriteResult、WorkspaceBaseline、
    WorkspaceComparison、
   SandboxProcessResult、错误码和调用约束；
11. 生成 `acceptance-report.md`，用实际复跑证据决定是否 `accepted`。

Sandbox 完成不等于 Gate、审批、真实工具、恢复或 Completion 完成。

## 3. 文件范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── sandbox.py          # 新增：路径、安全 I/O、Git、命令、环境、进程边界
├── domain.py           # 扩展：Sandbox 公共类型、状态和错误码
├── config.py           # 原则上不改；如决策要求新增字段，先修订 WP-00
└── __init__.py         # 扩展：冻结公共导出

tests/
├── conftest.py          # 按需扩展临时 Git/符号链接/进程辅助
└── test_sandbox.py      # 新增：WP-03 全部验收场景

docs/work-packages/wp-03-sandbox-boundary/
├── spec.md
├── plan.md
└── acceptance-report.md # 施工完成并实测后创建
```

不在 WP-03 创建 `gate.py`、`tools/` 下的生产 handler、`checkpoint.py`、CLI 或 Agent 适配。

### 3.2 依赖方向

```text
domain.py
   ^
   |
sandbox.py ─────> config.py
   |
   +──────> __init__.py

tests/test_sandbox.py ─────> sandbox.py + domain.py + config.py
```

禁止：

- `sandbox.py` 导入 Gate、Approval、StateStore、Audit 或 `smolagents`；
- 通过 `step_callbacks`、模型提示或测试专用开关替代路径/进程检查；
- 直接读取 TaskKind 任务名选择不同安全规则；
- 让 Sandbox 私有 helper 绕过冻结错误码；
- 修改或 monkey patch `smolagents`。

## 4. 详细设计

### 4.1 构造与身份

建议内部值对象：

```text
Sandbox:
  _workspace_root: Path
  _git_root: Path
  _workspace_prefix: str
  _deny_read: tuple[str, ...]
  _deny_write: tuple[str, ...]
  _allow_write: tuple[str, ...]
  _command_policy: CommandPolicy
  _executables: Mapping[str, Path]
  _commands: Mapping[str, TrustedCommandSpec]
  _clock: Clock

SandboxPath:
  schema_version
  original: str
  absolute_path: str
  workspace_relative: str
  git_relative: str
  access: SandboxAccess
  identity_digest: Sha256Digest
```

`Sandbox.revalidate_path(path)` re-runs the original access policy and compares both
canonical location and identity digest. A policy or identity mismatch maps to
`SANDBOX_PATH_CHANGED`.

构造顺序：

```text
验证 ResolvedHarnessConfig 和 CommandPolicy
  -> 规范化 workspace_root
  -> 用固定 Git argv 推导 git_root 和 prefix
  -> 校验包含关系
  -> 编译路径 glob 与平台级规则
  -> 解析允许的可执行文件
  -> 返回不可变 Sandbox
```

任一阶段失败都不应启动子进程、创建目标文件或创建目录。错误不得泄漏用户环境变量。

### 4.2 路径解析

建议实现两个内部函数：

```text
_nearest_resolved_path(candidate) -> Path
_assert_within(root, candidate) -> Path
```

算法：

1. 在字符串层拒绝 NUL/控制字符、反斜杠、Windows drive/UNC、过长输入；
2. 相对路径拼到固定 workspace；绝对路径保留但其真实落点仍需在 workspace；
3. 若候选存在，`resolve(strict=True)`；若不存在，找到最近存在祖先并解析；
4. 使用 `relative_to` 或等价真实路径关系判断包含；
5. 生成工作区相对和 Git 相对 POSIX 路径；
6. 对输入规范化相对路径和真实落点相对路径分别做敏感与规则匹配；
7. 写路径额外检查平台保护与任务 `deny_write`/`allow_write`。

建议内部使用 `Path` 执行系统调用，跨模块结果使用字符串，避免公共模型保存平台相关对象。

### 4.3 安全文件 I/O

建议新增不可变结果类型：

```text
SandboxFile:
  schema_version
  path: SandboxPath
  data: bytes
  content_digest: Sha256Digest
  byte_count: int

SandboxWriteResult:
  schema_version
  path: SandboxPath
  mode: CREATE | REPLACE
  created: bool
  previous_content_digest: Sha256Digest | null
  content_digest: Sha256Digest
  byte_count: int
```

`read_file` 流程：

```text
check_read
  -> revalidate target identity/type
  -> lstat/open ordinary file
  -> fstat and enforce <= max_bytes and <= 10 MiB hard cap
  -> read exact bounded content
  -> verify not truncated and target identity still matches
  -> return data + SHA-256
```

读取超限或读到的文件不是普通文件时失败关闭，不返回部分内容。读取失败可发生在打开、读取或复核
阶段，所有错误统一映射为稳定 Sandbox 错误，不携带内容片段。

`write_file_atomic` 流程：

```text
check_write target and optional parents
  -> validate CREATE/REPLACE and expected digest
  -> enforce <= 10 MiB
  -> create exclusive temp file in target directory
  -> write, flush, fsync
  -> preserve existing mode or apply controlled new-file mode
  -> revalidate target identity and policy
  -> os.replace(temp, canonical target)
  -> fsync parent directory
  -> return previous/new digest
```

- `CREATE` 要求目标不存在；
- `REPLACE` 要求目标是普通文件；expected digest 非空时必须匹配；
- `create_parents` 只能来自可信工具规格，每一级新目录在创建前后都通过写策略；
- 所有失败路径清理临时文件，不能留下部分目标；
- 同目录临时文件使用排他创建，不跟随攻击者预先放置的临时路径；
- 新建文件使用受控 umask；覆盖保留原权限；
- 真实目标为工作区内普通文件时才允许写入；symlink 只作为指向该文件的路径别名。

若最终实现选择目录文件描述符/`dir_fd` 等更强的 no-follow 机制，必须保持本流程的可观察语义并
增加对应平台测试。

### 4.4 路径模式

不应直接混用 `fnmatch` 与 `PurePosixPath.match` 的模糊差异。计划在 `sandbox.py` 内实现单一编译
规则：

- `/` 分段；
- `**` 匹配零个或多个完整段；
- `*`、`?`、字符类只匹配当前段；
- `.` 不进入编译后的模式；
- 大小写策略先按配置/平台规范化，再用 casefold 规则测试；
- 同一路径对平台和任务规则的匹配顺序固定，便于审计 `rule_id`。

必须用固定 pattern 表测试 `artifacts/**`、`tests/generated/**`、`**/.env` 和精确文件名，不能只
测试一个 happy path。

### 4.5 平台级规则

平台敏感读、禁写和依赖目录规则集中为常量/不可变集合，不散落在 handler。推荐结构：

```text
PLATFORM_SENSITIVE_NAMES
PLATFORM_SENSITIVE_SUFFIXES
PLATFORM_SENSITIVE_SEGMENTS
PLATFORM_DENY_WRITE_SEGMENTS
```

每次路径检查同时匹配：

- 输入规范化后的相对路径；
- 真实落点相对路径；
- 每个路径段的 NFC/casefold 版本。

规划测试必须在 Linux/macOS 大小写行为差异下仍确定；无法稳定测试大小写不敏感文件系统时，
至少验证规则匹配层的 casefold 行为。

### 4.6 Git 基线

建议内部 `GitProbe` 只用固定 argv：

```text
git -C <workspace> rev-parse --show-toplevel
git -C <workspace> rev-parse --show-prefix
git -C <workspace> rev-parse --verify HEAD
git -C <workspace> ls-files --stage -z
git -C <workspace> status --porcelain=v2 -z --untracked-files=all
git -C <workspace> diff --raw -z --no-ext-diff --no-textconv
```

实际命令可按 Git 版本调整，但必须：

- 固定 `cwd`/`-C` 与最小环境；
- 禁止 shell、分页、交互、外部 diff 和 textconv；
- 禁用系统/全局 Git 配置；
- 解析 `-z` 输出，不按换行标题解析；
- 把 Git 输出错误映射到稳定 Sandbox 错误码；
- 不把 Git 命令变成模型可调用的通用命令。

内容摘要由受控文件遍历计算：

- 普通文件使用 SHA-256；
- symlink 摘要目标字符串，不跟随到工作区外读取内容；
- 删除用固定哨兵；
- 子模块/gitlink 使用 Git 对象 ID；
- 特殊文件/不可读文件失败关闭，不阻塞在 FIFO 上。

`WorkspaceBaseline` 捕获前后都按路径排序，确保相同工作区得到相同摘要。状态摘要至少绑定：
workspace identity、HEAD、index、工作区 entries、外围 entries 和 schema version。

无 HEAD 仓库使用 `UNBORN`；`require_clean_workspace=false` 时把初始工作区 entries 作为精确
比较快照。实现不能把“本来已脏”简化为忽略这些路径，因为后续对同一路径继续修改也必须被检测。

### 4.7 外围保护

工作区外路径收集：

1. 从 Git status 得到 `git_root` 内变化路径；
2. 排除规范化 `workspace_root` 内路径；
3. 精确排除 `state_dir` 时，只比较规范路径相等/父目录关系，不用字符串前缀；
4. 对每个 Git-visible 外部路径按类型计算摘要；
5. 记录排序后的路径、status code 和摘要；
6. 比较时重跑同一流程并检查集合差异。

上述 1、4、5、6 只在 `protect_other_git_changes=true` 时执行。开关为 `false` 时仍采集 Git
身份、HEAD 和 index；`external_entries=()`，外围指纹使用固定“未监控”域分隔哨兵，
`WorkspaceComparison.external_protection=DISABLED`。测试必须证明关闭状态不会被序列化成
“无变化”或受保护。

被 `.gitignore` 忽略的外部文件不在 Git status 中，测试必须验证文档已记录该限制，但不能伪造
“受保护”结论。

### 4.8 命令注册与可执行文件

建议 `CommandPolicy` 默认由代码内固定映射构建：

```text
python -> sys.executable（真实解析）
git    -> 固定系统 Git（真实解析）
```

任务配置的 `test_command` 由 WP-03 适配层转换为 `TrustedCommandSpec(command_id="acceptance_test")`；
WP-06 的 `run_test` 只引用该 ID。未来增加命令必须显式扩展代码 allowlist，不在配置中接受任意
解释器路径。

注册校验：

- `command_id` 稳定、唯一、非空；
- argv 非空，第一项为可信可执行文件名；
- 拒绝 shell、`env`、`xargs` 等包装器；
- timeout/grace/output 限制为正整数且在上限内；
- `effect_class` 必须显式声明；
- 相同 command ID 注册不同载荷时冲突，不静默覆盖。

### 4.9 环境构造

环境从显式字典开始，不从 `os.environ.copy()` 开始。建议默认：

```text
PATH             = 受控系统目录 + 当前虚拟环境 bin
LANG             = C.UTF-8
LC_ALL           = C.UTF-8
TZ               = UTC
HOME             = 本次进程隔离临时目录
TMPDIR           = 本次进程隔离临时目录
PYTHONDONTWRITEBYTECODE = 1
PYTHONNOUSERSITE = 1
PYTHONHASHSEED   = 0
GIT_CONFIG_NOSYSTEM = 1
GIT_CONFIG_GLOBAL   = /dev/null
GIT_TERMINAL_PROMPT = 0
GIT_PAGER           = cat
PAGER               = cat
```

以上集合由 D-34 冻结。调用方和任务配置都不得追加 env。`PATH` 从已解析解释器目录、固定 Git
目录和固定系统目录构造，不直接使用宿主 `PATH`。locale 在测试环境不支持 `C.UTF-8` 时必须在
WP-03 验收前解决，不能静默改为继承宿主 locale。

临时 HOME/TMPDIR 必须由 Sandbox/ProcessRunner 生命周期管理，执行结束后清理；清理失败记录风险，
不得把临时目录路径当作工作区证据。

### 4.10 进程执行

建议流程：

```text
assert posix process-group platform supported
  -> if unsupported: SANDBOX_UNSUPPORTED_PLATFORM before Popen
lookup command_id
  -> validate still registered/trusted
  -> build environment and cwd
  -> Popen(shell=False, start_new_session=True, stdin=DEVNULL)
  -> drain stdout/stderr concurrently with bounded buffers
  -> wait for exit, timeout or cancellation
  -> TERM process group
  -> grace wait
  -> KILL process group if needed
  -> confirm/report termination
  -> classify result
```

实现时不要把 stdout/stderr 的 pipe 读取放到单线程串行等待，否则任一流超限或卡住会造成死锁。
可使用 `selectors`、受控线程或等价并发读取；线程失败必须映射为 `UNKNOWN_RESULT` 并清理进程组。

超时使用单调时钟/`Popen.wait(timeout)`，不使用墙钟差值判断。取消使用窄接口，例如：

```text
class CancellationToken(Protocol):
    def wait(self, timeout: float) -> bool: ...
```

Sandbox 不把 `threading.Event` 或模型对象作为公共持久化字段。

Linux/macOS 通过 `start_new_session=True` 或等价公开 POSIX 机制创建独立进程组。Windows 及其它
无法可靠终止后代的平台在构造或命令能力初始化时失败关闭，不进入 Popen 路径；不得静默改用
`child.kill()` 冒充进程组终止。

### 4.11 输出缓冲

建议每个流保留 head/tail 两段：

- 每个流硬上限为 1,048,576 字节；
- 总上限内优先保证可诊断的尾部；
- 插入唯一 `...[truncated N bytes]...` 标记；
- UTF-8 解码使用 replacement 或受控字节摘要，不抛未处理异常；
- 返回前再经过统一 redaction/字符上限接口；
- `stdout_truncated`/`stderr_truncated` 独立标记；
- 不因输出超限自动杀掉仍正常运行的只读命令，除非配置/实现明确选择；
- 命令结束后管道 EOF，不能遗留 reader 线程。

头部和尾部的合计仍不得超过 1 MiB。中间丢弃字节数写入单一稳定截断标记。截断不改变真实退出码，
但 `SandboxProcessResult` 必须设置对应 `stdout_truncated`/`stderr_truncated`，下游不得把依赖
完整输出的报告当作有效测试证据。

### 4.12 结果分类

分类函数必须是纯、可单元测试的：

```text
classify_process_result(returncode, timed_out, cancelled, group_terminated,
                        effect_class, start_failed) -> SandboxProcessStatus
```

至少覆盖：

- 启动失败；
- 0 退出；
- 非零退出；
- 只读超时且终止成功；
- 幂等超时且终止成功；
- 非幂等超时/取消；
- TERM/KILL 失败或无法确认；
- 取消优先级高于普通非零退出。

分类结果不得伪造 exit code、signal 或 `process_group_terminated=true`。

## 5. 施工任务卡

所有任务在规格冻结后执行；每张卡先写测试，再实现，再运行目标测试。

### WP03-T01 公共类型与错误码

| 项目 | 内容 |
| --- | --- |
| 目标 | 冻结 `SandboxAccess`、`SandboxViolation`、`SandboxPath`、`SandboxFile`、`SandboxWriteMode`、`SandboxWriteResult`、`WorkspaceBaseline`、`WorkspaceComparison`、`CommandPolicy`、`TrustedCommandSpec`、`SandboxProcessResult` 和错误码 |
| 前置 | D-29 至 D-36 已确认，spec frozen |
| 涉及文件 | `domain.py`、`sandbox.py`、`__init__.py`、`tests/test_domain.py`、`tests/test_sandbox.py` |
| 对应规格 | 第 5、10、11 节；WP03-AC-001、WP03-AC-022、WP03-AC-024、WP03-AC-028 |
| 正常路径 | 合法值可构造；错误码/枚举/JSON round-trip 稳定 |
| 拒绝路径 | 未知枚举、缺字段、非法限制、非法路径/命令组合失败关闭 |
| 完成条件 | 公共导出测试通过；没有裸字典跨模块漂移 |

### WP03-T02 工作区身份与路径边界

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 workspace/git/prefix 解析、路径输入规范化、最近祖先真实解析和包含关系判断 |
| 前置 | T01 |
| 涉及文件 | `sandbox.py`、`domain.py`、`tests/test_sandbox.py` |
| 对应规格 | WP03-AC-001、WP03-AC-002、WP03-AC-003、WP03-AC-004、WP03-AC-005、WP03-AC-006 |
| 正常路径 | 工作区内普通/缺失路径得到规范 absolute、workspace relative 和 git relative |
| 拒绝路径 | 穿越、绝对越界、前缀误判、Windows/UNC/反斜杠、符号链接逃逸 |
| 完成条件 | 拒绝测试证明 `Path.read_*`/目标写入/副作用计数均为 0 |

### WP03-T03 路径策略与平台规则

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现敏感读、平台禁写、任务 deny/allow 规则和稳定匹配顺序 |
| 前置 | T02 |
| 涉及文件 | `sandbox.py`、`tests/test_sandbox.py` |
| 对应规格 | WP03-AC-007、WP03-AC-008、WP03-AC-009、WP03-AC-010 |
| 正常路径 | 非敏感工作区源码/文档路径按任务规则通过 |
| 拒绝路径 | 敏感文件直接/别名/软链接、`.git`/依赖写入、allow 绕过平台规则 |
| 完成条件 | 平台规则不可被任务 allowlist 覆盖；检查后身份变化拒绝 |

### WP03-T04 安全文件 I/O

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 10 MiB 有界读取、原子替换、预期摘要、受控父目录创建和文件类型检查 |
| 前置 | T02、T03；D-30 已确认 |
| 涉及文件 | `sandbox.py`、`domain.py`、`__init__.py`、`tests/test_sandbox.py` |
| 对应规格 | WP03-AC-010、WP03-AC-028 至 WP03-AC-031、WP03-AC-034 |
| 正常路径 | 普通文件读取摘要稳定；CREATE/REPLACE 成功且无部分内容 |
| 拒绝路径 | 超限、目录/特殊文件、摘要不匹配、无权限父目录、创建/替换冲突 |
| 完成条件 | 失败无部分目标；权限语义正确；handler 没有自行 I/O 的公共旁路 |

### WP03-T05 Git 基线与变化比较

| 项目 | 内容 |
| --- | --- |
| 目标 | 采集 HEAD/index、工作区 entries、可选外围 dirty 摘要，并实现精确稳定比较 |
| 前置 | T02；D-35、D-36 已确认 |
| 涉及文件 | `sandbox.py`、`tests/conftest.py`、`tests/test_sandbox.py` |
| 对应规格 | WP03-AC-011 至 WP03-AC-015、WP03-AC-024、WP03-AC-033、WP03-AC-034 |
| 正常路径 | 干净/空仓库、脏基线、已有外围脏文件、重采集相同基线稳定 |
| 拒绝路径 | 工作区脏、外围变化、HEAD/index/身份变化、Git 不可用/输出损坏、特殊文件 |
| 完成条件 | 外围保护开关语义明确；基线不保存文件内容；排序稳定；忽略文件风险文档化 |

### WP03-T06 受信命令与最小环境

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 CommandPolicy、command_id 注册、可执行文件解析和显式环境构造 |
| 前置 | T01；D-32、D-34 已确认 |
| 涉及文件 | `sandbox.py`、`domain.py`、`tests/test_sandbox.py` |
| 对应规格 | WP03-AC-016、WP03-AC-017、WP03-AC-018、WP03-AC-024 |
| 正常路径 | 配置命令映射为固定 command ID；cwd/env 正确 |
| 拒绝路径 | 未注册命令、shell/包装器、模型 argv/env/cwd、宿主凭据变量 |
| 完成条件 | 拒绝发生在 Popen 前；环境字典可审计且不泄漏值 |

### WP03-T07 进程执行、超时、取消与输出

| 项目 | 内容 |
| --- | --- |
| 目标 | 实现 POSIX 独立进程组、并发输出采集、TERM/KILL、取消、平台失败关闭和结果分类 |
| 前置 | T06；D-31、D-33 已确认 |
| 涉及文件 | `sandbox.py`、`domain.py`、`tests/test_sandbox.py` |
| 对应规格 | WP03-AC-019 至 WP03-AC-023、WP03-AC-032 |
| 正常路径 | 成功/失败命令有确定 exit code；超时/取消终止父和后代 |
| 拒绝/故障 | 启动失败、终止失败、输出截断、非幂等超时进入 UNKNOWN_RESULT |
| 完成条件 | 无孤儿进程、无管道死锁、无自动重试；测试有界 |

### WP03-T08 副作用证明与安全回归

| 项目 | 内容 |
| --- | --- |
| 目标 | 汇总路径/文件 I/O/命令拒绝不触达 handler/外部副作用/进程的证据，执行完整 WP03 场景矩阵 |
| 前置 | T02 至 T07 |
| 涉及文件 | `tests/test_sandbox.py`、必要的 Fixture |
| 对应规格 | WP03-AC-025 至 WP03-AC-027、WP03-AC-030、WP03-AC-032 |
| 正常路径 | 全部 AC 有明确测试名称和断言 |
| 拒绝/故障 | 失败不得以 skip、xfail 或宽泛异常断言掩盖 |
| 完成条件 | 目标/全量 pytest、Ruff lint、format check 通过 |

### WP03-T09 文档、下游交接与验收准备

| 项目 | 内容 |
| --- | --- |
| 目标 | 同步公共接口、错误码、限制、测试命令和下游调用约束，生成验收报告草稿 |
| 前置 | T01 至 T08；所有 AC 有实际证据 |
| 涉及文件 | `docs/README.md`、`docs/work-packages/index.md`、`docs/task-breakdown.md`（如需链接）、WP-03 文档和 `acceptance-report.md` |
| 对应规格 | 第 13 至 16 节 |
| 正常路径 | spec/plan/实现/测试一致；S1-S4 的包内责任和下游责任分开记录 |
| 拒绝路径 | 关键测试失败、skip、未知结果未记录、P1 结果冒充 P0 时不得 accepted |
| 完成条件 | 验收报告实际复跑、复核并标记 accepted 后，工作包才允许 DONE |

### WP03-T10 安全目录枚举（v1.1 兼容修订）

| 项目 | 内容 |
| --- | --- |
| 目标 | 新增 `list_directory`、`walk_readable_files` 及其公共结果类型，避免下游直接遍历真实仓库 |
| 前置 | D-46 已确认 |
| 涉及文件 | `sandbox.py`、`__init__.py`、`tests/test_sandbox.py` |
| 对应规格 | 第 5.1、12、16 节；WP03-AC-035、WP03-AC-036 |
| 完成条件 | 稳定排序、省略敏感名称、目录 symlink 不递归、各类上限与截断原因测试通过 |

### WP03-T11 受限 pathspec（v1.1 兼容修订）

| 项目 | 内容 |
| --- | --- |
| 目标 | `TrustedCommandSpec` schema v2 增加 `allow_pathspecs`，`Sandbox.execute` 只追加已复核 READ 路径 |
| 前置 | D-47 已确认 |
| 涉及文件 | `sandbox.py`、`__init__.py`、`tests/test_sandbox.py` |
| 对应规格 | 第 5.4、12、16 节；WP03-AC-037、WP03-AC-038 |
| 完成条件 | 未授权、跨工作区、非 READ、过量 pathspec 在 Popen 前拒绝；旧命令调用保持兼容 |

## 6. 施工顺序与可提交增量

```text
T01 公共类型/错误
  -> T02 工作区与路径
  -> T03 策略与敏感路径
  -> T04 安全文件 I/O
  -> T05 Git 基线
  -> T06 命令/环境
  -> T07 进程/超时/输出
  -> T08 安全回归
  -> T09 文档/验收
```

建议提交边界：

```text
test(wp-03): define sandbox path and process contracts
feat(wp-03): enforce canonical workspace path boundaries
feat(wp-03): add bounded reads and atomic workspace writes
feat(wp-03): capture git workspace and external-dirty baselines
feat(wp-03): register trusted commands and minimal environments
feat(wp-03): run bounded process groups with timeout and cancellation
test(wp-03): cover sandbox side-effect and failure boundaries
docs(wp-03): accept sandbox boundary
```

T01/T02 未完成前不得把 Sandbox 暴露给真实仓库工具。T04 未完成前不得把文件 I/O 交给 WP-05/06。
T07 未完成前不得把命令执行接入 Gate 或
`run_test`。

## 7. 测试计划

### 7.1 测试基础设施扩展

`tests/conftest.py` 可新增但不限于：

| Fixture/helper | 用途 | 约束 |
| --- | --- | --- |
| `workspace_with_commit` | 有稳定 HEAD/index 的临时 Git 仓库 | 不污染真实仓库 |
| `nested_workspace_repo` | `git_root` 下的独立任务子目录 | 检验 prefix |
| `external_dirty_repo` | 工作区外已有 modified/untracked 文件 | 检验外围保护 |
| `symlink_factory` | 创建文件/目录/断链/越界链接 | 平台不支持时显式失败或限定平台 |
| `process_marker_factory` | 记录 handler/文件/子进程是否被执行 | 拒绝路径计数必须为 0 |
| `bounded_process_command` | 产生子进程、输出或超时的固定 Python 命令 | 不依赖 shell 字符串 |
| `atomic_write_fault` | 在临时写、fsync、替换阶段注入故障 | 每个故障点验证无部分目标 |

Fixture 不得把本机绝对用户路径写进测试断言，不访问网络、真实模型或用户凭据。

### 7.2 `tests/test_sandbox.py` 分组

| 测试组 | 覆盖 |
| --- | --- |
| workspace_identity | 根、嵌套、前缀、非 Git、HEAD/index |
| path_lexing | 相对、绝对、点段、NUL、反斜杠、Windows drive/UNC、长度 |
| containment | `..` 逃逸、绝对越界、兄弟前缀、大小写/Unicode 别名 |
| symlinks | 工作区内、工作区外、断链、祖先软链接、写入父级替换 |
| safe_io | 10 MiB 上限、普通文件、原子写、摘要冲突、父目录创建、权限 |
| sensitive_policy | `.env`、密钥、凭据、Git `.git`、依赖目录、大小写 |
| task_policy | `deny_read`、`deny_write`、`allow_write` 优先级 |
| git_baseline | 干净、内部脏、外围脏、新增/删除、HEAD/index/身份变化、忽略文件风险 |
| command_policy | 注册、未知、shell、包装器、argv/env/cwd 注入、可执行文件不允许 |
| environment | 显式 allowlist、凭据移除、受控 HOME/TMPDIR、Git/Python 安全变量 |
| process_lifecycle | cwd、成功、非零、启动失败、超时、取消、后代终止、终止失败 |
| output_bounds | stdout/stderr 独立上限、head/tail、截断标记、无死锁 |
| no_side_effect | 路径/命令拒绝时 handler/I/O/进程计数为 0 |
| determinism | 重复运行摘要、排序和结果稳定 |

### 7.3 规格场景到测试与证据映射

| 规格场景 | 建议测试入口 | 关键证据 |
| --- | --- | --- |
| WP03-AC-001 | `test_workspace_and_git_identity` | 规范化根、prefix、重开一致 |
| WP03-AC-002 | `test_path_syntax_boundaries` | 每类非法输入稳定错误码 |
| WP03-AC-003 | `test_traversal_and_absolute_escape_are_rejected` | 文件读取/写入/副作用计数 0 |
| WP03-AC-004 | `test_sibling_prefix_is_not_inside` | `relative_to` 关系断言 |
| WP03-AC-005 | `test_symlink_escape_is_rejected` | 真实目标未读取、未写入 |
| WP03-AC-006 | `test_internal_symlink_resolves_to_real_target` | canonical path 与 rel path |
| WP03-AC-007 | `test_platform_sensitive_paths_cannot_be_read` | 直接/别名/软链接均拒绝 |
| WP03-AC-008 | `test_platform_deny_write_beats_task_allow` | `.git`/依赖目录不变 |
| WP03-AC-009 | `test_task_deny_allow_precedence` | allow 只绕过对应 deny |
| WP03-AC-010 | `test_path_identity_change_fails_before_io` | `SANDBOX_PATH_CHANGED` |
| WP03-AC-011 | `test_dirty_workspace_fails_baseline` | handler/命令计数 0 |
| WP03-AC-012 | `test_external_dirty_baseline_roundtrip` | 相同摘要、变化检测 |
| WP03-AC-013 | `test_external_new_delete_detected` | 路径集合差异 |
| WP03-AC-014 | `test_git_identity_changes_detected` | HEAD/index/prefix 错误 |
| WP03-AC-015 | `test_ignored_external_paths_are_documented_risk` | 不宣称保护 |
| WP03-AC-016 | `test_command_registry_rejects_untrusted_inputs` | Popen 前拒绝 |
| WP03-AC-017 | `test_environment_allowlist_and_controlled_dirs` | 子进程输出安全 env |
| WP03-AC-018 | `test_shell_metacharacters_are_not_interpreted` | 副作用 marker 不存在 |
| WP03-AC-019 | `test_stdout_stderr_are_bounded` | 截断标记、进程完成 |
| WP03-AC-020 | `test_timeout_kills_process_group` | 父/后代 PID 消失 |
| WP03-AC-021 | `test_cancel_kills_process_group_and_classifies_unknown` | cancel + UNKNOWN |
| WP03-AC-022 | `test_process_status_classification_matrix` | 纯分类表测试 |
| WP03-AC-023 | `test_sandbox_never_auto_retries` | 启动计数恰好 1 |
| WP03-AC-024 | `test_git_and_filesystem_failures_fail_closed` | 拒绝而非降级 |
| WP03-AC-025 | `test_rejections_have_no_real_side_effect` | marker/file/git snapshot |
| WP03-AC-026 | `test_feishu_s1_s4_partial_mapping` | 包内证据与下游责任 |
| WP03-AC-027 | `test_sandbox_suite_is_offline_and_repeatable` | 无网络/模型/凭据、重复运行一致 |
| WP03-AC-028 | `test_read_file_is_bounded_and_regular_only` | 超限/目录/特殊文件拒绝，无部分内容 |
| WP03-AC-029 | `test_atomic_write_create_replace_and_expected_digest` | CREATE/REPLACE 与冲突字节不变 |
| WP03-AC-030 | `test_atomic_write_failure_leaves_no_partial_target` | 注入故障后目标摘要不变且临时文件已清理 |
| WP03-AC-031 | `test_create_parents_obeys_write_policy` | 父目录逐级检查，关闭时不创建 |
| WP03-AC-032 | `test_unsupported_platform_fails_before_popen` | 平台错误、Popen 计数 0 |
| WP03-AC-033 | `test_disabled_external_protection_is_explicit` | identity 有值、`external_protection=DISABLED` |
| WP03-AC-034 | `test_unborn_and_special_git_entries` | `UNBORN`、gitlink/symlink、特殊文件拒绝 |
| WP03-AC-035 | `test_list_directory_is_safe_sorted_and_bounded` | 敏感/越界条目省略、目录 symlink 不递归 |
| WP03-AC-036 | `test_walk_readable_files_reports_limit_reasons` | ENTRY/FILE/BYTE 上限稳定且无无界扫描 |
| WP03-AC-037 | `test_pathspec_execution_rejects_untrusted_paths` | Popen 前拒绝未授权、非 READ、过量 pathspec |
| WP03-AC-038 | `test_pathspec_execution_uses_validated_read_paths` | argv 独立追加、旧调用兼容、无 shell 展开 |

场景映射只指向测试类别；具体函数名可在施工时稳定化，但不得删除场景或把它改成只检查模块存在。

## 8. 验收命令

规格冻结并完成施工后至少运行：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_sandbox.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m pip check
```

最终证据必须记录 Python/pip/Git/OS、退出码、测试数量、失败/skip、关键进程组证据、路径拒绝
副作用计数和工作区 diff 范围。不得复制本文预期值作为报告结果。

## 9. 安全与故障矩阵

| 边界 | 预期失败点 | 必须留下的证据 |
| --- | --- | --- |
| 相对路径穿越 | 最近祖先解析/包含关系 | 稳定错误码，目标文件摘要不变 |
| 绝对越界 | canonical relative 检查 | 目标文件未读/写 |
| 符号链接逃逸 | 真实目标包含关系 | canonical 逃逸路径和 rule |
| `.env`/密钥 | 平台敏感规则 | 内容不出现在异常/审计/输出 |
| `.git`/依赖写入 | 平台禁写 | `.git`/依赖路径不存在副作用 |
| `allow_write` 过度 | 平台规则先于任务例外 | 任务例外不能放宽平台安全 |
| 初始工作区脏 | baseline gate | 不启动 handler/命令 |
| 外围脏文件变化 | entries/hash 比较 | 变化路径和稳定错误码 |
| HEAD/index 变化 | 身份比较 | identity changed，拒绝继续 |
| 未注册命令 | command registry | Popen 计数 0 |
| shell/包装器 | executable policy | Popen 计数 0 |
| 宿主凭据变量 | 显式环境构造 | 子进程输出中无原值 |
| 输出超限 | 有界 reader | 截断标记，父进程不阻塞 |
| 超时 | TERM/KILL 进程组 | 父/后代终止、timed_out |
| 取消 | cancellation token | cancelled/UNKNOWN 分类正确 |
| TERM/KILL 失败 | 终止确认 | UNKNOWN_RESULT，不自动重试 |
| Git 不可用/损坏 | 固定 GitProbe | 基线失败关闭 |
| 检查后替换 | identity revalidation | PATH_CHANGED，真实 I/O 未执行 |
| 读取超限/特殊文件 | 有界安全读取 | 失败关闭，无部分内容 |
| 写摘要冲突 | REPLACE expected digest | 原文件摘要不变 |
| 原子写中途失败 | 临时文件清理 | 无部分目标、无未清理临时文件 |
| 不支持进程平台 | 构造/能力检查 | Popen 计数 0 |
| 外围保护关闭 | 显式监控状态 | `external_protection=DISABLED` |

## 10. 风险、限制与缓解

| 风险/限制 | 影响 | 处理 |
| --- | --- | --- |
| 同进程文件路径检查存在 TOCTOU 窗口 | 极小概率绕过 | D-30 选择安全 I/O 原语；复核目标身份；记录残余风险 |
| 子进程以当前用户权限运行 | 目标测试代码可访问工作区外文件 | 明确不作为 OS 级沙箱；固定命令/最小 env；未来另立容器工作包 |
| Git-ignored 外围文件不可见 | 外部变化可能漏检 | 文档和验收报告记录剩余风险，不宣称强隔离 |
| POSIX 进程组不同平台差异 | Windows 不能保证后代终止 | D-31 只支持 POSIX，其他平台 fail closed |
| 输出上限导致诊断缺失 | 测试失败难以定位 | D-33 使用 head/tail 保留；显式截断标记 |
| 环境 allowlist 过窄 | 某些测试命令无法运行 | D-34 冻结固定集合；变更需安全评审和测试 |
| 符号链接/大小写文件系统差异 | 跨平台漏判 | canonical 检查 + casefold 匹配 + 平台测试 |
| 空 Git 仓库/子模块特殊状态 | 基线解析复杂 | 使用 `UNBORN`/gitlink 哨兵并补测试，不猜 commit |

## 11. 回滚策略

1. 实现前记录已验收 WP-00/WP-01/WP-02 代码基线和现有测试结果；
2. 任务按 T01 至 T09 独立可回滚，优先撤回公共导出和新增文件；
3. 不修改已发布 migration；WP-03 不新增数据库 migration；
4. 若路径安全规则产生误拒，不能直接放宽为字符串前缀或忽略 allowlist，应先补测试并提升规格；
5. 若进程组终止在某些平台不可用，关闭该平台的 Sandbox 命令能力并保留只读路径/基线能力；
6. 若已产生真实执行事实，回滚代码不能删除审计或状态，必须保留事实并由人工/前向修复处理；
7. 回滚后下游 WP-04/WP-05/WP-06 必须停止调用未验证的 Sandbox API。

## 12. 已确认决策与实施约束

D-29 至 D-36、D-46、D-47 已写入 `decisions.md`，并在冻结规格第 16 节形成行为契约。实施时按以下
方式落地：

| 决策 | 实施约束 | 主要任务 |
| --- | --- | --- |
| D-29 | 只支持可信但可能出错的仓库；文档不宣称 OS 级文件系统或网络隔离 | T01、T09 |
| D-30 | Sandbox 负责 10 MiB 有界读取与原子整文件写；handler 不自行访问目标文件 | T04 |
| D-31 | Linux/macOS POSIX 进程组；其他平台命令能力构造失败关闭 | T07 |
| D-32 | 模型只提交 `command_id`；argv/env/cwd/限制来自可信装配 | T06 |
| D-33 | stdout/stderr 各 1 MiB，头尾保留，截断报告不得当完整验收证据 | T07 |
| D-34 | 空白环境 + 固定 allowlist，任务配置不得追加 env | T06 |
| D-35 | 外围保护关闭时仍记录身份，但明确返回 `external_protection=DISABLED` | T05 |
| D-36 | 支持 `UNBORN` 与精确脏基线；symlink/gitlink 摘要；特殊文件失败关闭 | T05 |
| D-46 | 目录枚举和递归遍历只通过 Sandbox 公共接口，敏感条目聚合省略 | T10 |
| D-47 | 可信命令只追加已复核 READ pathspec，不接受任意 argv/revision | T11 |

这些是已批准计划的一部分，不再是待确认项。若施工发现需要改变，必须停止并先提升规格版本，
不能通过任务卡或测试约定静默改变行为。

## 13. 下游交接

WP-03 验收后必须向 WP-04、WP-05、WP-06、WP-07 交付：

- `SandboxPath`、`SandboxFile`、`SandboxWriteResult`、`WorkspaceBaseline`、
  `WorkspaceComparison`、`SandboxProcessResult` 和错误码；
- 路径检查顺序、平台敏感规则、任务 deny/allow 优先级；
- 10 MiB 有界读取、原子写、预期摘要和受控父目录创建；
- Git 基线采集范围和 ignored external 剩余风险；
- command_id allowlist、固定 cwd、环境 allowlist 和 argv 约束；
- 超时/取消/终止/截断/UNKNOWN_RESULT 的结果语义；
- 每个 WP03-AC 的测试入口和实际证据；
- 下游必须重新经过 Gate、审计、审批、状态更新和 Completion 的边界清单。

下游不得复制路径或环境判定逻辑，也不得把 Sandbox 的“允许执行”当作业务授权。

## 14. 实际完成情况

当前规格、实现、测试和验收报告均已完成。

| 项目 | 状态 |
| --- | --- |
| 规格 | `spec.md` v1.1.0 / frozen |
| 计划 | `plan.md` v1.1.0 / done |
| 决策 | D-29 至 D-36、D-46、D-47 已确认并同步 `decisions.md`/`design.md` |
| `sandbox.py` | 路径、10 MiB 安全 I/O、Git 基线、命令、环境和 POSIX 进程组已实现 |
| 测试 | v1.1 目标 `56 passed`；全量 `196 passed`；无 skip/error |
| 验收报告 | [acceptance-report.md](acceptance-report.md) v1.1.0 / accepted |

WP-03 已完成。Gate、真实工具、恢复和 Completion 仍由后续工作包负责。
