# Guarded Coding Harness 设计决策

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档状态 | 已确认决策记录 |
| 对应需求 | [requirements.md](requirements.md) |
| 对应设计 | [design.md](design.md) |
| 决策范围 | 已确认的 D-01 至 D-22 |

本文记录已与需求方确认的设计决策。实现和后续变更必须以这些结论为基线；如需调整，应追加新的
决策记录并同步更新需求、设计和相关测试文档。

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
