# Guarded Coding Harness 概要设计

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档状态 | 概要设计基线，待评审 |
| 对应需求 | [requirements.md](requirements.md) |
| 上游输入 | 飞书文档“AI Agent 全栈工程师训练营-作业三”及其中“必须交付”“核心验收”表格 |
| 参考实现 | `ai-agent-fullstack-training/course_code/week03/3-7/project3` |
| 目标运行时 | Python 3.10+、`smolagents==1.26.0`、Pydantic 2、jsonschema 4、SQLite |
| 首期任务 | 代码理解、测试修复 |

本文描述计划实现的结构和安全语义，不代表这些能力已经实现。实现完成与否仍由自动化测试、
工作区证据、验收命令结果和审计记录共同决定。

飞书正文当前 revision 为 14；其中内嵌表格 revision 为 11，与 `docs/requirements.md` 记录的
“必须交付”基线一致。最新“核心验收”表补充强调：审批只能消费一次、结果未知不得重放、
恢复时旧证据和旧审批必须失效、模型无交付证据不得通过验收。本文已纳入这些要求。

## 2. 设计范围与目标

本设计在不修改、复制或 monkey patch `smolagents` 核心的前提下，为
`smolagents.ToolCallingAgent` 增加一个确定性的治理层：

- 模型保留任务理解、规划、工具选择和下一步决策。
- Harness 负责任务装配、参数校验、权限判断、路径与命令边界、审批、预算、状态持久化、
  审计、恢复和最终验收。
- 所有仓库读取、文件写入、进程启动、测试执行和审批相关操作都经过同一个 Gate。
- 模型最终回答只作为候选结果；程序根据交付物、工作区 diff、测试结果和审计链决定是否接受。
- 默认测试完全离线，通过 Fake Model 驱动，不依赖真实模型、网络或用户凭据。

首期不实现多仓库调度、分布式队列、Web 管理台、无限 shell、包安装、后台常驻进程或
任意网络访问。

## 3. 总体架构

系统分为控制面、模型决策面和受控执行面。`Harness` 是唯一的 Agent Run 编排入口；模型只能
看到受控工具，无法直接访问 handler、Sandbox、审批表或验收器。

```text
CLI / 程序化调用
       |
       v
+---------------------+
|     Harness.run     |
|  唯一 Agent 编排入口 |
+----------+----------+
           |
           +--------------------> Config 加载与校验
           |
           +--------------------> Checkpoint / Resume
           |
           v
+---------------------+
|  ToolCallingAgent   |
| 模型决策 / ReAct 循环 |
+----------+----------+
           |
           v
+---------------------+
| GovernedTool 包装器 |
+----------+----------+
           |
           v
+---------------------+       +--------------------------+
| Gate 唯一执行入口   |------>| 策略 / 权限 / 审批 / 预算 |
+----------+----------+       +--------------------------+
           |
           +--------------------> Sandbox
           |
           +--------------------> 单一职责 Handler
           |
           +--------------------> StateStore
           |
           +--------------------> Audit
           |
           v
      结构化 ToolObservation
           |
           v
     返回模型继续决策


Agent 停止后：

+---------------------+
| CompletionChecker   |
+----------+----------+
           |
           +--------------------> Gate（内部控制面执行验收命令）
           |
           +--------------------> StateStore
           |
           +--------------------> Audit
           |
           v
     验收结论：ACCEPTED / REJECTED / UNVERIFIABLE


持久化关系：

      StateStore --------+
                         |
      Audit -------------+----> SQLite
                         |
      Checkpoint --------+
```

架构中有两个重要闭环：

1. **执行闭环**：模型提交工具名和参数，GovernedTool 转入 Gate；Gate 完成校验和授权后，
   才允许 Sandbox 与 handler 执行；结构化结果回到模型。
2. **验收闭环**：Agent 停止后，Harness 独立运行 CompletionChecker；校验证据写回 StateStore
   和 Audit，最终结果不采信模型自述。

## 4. 模块划分

推荐包结构如下。`src/guarded_harness/` 是导入边界；六个必须模块保留固定文件名。

```text
src/guarded_harness/
├── __init__.py
├── harness.py
├── gate.py
├── sandbox.py
├── state_store.py
├── checkpoint.py
├── audit.py
├── completion.py
├── config.py
├── domain.py
├── registry.py
├── agent_adapter.py
├── approval.py
├── policy.py
├── storage.py
├── redaction.py
├── tools/
└── cli.py
```

| 模块 | 主要职责 | 对外关系 |
| --- | --- | --- |
| `harness.py` | 唯一运行编排器；加载配置、创建 Run、装配组件、启动或恢复 Agent、停止循环、调用 Completion | 依赖下列全部核心模块；CLI 只调用它 |
| `agent_adapter.py` | 隔离 `smolagents` 公开 API；构造 `ToolCallingAgent`、GovernedTool 和流式运行适配 | 向上服务 Harness，向下只通过 Gate 执行工具 |
| `gate.py` | 所有工具调用和系统验收动作的唯一执行入口；固定顺序完成门禁、审批、重复调用、预算与执行意图落库 | 依赖 Registry、Policy、Sandbox、StateStore、Audit |
| `sandbox.py` | 仓库路径规范化、真实路径边界、受控安全 I/O、敏感路径策略、命令 argv 校验、最小环境变量、POSIX 进程组、超时和输出截断 | 由 Gate 在执行前调用，由受控 handler 复用；handler 不直接打开目标仓库文件 |
| `state_store.py` | 持久化 Run、步骤、工具调用、预算、审批、证据和验收结果；实现合法状态转移 | 与 Audit 共用 SQLite 事务基础设施 |
| `checkpoint.py` | 在安全边界生成快照、校验恢复前置条件、计算恢复动作、禁止副作用重放 | 读取/写入 StateStore、Audit 和 SQLite |
| `audit.py` | append-only 事件账本；统一事件封装、顺序、脱敏、摘要和哈希链 | 由 Gate、Harness、Checkpoint、Completion 写入 |
| `completion.py` | 程序化最终验收；按配置选择验收策略，执行公开验收和证据检查并输出结构化结论 | 通过 Gate 的内部控制面执行命令，不暴露给模型 |
| `config.py` | 读取 YAML，完成严格 Pydantic 校验、路径归一化和配置指纹 | Harness 启动时调用 |
| `domain.py` | 定义类型化领域模型、枚举、稳定错误码和接口协议 | 被所有模块复用，避免裸字典漂移 |
| `registry.py` | 注册工具元数据和 handler；生成模型可见工具快照 | 不直接执行 handler，只向 Gate 提供描述和具体实现 |
| `approval.py` | 创建、批准、拒绝、过期、认领和消费审批单；对本地 CLI 与未来外部通道复用同一套审批规则 | 由 Gate 和 CLI/审批适配器调用 |
| `policy.py` | 角色权限、任务模式、风险等级和工具可见性策略 | Gate 执行策略阶段调用 |
| `storage.py` | SQLite 连接、迁移、事务、锁和 append-only 约束 | StateStore、Audit、Checkpoint 共用的基础设施 |
| `tools/` | 文件读取、搜索、写入、git 状态、git diff、白名单测试命令等单一职责 handler | 只处理已经通过 Gate 的实际操作 |
| `cli.py` | 提供 `run`、`resume`、审批和只读查看命令 | 不自行拼装缺少安全组件的运行链 |

