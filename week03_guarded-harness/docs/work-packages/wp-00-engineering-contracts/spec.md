# WP-00 工程与契约内核规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-00 工程与契约内核 |
| 文档状态 | frozen |
| 规格版本 | 1.0.2 |
| 冻结日期 | 2026-11-03（依赖入口修订） |
| 依赖 | 无 |
| 下游工作包 | WP-01、WP-03、WP-04、WP-07、WP-08、WP-09、WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-HARNESS-001`、`REQ-HARNESS-004`、`REQ-GATE-002`、`REQ-GATE-003`、`REQ-SANDBOX-001`、`REQ-TASK-001`、`REQ-TASK-002`、`REQ-COMPLETE-002` |
| 对应设计 | `design.md` 第 4、5、7.2、11.1、13、14 节 |
| 已确认决策 | D-02 至 D-09、D-10 至 D-15 |
| 飞书输入 | 作业文档 revision 14；“核心验收”内嵌表 revision 11 |

本文是 WP-00 已冻结的行为与验收契约。状态为 `frozen` 表示规格评审完成，不表示相关工程能力
已经实现；实现是否完成仍由测试、lint、格式检查和验收证据决定。

发生冲突时，文档权威顺序为
`requirements.md / design.md / decisions.md > 本 spec.md > plan.md > 具体任务卡`。本规格不重新定义
Gate、Sandbox、持久化、恢复或最终验收的跨工作包语义，只为这些能力冻结共同的数据和配置契约。

## 2. 目标

WP-00 必须建立以下可独立验证的能力闭环：

1. 工程在明确的 Python 和依赖边界内可安装、可导入、可离线测试；
2. 所有跨模块公共对象都有严格、类型化、可版本化、可稳定序列化的模型；
3. 错误码、枚举、ID、时间和 JSON 序列化规则稳定，不依赖调用方的临时约定；
4. YAML 配置经过严格加载、语义校验、路径归一化和确定性指纹计算；
5. 两类首期任务都有不依赖真实仓库的 Fixture 配置，可验证配置契约；
6. 测试基础设施提供临时 Git 工作区、SQLite 路径、可控时钟和确定性故障注入能力；
7. 下游工作包可以依赖一份冻结的公共契约，而不必复制模型或猜测字段语义。

## 3. 范围与非目标

### 3.1 范围内

- `.gitignore`、`.python-version`、`pyproject.toml`；
- `src/guarded_harness/__init__.py`、`domain.py`、`config.py`；
- 公共枚举、ID、时间、错误、协议和领域模型；
- YAML 严格加载、配置语义校验、路径归一化和配置指纹；
- `tests/conftest.py`、`tests/test_domain.py`、`tests/test_config.py`；
- 两类任务的最小测试 Fixture；
- 包内文档和文档索引同步。

### 3.2 非目标

- 不创建 SQLite 业务表、migration、审计链或租约，这些属于 WP-01；
- 不实现 Gate 固定检查链、审批消费或 Tool 执行，这些属于 WP-02 和 WP-04；
- 不实现文件、Git、命令或进程 Sandbox 操作，这些属于 WP-03 和 WP-06；
- 不接入真实工具、`ToolCallingAgent`、Fake Model 或 Harness 循环；
- 不实现 Completion 策略、测试报告解析或恢复动作；
- 不交付 `examples/config/` 下用于最终演示的正式样例，这些属于 WP-12；
- 不以“模型可以使用某个模型类”替代真实门禁、执行或验收。

## 4. 参与组件与公开接口

### 4.1 组件边界

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| 打包配置 | 固定 Python、构建后端、依赖、包发现和统一测试入口 | 业务状态与安全策略 |
| `domain.py` | 公共枚举、错误码、ID/时间协议、领域模型和协议 | 读取 YAML、访问仓库或执行工具 |
| `config.py` | 严格读取和校验 YAML、路径归一化、配置指纹 | 创建状态库、启动进程、执行审批 |
| `tests/conftest.py` | 隔离工作区、Git、SQLite 路径、时钟、ID 和故障注入 | 替代下游安全测试 |

`domain.py` 不得导入 `config.py`、Gate、Sandbox、State Store、Audit 或 `smolagents`。`config.py`
可以导入 `domain.py`，不得形成反向依赖。

### 4.2 最小公共 API

包根 `guarded_harness` 必须导出且稳定暴露以下最小 API：

```text
__version__: str

load_config(path: str | Path) -> ResolvedHarnessConfig
config_fingerprint(config: ResolvedHarnessConfig) -> str

