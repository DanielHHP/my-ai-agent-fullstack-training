# WP-08 验收引擎与代码理解工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-08 验收引擎与代码理解 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 冻结日期 | 2026-11-07 |
| 依赖 | WP-04 v1.0.0 accepted；WP-05 v1.0.0 accepted；使用 WP-01/WP-03 的既有公共接口 |
| 下游工作包 | WP-09、WP-10、WP-12 |
| 对应需求 | `REQ-HARNESS-003`、`REQ-HARNESS-004`、`REQ-GATE-002`、`REQ-STATE-001`、`REQ-AUDIT-001` 至 `REQ-AUDIT-004`、`REQ-TASK-001`、`REQ-TASK-002`、`REQ-TASK-004`、`REQ-COMPLETE-001` 至 `REQ-COMPLETE-005` |
| 对应设计 | `design.md` 第 5、6.1、6.5、9、10、11.1、11.2、13 节 |
| 已确认决策 | D-02 至 D-04、D-14、D-20、D-22、D-33、D-56、D-59 至 D-72 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |
| 原文件任务 | P4-01、P4-02 的代码理解部分 |

本文是 WP-08 已冻结的行为与验收规格，不表示 `completion.py`、代码理解验收或内部控制面已经
实现。
验收引擎只能证明“路径合法、文档结构可解析、引用位置被读取、读取版本仍为当前版本、工作区
变更未越界”，不能证明模型对代码语义的解释必然正确。