### 4.1 必须交付模块的边界

| 必须文件 | 不承担的职责 |
| --- | --- |
| `gate.py` | 不实现具体文件或进程操作，不定义任务名分支 |
| `sandbox.py` | 不管理审批和业务状态，不直接决定任务是否完成 |
| `state_store.py` | 不解释模型对话，不承担 append-only 事件语义 |
| `checkpoint.py` | 不重放未知副作用，不自行批准待审批动作 |
| `audit.py` | 不作为业务状态数据库，不修改既有事件 |
| `harness.py` | 不承载具体路径、命令或验收规则；只做装配和编排 |

## 5. 核心领域模型

所有跨模块对象使用 Pydantic 模型或 dataclass，不接受任意字典。持久化 JSON 在写入和读取时都
重新校验，历史数据不因版本变化被静默放过。

### 5.1 Run

`Run` 至少保存：

- `run_id`、`trace_id`、任务类型、任务说明和配置指纹；
- `workspace_root`（本次允许访问的任务子目录）、`git_root`（所在 Git 仓库根）和
  `workspace_prefix`（前者相对后者的规范化路径）；
- 创建、开始、更新、结束时间，以及执行该 Run 的租约信息；
- `workspace_root` 内的初始 status/diff 摘要和内容指纹；
- `git_root` 内、`workspace_root` 外已有脏路径的 status 基线和内容指纹；
- 当前运行状态、停止原因、预算上限和已用量；
- 当前步骤、恢复来源、最后一个安全 Checkpoint；
- 候选最终回答、最终验收结论和证据 ID。

### 5.2 ToolInvocation

```text
tool_call_id -> 模型原始调用 ID
step_id -> 所属 Agent 步骤
tool_name -> 规范化工具名
raw_args -> 模型原始参数，仅用于受控审计
normalized_args -> schema 校验和路径规范化后的参数
risk, permissions, idempotent -> 来自可信工具注册表
args_digest -> 工具名和规范参数的 canonical 摘要（完整 Run/工作区绑定由审批 binding_digest 负责）
status -> PREPARED / STARTED / SUCCEEDED / FAILED / TIMED_OUT / UNKNOWN_RESULT
```

### 5.3 ToolObservation

Gate 返回给模型的结果是有界结构化对象，至少包含：

```json
{
  "schema_version": 2,
  "status": "allowed|denied|waiting_approval|failed",
  "code": "STABLE_ERROR_CODE",
  "tool_call_id": "model-supplied-call-id",
  "phase": "schema|policy|approval|repeat|budget|execution",
  "message": "脱敏、可行动且长度受限的说明",
  "retryable": false,
  "approval_id": "optional-approval-id",
  "evidence_ids": ["optional-evidence-id"],
  "truncated": false,
  "output": {}
}
```

拒绝结果不得包含伪造的成功结果，不得触达 handler。`WAITING_APPROVAL` 表示当前进程停止
继续决策，但 Run 仍可通过恢复入口继续；默认先写出安全 Checkpoint，再由 `Harness.run(...)`
返回 `WAITING_APPROVAL` 并结束当前进程。`UNKNOWN_RESULT`、审计不可写等条件则终止自动恢复。
兼容字段 `evidence_id` 由 `evidence_ids` 的首项派生，不能表达与列表不同的事实。

### 5.4 状态与验收分离

运行状态描述“这一趟怎么结束”，验收状态描述“交付是否成立”，两者不得合并：

| 维度 | 值 |
| --- | --- |
| Run 状态 | `NEW`、`RUNNING`、`WAITING_APPROVAL`、`VERIFYING`、`COMPLETED`、`STOPPED`、`FAILED`、`BLOCKED` |
| StopReason | `FINAL_ANSWER`、`BUDGET_EXHAUSTED`、`APPROVAL_REQUIRED`、`APPROVAL_REJECTED`、`APPROVAL_EXPIRED`、`GATE_BLOCKED`、`UNKNOWN_RESULT`、`WORKSPACE_CHANGED`、`INTERNAL_ERROR` 等 |
| 验收状态 | `NOT_EVALUATED`、`ACCEPTED`、`REJECTED`、`UNVERIFIABLE` |

`COMPLETED` 只表示 Agent 循环正常结束且验收流程已执行，不表示验收通过。只有
`acceptance_status=ACCEPTED` 才能让 CLI 以成功退出。预算耗尽、审批等待和结果未知分别进入
明确的停止或阻塞状态，不能伪装为成功。

任一轮数、工具调用数、Token/成本或时间预算耗尽时，Run 必须进入
`RunStatus=STOPPED`、`StopReason=BUDGET_EXHAUSTED`、`acceptance_status=NOT_EVALUATED`，
CLI 返回非成功状态。Harness 不继续发起模型调用、工具调用或最终验收；现有产物和测试结果只作为
诊断证据保留。预算边界不得表示为 `COMPLETED`、`FAILED` 或 `ACCEPTED`。

```text
                     +--------------------+
         批准后返回  |                    |  停止/失败/阻塞
       +------------>| WAITING_APPROVAL   |------------+
       |             +--------------------+            |
       |                      ^                        |
       |                      | 需要审批               |
       |                      |                        v
+-----+   +---------+   +----------+   +-----------+
| NEW |-->| RUNNING |-->| VERIFYING|-->| COMPLETED |
+-----+   +----+----+   +----+-----+   +-----------+
               |             |
               |             |
               +-------------+-----------> STOPPED
                             +-----------> FAILED
                             +-----------> BLOCKED

状态转移：
  NEW                 -> RUNNING
  RUNNING             -> WAITING_APPROVAL | VERIFYING | STOPPED | FAILED | BLOCKED
  WAITING_APPROVAL    -> RUNNING | STOPPED | FAILED | BLOCKED
  VERIFYING           -> COMPLETED | FAILED | BLOCKED
```

状态转换由 StateStore 的条件更新完成，并校验“旧状态 -> 新状态”是否合法。非法转换、
版本冲突或租约失效都失败关闭。

## 6. 主要运行流程

### 6.1 新建 Run

1. CLI 或调用方只调用 `Harness.run(RunRequest)`；不直接构造 Agent 或 handler。
2. `config.py` 加载并严格校验配置，计算规范化配置指纹，展开任务中的相对路径。
3. Harness 分别识别 `git_root` 和 `workspace_root`，要求工作区位于 Git 根内；只检查
   `workspace_root` 是否干净，并为 `git_root` 内、工作区外的已有脏路径记录保护基线。
   测试修复任务同时记录工作区初始 status/diff。