ConfigurationError
ValidationIssue
HarnessConfig
ResolvedHarnessConfig
WorkspaceConfig
PathPolicyConfig
BudgetConfig
TaskConfig
ApprovalConfig
CodeUnderstandingAcceptance
TestRepairAcceptance

Clock
IdFactory
FaultInjector

Run
Step
ToolSpec
ToolInvocation
ToolObservation
Approval
Evidence
CompletionCheck
CompletionItem

RunStatus
StopReason
AcceptanceStatus
StepStatus
InvocationStatus
ObservationStatus
GatePhase
ToolRisk
ToolPermission
ToolOutputKind
RetryPolicy
ApprovalRequirement
ConcurrencyClass
ApprovalStatus
EvidenceKind
TaskKind
ErrorCode
```

具体实现文件中的辅助类可以增加，但删除、重命名或改变上述公共语义属于破坏性规格变更。所有
`__all__` 导出必须去重，并由契约测试锁定。

### 4.3 可注入时钟和 ID 工厂

`Clock` 是只读协议，至少提供返回带时区 UTC `datetime` 的 `now()`。生产适配器使用 UTC 当前时间；
测试适配器返回预设序列。所有公共时间字段不得使用无时区时间或本地时区时间。

`IdFactory` 至少提供 Run、Step、Evidence、Approval 和 Checkpoint ID 的生成入口。生产实现必须返回
唯一且不可预测的 ID；测试实现必须返回固定序列。模型提供的 `tool_call_id` 由模型侧产生，WP-00
只定义其字符串边界，不由 `IdFactory` 重写。

### 4.4 故障注入协议

`FaultInjector` 是 WP-00 冻结的测试侧窄接口，至少支持按稳定故障点名称注入“抛出受控异常”和
“记录调用次数”，并支持在 Fixture 中关闭。WP-00 只验证协议和确定性；具体故障点由 WP-01、
WP-04、WP-07 和 WP-12 接入。`FaultInjector` 不得进入生产安全判定路径，也不得成为绕过
失败关闭语义的开关。

## 5. 通用模型与序列化契约

### 5.1 严格性

所有公共 Pydantic 模型必须满足：

- `extra="forbid"`，未知字段一律拒绝；
- 必填字段缺失、错误类型和非法枚举值一律拒绝；
- 不使用静默字符串裁剪、类型猜测或宽松数字转换；
- 可变模型启用赋值校验；需要表达不可变事实的模型使用不可变模型；
- 持久化模型显式包含 `schema_version`；
- 读取 JSON/YAML 后重新构造并校验模型，不直接信任字典；
- 模型错误必须转换为稳定错误码、字段路径和有界说明。

### 5.2 ID

公共 ID 是经校验的 `str`，序列化后不依赖 Python 对象身份：

- `run_id`、`trace_id`、`step_id`、`evidence_id`、`approval_id`、`checkpoint_id` 由 `IdFactory`
  生成，非空、长度和字符集受限；
- `tool_call_id` 由模型提供，至少保留原始字符串，不重新生成；非空、长度受限，并允许常见
  provider 字符集；
- ID 值不用于路径拼接，不能作为文件名或 SQL 片段直接使用；
- 比较使用精确字符串相等，不进行大小写折叠或 Unicode 模糊匹配。

### 5.3 时间

- 内部值使用 `datetime`，必须带 UTC 时区；
- 序列化使用 RFC 3339/ISO 8601，并统一为 UTC `Z` 后缀；
- 禁止 naive datetime、本地时区和依赖本机默认时区；
- 需要顺序的事实时间使用模型字段记录，不只依赖日志时间；
- 测试中的时间由 `Clock` 固定，断言不依赖墙钟。

### 5.4 JSON 和持久化边界

规范的 JSON 序列化必须：

- 使用公开字段名，不因 Python 私有变量或别名改变协议；
- 保持枚举值稳定；
- map 的键按 Unicode 码点排序；有顺序语义的列表保持输入顺序；
- 使用 UTF-8、`ensure_ascii=false`、固定分隔符和无额外空白；
- 不包含随机盐、进程地址、模块对象或无法重建的运行时类型；
- 对固定模型执行 `dump -> validate -> dump` 后语义和规范字节保持一致。

WP-00 不承诺数据库表结构。`schema_version=1` 只表示 WP-00 的模型和 JSON 契约版本，不能作为
WP-01 SQLite migration 版本。

## 6. 公共枚举和错误码

### 6.1 枚举

至少冻结以下枚举值；下游可以追加值，但删除、重命名或复用旧值语义需要版本升级。

| 枚举 | 值 |
| --- | --- |
| `TaskKind` | `code_understanding`、`test_repair` |
| `RunStatus` | `NEW`、`RUNNING`、`WAITING_APPROVAL`、`VERIFYING`、`COMPLETED`、`STOPPED`、`FAILED`、`BLOCKED` |
| `AcceptanceStatus` | `NOT_EVALUATED`、`ACCEPTED`、`REJECTED`、`UNVERIFIABLE` |
| `StepStatus` | `PLANNED`、`RUNNING`、`COMPLETED`、`FAILED` |
| `InvocationStatus` | `PREPARED`、`STARTED`、`SUCCEEDED`、`FAILED`、`TIMED_OUT`、`UNKNOWN_RESULT` |
| `ObservationStatus` | `ALLOWED`、`DENIED`、`WAITING_APPROVAL`、`FAILED`、`UNKNOWN_RESULT` |
| `GatePhase` | `VISIBILITY`、`SCHEMA`、`SEMANTIC`、`PERMISSION`、`RISK`、`APPROVAL`、`REPEAT`、`BUDGET`、`EXECUTION` |
| `ToolRisk` | `READ`、`WRITE`、`EXECUTE` |
| `ToolPermission` | `READ`、`WRITE`、`EXECUTE` |
| `ToolOutputKind` | `JSON`、`TEXT`、`NONE` |
| `RetryPolicy` | `NEVER`、`SAFE_READ_ONLY`、`IDEMPOTENT_ONLY` |
| `ApprovalRequirement` | `NEVER`、`REQUIRED` |
| `ConcurrencyClass` | `SERIAL`、`READ_ONLY_PARALLEL` |
| `ApprovalStatus` | `PENDING`、`APPROVED`、`CLAIMED`、`CONSUMED`、`REJECTED`、`EXPIRED`、`INVALIDATED` |
| `EvidenceKind` | `READ`、`DOCUMENT`、`COMMAND`、`TEST_REPORT`、`DIFF`、`AUDIT` |

`StopReason` 至少包含 `FINAL_ANSWER`、`BUDGET_EXHAUSTED`、`APPROVAL_REQUIRED`、`GATE_BLOCKED`、
`UNKNOWN_RESULT`、`WORKSPACE_CHANGED`、`INTERNAL_ERROR`。若实现需要更多原因，必须同步
`design.md` 和契约测试，不能使用任意自由文本代替枚举。

### 6.2 错误码

`ErrorCode` 是稳定字符串枚举。WP-00 至少提供：

```text
CONFIG_FILE_NOT_FOUND
CONFIG_IO_ERROR
CONFIG_TOO_LARGE
CONFIG_YAML_INVALID
CONFIG_DUPLICATE_KEY
CONFIG_MULTIPLE_DOCUMENTS
CONFIG_ROOT_NOT_MAPPING
CONFIG_SCHEMA_INVALID
CONFIG_PATH_INVALID
CONFIG_PATH_ESCAPE
CONFIG_DUPLICATE_DELIVERABLE
CONFIG_KIND_STRATEGY_MISMATCH
CONFIG_APPROVAL_INVALID
CONFIG_FINGERPRINT_ERROR