文档冲突时按 `requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。
WP08-Q01 至 WP08-Q06 已按 D-59 至 D-64 关闭，本文无阻塞性未决问题。

D-65 至 D-72 扩展同一 Gate 的 WP-09 测试验收用途，但不放宽本规格的只读代码理解内部控制面。

## 2. 目标

WP-08 必须提供一个独立于模型自述的代码理解验收引擎：

1. 在 Agent 停止后，由可信装配入口显式调用 `CompletionChecker`；
2. 只根据持久化 Run、任务配置、工作区事实、工具 Evidence 和 append-only Audit 判定；
3. 检查全部声明交付物存在、路径合法、内容非空，并且只在任务允许范围内改动；
4. 按固定、可测试的 Markdown 语法解析“事实”“推断”“代码依据”三个区；
5. 将文档中的本地证据编号解析到同一 Run 内有效、可追溯且仍对应当前文件版本的 `READ` Evidence；
6. 校验被引用路径、行范围和完整文件摘要，拒绝越界、未读、部分读取或读取后变化的源码；
7. 用 Git status/diff 和启动工作区基线证明没有源码、测试、配置或其他越界修改；
8. 检测模型“已完成”但没有交付物支撑的声明，并把它与真实产物分开表达；
9. 将逐项检查、最终结论、证据、时间和关联 ID 原子写入 StateStore 与 Audit；
10. 验收工具和验收能力不进入模型工具快照，模型不能调用、替换、跳过或覆盖最终结论。

## 3. 范围与非目标

### 3.1 范围内

- 新增 `src/guarded_harness/completion.py`；
- Completion 公共入口、代码理解策略、Markdown 解析和证据解析；
- `domain.py` 中 Completion 所需的新稳定错误码、审计事件类型或严格 DTO；
- `gate.py` 中受限、非模型可见的 Completion 内部控制面；
- `state_store.py`/`audit.py` 中为逐项验收事实和最终原子写入所需的窄范围扩展；
- `__init__.py` 的公共导出；
- 新增 `tests/test_completion.py`；
- 按需扩展 `tests/test_domain.py`、`tests/test_gate.py`、
  `tests/test_state_checkpoint.py` 和 `tests/test_tools.py`；
- WP-08 文档、工作包索引和后续验收报告。

### 3.2 非目标

- 不实现测试修复验收、测试基线、测试节点提取或最终测试报告解析，这些属于 WP-09；
- 不创建 `ToolCallingAgent`、Fake Model、Harness 主循环、CLI 或跨进程恢复；
- 不修改、复制或 monkey patch `smolagents`；
- 不自动批准、拒绝或重放工具调用；
- 不执行测试命令、隐藏测试或模型不可见的私有验收用例；
- 不用语言模型、启发式摘要或审计文本判断代码解释的语义正确性；
- 不检查或证明任务范围之外的架构合理性、性能、安全性结论；
- 不把文档存在、引用存在或样式正确等同于“解释一定正确”；
- 不在验收失败时修改交付物、替代模型继续写文档或自动修复引用；
- 不把 `CompletionCheck`、最终回答或本文当作实际实现证据。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。WP-08 直接负责代码理解
场景，并为后续测试修复验收和最终端到端验收提供通用 Completion 框架。

| 编号 | 优先级 | 飞书通过标准 | WP-08 责任 | 最终归属 |
| --- | --- | --- | --- | --- |
| H1 | P0 | 模型说完成但无交付物时，final answer 被拒绝，不能进入完成态 | 直接：交付物、文档结构和证据检查决定结论；模型自述只作为 `candidate_final_answer` 观测事实 | WP-08 / WP-10 / WP-12 |
| H2 | P0 | 测试通过后又修改源码，旧测试证据失效并必须重跑当前版本 | P0 代码理解部分直接：源码 READ Evidence 与当前文件摘要不一致时拒绝；测试报告失效由 WP-09 完成 | WP-08 / WP-09 / WP-12 |
| H3 | P0 | 达到最大轮数或预算时以明确 StopReason 停止，不伪装成功 | 直接：预算停止、非正常终态或不可验收状态不得产生 `ACCEPTED`；Harness 终态编排由 WP-10 完成 | WP-08 / WP-10 / WP-12 |
| A2 | P1 | 完整任务可关联 Gate、Sandbox、State、Approval 和执行结果 | 直接：Completion 项、Evidence、ToolInvocation、Checkpoint/审批关联通过 `run_id` 和稳定 ID 可重建 | WP-08 / WP-10 / WP-12 |
| T1 | P0 | 所有 P0 场景都有确定性测试且可重复通过 | 直接：WP08-AC 全部离线、确定、无关键 skip | WP-08 / WP-12 |

表中一条跨工作包的验收只有在最终归属工作包完成端到端证据后才可声明关闭。WP-08 不得把
代码理解验收通过表述为测试修复、真实模型或整个 Harness 已验收。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `completion.py` | 代码理解策略、Markdown/证据解析、工作区范围判定和 Completion 编排 | Agent 循环、写文档、批准、执行测试 |
| `gate.py` | 为 Completion 提供受租约、Run 状态和只读边界约束的内部控制面 | 解释 Markdown、决定验收业务规则 |
| `registry.py` | 保存可信工具定义；内部工具必须与模型可见快照隔离 | 不执行工具、不暴露内部 handler |
| `sandbox.py` | 实际路径、文件读取、Git 和工作区快照边界 | 判断文档结构或引用业务含义 |
| `state_store.py` | 持久化 Completion Evidence、逐项结果、最终结论和审计 | 决定某项是否通过 |
| `audit.py` | append-only 记录门禁、读取、证据失效和逐项验收事实 | 作为第二套业务状态 |
| `domain.py` | 严格错误码、枚举和公共 DTO | 访问文件或数据库 |

依赖方向：

```text
domain.py <──── completion.py ────> config.py
                         │
                         ├────────> gate.py（仅内部控制面）
                         ├────────> sandbox.py（纯比较/公共契约）
                         └────────> state_store.py + audit.py

gate.py ───> registry.py + policy.py + sandbox.py + state/audit

WP-10/WP-12 ───> CompletionChecker + Gate + State + Audit
```

禁止：

- `completion.py` 直接调用工具 handler、ToolDefinition handler 或路径读写 API；
- `completion.py` 直接对目标仓库执行 `open()`、`Path.read_text()`、`subprocess`、
  `os.scandir()`、`Path.rglob()`；
- 把 Completion 验收工具加入模型工具白名单、提示或 observation；
- 直接 SQL 修改 Run、Invocation、Evidence、Completion 或 Audit；
- 在验收失败后自动重写文档、删除文件、恢复源码或重新运行模型；
- 把任一模型最终回答文本当作交付物证据；
- 用“文件存在”替代引用、读取版本和 diff 校验。

## 6. 公共接口与数据契约

### 6.1 可信装配入口

候选公共入口：

```text
CompletionChecker(
    storage: SQLiteStorage,
    config: ResolvedHarnessConfig,
    gate: Gate,
    sandbox: Sandbox,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
)