4. Harness 创建 `run_id`，在同一事务中写入 Run 初始状态、预算和 `RUN_STARTED` 审计事件。
5. Harness 装配 StateStore、Audit、Sandbox、Registry、Gate、CompletionChecker 和
   `ToolCallingAgent`。
6. 测试修复任务先通过 Gate 的内部控制面执行基线测试，保存命令、退出码、报告、
   工作区指纹和失败摘要。
7. Harness 以流式公开接口驱动 Agent，并在每个安全边界保存 Checkpoint。
8. 如果 Gate 要求审批，Harness 在安全边界持久化 `WAITING_APPROVAL`、审批单和待执行调用。
   默认模式结束当前进程；可选等待模式在同一进程轮询审批状态。
9. Agent 正常停止、预算停止或异常停止后，先持久化 stop reason；允许验收时进入 `VERIFYING`。
10. CompletionChecker 独立验收，把验收结论写入 StateStore 和 Audit。
11. Harness 返回包含 Run 状态、验收结论、证据位置和审计范围的 `RunResult`。

### 6.2 单次工具调用

```text
smolagents Model   ToolCallingAgent   GovernedTool       Gate       State/Audit   Sandbox+Handler
       |                  |                 |              |             |              |
       | ToolCall(        |                 |              |             |              |
       | id,name,args)    |                 |              |             |              |
       |----------------->|                 |              |             |              |
       |                  | invoke(...)     |              |             |              |
       |                  |---------------->|              |             |              |
       |                  |                 | gate(...)    |             |              |
       |                  |                 |------------->|             |              |
       |                  |                 |              |             |              |
       |                  |                 |              | schema/语义 |              |
       |                  |                 |              | 权限/风险   |              |
       |                  |                 |              | 审批/重复   |              |
       |                  |                 |              | 预算检查    |              |
       |                  |                 |              |             |              |
       |         [任一检查拒绝]              |              |             |              |
       |                  |                 |              | 原子写拒绝  |              |
       |                  |                 |              | 审计        |              |
       |                  |                 |              |------------>|              |
       |                  |                 | 结构化拒绝   |             |              |
       |                  |                 |<-------------|             |              |
       |                  | 拒绝 observation|              |             |              |
       |                  |<----------------|              |             |              |
       |                  |                 |              |             |              |
       |         [全部检查通过]              |              |             |              |
       |                  |                 |              | 预执行意图  |              |
       |                  |                 |              | +approval   |              |
       |                  |                 |              | claim       |              |
       |                  |                 |              |------------>|              |
       |                  |                 |              | STARTED     |              |
       |                  |                 |              |------------>|              |
       |                  |                 |              | 真实边界    |              |
       |                  |                 |              | 校验并调用  |              |
       |                  |                 |              | handler     |              |
       |                  |                 |              |---------------------------->|
       |                  |                 |              | 结果、退出码 |              |
       |                  |                 |              | 截断/超时    |              |
       |                  |                 |              |<----------------------------|
       |                  |                 |              | 确定结果或  |              |
       |                  |                 |              | UNKNOWN +   |              |
       |                  |                 |              | 审计        |              |
       |                  |                 |              |------------>|              |
       |                  | 有界 observation|              |             |              |
       |                  |<----------------|              |             |              |
       | 工具结果         |                 |              |             |              |
       |<-----------------|                 |              |             |              |
       |                  |                 |              |             |              |
```

写文件、启动进程和审批相关调用默认串行。首期通过
`ToolCallingAgent(max_tool_threads=1)` 将 Agent 并发执行降为串行，Gate 内部仍使用
Run 级互斥锁，防止绕过 Agent 的直接并发调用。后续如开放只读并发，必须增加上限、独立调度队列，
并由 Gate 保证审计顺序稳定。

### 6.3 审批交互与等待模式

首期采用本地 CLI 和 SQLite 审批单，默认将“审批”和“恢复执行”拆成两个显式动作：

```text
harness run <request>
        |
        v
Run = WAITING_APPROVAL，进程退出
        |
        +--> harness approvals list
        |
        +--> harness approvals approve <approval_id> --actor <actor>
        |        （只改变授权，不执行 handler）
        |
        +--> harness resume <run_id>
                 （重建 Agent，重新经过 Gate，执行一次获批调用）
```

CLI 命令语义：

| 命令 | 行为 |
| --- | --- |
| `harness run ...` | 默认在 `WAITING_APPROVAL` 安全退出，不执行待审批 handler |
| `harness approvals list` | 只读列出 PENDING、APPROVED、REJECTED、EXPIRED 等审批单 |
| `harness approvals approve <id>` | 原子执行 `PENDING -> APPROVED` 并写审计，不执行仓库操作 |
| `harness approvals reject <id>` | 原子执行 `PENDING -> REJECTED`、Run `STOPPED/APPROVAL_REJECTED/NOT_EVALUATED` 并写审计 |
| `harness resume <run_id>` | 从持久化 Checkpoint 恢复；重新校验配置、工作区、参数摘要和审批 |

可选等待模式通过 `harness run ... --wait-for-approval [timeout]` 启用：

- Gate 写出 `WAITING_APPROVAL` 后，在同一进程轮询 SQLite 审批状态；
- 审批变为 `APPROVED` 后，Gate 重新校验同一调用、原子 claim 并执行；
- 批准、拒绝和执行结果仍全部落盘并写审计；
- 等待超时只结束当前进程并保持 `WAITING_APPROVAL`，不得把审批擅自改为拒绝或过期；
- 进程崩溃后与默认模式完全一致，通过 `resume` 从安全边界恢复。

等待模式是交互便利，不改变审批单的一次性、绑定和失败关闭语义。

审批默认 TTL 为 900 秒，任务可在 `1..86400` 秒内配置。`PENDING/APPROVED` 到期后转为
`EXPIRED`，并与 Run `STOPPED/APPROVAL_EXPIRED/NOT_EVALUATED` 在同一事务提交；`CLAIMED` 不按
审批 TTL 自动释放。即使读取审批触发到期物化，也必须由受控编排同时完成审批和 Run 收口。

创建审批、关联 ToolInvocation、Run `RUNNING -> WAITING_APPROVAL` 和审计属于同一事务。resume
通过 Gate 后，审批 claim、Run `WAITING_APPROVAL -> RUNNING`、预执行意图和审计也属于同一事务；
随后另一个事务写入 Invocation `STARTED`，再触达 handler。

### 6.4 恢复 Run

1. 新的 Python 进程调用同一个 `Harness.run(RunRequest(mode=RESUME, run_id=...))`。
2. StateStore 读取完整业务状态；Checkpoint 校验快照哈希、配置指纹、工作区身份、审批有效期和
   最后一次安全边界。
