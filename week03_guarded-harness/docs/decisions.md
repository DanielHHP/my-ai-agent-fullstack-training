# Guarded Coding Harness 设计决策

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档状态 | P0 与 WP-13 P1 决策均已确认 |
| 对应需求 | [requirements.md](requirements.md) |
| 对应设计 | [design.md](design.md) |
| 决策范围 | D-01 至 D-100 已确认 |

本文记录项目设计决策。D-01 至 D-86 是 P0 已确认基线；D-87 至 D-100 是 2026-11-09 至
2026-11-10 基于真实模型接入评估确认的 WP-13 P1 决策。D-07、D-78 继续约束 P0 和 version 1
配置，不阻止 version 2 的受控 P1 Provider 扩展。

## 2. 已确认决策

| ID | 决策 | 结论 |
| --- | --- | --- |
| D-01 | 审批交互入口与等待语义 | 首期采用本地 CLI + SQLite 审批单，复用统一审批策略并保留 `ApprovalChannel` 接口。默认在 `WAITING_APPROVAL` 安全退出，用户审批后显式 `resume`；同时提供可选 `--wait-for-approval` 模式在同一进程轮询并继续。审批命令只改变授权，不执行 handler。 |
| D-02 | 初始 Git 干净范围 | 只要求 `workspace_root` 干净，允许同一 `git_root` 下其它子目录预先存在修改。启动时记录仓库外围 Git-visible 脏路径和内容哈希，Run 结束后复查；新增修改、既有修改变化、HEAD/index/身份变化均拒绝验收。`workspace_root` 等于 `git_root` 时退化为整仓库干净。 |
| D-03 | 代码理解交付物路径 | 使用任务配置中的 `deliverables` 非空路径列表，由配置加载阶段规范化并进入配置指纹；同一列表同时约束提示、写权限、审批和磁盘验收。首期列表内每项均为必需，不支持 CLI 覆盖。 |
| D-04 | 文档引用校验强度 | 使用“事实”“推断”“代码依据”三个结构化区。每个事实列表项必须引用已定义证据编号；证据使用 `E<n>: 相对路径:起始行[-结束行]`，逐条校验路径、行号、读取审计和文件内容版本。推断与事实分区，不要求推断提供代码证据；更严格的自然语言语义审计不纳入 P0。 |
| D-05 | 新增测试目录策略 | 对齐参考项目：平台不隐式开放测试目录，任务通过 `allow_write` 显式打开受控目录，例如 `tests/**` 禁写、`tests/generated/**` 放行。命中例外目录的文件允许创建或覆盖；目录外既有测试仍只读。Completion 仍校验基线测试节点、跳过标记、退出码和 diff 范围。 |
| D-06 | 隐藏验收是否纳入 P0 | 不纳入 P0。Completion 只执行公开验收命令、交付物检查和程序化 diff/测试证据检查，不增加隐藏命令、私有测试夹具或额外解析器。未来如扩展，隐藏内容只能由程序执行，不得进入模型上下文或可读审计摘要。 |
| D-07 | 真实模型是否纳入 P0 | 不纳入 P0。Harness 保留 `smolagents.Model` 注入缝，但只交付和验收 Fake Model；不实现真实 Provider 工厂、密钥配置或网络 smoke test。真实模型端到端验证属于 P1，不得成为自动化验收前置条件。 |
| D-08 | 恢复上下文如何重建 | 使用 StateStore 中的结构化状态生成有界、确定性的 continuation prompt，并创建新的 Agent。上下文包含任务、生效配置、计划、步骤摘要、有效证据、待执行调用、审批和预算；不序列化旧 memory、不保存模型隐藏推理、不回放完整 Audit，文件证据在版本变化后失效。 |
| D-09 | 预算停止的最终状态 | 预算耗尽固定使用 `RunStatus=STOPPED`、`StopReason=BUDGET_EXHAUSTED`、`acceptance_status=NOT_EVALUATED`。CLI 返回非成功状态，不继续模型、工具或最终验收；部分产物只作为诊断证据保留。 |
| D-10 | Python 运行时边界 | 支持 Python `>=3.10,<3.14`；开发、依赖安装和验收使用仓库现有 `.venv`（Python 3.13），Ruff 以 `py310` 目标检查最低版本语法。当前环境不单独声明 Python 3.10 运行时验证。 |
| D-11 | ID 与时间格式 | 生成 ID 使用固定类型前缀加 32 位小写十六进制 UUIDv4；`run_id` 与 `trace_id` 必须不同，`trace_id` 使用 32 位小写十六进制。时间统一为 UTC、`Z` 后缀并固定保留微秒。 |
| D-12 | 配置路径与指纹 | `workspace.root` 和 `state_dir` 支持配置文件目录相对路径或绝对路径，先解析为规范绝对路径；交付物和路径 pattern 只接受工作区相对 POSIX 路径。指纹包含全部有效配置字段和规范化绝对路径；集合语义字段排序，交付物与命令顺序保留。 |
| D-13 | 测试修复配置深度 | WP-00 冻结测试命令 argv、基线要求和路径/预算/审批字段；测试报告格式及节点证据由 WP-09 冻结，不在 WP-00 提前实现。 |
| D-14 | 公共契约扩展与命名 | 公共模型字段只能通过 schema 版本升级或兼容评审扩展，枚举只允许追加并补契约测试；保留 `CompletionCheck`、`RunStatus` 等冻结名称。 |
| D-15 | Python 依赖清单组织 | `pyproject.toml` 是运行时依赖和开发依赖的唯一来源；不再维护 `requirements.txt`。开发、CI 和验收统一执行 `python -m pip install -e '.[dev]'`，普通安装使用 `python -m pip install .`。 |
| D-16 | State 数据库隔离与配置契约 | `state_dir` 必填且必须位于规范化 `workspace_root` 外；配置加载和 WP-01 打开数据库时分别校验。配置 `version` 保持 `1`，作为 P0 发布前的兼容修订同步提升 WP-00 规格和计划版本。若状态目录仍位于同一 `git_root`，WP-03 只排除该精确目录，不使用宽松前缀。 |
| D-17 | 预算增量幂等账本 | 使用 `budget_ledger`，操作键由 `run_id`、`source_kind`、`source_id` 和 `category` 确定性生成；category 为 `STEPS`、`TOOL_CALLS`、`TOKEN_COST`、`WALL_TIME`。同一 key 和相同增量返回原结果，载荷变化返回冲突。Run usage 是物化总量，必须与 ledger 汇总一致。 |
| D-18 | 当前计划建模 | 使用独立 `RunPlan`、`PlanItem` 和 `run_plans` 表。首次设置前允许 `null`，设置后非空；ordinal 从 0 连续递增；最多 50 项、单项 1,000 字符、整计划 32 KiB。相同内容重复提交为幂等 no-op；PlanItem 不保存隐藏推理或 Step 执行状态。 |
| D-19 | Run 租约与心跳 | Run 创建和租约获取原子完成。默认 TTL 30 秒、允许 10 至 300 秒；Harness 以 `min(10, TTL/3)` 秒独立心跳，WP-01 不启动隐藏线程。连续两次续租失败进入危险状态并请求取消 handler；普通续租不写业务审计，获取、接管和释放写审计。token fencing、revision 和 `STARTED` 记录共同防止旧进程提交或新进程重放副作用。 |
| D-20 | 状态变更强制审计 | StateStore 公共业务写操作自动追加固定领域审计事件，禁止状态-only 成功；审计失败则整个事务回滚。`status` 必填，`code` 仅在拒绝、失败、超时和未知结果时必填；`actor` 来自可信 UnitOfWork 上下文，模型不能覆盖。 |
| D-21 | 状态文件权限 | POSIX 要求状态目录、数据库、WAL/SHM 由当前有效用户拥有，文件类型安全；新建目录使用 `0700`，数据库及 WAL/SHM 使用 `0600`。已有路径权限不安全或无法收紧时失败关闭，不提供生产旁路。Windows ACL 不作为本包强隔离能力宣称。 |
| D-22 | Evidence 单一生产者 | `Evidence.tool_call_id` 是唯一生产该证据的 ToolCall，可为 `null` 表示 Completion-only 证据；`ToolInvocation.evidence_ids` 是从 Evidence 表派生的只读投影。删除 `tool_call_evidence` junction，`transition_invocation` 不再接收 evidence_ids。Completion 可以引用 Evidence，但不改变其生产者。 |
| D-23 | Approval v2 与审批配置 | `Approval` 升级为 `schema_version=2`，增加 `tool_call_id`、`task_id`、`binding_digest`、`claimed_at` 和 `revision`。任务 `ApprovalConfig` 增加 `approver_roles` 与 `ttl_seconds`；有必审工具时角色列表不得为空，TTL 默认 900 秒、允许 1 至 86400 秒，全部进入配置指纹。 |
| D-24 | 审批身份、自批与 Channel 信任 | 审批 actor 是本地可信但未认证身份；必须提供与 `allowed_approver_roles` 相交的角色，并禁止 `requested_by == decided_by`。`requested_by` 来自 Gate 的可信 Agent 上下文，不采用模型参数。`ApprovalChannel` 只能提交或返回未授权决定，不能直接写审批状态；P0 只提供本地 SQLite 语义和 disabled/fake 通道。 |
| D-25 | 审批与 Run 的原子边界 | Gate 在同一 UnitOfWork 中创建审批、关联 ToolInvocation、将 Run 转为 `WAITING_APPROVAL` 并写审计。claim 成功时同一 UnitOfWork 将 Run 恢复为 `RUNNING`、提交审批 claim 和预执行意图；随后单独持久化 Invocation `STARTED`，再调用 handler。`execution_intent_id` 固定使用 `tool_call_id`。 |
| D-26 | 审批失败与结果终态 | `PENDING/APPROVED` 到期转 `EXPIRED`，`CLAIMED` 不按审批 TTL 自动过期。人工拒绝或审批过期必须与 Run `STOPPED`、新 `StopReason=APPROVAL_REJECTED/APPROVAL_EXPIRED`、`NOT_EVALUATED` 及审计原子提交。确定 `SUCCEEDED/FAILED/TIMED_OUT` 消费审批；`UNKNOWN_RESULT` 保持 `CLAIMED` 且不可重放。绑定变化使旧审批 `INVALIDATED`，Run `BLOCKED`，人工处理完成后启动新 Run。 |
| D-27 | 审批幂等与恢复 | `(run_id, tool_call_id)` 最多一张审批；同绑定且 PENDING/APPROVED 的重复创建返回原单，绑定不同返回冲突，CLAIMED/终态重放拒绝。claim 后、STARTED 前崩溃时，新进程在重新取得租约并全量复核后通过显式 `resume_claim` 继续原 execution intent，并追加恢复审计。 |
| D-28 | 审批参数绑定摘要 | Gate 对脱敏前的规范化参数计算 domain-separated SHA-256，工具名和参数进入 `canonical_args_digest`，完整上下文进入 `binding_digest`；审批表只保存摘要与脱敏摘要，不保存未脱敏参数。低熵秘密摘要的离线猜测风险作为 P0 限制记录。 |
| D-29 | WP-03 威胁模型 | 目标仓库和测试代码按“可信但可能出错”处理。WP-03 不承诺容器、虚拟机或 OS 级文件系统/网络隔离，只承诺进程内路径与命令治理、固定 `cwd`、最小环境、POSIX 进程组终止和 Git-visible 外围变化检测；可访问工作区外文件或网络的风险必须在规格、验收报告和运维文档中明确声明。 |
| D-30 | Sandbox 安全文件 I/O | 文件最终 I/O 必须经过 `Sandbox.read_file` 和 `Sandbox.write_file_atomic`，handler 不得自行用未受控路径读写目标仓库。整文件读取和写入上限均为 10 MiB；读取超限拒绝而不是静默截断。覆盖写入支持预期内容摘要，创建写入默认要求目标不存在；父目录创建只能由可信工具规格开启且每一级都重新经过写策略。原子写使用同目录临时文件、`fsync` 和替换，保留既有文件权限，新文件使用受控 umask 结果。 |
| D-31 | Sandbox 平台支持矩阵 | P0 的进程执行只支持能够可靠创建并终止独立 POSIX 进程组的 Linux 和 macOS。Windows 或其他不满足该条件的平台在 Sandbox 构造时以 `SANDBOX_UNSUPPORTED_PLATFORM` 失败关闭，不降级为仅终止父进程。路径和 Git 能力若可安全实现，可单独保留，但不得让平台误以为 S4 已受支持。 |
| D-32 | 模型命令输入 | 模型只能向受控工具提交可信 `command_id`，不得提交 argv、可执行文件路径、shell 片段、环境变量、`cwd`、超时、输出上限或重试策略。可执行文件、完整 argv 和限制全部来自代码固定 allowlist 或经过严格校验的任务配置；未来若需要测试节点选择，必须以受控参数槽单独评审，不改变本决策。 |
| D-33 | 命令输出与截断语义 | 子进程 stdout 和 stderr 分别以 1,048,576 字节为上限，保留头部和尾部并插入稳定的截断标记。截断不篡改进程真实退出码，但依赖完整输出的测试报告或其他证据无效，Completion 不得基于截断报告判定通过。输出在进入模型前还必须再次经过 WP-05/WP-06 的字符上限和脱敏。 |
| D-34 | 子进程最小环境 | 子进程环境从空白映射构造，只注入受控 `PATH`、`LANG=C.UTF-8`、`LC_ALL=C.UTF-8`、`TZ=UTC`、隔离的 `HOME`/`TMPDIR`、`PYTHONDONTWRITEBYTECODE=1`、`PYTHONNOUSERSITE=1`、`PYTHONHASHSEED=0` 以及 Git 非交互/禁用系统与全局配置变量。P0 不允许任务配置追加 env，也不从父进程继承代理、凭据、Python、pytest 或云配置变量。 |
| D-35 | 外围 Git 保护开关 | `workspace.protect_other_git_changes=true` 时采集并强制比较 `git_root` 内、`workspace_root` 外的 Git-visible 脏路径、内容摘要和仓库身份。为 `false` 时仍记录 Git 身份、HEAD 和 index，但明确跳过外围脏路径采集与比较，`WorkspaceIdentity.external_dirty_baseline_fingerprint` 使用“未监控”域分隔哨兵；Sandbox 不得把关闭状态报告成受保护或无变化。 |
| D-36 | Git 基线边界状态 | 支持无 HEAD 的空仓库，使用稳定 `UNBORN` 哨兵而不伪造 commit。`require_clean_workspace=false` 时记录工作区初始 entries 的完整路径、状态、类型和内容摘要，后续任何偏离该基线的情况均视为变化。symlink 只摘要目标字符串且不跟随到工作区外；submodule/gitlink 使用 Git 对象 ID；FIFO、设备、socket 等特殊文件不支持并在基线阶段失败关闭。 |
| D-37 | WP-04 revision 与租约读取 | WP-01 追加只读公共接口 `get_run_revision`、`get_invocation_revision` 和 `verify_lease`。Gate 只能通过这些接口组合条件更新，不使用私有 helper 或直接 SQL。 |
| D-38 | ToolObservation v2 | `ToolObservation` 升级为 schema v2，增加 `approval_id`、`evidence_ids` 和 `truncated`；保留 `evidence_id` 作为由首条证据派生的兼容字段。 |
| D-39 | 工具参数 Schema | 运行时依赖增加 `jsonschema>=4.23,<5`；Registry 和 Gate 统一使用 JSON Schema draft 2020-12，工具根 schema 必须为 object 且显式设置 `additionalProperties=false`。 |
| D-40 | 重复与 replay 上限 | 只读工具默认最多进入 handler 2 次，写/执行工具默认最多 1 次；可信 PolicyConfig 可以覆盖。已完成写操作即使换新 `tool_call_id` 也先做 replay suppression。 |
| D-41 | handler 超时边界 | WP-04 使用同步协作 deadline 和事后 elapsed 判定；真实阻塞操作由 WP-05/WP-06 交给 Sandbox 硬终止。超时或迟到结果进入 `UNKNOWN_RESULT`，不得用后台线程伪造强制停止。 |
| D-42 | Gate 自动重试 | WP-04 不自动重试 handler；仅校验 retry metadata 并返回 `retryable`。安全重试必须由 WP-06 在真实进程结果和幂等证据下重新经过 Gate。 |
| D-43 | 角色与权限装配 | WP-04 通过可信代码构造的 `PolicyEngine` 接收任务工具白名单、角色权限和审批角色；本包不新增 YAML 字段，Harness 配置装配由 WP-10 负责。 |
| D-44 | 前置拒绝持久化 | 前置拒绝不创建 ToolInvocation，只写带 `run_id`、`tool_call_id`、actor、phase、rule 和 decision 的 Gate 审计事实；Invocation 仅表示已进入执行意图的候选。 |
| D-45 | 只读搜索语义 | P0 `search_code` 只接受字面量模式和可选大小写折叠，不把模型输入交给正则引擎。未来开放正则必须单独评审，并使用有硬超时或线性匹配保证的实现。 |
| D-46 | Sandbox 安全目录枚举 | Sandbox 新增 `list_directory` 与 `walk_readable_files` 公共兼容接口。目录枚举、条目类型检查和递归遍历都在 Sandbox 内完成，工具 handler 不得直接调用 `os.scandir`、`os.walk` 或 `Path.rglob`。 |
| D-47 | 受限 Git pathspec | 可信命令规格可显式开启 pathspec 参数槽；调用只能提交当前 Sandbox 生成并复核的 `SandboxPath`，这些路径只作为 `--` 后的独立 argv 元素追加。Git 只读查询固定启用 `--no-optional-locks` 与 `--literal-pathspecs`，拒绝 revision、shell 和 pathspec magic 注入。 |
| D-48 | WP-06 单文件 patch 语义 | `apply_patch` 第一版只修改一个已有普通 UTF-8 文件，输入为 `{path, edits:[{oldText,newText}]}`。每段 `oldText` 必须在前序编辑后的文本中恰好出现一次，编辑按数组顺序应用，全部计算成功后通过一次 Sandbox 原子替换提交；空、缺失、歧义、无变化或超限均整批拒绝。不支持统一 diff、多文件、文件创建/删除/重命名或二进制 patch。 |
| D-49 | WP-06 测试命令模型输入 | `run_test` 的严格 schema 只接受 `{"command_id":"acceptance_test"}`。完整 argv、可执行文件、cwd、env、shell、超时、输出限制和重试策略全部来自可信任务配置或固定策略；第一版不接受测试文件子集参数。 |
| D-50 | WP-06 写入 no-op 语义 | `apply_patch` 拒绝所有 no-op，包括每段 `oldText == newText` 及按顺序应用后内容不变。`write_file` 在目标内容与提交内容完全相同时返回成功且 `changed=false`，不进行第二次物理替换，不改变文件内容或 mtime。 |
| D-51 | WP-06 副作用工具审批策略 | `write_file` 与 `apply_patch` 的 `ToolSpec.approval_requirement` 固定为 `REQUIRED`。`run_test` 默认不固定必审，但任务 `approval.required_tools` 命中时必须提升为必审；审批仍绑定规范化参数、工作区、Run、任务和配置指纹。 |
| D-52 | WP-06 测试命令效果分类 | `run_test` 固定使用 `CommandEffectClass.NON_IDEMPOTENT`，不假设测试命令不会写缓存、报告或其他文件。超时、取消、进程组终止失败或采集失败均产生 `UNKNOWN_RESULT`，不得自动重试、消费为成功或生成完整命令 Evidence。 |
| D-53 | WP-07 PREPARED 恢复入口 | Gate 新增可信公共入口 `reenter_prepared`。恢复只使用持久化 Invocation 的工具名、规范化参数和调用 ID，重新执行现有可见性、schema、语义、权限、风险、审批、重复与预算检查；`CLAIMED + PREPARED` 先 `resume_claim`，随后写 STARTED 并最多执行 handler 一次。不得直接执行 handler、复制弱化门禁或 monkey patch。 |
| D-54 | WP-07 未知/阻塞恢复边界 | P0 不允许同一 Run 从 `UNKNOWN_RESULT` 或 `BLOCKED` 自动恢复，也不提供把 UNKNOWN 改写为成功、释放 CLAIMED 审批或自动重放原执行的 API。人工入口只查看/核对事实；需要继续工作时启动新 Run。 |
| D-55 | WP-07 W6 工作区差异 | “结果已提交但 Checkpoint 未提交”的恢复先验证配置、Git/工作区身份、HEAD/index、外围保护基线和证据版本。W6 只有不存在 STARTED/UNKNOWN/冲突执行，且工作区差异能归因于自上一 Checkpoint 以来已提交确定结果或其 Evidence 时，才允许补建 Checkpoint；无法归因或外围变化时 `BLOCKED/WORKSPACE_CHANGED`。 |
| D-56 | WP-07 Evidence 失效 | 复用 `Evidence.is_valid`，新增受租约保护的单向 `true -> false` 状态操作并追加 `EVIDENCE_INVALIDATED` 审计。恢复上下文和后续 Completion 只能消费仍有效的 Evidence；不得把失效证据恢复为有效或删除原事实。 |
| D-57 | WP-07 Checkpoint 上限与保留 | Checkpoint canonical snapshot 上限 1 MiB，continuation context 与渲染文本上限均为 64 KiB。P0 保留全部 Checkpoint 和 recovery record，不自动清理；超限失败关闭，不静默截断关键安全事实。 |
| D-58 | WP-07 待审批 Checkpoint 边界 | 追加 `CheckpointBoundaryKind.APPROVAL_REQUESTED`，用于审批已创建且处于 PENDING、Run 已安全进入 `WAITING_APPROVAL` 的 Checkpoint；`APPROVAL_SETTLED` 继续表示批准、拒绝、过期、撤销、claim 恢复或消费等已落定状态。两者都必须在快照中保存完整审批与待执行调用。 |
| D-59 | WP-08 Completion 内部控制面与预算 | Gate 新增 `invoke_internal` 可信入口，只接受独立内部 Registry 中固定的只读 Completion 工具定义；仅允许 `VERIFYING` Run，actor/role 固定为系统 Completion 身份，不由调用方或模型覆盖。内部工具不进入模型快照，经同一 Sandbox、Invocation、Evidence 和 Audit 边界执行；使用专门 Completion step/调用记录，并采用独立固定次数、超时和输出预算，不消耗 Agent 的 step 或 tool-call 预算。 |
| D-60 | WP-08 分页读取证据覆盖 | 同一 Run、同一路径、同一完整文件摘要且由同一 Agent producer 产生的多个连续 `READ` Evidence 可以取行区间并集，完整覆盖一个文档引用范围；不同内容摘要、跨 Run、非 Agent 生产者或存在未读空洞时不得视为完整读取。 |
| D-61 | WP-08 启动工作区基线 | 可信 Harness 在启动时保留完整的 `WorkspaceBaseline` 并显式注入 `CompletionChecker`。Completion 必须先校验其身份和 fingerprint 与 Run 一致，再比较当前工作区；不得从当前工作区反推启动基线。跨进程或 CLI 验收需要时再单独评审基线持久化。 |
| D-62 | WP-08 交付物变更要求 | 代码理解任务的每个配置交付物必须在本次 Run 中新增或内容发生变化；交付物预先存在但未修改时不得 `ACCEPTED`。存在性、非空、路径和引用检查仍需同时满足。 |
| D-63 | WP-08 陈旧 Evidence 失效 | Completion 发现引用源码读取后变化时，在持有有效租约的前提下调用 WP-07 的 Evidence 单向失效操作并追加 Audit，然后将对应检查判为 `REJECTED`。失效操作或审计失败时整体验收失败关闭，不得返回 `ACCEPTED`。 |
| D-64 | WP-08 完成声明识别 | 对 Run 的 `candidate_final_answer` 使用有界、保守的中英文完成声明标记，否定表达不计为完成声明。检测只用于 `claim_without_evidence` 检查项和 Audit 诊断；最终验收仍完全由文件、引用、工作区 diff、Evidence 和 Audit 决定。 |
| D-65 | WP-09 测试报告与命令适配 | P0 只支持 `pytest_junit_xml`。任务配置新增必填 `report_format` 并纳入配置指纹；固定适配器只向可信基础命令追加 `-o junit_family=xunit2` 和私有临时报告路径对应的 `--junitxml` 参数。未知格式、已有报告覆盖项或任意 argv/cwd/env 注入全部拒绝。 |
| D-66 | WP-09 内部测试执行控制面 | 新增可信 `Gate.invoke_test_verification`。`BASELINE` 只允许 `RUNNING` 且 Agent 尚无副作用，`FINAL` 只允许 `VERIFYING`；只接受独立内部 Registry 的固定测试定义和相位，不进入模型快照，不占用 Agent step/tool-call 预算。D-59 的只读约束继续只约束 WP-08 代码理解读取。 |
| D-67 | WP-09 内部测试审批 | 内部基线/最终测试只执行任务配置声明的唯一基础命令，由可信 Harness 启动，不额外要求模型审批；仍必须经过 Gate、Sandbox、Invocation、Evidence、Audit、租约和状态检查。模型可见 `run_test` 继续按任务 `required_tools` 审批策略执行。 |
| D-68 | WP-09 跨进程基线持久化 | 追加 schema migration v6，持久化测试修复启动 WorkspaceBaseline、测试执行和测试节点。基线、执行和节点事实 append-only；审批后新进程恢复只复用已持久化有效基线，不重跑基线。迁移不得修改 v1-v5。 |
| D-69 | WP-09 基线失败要求 | 测试修复基线必须至少包含一个 `FAILED` 或 `ERROR` 节点。基线全部通过时任务不是有效测试修复，返回 `COMPLETION_TEST_BASELINE_NOT_FAILING`；回归加固需要未来显式新增任务模式。 |
| D-70 | WP-09 skip/xfail 语义 | 基线已存在且最终保持的 `SKIPPED/XFAIL` 允许作为历史测试债务保留并显式审计；原本执行节点变为 skip/xfail，或新增节点为 skip/xfail，均拒绝。所有实际执行节点最终必须通过。 |
| D-71 | WP-09 报告原文保留 | JUnit 报告只在独立私有临时目录中读取，完成解析后删除。State/Audit 只保留报告 SHA-256、字节数、parser 版本、计数、节点键/脱敏标签和脱敏有界失败摘要，不保存完整报告或断言正文。 |
| D-72 | WP-09 资源上限 | 单份报告最大 8 MiB、最多 100,000 个测试节点、单字段原始长度 4,096 字符、持久化节点标签 512 字符、失败摘要 8 KiB。超过任一上限或 XML 含 DOCTYPE/ENTITY 时失败关闭。 |
| D-73 | WP-10 token/cost 计量 | 使用可信注入的 `TokenCostPolicy` 将公开 `TokenUsage.total_tokens` 转为 Decimal token-cost；P0 默认费率为每 token `0.000001` 资源单位，费率版本随策略记录并进入审计。配置继续使用 `max_token_cost`，不从模型响应或环境读取价格。 |
| D-74 | WP-10 启动工作区基线持久化 | 追加 migration v7，按 Run 通用保存一次完整 `WorkspaceBaseline` JSON、摘要、配置指纹和创建时间；两类任务都从该事实恢复启动基线。追加 `RUN_STARTED` Checkpoint boundary，在 Agent 首次调用模型前创建初始安全点。 |
| D-75 | WP-10 最终回答上限 | `final_answer` 必须是可接受的有界文本，原始值超过 16,000 字符时以 `HARNESS_FINAL_ANSWER_INVALID` 失败关闭，不静默截断；未超限值先脱敏后以原子 State/Audit 写入，失败时不得进入 `VERIFYING`。 |
| D-76 | WP-10 多工具批次终止 | P0 的同一模型响应内工具调用按声明顺序串行执行；遇到 `WAITING_APPROVAL` 或 `UNKNOWN_RESULT` 后不再执行同批后续调用，也不为其预先写 `STARTED`。此前已完成的确定调用事实和 Checkpoint 保留，resume 从持久化安全边界继续。 |
| D-77 | WP-10 Agent 身份与权限装配 | Agent actor 固定为 `agent`、role 固定为 `developer`；该 role 的权限集合只取当前任务允许工具声明权限的并集。`RunRequest` 和模型不能覆盖 actor、role、权限、工作区、预算或审批角色。 |
| D-78 | WP-11 P0 模型来源 | CLI 只接受可信注入的 `model_factory`。标准 console/module 入口在没有 P1 Provider 或受控脚本模型时以 `CLI_MODEL_UNAVAILABLE` 失败关闭；自动化验收通过注入 Fake Model 的同一 `main` 完成，不从 argv 或环境加载任意 Python 实现。 |
| D-79 | WP-11 CLI 入口与配置边界 | canonical console script 为 `guarded-harness`，`python -m guarded_harness` 必须调用同一 `main`；`--config PATH` 为全局参数。命令固定为 `run`、`resume`、`approvals list/approve/reject` 和 `inspect`，CLI 不得覆盖 workspace、task、工具、预算、测试命令或审批配置。 |
| D-80 | WP-11 审批身份输入 | `approvals approve/reject` 必须显式提供 `--actor` 和至少一个可重复的 `--role`；不使用环境变量、操作系统用户或配置中的唯一角色作为隐式身份。Approver 仍须通过任务角色交集、自批和一次性审批检查。 |
| D-81 | WP-11 审批列表语义 | `approvals list` 默认跨 Run、按 `requested_at, approval_id` 稳定排序并限制最多 1000 条；`--run-id`、`--status` 和 `--limit` 仅作窄过滤。列表只返回 persisted/effective status，不物化过期；wait、approve、reject 和 resume 才通过 ApprovalStore 受控物化到期并收口 Run。 |
| D-82 | WP-11 退出码与 JSON | `run/resume` 仅 `ACCEPTED` 返回 0；2 为 usage/config，3 为 WAITING_APPROVAL，4 为 STOPPED，5 为 BLOCKED，6 为 FAILED，7 为 COMPLETED/REJECTED，8 为 UNVERIFIABLE，9 为内部查询失败。所有命令支持单一 JSON envelope；审批和只读控制命令成功完成返回 0。 |
| D-83 | WP-11 等待审批超时 | `--wait-for-approval SECONDS` 必须显式给出 `1..86400` 的整数秒；本地超时保持 Run `WAITING_APPROVAL` 并返回退出码 3，不自动 approve、reject 或把等待超时写成审批过期。 |
| D-84 | WP-12 提前施工授权 | 需求方明确允许在 WP-08 至 WP-11 尚未全部 accepted 时提前开展 WP-12 施工。可记录实现和测试证据，但不得把依赖包或 WP-12 标记为 `DONE`，也不得据此声明项目 P0 完成；所有上游验收关闭后必须重新执行最终门禁。2026-11-08 上游已全部 accepted，人工验收和最终门禁已闭环。 |
| D-85 | WP-12 正式样例形态 | 提供两个最小、自包含的样例工作区和 `examples/config/` 下的两份 YAML，使正式配置可由现有 `load_config` 从仓库相对路径加载，并在复制到临时 Git 工作区后可离线执行；不依赖开发者绝对路径。 |
| D-86 | WP-12 飞书 P1 处理 | S2、S4、A2、T2 不作为 P0 阻断。S2/S4/A2 汇总已有确定性证据，T2 因 D-07 标为 `NOT_RUN/P1`；最终报告必须明确优先级、证据和未执行范围，不得把 Fake Model 结果冒充真实模型结果。 |
| D-87 | WP-13 配置与真实 Provider 边界 | 真实模型只允许在任务配置 schema v2 的严格 `model` 块中声明，首版不引入独立 model profile；v1 保持 P0 兼容并只能使用可信注入的 Fake Model。v2 `ModelConfig` 使用 `extra="forbid"`、固定 Provider 枚举和窄范围字段，Provider/模型/端点/价格/上限进入非秘密配置指纹。 |
| D-88 | WP-13 首个 Provider 与依赖 | P1 首版只实现 `openai_chat_completions_v1`，使用公开 `smolagents.OpenAIServerModel`；不引入 LiteLLM、Responses API 或任意动态 Provider。受约束的 OpenAI SDK 是默认运行时依赖，不以 optional extra 提供；安装缺包或版本不兼容时返回 `MODEL_PROVIDER_UNAVAILABLE`，不得回退 Fake Model。 |
| D-89 | WP-13 凭据与端点 | `credential_ref` 只保存非秘密引用，由固定 allowlist 的 `SecretResolver` 解析；不读取 `.env`，不把密钥写入 fingerprint、State、Audit、日志、异常、CLI 或子进程环境。endpoint 使用精确 origin/path allowlist，默认 HTTPS，拒绝 userinfo、URL 凭据、非 HTTP(S) scheme、cross-host redirect 和代理环境；localhost/私网 HTTP 需显式窄开关。HTTP client 固定 `trust_env=False`。 |
| D-90 | WP-13 模型调用包装边界 | Harness 为每个 Run 将 Raw Model 包装为 GovernedModel；所有公开 `Model.generate` 必须经过 ModelCallGateway。Provider 只负责传输，`step_callbacks`、SDK 日志和 smolagents 内部日志不能充当门禁；不修改 ToolCallingAgent 决策循环或私有字段。 |
| D-91 | WP-13 成本预留与预算 | 调用前按可信 `max_input_tokens`、`max_output_tokens` 和版本化 input/output 价格计算 Decimal reservation，并从可用预算中扣除；成功按实际 usage 结算，明确未生成内容的失败可释放，超时、5xx、连接中断或结果未落盘保留 reservation 并标记 unknown。预算不足时网络调用为 0。 |
| D-92 | WP-13 ModelCall 持久化与响应上限 | 下一次单调 migration 增加 `model_calls`、`model_cost_reservations`、Audit 可空 `model_call_id`/`audit_schema`；旧事件保持 schema 1 哈希语义，新 `MODEL_*` 事件使用 schema 2。每次 attempt 使用独立 `model_calls` 行，`logical_model_call_id` 将重试归组；P1 STARTED 的 `step_id` 必须非空。调用 STARTED、reservation 和 Audit 同事务提交，结果与结算在下一事务提交。响应必须是 `ChatMessage`，usage 非空且非负，内容/tool call 数量/ID/大小受限；不保存完整 prompt、raw payload 或隐藏推理。 |
| D-93 | WP-13 超时、限流与 usage | SDK `max_retries=0` 且 smolagents `retry=False`，所有显式 attempt 记录和审计。只对明确未生成内容的 429 或连接建立失败进行有限重试；read timeout、5xx、响应截断、连接后中断和解析失败不自动重发。真实 Provider 缺失或非法 usage 使用 `MODEL_USAGE_INVALID`，不得按 0 计费。 |
| D-94 | WP-13 模型调用恢复 | resume 时重新校验 v2 模型身份、Provider、端点、价格和凭据引用；配置变化继续失败关闭。ModelCall `STARTED` 后崩溃且结果未提交时物化为 `MODEL_CALL_UNKNOWN`，Run 进入 `BLOCKED`、`StopReason=UNKNOWN_RESULT`，第一版不自动重发或强制 reconcile；需要继续时启动新 Run。 |
| D-95 | WP-13 CLI 与只读命令 | 标准 `run/resume` 可从 v2 配置构造 Provider；只有 `run/resume` 可以创建模型。`approvals` 和 `inspect` 不构造模型、不读取凭据、不访问网络。P0 退出码保持不变，具体失败由稳定 `MODEL_*` 错误码区分。 |
| D-96 | WP-13 离线测试与 live smoke | 默认 pytest 继续完全离线，使用 fake transport/client 覆盖配置、凭据、预算、响应、重试、审计和恢复。真实服务只通过显式 marker/环境变量 opt-in，运行低预算只读任务，不作为普通 CI 或 P0 验收前置。live smoke 使用发布时固定的 OpenAI Chat Completions 模型快照和低预算验收配置，拒绝标记为移动别名的模型。 |
| D-97 | WP-13 TokenCounter 与上下文超限 | `tiktoken` 作为默认运行时依赖，v2 模型配置必须指定固定 registry 中可验证的 tokenizer；调用前必须用该 tokenizer 证明输入不超过 `max_input_tokens`。无法计数、配置未知 tokenizer 或输入超限时在本地失败关闭。首版不执行上下文压缩、旧 observation 截断或隐式模型摘要；长任务应拆分任务或提高明确配置的上下文上限。 |
| D-98 | WP-13 结构化工具调用与调用 ID | 首版只接受 Chat Completions 返回的结构化 tool calls；不启用文本内容中的 tool-call fallback 解析。tool-call ID 必须非空且同一响应唯一；Provider 不返回 ID 时 `MODEL_TOOL_CALLS_INVALID`，不自动合成 ID。`final_answer` 不得与其它工具混用。 |
| D-99 | WP-13 Step 与 attempt 身份 | Harness 必须在首次真实模型调用前创建并持久化 Agent Step；所有 STARTED ModelCall 必须绑定非空 `step_id`。每次重试是独立 attempt 和独立计费行，并通过 `logical_model_call_id` 关联同一逻辑调用；不增加第二套可漂移的 attempt 表。 |
| D-100 | WP-13 网络与 live smoke 约束 | OpenAI/httpx 客户端不继承 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 等环境代理，不允许跨主机 redirect；如未来需要代理，必须作为独立受控配置评审。live smoke 的配置、模型快照、端点和单次预算由发布验收清单固定，自动化不得选择移动别名。 |
| D-101 | WP-14 START Run 指令覆盖 | CLI `run --instruction TEXT` 可为本 Run 提供一次性任务指令。提供时，该文本替换 `task.description` 作为初始 Agent 任务并持久化为 `Run.task_description`；未提供时继续使用配置描述。YAML 仍决定任务 id/kind、工具、路径、预算、审批、验收和配置指纹。`RESUME` 禁止重新提供指令，恢复必须使用 Run 中持久化的有效指令；指令不扩大任何授权。 |