evaluate_code_understanding(
    run_id: RunId,
    lease_token: str,
    *,
    startup_workspace: WorkspaceBaseline,
) -> CompletionCheck
```

前置条件：

- 只能由可信 Harness/测试装配代码构造和调用；
- `config` 与 `sandbox` 绑定同一规范工作区，且 `task.kind=code_understanding`；
- Run 存在、租约有效、配置指纹匹配，且已经由 Harness 进入 `VERIFYING`；
- `startup_workspace.identity.baseline_fingerprint` 与 Run 的
  `workspace.baseline_fingerprint` 一致；
- 模型最终回答只从 Run 的 `candidate_final_answer` 读取，不接受调用方额外传入“验收结论”；
- Completion 访问仓库时只使用本规格第 6.6 节定义的 Gate 内部控制面。

后置条件：

- 返回的 `CompletionCheck` 已经与对应 Completion Evidence、逐项审计和最终审计在同一事务提交；
- 任一步骤无法可靠执行或无法持久化时，不返回伪造的 `ACCEPTED`；
- Run 的最终 `COMPLETED`、`REJECTED`、`UNVERIFIABLE` 业务流转由 WP-10 Harness 编排；WP-08
  只产生程序化验收状态和事实。

Completion 访问仓库使用已冻结的 Gate 内部入口。候选签名为：

```text
Gate.invoke_internal(
    run_id: RunId,
    completion_step_id: StepId,
    tool_name: ToolName,
    args: dict[str, JsonValue],
    *,
    lease_token: str,
    expected_run_revision: int,
) -> ToolObservation
```

`tool_name` 只能解析到独立内部 Registry 中的固定只读工具定义；调用方不能注入 actor、role、
handler、审批决定或任意可执行路径。更细的实现私名可以在 `plan.md` 中调整，但不得放宽
第 6.6 节的安全语义。

### 6.2 Completion 结果

继续使用冻结的 `CompletionCheck`、`CompletionItem`、`AcceptanceStatus` 和 `EvidenceKind`，
不新造第二套验收结果类型。

每个检查项使用稳定 `item_id`：

| `item_id` | 通过条件 |
| --- | --- |
| `deliverable_present` | 配置中的每个交付物均存在，规范化路径精确匹配，类型为普通 UTF-8 文本文件 |
| `deliverable_nonempty` | 每个交付物内容非空且不超过 1 MiB |
| `deliverable_changed` | 每个交付物相对启动基线为新增或内容变化 |
| `document_structure` | 三个必需 H2 区各出现一次，结构可解析 |
| `facts_cited` | 每个“事实”列表项至少引用一个已定义证据编号 |
| `evidence_defined` | 所有引用编号均有唯一定义；“代码依据”无重复、歧义或失效格式 |
| `evidence_read` | 每条定义对应同一 Run 内、由 Agent 产出的有效 `READ` Evidence |
| `evidence_current` | 引用路径和行范围仍存在，Evidence 完整文件摘要等于当前文件摘要 |
| `workspace_scope` | 工作区变更只发生在声明的交付物，HEAD/index/身份和外围保护基线符合规则 |
| `audit_chain` | 本 Run 的 Audit 链可完整校验，Completion 所需事实可关联 |
| `claim_without_evidence` | 模型完成声明存在时，全部必需检查均有程序化证据；未声明完成时不单独拒绝 |

`ACCEPTED` 当且仅当所有必需检查项 `passed=true`。`REJECTED` 用于已确定不满足规格；
`UNVERIFIABLE` 用于证据被截断、无法解析或验收所需事实不可可靠取得，但无法认定确定违规。
`NOT_EVALUATED` 只能表示预算停止或其他不进入验收的终态，本入口不得把非验收状态包装成
`COMPLETED`。

通过项使用稳定代码 `OK`；失败项使用第 9 节的 `COMPLETION_*` 错误码。`CompletionItem`
只保存有界、脱敏的说明和真实 Evidence ID，不保存交付物正文或源码片段。

失败摘要按 `item_id` 稳定排序、去重、脱敏并限制在 `CompletionCheck` 既有长度内。

### 6.3 文档最小结构

每个交付物必须使用严格 UTF-8 文本，含 NUL 时按二进制拒绝。结构按 Markdown ATX 标题解析，
必需标题必须各出现一次：

```markdown
## 事实

- 登录请求由路由进入认证服务。[E1]
- 认证失败时返回统一错误。[E2]

## 推断

- 当前错误处理方式可能不适合直接用于生产环境。

## 代码依据