3. 如果最后一次副作用状态为：
   - `PREPARED` 且从未进入 `STARTED`：可确认未执行；重新通过 Gate 后继续。
   - `STARTED` 但没有确定结果：进入 `UNKNOWN_RESULT`，停止自动恢复，等待人工核对。
   - `SUCCEEDED/FAILED`：使用已持久化结果，不重复执行。
4. Harness 新建模型和 Agent 对象，不依赖旧进程内存。它从 StateStore 生成确定性的
   continuation prompt，注入原任务、生效配置、计划、已完成步骤摘要、仍有效证据、待执行调用、
   未决审批、预算消耗和剩余约束，再调用公开的 `agent.run(stream=True)`。
5. 恢复后的每个工具调用仍必须经过 Gate；已完成写入不会被当作新的授权。
6. 工作区、配置、审批或证据任一不匹配时，Run 进入 `BLOCKED`，不自动重放。
7. 默认模式必须显式调用 `resume`；可选等待模式只改变当前进程是否继续等待，不改变恢复校验。

### 6.5 最终验收

CompletionChecker 在 Agent 停止后运行，模型不能跳过、替换或覆盖它。验收过程与普通工具调用
一样经过 Gate 内部控制面，使用独立的系统 actor，不会把该能力暴露给模型。

验收输出至少包含：

- `accepted`、`acceptance_status`、检查项列表和每项结果；
- 交付物路径、工作区指纹、diff 摘要、测试运行 ID；
- 关联的 audit event ID、tool result ID、checkpoint ID；
- 失败原因、完成时间和是否检测到“模型声称完成但无证据”。

最终验收写 StateStore 和 Audit 失败时，Run 不得返回 accepted。

## 7. Gate 设计

### 7.1 固定检查顺序

Gate 对每个调用按下列顺序检查，前一步失败后立即返回，不允许后续 allow 规则覆盖：

| 阶段 | 检查 | 典型错误码 |
| --- | --- | --- |
| 1. 可见性与注册 | 工具存在、属于当前 Agent 的任务可见白名单 | `GATE_TOOL_NOT_FOUND`、`GATE_TOOL_NOT_VISIBLE` |
| 2. 参数 schema | `jsonschema` draft 2020-12 严格校验，拒绝类型错误和未声明字段 | `GATE_INVALID_ARGUMENT` |
| 3. 语义与规范化 | 参数边界、路径格式、命令规格、规范化参数摘要 | `GATE_SEMANTIC_INVALID`、`SANDBOX_PATH_OUT_OF_WORKSPACE` |
| 4. 权限 | 当前 actor、任务、模式具备读/写/执行权限 | `GATE_PERMISSION_DENIED` |
| 5. 风险与模式 | plan 模式禁止写；风险等级必须被任务策略允许 | `GATE_RISK_DENIED`、`GATE_MODE_DENIED` |
| 6. 审批 | 审批对象、有效期、绑定摘要和一次性消费均有效 | `APPROVAL_REQUIRED`、`APPROVAL_INVALID`、`APPROVAL_EXPIRED`、`APPROVAL_REPLAYED` |
| 7. 重复调用 | 使用规范化工具名和参数摘要检查重复预算 | `GATE_REPEATED_CALL` |
| 8. Run 预算 | 轮数、工具数、Token/成本、时间仍允许继续 | `GATE_BUDGET_EXHAUSTED` |

所有 Allow 与 Deny 都要写入 Audit。拒绝审计写失败时，拒绝结果仍可返回，但不得执行 handler；
允许审计或执行意图写失败时直接拒绝执行。Sandbox 预检在执行前完成，handler 内部仍需复用同一
边界对象完成实际落点校验，防止检查后目标变化。

### 7.2 工具注册元数据

每个可信工具至少声明：

```text
name, description, input_model, output_kind
risk: read | write | execute
required_permissions
timeout, retry_policy, idempotent
path_arguments
output_limit
approval_requirement
concurrency_class
```

工具描述和输入 schema 可由模型看到，但 handler 和内部可执行文件路径不可见。
“模型看到工具”不等于“有权执行工具”，每次执行仍重新检查。

### 7.3 审批语义

审批单绑定以下值生成规范化摘要：

- `run_id`、任务类型、`git_root`、`workspace_root` 和 `workspace_prefix`；
- 工具名、规范化参数；
- 配置指纹；
- 创建时间和过期时间；
- 请求 actor 与允许审批角色。

审批状态为：

```text
PENDING -> APPROVED -> CLAIMED -> CONSUMED
       \-> REJECTED
       \-> EXPIRED
       \-> INVALIDATED
```

- 参数、工作区、任务、Run 或配置变化会使旧审批失效。
- `CLAIMED` 只表示 Gate 已被某个执行意图占用，不表示副作用已完成。
- `CONSUMED` 只能由确定结果产生；结果未知时审批和执行调用一起停在不可重放状态。
- 重放、跨 Run、跨工作区、已过期或已撤销审批一律拒绝。
- 首期采用本地 CLI 审批命令，同时保留 `ApprovalChannel` 接口，后续可接外部审批系统。
- 默认行为是审批前安全退出、审批后显式 `resume`；可选 `--wait-for-approval` 在同一进程等待。
- `approve/reject` 只改变授权状态，不执行 handler；执行只能从 `resume` 进入 Gate。
- 审批 actor 必须至少命中一个允许审批角色，且不得等于可信运行上下文中的 `requested_by`；
  本地 actor 不做密码学身份认证，外部通道只有在补充可信身份适配后才能启用。
- `canonical_args_digest` 对脱敏前的规范化工具名和参数做 domain-separated SHA-256；
  `binding_digest` 覆盖 Run、工具调用、任务、工作区、配置、角色和有效期。审批表只保存摘要及
  脱敏摘要。
- 同一 `(run_id, tool_call_id)` 最多一张审批。相同绑定且仍为 PENDING/APPROVED 的重复创建返回
  原单；不同绑定冲突；CLAIMED 或终态重放拒绝。
- claim 与 Run 恢复、预执行意图同事务提交，`execution_intent_id` 使用 `tool_call_id`。
  claim 后、STARTED 前的崩溃可通过显式 `resume_claim` 重新校验后继续；STARTED 后只能进入
  `UNKNOWN_RESULT`。
- 人工拒绝或审批过期原子终止 Run，新增 `APPROVAL_REJECTED/APPROVAL_EXPIRED` StopReason，
  模型不得换工具继续同一目标。绑定变化使旧审批失效并将 Run 置为 BLOCKED，人工处理完成后
  启动新 Run。

### 7.4 重复调用和预算

重复调用键为 `(run_id, tool_name, canonical_args_digest)`，并排除已由审批消费的敏感明文。
读取操作可在配置的重复预算内重复；写操作和进程操作默认只允许一次成功执行。
若恢复上下文再次提出同一次已完成写操作，Gate 返回 `REPLAY_SUPPRESSED` 和原证据，不再次调用
handler。

预算在 Gate 和 Harness 两层检查：