DOMAIN_INVALID_MODEL
DOMAIN_SERIALIZATION_ERROR
DOMAIN_ENUM_INVALID
DOMAIN_ID_INVALID
DOMAIN_TIME_INVALID
DOMAIN_SCHEMA_VERSION_UNSUPPORTED
```

`ConfigurationError` 至少包含 `code` 和有界的 `issues`；每个 `ValidationIssue` 至少包含
`code`、规范化字段路径和脱敏说明。错误不得包含完整 YAML、环境变量值、凭据或未裁剪的文件内容。
所有问题的顺序必须稳定，重复加载同一输入得到相同错误序列。

## 7. 公共领域模型

下列字段是最小冻结契约。未列出的实现细节可以调整；字段改名、类型变化或语义变化必须提升规格
版本。

### 7.1 Run 及相关值对象

`WorkspaceIdentity` 至少包含：

```text
git_root: str
workspace_root: str
workspace_prefix: str
baseline_fingerprint: str
external_dirty_baseline_fingerprint: str
```

路径字段表示已经验证的规范化绝对路径或 POSIX 相对路径字符串，不能依赖当前工作目录。
`workspace_prefix="."` 表示工作区等于 Git 根。

`RunBudget` 至少包含 `max_steps: int`、`max_tool_calls: int`、`max_token_cost: Decimal`、
`max_wall_time_seconds: int`。`BudgetUsage` 至少包含已用步数、工具调用数、Token/成本和时间，
均不得为负，且 Run 级校验不得出现 usage 超过上限的已提交状态。

`Run` 至少包含：

| 字段 | 类型/约束 | 说明 |
| --- | --- | --- |
| `schema_version` | `Literal[1]` | 持久化模型版本 |
| `run_id`、`trace_id` | ID 字符串 | 唯一关联 ID |
| `task_id`、`task_kind`、`task_description` | 字符串、枚举 | 任务身份 |
| `config_fingerprint` | `sha256:<64 hex>` | 启动配置指纹 |
| `workspace` | `WorkspaceIdentity` | 仓库/工作区身份 |
| `budget`、`usage` | `RunBudget`、`BudgetUsage` | 预算上限和已用量 |
| `status` | `RunStatus` | 运行状态 |
| `stop_reason` | `StopReason \| null` | 停止原因 |
| `acceptance_status` | `AcceptanceStatus` | 验收状态，默认 `NOT_EVALUATED` |
| `current_step_id`、`last_checkpoint_id`、`resumed_from_checkpoint_id` | ID 或 `null` | 恢复关联 |
| `candidate_final_answer` | 有界字符串或 `null` | 模型候选结果，不是验收结论 |
| `created_at`、`updated_at`、`started_at`、`ended_at` | UTC 时间 | 生命周期 |

### 7.2 Step

`Step` 至少包含 `step_id`、`run_id`、从零或一稳定编号的 `ordinal`、状态、脱敏且有界的摘要、
开始时间和结束时间。步骤摘要不是模型隐藏推理，也不得复制完整 Prompt 或 Audit 事件流。

### 7.3 ToolSpec

`ToolSpec` 是运行期可信注册信息，不是模型参数：

```text
name: str
description: str
input_schema: Mapping[str, JsonValue]
output_kind: ToolOutputKind
risk: ToolRisk
required_permissions: set[ToolPermission]
timeout_seconds: int
retry_policy: RetryPolicy
idempotent: bool
path_arguments: tuple[str, ...]
output_limit_bytes: int
approval_requirement: ApprovalRequirement
concurrency_class: ConcurrencyClass
```

`input_schema` 是 JSON Schema 形状的兼容契约，只包含可序列化值；WP-00 校验其顶层结构和大小，
具体工具参数 schema 校验由 WP-04 执行。`handler`、真实可执行文件路径、凭据和内部实现不得出现
在该公共模型中。

### 7.4 ToolInvocation 与 ToolObservation

`ToolInvocation` 至少包含：

```text
schema_version, tool_call_id, run_id, step_id, tool_name
raw_args, normalized_args, args_digest
risk, required_permissions, idempotent
status, approval_id, result_summary, evidence_ids
error_code, created_at, updated_at
```

`raw_args` 仅用于受控审计；`normalized_args` 是由 Gate 生成的可重放规范参数。WP-00 只冻结字段和
可序列化边界，不提供参数授权。`args_digest` 在 WP-02/WP-04 按审批摘要规则生成。

`ToolObservation` 至少包含 `status`、`code`、`tool_call_id`、`phase`、有界 `message`、
`retryable`、可选 `evidence_id` 和可选结构化 `output`。拒绝状态不得包含伪造成功 output。

### 7.5 Approval

`Approval` 至少包含：

```text
schema_version, approval_id, run_id, task_kind
git_root, workspace_root, workspace_prefix
tool_name, canonical_args_digest, config_fingerprint
requested_by, allowed_approver_roles
status, requested_at, expires_at, decided_at, consumed_at
decided_by, execution_intent_id
```

这些字段只定义审批绑定契约，不定义审批服务实现。参数、工作区、任务、Run 或配置指纹变化必须
使旧审批失效；一次性消费和状态机由 WP-02 实现。

### 7.6 Evidence

`Evidence` 至少包含 `schema_version`、`evidence_id`、`run_id`、可选 `tool_call_id`、
`kind`、相对路径或命令标识、行范围、内容/结果摘要、内容版本摘要、创建时间和有效性状态。
路径证据必须引用工作区内规范化路径；WP-00 不采集文件证据，只冻结结构。

### 7.7 CompletionCheck 与 CompletionItem

`CompletionCheck` 至少包含 `schema_version`、`run_id`、`acceptance_status`、检查项、
失败摘要、完成时间和证据 ID。`CompletionItem` 至少包含稳定 `item_id`、是否通过、错误码、
有界说明和证据 ID。

`CompletionCheck.acceptance_status=ACCEPTED` 只能由 WP-08/WP-09 的 Completion 流程产生。
WP-00 的模型必须允许其他层表达 `REJECTED`、`UNVERIFIABLE` 和 `NOT_EVALUATED`。

## 8. 配置契约

### 8.1 输入边界

`load_config` 只接受一个本地 YAML 文件路径，并保证：

- 文件必须存在且是普通文件；读取失败返回 `CONFIG_IO_ERROR`；
- 文件大小上限为 1 MiB，超限返回 `CONFIG_TOO_LARGE`；
- 使用 safe loader，不允许任意 Python 对象标签；
- 只接受一个 YAML 文档，空文档和多文档拒绝；
- 同一 mapping 的重复键拒绝，不能以后一个值静默覆盖前一个值；
- 根节点必须是 mapping；
- 完成阅读后转换成 Pydantic 严格模型，未知字段、缺失字段和类型错误失败关闭；
- 配置加载是只读操作，不创建目录、数据库、仓库文件或审批记录。

### 8.2 有效配置结构

有效配置至少包含以下逻辑结构：

```yaml
version: 1
state_dir: .guarded-harness
workspace:
  root: .
  require_clean_workspace: true
  protect_other_git_changes: true