- E1: `src/routes/login.ts:12-28`
- E2: `src/services/auth-service.ts:35-47`
```

规则：

- “事实”区只接受非空 Markdown 列表项作为可验收事实；每个列表项必须引用一个或多个
  `[E<n>]`；
- “事实”区至少包含一条列表项，不允许用“无”绕过事实证据要求；
- “推断”区内容非空；没有推断时必须显式写“无”，推断不要求代码证据；
- “代码依据”区至少定义一条证据；没有证据时不能通过；
- 证据定义只接受列表项格式 `E<n>: \`workspace-relative-path:start_line[-end_line]\``；
- `<n>` 为不带前导零的正整数，证据编号在单个交付物内唯一；
- 行号从 1 开始、闭区间；省略结束行时表示单行，显式范围不得逆序；
- 路径必须是工作区相对 POSIX 路径，拒绝绝对路径、反斜杠、NUL、`..` 越界和符号链接逃逸；
- 未列入“事实”区的自然语言不产生可验收事实；“推断”或“代码依据”中的叙述不替代事实引用；
- 重复必需区、未知证据引用、未定义证据、同编号多定义、空定义和歧义格式均失败关闭；
- 解析上限建议为每个交付物 1 MiB、最多 2,000 个事实项和 2,000 条证据定义；超限不得静默
  截断后通过。

### 6.4 证据可追溯性

文档中的 `E<n>` 是交付物本地编号，不是持久化 `evidence_id`。验收器必须把每个定义解析到同一
Run 的持久化 Evidence，并满足：

- Evidence `kind=READ`、`is_valid=true`；
- Evidence 的 `path` 与引用路径规范化后精确相等；
- Evidence 的 `content_digest` 与当前文件完整内容摘要相等；
- 引用的闭区间行范围全部存在；
- 引用范围被同一 Agent 读取版本中的 `READ` Evidence 完整覆盖；
- Evidence 的生产 ToolCall 不属于 Completion 自身，不能由验收器先读一遍源码再自证模型已读；
- Evidence 对应的 ToolCall 可回溯到 Agent actor、Gate allow 审计和实际读取结果；
- 引用不得指向交付物自身，以免文档自引用形成循环证据。

分页读取可能产生多个相邻 Evidence。按 D-60，同一 Run、同一路径、同一完整文件摘要且由同一
Agent producer 产生的多个 `READ` 行段可以取并集完整覆盖一个引用区间；不同摘要、跨 Run、
非 Agent 生产者或存在未读空洞时不得把部分范围冒充完整读取。

当前代码版本由 Completion 通过 Gate 内部控制面重新读取或摘要取得。发现以下情况时，旧引用证据
不能支撑 `ACCEPTED`：

- 文件已删除、变为目录、符号链接、二进制或不可读文本；
- 当前摘要与 Evidence 摘要不同；
- Evidence 已由 WP-07 标记失效；
- 引用行超出当前文件范围；
- 只能取得截断或不可验证的内容。

按 D-63，WP-08 在持有有效租约时调用 WP-07 的单向 Evidence 失效操作并追加 Audit，然后把对应
检查判为 `REJECTED`。失效操作或审计失败时整体验收失败关闭。`CompletionCheck` 不得把陈旧
Evidence 当作当前事实，也不得把旧证据恢复为有效。

### 6.5 工作区与范围校验

Completion 至少检查：

1. 当前 Git/workspace identity、`git_root`、`workspace_root` 和 `workspace_prefix` 与启动基线
   一致；
2. HEAD 或 `UNBORN` 哨兵、index digest 未变化；
3. `workspace_root` 内所有新增、修改、删除和未跟踪路径的相对路径集合等于或包含于
   `task.acceptance.deliverables` 所允许的交付物集合，不允许源码、测试、配置、状态文件或其他
   路径变化；
4. 每个交付物在启动基线中不存在（新增）或内容摘要发生变化（修改）；按 D-62，仅存在但未变化
   不得通过；
5. `protect_other_git_changes=true` 时，`git_root` 内工作区外的基线脏路径、内容摘要、身份、
   HEAD 和 index 均未变化；
6. `protect_other_git_changes=false` 时必须显式记录“外围未监控”，不得将该状态描述为
   “外围无变化”或“已验证隔离”；
7. Git 输出被截断、解析失败、身份变化或 path 无法可靠比较时，不得 `ACCEPTED`；
8. 工作区比较和 Git 读取均通过 Gate 内部控制面或 Sandbox 的公共、受控接口完成。