- Gate 防止继续执行工具；
- Harness 在每次模型步骤后检查 Token、成本和墙钟时间，预算耗尽时不再发起下一次模型调用。

预算更新和工具执行计数使用事务和条件更新，不能只保存在内存。

## 8. Sandbox 设计

### 8.1 工作区身份、干净基线与仓库外围保护

本设计区分两个路径概念：

| 概念 | 含义 | 用途 |
| --- | --- | --- |
| `git_root` | 目标工作区所在 Git 仓库的顶层目录 | Git 身份、status/diff、仓库级越界检查 |
| `workspace_root` | 本次任务允许 Gate 和 Sandbox 访问的子目录，可以等于 `git_root` | 文件权限、命令 `cwd`、交付物和验收范围 |

启动时只要求 `workspace_root` 内没有 tracked 或 untracked 修改。允许同一 Git 仓库的其它
子目录预先存在未提交变更，避免同一个 monorepo 中多个任务工作区互相阻塞。空仓库使用稳定的
`UNBORN` HEAD 哨兵，不伪造 commit。

当 `workspace.protect_other_git_changes=true` 时，为阻止 Agent 或验收命令改动仓库其它目录，
Harness 在启动时记录：

- `git_root` 内、`workspace_root` 外所有 Git-visible 修改、删除和未跟踪路径；
- 这些路径在基线时的类型、状态摘要和内容哈希；
- 仓库公开的 Git 身份、当前 HEAD 和 index 摘要。

Run 停止和执行验收命令后重新采集同一范围：

- 工作区外出现新的 Git-visible 修改、删除或未跟踪路径；
- 基线中已有的外部脏文件内容发生变化；
- Git 身份、HEAD、index 或工作区相对前缀发生变化；

任一情况都说明本次 Run 越界，验收不得通过。被 `.gitignore` 忽略且不在任务声明保护范围内的
外部文件不在 Git 基线中，作为剩余风险记录，不能据此宣称具备文件系统级强隔离。

当 `protect_other_git_changes=false` 时，仍记录 `git_root`、HEAD 和 index，但明确跳过工作区
外脏路径的采集和比较；外围指纹使用“未监控”域分隔哨兵，不得把关闭保护报告成“无变化”。
`require_clean_workspace=false` 时，工作区初始 entries 的完整状态和摘要作为精确比较基线，之后
任何偏离都视为变化。symlink 只摘要目标字符串且不跟随到工作区外，submodule/gitlink 使用 Git
对象 ID，FIFO、设备、socket 等特殊文件在基线阶段失败关闭。

工作区本身等于 `git_root` 时，该规则退化为“整个目标仓库必须干净”。

### 8.2 路径边界

Sandbox 为每个 Run 固定一个已解析的 `workspace_root`。`git_root` 只用于仓库身份和
paths-aware 的 Git 操作，不扩大文件读写边界。路径处理流程：

1. 将输入路径解析为绝对路径；
2. 对当前存在的最近祖先执行 `resolve()`，再拼接尚不存在的尾部组件；
3. 使用 `Path.relative_to()` 或等价路径关系判断是否位于根目录内，不使用字符串前缀；
4. 对读操作检查 `deny_read` 和平台级敏感规则；
5. 对写操作检查 `deny_write` 与窄范围 `allow_write`；
6. 返回规范化绝对路径、相对 `workspace_root` 的路径及相对 `git_root` 的前缀，供审批摘要和
   审计使用。

默认禁止读取 `.env`、`.env.*`、私钥、令牌和凭据文件；默认禁止写入 `.git/`、依赖目录、
任务基础配置和既有测试。平台级敏感规则不可被任务 allowlist 覆盖。

任务级 `allow_write` 是 `deny_write` 的受控例外。测试修复中典型配置为：

```yaml
paths:
  deny_write: ["tests/**"]
  allow_write: ["tests/generated/**"]
```

命中 `allow_write` 的测试路径允许创建或覆盖文件；不区分目标文件是否在 Run 开始时已经存在。
这是首期明确接受的参考项目语义。目录外既有测试仍不可写，目录内写入仍须经过 Gate、审批、
执行意图审计和最终 diff/测试覆盖验收。

WP-06 的 `write_file` 以单文件 CREATE/REPLACE 原子提交；`apply_patch` 只修改一个已有普通
UTF-8 文件，接受 `{path, edits:[{oldText,newText}]}`，每段必须唯一匹配并按数组顺序应用，
整批计算成功后才执行一次原子替换。空 patch、上下文缺失、歧义、no-op 或超限均整批拒绝。
`write_file` 内容与目标相同时返回 `changed=false`，不进行第二次物理替换。

路径判定不是最终 I/O 授权。真实文件访问必须通过 Sandbox 提供的整文件安全 I/O：读取和写入
上限均为 10 MiB，读取超限拒绝；写入使用同目录临时文件、`fsync` 和原子替换。覆盖写入支持预期
内容摘要，创建写入默认要求目标不存在；只有可信工具规格可以开启逐级受检的父目录创建。既有
文件权限保持不变，新文件使用受控 umask 结果。handler 不得绕过 Sandbox 自行打开目标仓库文件。

目录列表与递归文件发现同样属于真实仓库 I/O，必须使用 Sandbox 的 `list_directory` 和
`walk_readable_files` 公共接口。目录符号链接不被递归；敏感、越界和特殊条目只做聚合计数，
不得向模型回显其敏感名称或内容。枚举结果带稳定排序、条目数、文件数、总字节和显式截断原因。

### 8.3 命令边界

- 模型只能提交可信 `command_id`；可执行文件、完整 argv、超时和输出限制来自任务配置或固定
  allowlist，模型不得提交 argv、env、`cwd` 或限制字段；
- 只读 Git 子命令可在可信规格中显式开启 pathspec 参数槽；调用方只能传当前 Sandbox 已复核的
  READ `SandboxPath`，这些值仅作为 `--` 后的独立 argv 元素追加，不支持 Git revision 或 pathspec
  magic。Git 查询固定使用 `--no-optional-locks` 和 `--literal-pathspecs`；
- 使用 `subprocess.Popen(..., shell=False)` 或等价 API；
- `cwd` 固定为规范化 `workspace_root`，不接受模型传入；
- 环境变量从空白映射构造，只注入受控 PATH、locale、`TZ=UTC`、隔离 HOME/TMPDIR、Python 和
  Git 安全变量；不继承宿主代理、凭据或用户配置，任务配置不得追加 env；
- Linux/macOS 子进程进入独立 POSIX 进程组，超时先终止进程组，宽限期后强制终止；Windows
  等不支持可靠进程组的平台在构造阶段失败关闭；
- stdout/stderr 分开采集，各以 1 MiB 为上限；保留头部和尾部并显式标记截断，进入模型前再次
  做字符上限裁剪；
- 返回结构必须包含退出码、信号、是否超时、是否截断、耗时和已采集输出。