budget:
  max_steps: 12
  max_tool_calls: 40
  max_token_cost: 0.25
  max_wall_time_seconds: 1800
task:
  id: understand_login
  kind: code_understanding
  description: 说明登录调用链
  allowed_tools: [list_files, search_code, read_file, write_file, git_status, git_diff]
  paths:
    deny_read: ["**/.env", "**/*.pem", "**/*.key"]
    deny_write: ["src/**", "tests/**"]
    allow_write: ["artifacts/code-understanding.md"]
  acceptance:
    strategy: code_understanding
    deliverables: ["artifacts/code-understanding.md"]
  approval:
    required_tools: [write_file]
    channel: local_cli
```

`test_repair` 任务使用独立 discriminated acceptance 结构，至少包含受控 `test_command` 的 argv
列表和与 `code_understanding` 不同的 `strategy`。只读/可写测试目录的唯一权威来源是
`task.paths.deny_write` 与 `task.paths.allow_write`，不在 acceptance 中重复声明。两类任务的
`kind` 与 `strategy` 必须一一匹配。测试报告格式留给 WP-09 在其规格中冻结；WP-00 不得用自由
字符串隐藏该未决契约。

### 8.3 字段约束

- `version` 只接受整数 `1`；
- `task.id` 是稳定、非空、不含路径分隔符的标识符；
- `allowed_tools` 非空、无重复，并做集合语义规范化；
- `deny_read`、`deny_write`、`allow_write` 是无重复的 POSIX glob 列表，只允许工作区内相对模式；
- `required_tools` 必须是 `allowed_tools` 的子集；
- `max_steps` 和 `max_wall_time_seconds` 必须大于 0；
- `max_tool_calls` 和 `max_token_cost` 必须大于等于 0；
- `deliverables` 非空、无规范化后重复项，必须直接位于/位于工作区内且满足任务路径策略；
- `test_command` 非空、元素为非空字符串，属于可信配置而非模型参数；具体可执行文件策略由
  WP-03/WP-06 再校验；
- `channel` 首期只接受 `local_cli`；
- 未声明的任务类型、验收策略、审批通道和字段全部拒绝。

### 8.4 路径基准与归一化

- `workspace.root` 和 `state_dir` 中的相对路径以**配置文件所在目录**为基准；
- `deliverables` 的相对路径以规范化后的 `workspace.root` 为基准；
- `workspace.root` 必须已存在且为目录；`state_dir` 可以尚不存在；
- 输入路径使用 POSIX 风格写入配置，然后在当前平台转换为 `Path`；
- 对已存在路径执行真实路径解析；对不存在的尾部路径，先解析最近已存在祖先，再拼接剩余组件；
- 使用路径包含关系判断，禁止用字符串前缀；
- 拒绝绝对交付物、`..` 逃逸、符号链接逃逸、空路径和不稳定别名；
- 交付物规范化后统一保存为相对工作区的 POSIX 字符串；
- 配置加载阶段只做静态路径安全校验；真实操作前仍必须由 Sandbox 重新校验。

示例：`artifacts/../artifacts/report.md` 和 `./artifacts/report.md` 规范化后是同一交付物，若同时
出现必须返回 `CONFIG_DUPLICATE_DELIVERABLE`；`artifacts/../../.env` 和指向工作区外的符号链接
必须返回 `CONFIG_PATH_ESCAPE`。

### 8.5 配置指纹

配置指纹必须：

- 算法为 SHA-256，公开形式为 `sha256:<64 位小写十六进制>`；
- 对经过严格校验和路径归一化的全部有效配置字段计算，包含 `state_dir`；
- 在输入前加入固定 `fingerprint_schema: 1` 域分隔；
- 使用第 5.4 节的规范 JSON；
- 对集合语义字段排序，对有顺序语义的字段保留顺序；
- YAML 键顺序、缩进、注释、显式/隐式默认值不改变指纹；
- 路径等价写法规范化后得到相同指纹；
- 任一有效字段、任务策略、预算、路径、命令或审批字段变化都必须改变指纹；
- 不使用文件 mtime、随机值、环境变量、主机名或进程状态；
- 计算失败返回 `CONFIG_FINGERPRINT_ERROR`，不得返回空值或继续启动。

## 9. 测试基础设施契约

`tests/conftest.py` 至少提供以下隔离 Fixture：

- 临时 Git 仓库根和位于其中的任务工作区，不污染真实项目；
- 可配置的仓库外围脏文件和内容哈希基线，为 WP-03/WP-07 复用；
- 临时 SQLite 文件路径，不创建 WP-01 业务表；
- 固定 UTC 时间和 ID 序列，测试中禁止真实 sleep 和随机 UUID 断言；
- 可控 `FaultInjector`，可记录调用次数并注入受控异常；
- 配置 Fixture 工厂，避免每个测试复制大段 YAML。

Fixture 必须使用 `tmp_path` 或等价临时目录，不能使用本机绝对路径常量、用户凭据、网络或真实
模型。测试结束清理临时 Git 仓库、子进程和数据库文件。

## 10. 行为契约

### 10.1 配置加载顺序

```text
确认文件类型和大小
  -> 安全加载单个 YAML 文档
  -> 拒绝重复键和非 mapping 根节点
  -> Pydantic 严格结构校验
  -> 跨字段语义校验
  -> 路径归一化与逃逸检查
  -> 集合/列表规范化
  -> 计算配置指纹
  -> 返回不可变的 ResolvedHarnessConfig