按 D-61，启动基线必须由 Harness 在启动时保存并显式注入，Completion 校验其身份与 fingerprint
后使用；不得根据当前工作区反推。跨进程或 CLI 验收需要基线持久化时，必须另行评审和修订。

### 6.6 Completion 内部控制面

Completion 需要读取交付物、源码、Git 状态和启动后的工作区差异，但不能走模型工具快照。
按 D-59，使用以下已冻结安全语义：

- Gate 提供独立的 `invoke_internal` 可信入口；
- 仅接受 Completion 专用、固定只读工具定义，不接受模型生成的工具名或任意 handler；
- 使用独立系统 actor 和只读角色，模型不能构造、覆盖或复用该 actor；
- 仅允许 Run 处于 `VERIFYING` 的指定只读操作；写、执行、审批和未知副作用工具一律拒绝；
- 内部工具不进入模型提示、工具 schema、模型可见 snapshot 或普通 observation；
- 每次内部访问仍经过租约、Run/config 校验、schema、Sandbox 路径/命令边界、执行意图、
  Evidence 与 Audit；
- 内部调用失败、审计不可写或证据不可持久化时失败关闭；
- 内部读取产生的 Evidence 必须标识为 Completion 生产者，不能伪装成 Agent 曾读取源码；
- 内部访问有固定调用次数、超时和输出上限，防止验收器自身形成无界循环。

内部 Registry 与普通模型 Registry 隔离。Completion 使用专门 step 和内部 Invocation 记录，
但不增加 Run 的 Agent step/tool-call 预算 usage；内部次数、超时和输出限制使用独立固定预算。

## 7. 固定处理链

```text
可信 Harness 调用 CompletionChecker
  -> 校验 config/run/task kind/lease/VERIFYING/startup workspace fingerprint
  -> 校验 Audit 链和既有有效 Evidence
  -> 通过 Gate 内部只读控制面读取全部交付物
  -> 解析三个必需 Markdown 区并构建本地证据图
  -> 校验证据编号、事实引用和引用范围
  -> 通过 Gate 内部只读控制面取得引用源码的当前版本
  -> 校验 READ Evidence 生产 actor、完整范围、内容摘要和有效性
  -> 通过 Gate 内部只读控制面取得 Git status/diff/工作区快照
  -> 比较启动基线、交付物集合、HEAD/index 和外围保护
  -> 检测 candidate_final_answer 的完成声明与程序事实是否一致
  -> 生成稳定排序的 CompletionItem 列表
  -> 在同一事务写入 Completion-only Evidence、逐项 Audit、
     CompletionCheck 和最终 Audit
  -> 返回已持久化的 CompletionCheck
```

任一步失败：

- 已确定的规格违规生成 `REJECTED` 项，后续只做不产生副作用的事实收集；
- 无法可靠判定时生成 `UNVERIFIABLE`，不得改写为 `ACCEPTED`；
- StateStore/Audit 写入失败时整体回滚并抛出稳定 Completion 基础设施错误；
- 已发生的只读 Gate 调用保留其审计事实，不回滚或删除；
- 不自动修复文档、不重新读取后覆盖旧证、不重试写操作。

## 8. 状态、事务与不变量

### 8.1 状态

- WP-08 不新增 Run 业务状态；Completion 入口要求 Run 已经为 `VERIFYING`；
- `AcceptanceStatus` 只使用冻结的 `NOT_EVALUATED`、`ACCEPTED`、`REJECTED`、
  `UNVERIFIABLE`；
- `ACCEPTED` 只在 CompletionCheck、相关 Evidence、逐项 Audit 和最终 Audit 全部成功提交后
  可见；
- 预算停止和不可进入验收的终态保持 `NOT_EVALUATED`，由 WP-10 负责最终 Run 状态。

### 8.2 事务边界

- Completion 读取阶段的每个 Gate 内部调用按 WP-04 事务规则独立提交；
- 最终 Completion-only Evidence、`completion_results`、`completion_items`、逐项验收 Audit 和
  最终 `COMPLETION_RECORDED` Audit 必须同一事务提交；
- Audit 或 Completion 任一写入失败时整体回滚；
- `CompletionCheck` 引用缺失 Evidence、跨 Run Evidence、失效 Evidence 或未提交 Evidence 时
  拒绝；
- Run revision 在检查期间变化时返回冲突并要求重新验收，不得复用此前读取的结论。