WP-06 的 `run_test` 第一版只接受 `{"command_id":"acceptance_test"}`，完整 argv 来自任务
`acceptance.test_command`，不接受测试文件子集、额外 argv、env、cwd、shell 或模型 timeout。
该命令固定按非幂等处理；超时或取消必须终止进程组并进入 `UNKNOWN_RESULT`，不得自动重试。
`write_file` 和 `apply_patch` 固定必审，`run_test` 由任务 `approval.required_tools` 决定
是否必审。

超时只表示“本地停止等待”。非幂等命令超时或无法确认副作用时写入
`UNKNOWN_RESULT`，禁止自动重试和验收通过。只有只读或明确幂等、且上一次确定无副作用的操作
才允许按重试策略执行。

WP-03 不提供 OS 级文件系统或网络隔离。测试命令以当前用户权限运行目标仓库代码，因此只能用于
可信仓库。输出截断不改变命令真实退出码，但依赖完整报告的证据无效，Completion 不得据此判为
通过。

## 9. 状态、审计与 Checkpoint

### 9.1 单一 SQLite 持久化边界

StateStore、Audit 和 Checkpoint 共用同一个 SQLite 数据库和事务基础设施，避免三个独立存储之间
出现“状态已成功、审计没写入”的不可判定窗口。`state_dir` 必填且必须位于规范化
`workspace_root` 外；数据库固定为 `state_dir/harness.sqlite3`。如果它仍位于同一 `git_root`，
工作区外围保护只能排除该精确目录，不允许使用宽松前缀。

数据库启用 WAL、外键、忙等待和迁移版本。主要表：

| 表 | 作用 |
| --- | --- |
| `runs` | Run 当前业务状态、配置/工作区指纹、预算和最终结论 |
| `run_plans` | 当前计划、plan revision 和有序公开计划项 |
| `steps` | 模型步骤、stop reason 和安全边界序号 |
| `tool_calls` | 每次候选调用、规范参数摘要、状态和结果 |
| `evidence` | 可关联读取、命令、diff、测试和验收事实 |
| `budget_ledger` | 按来源和预算维度幂等记录增量 |
| `approvals` | 审批对象、摘要、有效期、决定和消费状态 |
| `checkpoints` | 快照、快照哈希、安全边界和 schema 版本 |
| `completion_results` | 验收项和证据关联 |
| `audit_events` | append-only 事件账本 |
| `run_leases` | 跨进程单写者租约和心跳 |

`audit_events` 通过 `BEFORE UPDATE`、`BEFORE DELETE` 触发器拒绝修改和删除；事件带有
`(run_id, sequence)`、前一事件哈希和当前事件哈希。业务数据不通过修改审计事件“回写”，
只能追加新事实。

`Evidence.tool_call_id` 是唯一生产者引用，可以为 `null` 表示 Completion-only 证据；
`ToolInvocation.evidence_ids` 是按 `run_id + tool_call_id` 从 Evidence 表派生的只读投影。
Completion 可以消费 Evidence ID，但不得改变证据归属。不得另建多对多关联形成第二套事实来源。

POSIX 新建状态目录使用 `0700`，数据库及 WAL/SHM 使用 `0600`；状态目录和文件必须由当前有效
用户拥有且类型安全。已有权限不安全或无法收紧时失败关闭，不静默继续。Windows ACL 的强隔离
不作为 P0 能力宣称。

### 9.2 事务与崩溃窗口

关键状态转换使用 `BEGIN IMMEDIATE` 或等价写事务。一个安全边界内可同时提交：

- Gate 决定或审批变化；
- 工具调用状态；
- 预算增量；
- 审计事件；
- Checkpoint 索引或快照。

Run 状态写入、预算 ledger、对应审计和必要关联行必须在同一 `BEGIN IMMEDIATE` 事务中提交。
StateStore 公共业务写操作自动产生固定领域审计；调用方不能通过只写业务状态完成成功提交。
租约普通续期只更新租约行，不生成高频业务审计。

崩溃窗口处理如下：

| 崩溃位置 | 持久化事实 | 恢复动作 |
| --- | --- | --- |
| handler 前，未提交执行意图 | 无该次副作用记录，审批未 claim | 重新经过 Gate，可重新决定 |
| 已提交意图和 claim，尚未写 STARTED | 明确“未开始” | 新租约下全量校验后 `resume_claim`，最多执行一次 |
| 已写 STARTED，handler 尚未返回 | 无法排除副作用 | 标记 `UNKNOWN_RESULT`，停止自动恢复 |
| handler 已返回，结果事务未提交 | 仍是 STARTED | 标记 `UNKNOWN_RESULT`，不得自动重放 |
| 结果与审计已提交 | `SUCCEEDED/FAILED` | 使用既有结果，不重复调用 |
| 结果已提交，Checkpoint 索引未提交 | 结果可由工具调用记录安全重建 | 补建下一安全边界 Checkpoint |

Checkpoint 至少保存 Run、当前步骤、计划、工具调用结果、预算、审批状态、停止原因、最后审计序号、
配置指纹、工作区证据和 schema 版本。其职责是描述“从哪个安全点继续”，不替代业务状态。

### 9.3 恢复校验

恢复前必须验证：

- Run 存在且未处于终态，或处于允许人工恢复的阻塞态；
- SQLite schema 与代码支持的版本兼容；
- 配置指纹未变化；
- `workspace_root`、`git_root`、工作区相对前缀和预期资源指纹一致；
- 工作区外已有脏路径的保护基线仍然匹配；
- 审批仍处于有效状态且绑定摘要匹配；
- 没有 `UNKNOWN_RESULT`；
- Run 租约可安全获取。

Run 创建与租约获取原子完成。租约默认 TTL 30 秒、允许 10 至 300 秒；Harness 在 handler 执行
期间以约 `min(10, TTL/3)` 秒间隔独立心跳。第一次续租失败进入风险状态并立即重试，连续第二次
失败请求取消 handler。旧 token 在接管后不能续租或提交 Run 状态；如果已有工具调用进入
`STARTED`，新进程只能按结果不确定处理，不能自动重放。

恢复不依赖旧模型对象、线程上下文或仅存于内存的重复调用缓存。

### 9.4 Continuation prompt 契约

恢复上下文只从 StateStore 中经过校验的结构化状态生成，至少包含：

- 原任务目标、任务类型和规范化生效配置指纹；
- `workspace_root`、`git_root`、当前 diff/status 摘要，以及仓库外围保护基线状态；
- 当前计划、最近安全步骤和已完成工具调用摘要；
- 仍然有效的产物、引用证据及其内容版本；
- `PREPARED` 待执行调用、有效审批和下一次允许动作；
- 已用与剩余轮数、工具次数、Token/成本和时间预算；
- 当前 stop reason，以及哪些操作禁止自动重放。

恢复上下文禁止包含：