```

前一阶段失败后不得继续执行后续阶段，不得创建目录或改变目标仓库。

### 10.2 领域模型行为

- 构造失败时不产生部分对象，也不把原始注入字段保留在异常上下文外；
- 固定输入模型可稳定 round-trip；
- 未知枚举值和 unknown schema version 明确拒绝；
- `run_id` 与 `trace_id` 缺失时由 Harness/IdFactory 创建，不能使用进程 PID 或时间戳冒充唯一 ID；
- `tool_call_id` 必须从模型调用透传，不因 Gate 包装而被替换；
- Validation error 的字段顺序稳定，便于测试和用户修正。

### 10.3 失败关闭

任何配置解析、路径归一化、模型反序列化或指纹计算异常都必须阻止返回有效配置。WP-00 不包含
handler，因此不直接执行仓库副作用；下游必须把“配置未成功加载”视为 Run 启动失败，不能退回
宽松默认值、环境变量覆盖或空配置。

## 11. 不变量

| ID | 不变量 |
| --- | --- |
| WP00-INV-001 | 公共模型不接受未声明字段，不静默忽略缺失或类型错误。 |
| WP00-INV-002 | 所有跨模块 ID 保留原始 `tool_call_id`，内部生成 ID 可复现于测试。 |
| WP00-INV-003 | 所有公共时间均为 UTC aware，序列化稳定。 |
| WP00-INV-004 | 配置加载只读，路径检查失败时不创建任何目标文件或目录。 |
| WP00-INV-005 | 配置指纹只取决于规范化的有效配置，不取决于 YAML 排版。 |
| WP00-INV-006 | 交付物路径不得逃逸工作区，符号链接按真实落点判断。 |
| WP00-INV-007 | 任务类型与验收策略匹配，未知组合失败关闭。 |
| WP00-INV-008 | WP-00 不执行 Gate、Sandbox、真实工具或 Agent 决策。 |
| WP00-INV-009 | 测试默认离线，不使用真实模型、网络、用户凭据或真实项目配置。 |

## 12. 验收场景

所有场景使用 `pytest` 验证；具体测试函数映射由 `plan.md` 维护。

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| WP00-AC-001 | 新环境安装并导入 | `pip install -e .` 成功，公开导入和 `__version__` 可用 |
| WP00-AC-002 | 公共模型缺字段/多字段/错类型 | 分别返回稳定 `DOMAIN_*` 错误，不生成对象 |
| WP00-AC-003 | 固定领域模型 round-trip | dump、validate、dump 后规范 JSON 字节一致 |
| WP00-AC-004 | 公共枚举和时间边界 | 拒绝未知枚举、naive 时间和本地时区时间 |
| WP00-AC-005 | 两份任务 Fixture | 代码理解与测试修复配置均可独立加载 |
| WP00-AC-006 | YAML 严格解析 | 空文档、多文档、重复键、根非 mapping、未知字段全部拒绝 |
| WP00-AC-007 | 预算和任务语义 | 负预算、零步数、非法工具、策略不匹配和审批越权全部拒绝 |
| WP00-AC-008 | 路径穿越和符号链接 | 绝对路径、`..`、工作区外软链接和非目录根全部拒绝 |
| WP00-AC-009 | 重复交付物 | 原始重复和规范化后重复均返回 `CONFIG_DUPLICATE_DELIVERABLE` |
| WP00-AC-010 | 指纹稳定与敏感 | 等价 YAML 和集合顺序相同；任一有效字段变化得到不同指纹 |
| WP00-AC-011 | 测试确定性 | 固定 Clock/IdFactory 输出固定；测试无网络、真实模型和睡眠依赖 |
| WP00-AC-012 | 失败不产生副作用 | 配置和模型拒绝路径不创建目标文件、目录、数据库或 handler 调用记录 |
| WP00-AC-013 | 公共导出与契约冻结 | 导入集合、枚举值、错误码和模型规范 JSON 与契约测试一致 |

## 13. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌表格 revision 11。原表共 19 条场景，其中
15 条 P0、4 条 P1，全部列入下表；P0 是最终交付必须满足的确定性命中项，P1 不得被宣称为
已完成。WP-00 只负责表中标为“契约/直接”的部分，其余场景由下游工作包实现和验收，但 WP-00
冻结的模型、错误和配置必须能够承载它们。

| 编号 | 原验收场景 | 原通过标准 | 优先级 | WP-00 关系 | 最终归属 |
| --- | --- | --- | --- | --- | --- |
| G1 | 参数缺字段或类型错误 | 返回明确错误，handler 调用次数为 0 | P0 | 直接：严格模型、字段错误码；契约：ToolObservation | WP-00 / WP-04 |
| G2 | 连续三次相同工具和参数 | 第三次在执行前拦截，handler 只执行两次 | P0 | 契约：规范化参数、重复预算字段 | WP-04 |
| G3 | 未审批的写操作 | 进入 `WAITING_APPROVAL`，目标文件不变 | P0 | 契约：Approval、RunStatus、ToolObservation | WP-02 / WP-04 / WP-06 |
| G4 | 审批后重复执行同一调用 | 第一次只执行一次，第二次拒绝，不重复写入 | P0 | 契约：Approval 状态和调用证据 | WP-02 / WP-04 / WP-07 |
| S1 | `artifacts/../../.env` 路径逃逸 | Sandbox 拒绝，handler 不执行 | P0 | 直接：配置路径归一化/逃逸拒绝；执行拦截归下游 | WP-00 / WP-03 |
| S2 | 软链接逃逸或敏感目录访问 | 真实落点校验后拒绝，审计留痕 | P1 | 直接：配置路径软链接检查；执行与审计归下游 | WP-00 / WP-03 |
| S3 | `run_test` 配置外命令 | 命令不在允许范围时不执行 | P0 | 契约：配置化 argv；真实命令策略归下游 | WP-00 / WP-03 / WP-06 |
| S4 | 命令超时或用户取消 | 进程组真实终止，不是只停止等待 | P1 | 契约：Run/调用状态可表达超时 | WP-06 |
| C1 | 每个安全步骤后保存 Checkpoint | 包含 run、step、计划、证据、预算和待审批动作 | P0 | 契约：Run、Step、Evidence、Approval、Budget 模型 | WP-07 |
| C2 | 写入完成后结果未保存时恢复 | 标记 `UNKNOWN_RESULT`，不得自动重放 | P0 | 契约：InvocationStatus/StopReason | WP-07 |
| C3 | 正常中断后换进程恢复 | 找回进度，已完成写入不重复，计数不清零 | P0 | 契约：ID、预算和恢复关联字段 | WP-07 |
| C4 | 恢复时工作区已变化 | 旧证据或旧审批失效，停止并等待核对 | P0 | 契约：Workspace/Evidence/Approval 指纹字段 | WP-07 |
| A1 | 查看一次被拒绝的调用 | 审计记录含 rule、decision、actor、toolCallId | P0 | 契约：tool_call_id、GatePhase、错误码 | WP-01 / WP-04 |
| A2 | 查看一次完整任务 | 能关联 Gate、Sandbox、State、Approval 和执行结果 | P1 | 契约：统一关联 ID 和 Evidence 模型 | WP-01 / WP-04 |
| H1 | 模型说完成但无交付物 | final answer 被拒绝，不能进入完成态 | P0 | 契约：candidate_final_answer 与 CompletionCheck 分离 | WP-08 |
| H2 | 测试通过后又修改源码 | 旧测试证据失效，必须重跑当前版本 | P0 | 契约：Evidence 内容版本和时间字段 | WP-09 |
| H3 | 达到最大轮数或预算 | 以明确 StopReason 停止，不伪装成功 | P0 | 直接：预算/StopReason 模型边界；运行行为归下游 | WP-00 / WP-01 / WP-10 |
| T1 | 运行完整测试集 | 所有 P0 场景都有确定性测试，且可重复通过 | P0 | 部分：WP-00 场景离线确定性；全局由最后交付验证 | WP-12 |
| T2 | 用真实模型跑通一类任务 | 产物、Diff、测试记录和审计能相互印证 | P1 | 不纳入 P0；WP-00 只保留 Model 注入边界 | P1 / WP-12 |

WP-00 不把 G2、G3、C2 等下游行为写成已实现。下游工作包必须在其规格中引用本表对应编号和
本规格冻结的契约条目，最终由 WP-12 检查 P0 场景没有被遗漏或替代。

## 14. 兼容与迁移

- Python 版本由 `.python-version` 约束；运行时依赖和开发依赖以 `pyproject.toml` 为唯一来源，
  开发与验收统一执行 `python -m pip install -e '.[dev]'`；
- 公共模型 JSON 契约使用 `schema_version=1`；不兼容变化必须提升版本并提供显式拒绝或迁移；
- 不得静默改变枚举值、默认值、时间格式、指纹 schema 或 ID 格式；
- 依赖升级必须运行 WP-00 契约测试，并记录 `smolagents` 公开 API 兼容风险；
- WP-00 不创建数据库 migration；WP-01 必须把模型 schema 与数据库 schema 分开管理。

## 15. 已冻结决策

以下原开放决策已于 2026-11-03 按推荐方案确认，并记录为 `decisions.md` 的 D-10 至 D-14。

### OD-01 Python 支持矩阵

`requires-python = ">=3.10,<3.14"`；项目使用仓库现有 `.venv`（Python 3.13）进行安装和验收，
Ruff `target-version=py310` 保持对 Python 3.10 语法的静态检查。当前环境不声明已完成 Python
3.10 运行时验证，该项作为兼容性风险保留。

### OD-02 ID 与时间外部格式

生成 ID 使用 `run_`、`step_`、`evidence_`、`approval_`、`checkpoint_` 前缀加 32 位小写十六进制
UUIDv4；`run_id` 与 `trace_id` 必须不同，`trace_id` 使用 32 位小写十六进制。时间使用 UTC、
`Z` 后缀、微秒精度固定保留。

### OD-03 配置相对路径基准与指纹范围

相对 `workspace.root` 和 `state_dir` 以配置文件目录为基准。`workspace.root` 与 `state_dir`
也允许使用绝对路径，但必须先解析为规范绝对路径并完成类型与安全检查。配置指纹包含全部有效
配置字段，包括规范化后的绝对工作区和 state 路径。集合语义字段排序，交付物与命令顺序保留。
交付物和路径 pattern 只接受工作区相对 POSIX 路径，不接受绝对路径。

### OD-04 测试修复配置的验收字段深度

WP-00 只冻结 `test_command` argv、基线要求和路径/预算/审批字段。测试报告格式与测试节点证据
留给 WP-09 的规格，不在 WP-00 中提前实现或伪造。

### OD-05 公共模型扩展

字段只允许在提升 `schema_version` 或完成明确兼容评审后追加；枚举只允许追加新值并补契约测试。
WP-00 的字段集合是 P0 冻结基线，下游不得用裸字典自行扩展。

### OD-06 公共导出命名

保留本规格中的公共类名，包括 `CompletionCheck` 和 `RunStatus`。下游不得在 WP-08/WP-09 中
无兼容迁移地改为 `CompletionResult`、`CompletionConclusion` 或其他别名。

## 16. 完成定义

WP-00 只有在以下条件全部满足时才能标记 `DONE`：

1. 本文档状态为 `frozen`，开放决策全部关闭并记录；
2. `plan.md` 对应本规格版本并完成批准；
3. 工程可安装、导入，公开契约测试通过；
4. 配置 Fixture 可加载，非法配置全部失败关闭；
5. 路径逃逸、软链接、重复交付物、预算和指纹测试通过；
6. 测试默认离线且不依赖真实模型、网络或用户凭据；
7. Ruff lint、Ruff format check 和相关 pytest 可复现通过；
8. 文档索引和 `task-breakdown.md` 的工作包链接已同步；
9. 没有把后续工作包的计划能力写成已实现功能。