### 8.3 不变量

| ID | 不变量 |
| --- | --- |
| WP08-INV-001 | 模型不能调用、替换、跳过或覆盖 CompletionChecker。 |
| WP08-INV-002 | 模型最终回答和文档自然语言都不是独立验收证据。 |
| WP08-INV-003 | 所有交付物、引用源码和 Git 事实必须经受治理只读入口取得。 |
| WP08-INV-004 | Completion 内部工具不进入模型工具快照或提示。 |
| WP08-INV-005 | 事实引用必须解析到同一 Run 的 Agent 读取 Evidence。 |
| WP08-INV-006 | 引用行范围必须完整读取，且 Evidence 摘要等于当前文件摘要。 |
| WP08-INV-007 | 交付物之外的任何 Git-visible 工作区变化都拒绝接受。 |
| WP08-INV-008 | 外围保护开启时任何外围变化都拒绝接受；关闭时必须明确未监控。 |
| WP08-INV-009 | `ACCEPTED` 必须有非空检查项、全部通过且已完成持久化。 |
| WP08-INV-010 | CompletionCheck、Evidence 或 Audit 无法原子提交时不得返回接受。 |
| WP08-INV-011 | 验收结果只能引用同一 Run 的事实和 Evidence。 |
| WP08-INV-012 | 重复验收同一 Run 不得产生互相矛盾的并发结论。 |

## 9. 错误契约

以下错误码为候选稳定契约，最终在 WP-08 规格冻结时写入 `domain.ErrorCode`；能与既有
`SANDBOX_*`、`GATE_*`、`STATE_*`、`AUDIT_*` 复用的错误优先复用。

```text
COMPLETION_UNSUPPORTED_TASK
COMPLETION_RUN_STATE_INVALID
COMPLETION_CONFIG_MISMATCH
COMPLETION_LEASE_INVALID
COMPLETION_INTERNAL_ACCESS_DENIED
COMPLETION_DELIVERABLE_MISSING
COMPLETION_DELIVERABLE_EMPTY
COMPLETION_DELIVERABLE_TOO_LARGE
COMPLETION_DELIVERABLE_NOT_CHANGED
COMPLETION_DOCUMENT_INVALID
COMPLETION_SECTION_MISSING
COMPLETION_SECTION_DUPLICATE
COMPLETION_FACT_WITHOUT_EVIDENCE
COMPLETION_EVIDENCE_UNDEFINED
COMPLETION_EVIDENCE_DUPLICATE
COMPLETION_EVIDENCE_MALFORMED
COMPLETION_EVIDENCE_PATH_INVALID
COMPLETION_EVIDENCE_LINE_OUT_OF_RANGE
COMPLETION_EVIDENCE_NOT_READ
COMPLETION_EVIDENCE_PARTIAL
COMPLETION_EVIDENCE_STALE
COMPLETION_EVIDENCE_INVALID
COMPLETION_WORKSPACE_OUT_OF_SCOPE
COMPLETION_WORKSPACE_CHANGED
COMPLETION_EXTERNAL_WORKSPACE_CHANGED
COMPLETION_IDENTITY_CHANGED
COMPLETION_AUDIT_INVALID
COMPLETION_CLAIM_WITHOUT_EVIDENCE
COMPLETION_RESULT_WRITE_FAILED
```

| 错误类别 | 触发 | 结论/重试 | 模型可见信息 |
| --- | --- | --- | --- |
| `COMPLETION_DELIVERABLE_*` | 缺失、空、超大或未变化 | `REJECTED`；修正后新 Run/新验收 | 路径、阶段和修正方向，不回显正文 |
| `COMPLETION_SECTION_*` / `COMPLETION_DOCUMENT_*` | 文档结构或编码非法 | `REJECTED` | 精确指出交付物和结构问题 |
| `COMPLETION_FACT_WITHOUT_EVIDENCE` | 事实项无证据编号 | `REJECTED` | 指出交付物和事实序号，不复述敏感内容 |
| `COMPLETION_EVIDENCE_*` | 编号、路径、行或版本不可追踪 | `REJECTED`/`UNVERIFIABLE` | 指出证据编号和失败类别 |
| `COMPLETION_WORKSPACE_*` / `COMPLETION_IDENTITY_CHANGED` | 越界修改、HEAD/index/身份变化 | `REJECTED` | 只返回允许显示的相对路径和摘要 |
| `COMPLETION_AUDIT_INVALID` | Audit 无法校验 | `UNVERIFIABLE`；不得接受 | 要求人工核对 |
| `COMPLETION_INTERNAL_ACCESS_DENIED` | 内部控制面拒绝或不可用 | `UNVERIFIABLE`；不得接受 | 说明阶段，不泄露 handler/绝对路径 |
| `COMPLETION_RESULT_WRITE_FAILED` | 结果、Evidence 或 Audit 无法提交 | 抛错并整体回滚；不得返回 ACCEPTED | 稳定错误码和有界摘要 |