- 序列化后的旧 `ToolCallingAgent` 或旧 Agent Memory 对象；
- 模型内部隐藏推理或框架私有字段；
- 完整 Audit 事件流；
- 文件内容已变化后仍标为“已读事实”的旧读取证据；
- 已过期、已消费或与当前参数摘要不匹配的审批。

Continuation prompt 使用固定字段顺序和稳定文本格式；历史 observation 按配置上限保留最近内容，
更早步骤只保留结构化摘要。恢复后的 Agent 仍从模型决策开始，但任何实际工具调用必须重新进入 Gate。

## 10. Audit 设计

审计事件采用稳定信封，至少包含：

```text
event_id, run_id, sequence, occurred_at
event_type, phase, status, code, decision
step_id, tool_call_id, approval_id
actor, tool_name, rule_id, workspace_fingerprint
input_summary, output_summary, evidence_ref
prev_hash, event_hash
```

`status` 必填，取值为 `PENDING`、`ALLOWED`、`DENIED`、`STARTED`、`SUCCEEDED`、`FAILED`、
`TIMED_OUT`、`UNKNOWN_RESULT` 等稳定状态；`code` 仅在拒绝、失败、超时和未知结果时必填；
`decision` 取值为
`ALLOW/DENY/NOT_APPLICABLE`。StateStore 自动追加固定领域事件，例如 `RUN_CREATED`、
`RUN_STARTED`、`STEP_COMPLETED`、`TOOL_SUCCEEDED`、`BUDGET_UPDATED`，调用方不能改写自动
事件类型。`actor` 来自可信 UnitOfWork 上下文。

必须记录的事件类别：

- Run 开始、恢复、停止和验收；
- 配置加载、工作区基线和预算变化；
- Gate allow/deny，以及具体规则和拒绝阶段；
- 审批请求、决定、过期、撤销、claim、claim 恢复和消费；
- 工具执行开始、成功、失败、超时、截断和结果不确定；
- Checkpoint 写入和安全边界；
- Completion 每项检查和最终结论。

敏感值在进入日志、审计、状态、错误消息或模型 observation 前统一脱敏。默认规则包括敏感键名、
环境变量值、Authorization header 和已知凭据形态；任务可以提供额外敏感字段，但不能关闭平台级
脱敏。用于审计的参数和结果按行和字符双上限裁剪，绝不从敏感 token 中间截取后仍保留可识别片段。

按 `run_id` 可重放决策链，按 `tool_call_id` 和 `approval_id` 可关联“提案 -> 门禁 -> 审批 ->
执行 -> 结果 -> 验收”。

## 11. 任务配置与两类验收策略

### 11.1 配置结构

配置使用 YAML，加载后转换成 `extra="forbid"` 的 Pydantic 模型。建议字段：

```yaml
version: 1
state_dir: /path/to/harness-state
workspace:
  root: /path/to/repo/task-workspace
  require_clean_workspace: true
  protect_other_git_changes: true
budget:
  max_steps: 12
  max_tool_calls: 40
  max_token_cost: 0.25
task:
  id: understand_login
  kind: code_understanding
  allowed_tools: [list_files, search_code, read_file, write_file, git_status, git_diff]
  paths:
    deny_read: ["**/.env", "**/*.pem"]
    deny_write: ["src/**"]
    allow_write: ["artifacts/code-understanding.md"]
  acceptance:
    strategy: code_understanding
    deliverables: ["artifacts/code-understanding.md"]
  approval:
    required_tools: [write_file]
    approver_roles: [local_operator]
    ttl_seconds: 900
    channel: local_cli
```

Gate、Sandbox 和 Harness 不包含 `understand`、`fix_tests` 等任务名分支。配置通过
`task.kind` 选择验收策略和权限集，新增任务类型应主要新增配置和策略插件。

`state_dir` 是必填字段，不再提供隐式默认值。配置加载阶段按配置文件目录解析为规范绝对路径，
并拒绝工作区内、根目录、受保护依赖目录和 `.git/` 路径；环境运行前 WP-01 仍须重新验证真实
路径和文件类型。

代码理解任务的 `acceptance.deliverables` 必须是非空、无重复的相对路径列表。配置加载时逐项
相对 `workspace_root` 规范化，拒绝绝对路径、`..` 逃逸、符号链接逃逸和重复路径；数组顺序
进入规范化配置与配置指纹。P0 中列表内每项都是必需交付物。

同一份交付物列表必须同时用于：

- 注入模型提示；
- 生成写工具的精确路径 allowlist；
- Gate 的写入前检查与审批参数绑定；
- Completion 的磁盘存在性、内容、引用和 diff 验收。

首期不提供 CLI `--deliverable` 覆盖。模型提交的写入路径不在该列表中时，Gate 直接拒绝且
不触达 handler。

### 11.2 代码理解策略

计划验收项：

1. 只允许任务声明的全部文档交付物被新增或修改，列表内所有路径都必须存在；
2. 每个交付物规范化后位于工作区，类型为文档，内容非空；
3. 文档必须包含结构化的“事实”“推断”“代码依据”区；
4. “事实”区至少包含一个列表项，每个事实列表项必须引用一个或多个已定义证据编号；
5. “代码依据”区中的每条证据使用 `E<n>: 相对路径:起始行[-结束行]` 格式，引用 ID 不得
   重复或未定义；
6. 每条引用的路径必须在 `workspace_root` 内，行号必须在当前文件范围内；
7. 每条证据对应的文件和完整行范围必须出现在同一内容版本的读取审计中；
8. 文件在读取后被修改时，旧引用证据失效；
9. 最终 Git status/diff 在 `workspace_root` 内未出现源码、测试、配置或其他越界修改，且仓库
   工作区外的保护基线未变化；
10. 如果模型声称完成但文件不存在、事实缺少证据、引用未定义或引用无法回溯到读取审计，记录
   `claim_without_evidence` 并拒绝。

程序只能机械证明“引用位置被读过、路径合法、行号存在”，不能证明模型对代码语义的解释必然正确。
因此代码理解交付物使用以下最小结构：

```markdown
## 事实

- 登录请求由路由进入认证服务。[E1]
- 认证失败时返回统一的鉴权错误。[E2]

## 推断

- 当前错误处理方式可能不适合直接用于生产环境。

## 代码依据

- E1: `src/routes/login.ts:12-28`
- E2: `src/services/auth-service.ts:35-47`
```

“事实”和“推断”必须使用不同标题；没有某类内容时应明确写“无”。验收器按 Markdown 列表项
执行事实证据规则，未列入“事实”区的叙述不作为可验收事实。更严格的自然语言语义审计不属于
首期自动验收范围。

### 11.3 测试修复策略

计划验收项：

1. Agent 开始前通过 Gate 执行基线命令，记录 argv、cwd、退出码、失败摘要、工作区 status/diff
   和配置指纹；
2. `tests/**` 等受保护既有测试默认只读；任务 `allow_write` 命中的目录允许创建或覆盖测试文件，
   目录外测试不得修改、删除或跳过；