错误文本必须脱敏、有界，不得包含绝对 workspace 路径、完整源码、完整文档、API Key、令牌、
密码、敏感环境变量或隐藏推理。

## 10. 兼容、迁移与数据保留

- 继续使用 schema v1 的 `CompletionCheck`、`CompletionItem` 和现有
  `completion_results`/`completion_items` 表；如需新增 Completion-only Evidence，
  复用 `Evidence.tool_call_id=null`；
- 不修改已发布的 WP-01 migration；只有新增持久化字段或表时追加 migration，并补兼容测试；
- 如新增 `COMPLETION_CHECKED` 审计事件类型，只追加枚举，不改写历史事件；
- 读取未知 Completion/Evidence schema 时失败关闭；
- 旧 Run 没有新策略版本证据时不得推断为已验收；
- P0 保留 Completion 和 Audit 历史，不自动清理；
- `CompletionCheck`、`CompletionItem` 等已冻结名称不得改名为第二套结果模型。

## 11. 验收场景

`WP08-AC-001` 至 `WP08-AC-024` 是 WP-08 的最小验收集合。每个场景必须映射到自动化测试；
无法测试的场景不得进入规格冻结。

| ID | 场景 | 预期 |
| --- | --- | --- |
| WP08-AC-001 | 合法代码理解文档和引用 | 返回 `ACCEPTED`，全部必需项通过并持久化 |
| WP08-AC-002 | 声明交付物不存在 | `REJECTED`，结论不可进入完成态 |
| WP08-AC-003 | 交付物为空、二进制或超过解析上限 | 稳定拒绝，不返回源码/正文 |
| WP08-AC-004 | 交付物路径绝对、`..` 或符号链接逃逸 | 配置/验收失败关闭，handler/I/O 不越界 |
| WP08-AC-005 | 缺少“事实”“推断”或“代码依据” | `REJECTED`，指出缺失区 |
| WP08-AC-006 | 重复区、空事实区或非列表事实 | `REJECTED`，不把普通叙述当事实 |
| WP08-AC-007 | 事实无 `[E<n>]`、引用未定义或编号重复 | `REJECTED` |
| WP08-AC-008 | Evidence 路径越界、行号不存在或区间逆序 | `REJECTED` |
| WP08-AC-009 | Evidence 只覆盖引用范围的一部分 | `REJECTED`/`UNVERIFIABLE`，不得声称已完整读取 |
| WP08-AC-010 | Evidence 来自其他 Run、已失效或非 READ | `REJECTED` |
| WP08-AC-011 | Evidence 由 Completion 自己读取产生 | 不得作为 Agent 已读依据 |
| WP08-AC-012 | 源码读取后被修改，摘要不再匹配 | `REJECTED`，旧引用不能支撑当前结论 |
| WP08-AC-013 | 分页读取后以同一版本多段 Evidence 覆盖引用 | 按 D-60 验证同路径、同摘要、同 Agent 的连续范围并集 |
| WP08-AC-014 | 任务声明交付物之外修改源码、测试或配置 | `REJECTED`，diff/status 证据记录越界 |
| WP08-AC-015 | 工作区内新增文件，但路径不在交付物列表 | `REJECTED` |
| WP08-AC-016 | HEAD、index 或 workspace 身份变化 | `REJECTED`/`UNVERIFIABLE`，不得接受 |
| WP08-AC-017 | 外围保护开启且外部脏路径变化 | `REJECTED` |
| WP08-AC-018 | 外围保护关闭 | 明确记录未监控；不得声称外围无变化 |
| WP08-AC-019 | 模型声称完成但交付物缺失或引用无效 | `claim_without_evidence` 项失败并记录原因，最终 `REJECTED` |
| WP08-AC-020 | 模型未声称完成但全部程序事实通过 | 仍按程序事实验收，不因缺少自述拒绝 |
| WP08-AC-021 | 预算停止或 Run 不在 `VERIFYING` | 不产生 `ACCEPTED`，保持 `NOT_EVALUATED`/稳定拒绝 |
| WP08-AC-022 | StateStore 或 Audit 故障 | 事务回滚，不得返回 `ACCEPTED` |
| WP08-AC-023 | Completion 内部控制面被模型调用或内部工具出现在 snapshot | Gate/Registry 拒绝，模型可见列表不包含该能力 |
| WP08-AC-024 | 同一 Run 重复/并发验收 | 使用同一安全事实得到一致结果或稳定冲突，不覆盖历史结论 |