3. 修复后使用同一验收命令在最终工作区复跑，不能使用模型自报的结果；
4. 测试运行前后分别取 `workspace_root` 指纹和仓库外围保护基线；任一变化则本次结果失效；
5. 最终退出码为 0，且测试报告可解析并证明声明的测试集合确实执行；
6. 基线中存在的测试节点不得从最终报告中丢失或被新增标记为跳过；允许新增测试节点；
7. `workspace_root` 内最终 diff 只包含任务允许的源码和 `allow_write` 测试目录中的变更，仓库
   外围保护基线未变化；
8. P0 不执行 Agent 不可见的额外隐藏测试；Completion 只使用任务配置声明的公开验收命令、
   交付物检查和程序化 diff/测试证据检查。

参考项目语义允许覆盖 `allow_write` 目录中已经存在的测试文件，因此 P0 不提供 `create_only`
模式。Completion 通过基线测试节点清单、最终执行报告、退出码和 diff 范围检测覆盖减少、节点
丢失或跳过；自然语言层面无法机械证明的断言弱化仍属于剩余风险。

未来如增加隐藏验收，只能由 CompletionChecker 通过内部控制面执行，并保持隐藏用例与命令不进入
模型提示、工具 schema、observation 或可读审计摘要；该能力不属于 P0。

如果测试命令缺失、不可执行、超时、报告无法解析、结果未知、diff 越界或旧测试证据失效，
验收结论只能是 `REJECTED` 或 `UNVERIFIABLE`，不能是 `ACCEPTED`。

## 12. `smolagents` 集成

### 12.1 使用的公开扩展点

依赖固定为 `smolagents==1.26.0`。Harness 计划使用以下公开接口：

- `Tool`：实现带元数据的 GovernedTool 包装器；
- `ToolCallingAgent(...)`：保留原生模型交互、工具选择和 ReAct 循环；
- `ToolCallingAgent(max_tool_threads=1)`：首期工具串行执行；
- `ToolCallingAgent.run(stream=True)`：由 Harness 消费公开事件流，在审批等待、预算停止和异常
  完成边界及时停止；
- `step_callbacks`：仅用于步骤完成后的观测和 Checkpoint 触发；
- `final_answer_checks`：可作为候选最终答案的结构检查，但不承担仓库验收；
- `RunResult` / memory 中的公开步骤数据：用于记录步骤摘要和恢复上下文。

不修改 site-packages、不复制 Agent Loop、不依赖私有属性完成安全判断。

P0 只交付和验收 Fake Model 驱动的确定性测试。Harness API 按 `smolagents.Model` 类型注入模型，
但不提供真实 Provider 的工厂、API Key 配置、网络 smoke test 或真实模型结果断言；接入真实模型
属于 P1，不得成为 `pytest -q`、安全边界测试或最终交付的前置条件。

### 12.2 捕获原始 `tool_call_id`

`smolagents==1.26.0` 的普通 `Tool.forward` 调用链不把模型原始 `tool_call_id` 传给工具，
而需求要求拒绝结果和审计保留该 ID。计划增加一个极薄的 `HarnessToolCallingAgent` 适配器，
仅覆写公开的 `process_tool_calls` 分派边界，将 `(tool_call_id, tool_name, args)` 交给 Gate；
参数校验、授权、执行、状态和审计仍全部在独立模块中。不复制模型请求、消息拼装或 ReAct 决策逻辑。

该适配器会增加版本兼容风险，因此必须在 `tests/test_harness_loop.py` 中验证：

- 单工具和多工具调用的 ID、顺序和 observation 正确；
- Fake Model 下仍由原生 `ToolCallingAgent` 产生决策流；
- 升级 `smolagents` 时契约测试会明确失败，而不是静默丢失安全关联。

替代方案是不覆写任何分派方法并自行生成 Gate 调用 ID，但这无法满足“返回原始
`tool_call_id`”的 P0 要求，不建议采用。

### 12.3 `final_answer`

内置 `final_answer` 不访问仓库，允许由 smolagents 原生执行。Harness 将其输出视为
`candidate_final_answer`，不视为完成证据。Agent 停止后仍由 CompletionChecker 独立验收；
`final_answer_checks` 即使通过也不能把 Run 标记为 accepted。

## 13. 错误与失败关闭

错误码使用稳定命名空间，至少覆盖：

- `CONFIG_*`：配置缺失、字段非法、指纹不一致；
- `GATE_*`：可见性、参数、权限、风险、重复和预算拒绝；
- `SANDBOX_*`：路径、敏感文件、命令、环境和进程边界；
- `APPROVAL_*`：缺失、过期、篡改、重放和状态非法；
- `STATE_*`：非法转移、版本冲突、租约和持久化失败；
- `CHECKPOINT_*`：快照损坏、配置/工作区变化和未知结果；
- `AUDIT_*`：append-only 违约、写入失败和链校验失败；
- `COMPLETION_*`：交付物、引用、diff、测试和证据不足；
- `SMOLAGENTS_*`：版本或公开适配契约不兼容。

基础设施异常遵循失败关闭：

- 参数、权限、审批或状态异常：拒绝本次 handler；
- 审计或执行意图不可写：不执行新的副作用；
- 副作用已发生但确定结果不可写：标记结果不确定并停止；
- Completion 不可执行或证据不完整：不得 accepted。

面向模型的错误文本要有界、脱敏，并说明拒绝阶段和可修正方向；完整内部异常只写受控本地日志，
不进入模型上下文。

## 14. 测试设计映射

默认测试完全离线，使用临时 Git 工作区、临时 SQLite 和 Fake Model。六个必须测试文件与模块的
计划对应关系：

| 测试文件 | 主要验证 |
| --- | --- |
| `tests/test_gate.py` | schema 注入、权限/风险、审批篡改/重放/过期、重复调用、拒绝不触达 handler |
| `tests/test_sandbox.py` | 路径穿越、符号链接、敏感文件、命令 allowlist、环境清理、超时、输出截断 |
| `tests/test_state_checkpoint.py` | 完整状态持久化、合法状态机、跨进程恢复、已完成写入不重放、未知结果停止 |
| `tests/test_audit.py` | append-only 约束、事件顺序、ID 关联、脱敏、决策链回放 |
| `tests/test_harness_loop.py` | Fake Model 正常完成、拒绝、审批等待、预算停止、异常停止、统一入口和 `smolagents` 兼容 |
| `tests/test_completion.py` | 两类任务验收、模型无证据声明、越界 diff、测试失效和结果不确定拒绝 |

实现阶段还应增加端到端故障注入：在 handler 前后、结果事务提交前后和 Checkpoint 写入前后模拟
进程终止，证明恢复不会重复已确定写入。详细用例和命令由后续 `docs/test-plan.md` 维护。

已确认的设计决策统一维护在 [decisions.md](decisions.md)。本文描述这些决策对应的架构与行为契约，
不再重复保存决策清单。