验收还必须覆盖：`test_completion.py` 存在、目标测试和全量测试通过、Ruff lint 和格式检查通过，
且没有未解释 skip。

## 12. 完成定义

WP-08 只有同时满足以下条件才可进入 `DONE`：

1. `CompletionChecker` 可由可信公共入口独立调用；
2. 所有仓库访问经过 Gate 内部控制面或明确批准的受控公共接口；
3. 合法文档可得到 `ACCEPTED`，缺少文档、结构错误、引用未读/失效/越界均被拒绝；
4. 文件读取后变化、越界 diff、HEAD/index/外围变化均被拒绝；
5. 模型自述不能单独产生完成状态；
6. CompletionCheck、Evidence、逐项 Audit 和最终 Audit 原子提交；
7. 模型工具快照和提示中不出现 Completion 验收工具；
8. WP08-AC 全部有离线自动化测试，目标测试和全量测试通过；
9. Ruff lint 和格式检查通过；
10. `acceptance-report.md` 记录实际命令、结果、限制和下游影响并标记 `accepted`；
11. 文档明确 WP-08 不负责测试修复、真实模型和 Agent/CLI 端到端验收。

## 13. 来源与追溯

| 来源 | 使用方式 |
| --- | --- |
| `requirements.md` `REQ-TASK-002`、`REQ-TASK-004`、`REQ-COMPLETE-*` | 代码理解产物、模型自述边界和最终验收 |
| `design.md` 11.1、11.2 | 交付物列表、文档三区、证据和 diff 规则 |
| `decisions.md` D-02、D-03、D-04、D-14、D-20、D-22、D-56、D-59 至 D-64 | 工作区、交付物、引用、证据、内部验收访问、基线、失效和模型声明语义 |
| 飞书文档 revision 14 | 只吸收内嵌“核心验收”表 revision 11 的 H1、H2、H3、A2、T1 相关要求 |
| WP-05 spec v1.0.0 | `READ` Evidence、路径/行语义、Git 只读工具和截断事实 |
| WP-07 spec v1.1.0 | Evidence validity 与恢复后旧事实不可复用 |

## 14. 已确认决策

2026-11-07 经需求方确认，六项评审问题全部采用推荐方案并登记为 D-59 至 D-64：

| 原问题 | 决策 | 冻结结论 |
| --- | --- | --- |
| WP08-Q01 Gate 内部控制面与预算 | D-59 | 新增 `invoke_internal` 和独立内部只读 Registry；仅允许 `VERIFYING`，系统身份固定，内部预算不占用 Agent 预算 |
| WP08-Q02 分页 Evidence 覆盖 | D-60 | 同 Run、同路径、同摘要、同 Agent producer 的连续 `READ` 范围可并集覆盖 |
| WP08-Q03 启动工作区基线 | D-61 | Harness 注入启动 `WorkspaceBaseline`，Completion 校验 fingerprint 后比较 |
| WP08-Q04 交付物变更要求 | D-62 | 每个交付物必须在本次 Run 中新增或变化 |
| WP08-Q05 陈旧 Evidence 失效 | D-63 | WP-08 在有效租约下调用单向失效并拒绝；失败关闭 |
| WP08-Q06 完成声明识别 | D-64 | 使用保守中英文标记作为诊断项，不替代程序事实 |

本规格无阻塞性未决问题。后续如改变上述结论，必须提升规格版本，更新决策、契约测试和下游
WP-09/WP-10 计划。

## 15. 当前结论

WP-08 规格已冻结，计划已完成，工作包当前为 `DONE`。`completion.py`、Gate
`invoke_internal`、内部 Registry、Markdown/Evidence 校验、全场景自动化矩阵和
`acceptance-report.md` 均已完成并 accepted。
