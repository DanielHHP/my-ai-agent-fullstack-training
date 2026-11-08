# WP-10 Agent 与 Harness 工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-10 Agent 与 Harness |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 冻结日期 | 2026-11-07 |
| 依赖 | WP-07 accepted；WP-08、WP-09 的冻结契约与实现 |
| 间接依赖 | WP-01 State/Audit、WP-03 Sandbox、WP-04 Gate、WP-05/WP-06 工具 |
| 下游工作包 | WP-11、WP-12 |
| 对应需求 | `REQ-HARNESS-001` 至 `REQ-HARNESS-005`、`REQ-GATE-*`、`REQ-CHECKPOINT-*`、`REQ-AUDIT-*`、`REQ-COMPLETE-*` |
| 对应设计 | `design.md` 第 5、6、7.4、12、13、14 节 |
| 相关决策 | D-01、D-07 至 D-09、D-17 至 D-20、D-37 至 D-44、D-53 至 D-77 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |
| 原文件任务 | P5-01、P5-02、P5-03、P5-06 的 Agent 运行部分 |

本文定义 WP-10 的目标行为、接口边界和验收要求，不表示 Fake Model、Agent 适配器或 Harness
主循环已经实现。WP-08 和 WP-09 尚需完成工作包验收；依赖未满足前，WP-10 最多进入设计与契约
准备，不得把计划中的行为写成已实现能力。

文档冲突时按 `requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。
WP10-Q01 至 WP10-Q05 已按 D-73 至 D-77 关闭，本文为冻结规格。

## 2. 目标

WP-10 必须在保留 `ToolCallingAgent` 原生模型交互、工具选择和 ReAct/Tool Calling 循环的前提下，
形成一个唯一、可恢复、可审计的 Agent Run 编排入口：

1. 使用 `smolagents==1.26.0` 的公开 API 构造 `ToolCallingAgent`；
2. 模型只看到受控工具的公开 schema，不接触 handler、Sandbox、Gate、审批表或验收器；
3. 模型提出的每个非 `final_answer` 工具调用都携带原始 `tool_call_id` 进入 Gate；
4. 多工具调用按确定顺序处理，写、进程和审批相关操作串行，审批或未知结果出现后不再继续
   执行同批后续调用；
5. Gate 的拒绝、等待审批、确定结果和未知结果以有界结构化 observation 返回模型或终止 Run；
6. Harness 负责新建 Run、装配安全组件、执行测试基线、驱动流式 Agent、记录预算、
   在安全边界写 Checkpoint，并完成恢复与终态编排；
7. 模型 `final_answer` 只保存为 `candidate_final_answer`，不能形成 `ACCEPTED`；
8. Agent 停止后由 `CompletionChecker` 独立决定验收状态；
9. 预算耗尽、异常退出、未知结果和恢复拒绝都有稳定状态与 StopReason；
10. 所有成功、拒绝、暂停、恢复和验收事实可通过 `run_id`、`step_id`、`tool_call_id`、
    `approval_id` 和 `checkpoint_id` 重建；
11. `smolagents` 版本或公开适配契约变化时显式失败，提示升级评审，不静默丢失调用 ID 或安全
    语义；
12. P0 的自动化验收完全离线，使用脚本化 Fake Model，不调用真实 Provider 或网络。

## 3. 范围与非目标

### 3.1 范围内

- 新增 `tests/fake_model.py`，提供确定性脚本化 `smolagents.Model`；
- 新增 `src/guarded_harness/agent_adapter.py`；
- 新增 `src/guarded_harness/harness.py`；
- `domain.py` 中 `RunMode`、`RunRequest`、`RunResult` 和 WP-10 稳定错误码；
- `state_store.py` 中保存候选最终回答所需的窄范围公共写操作；
- 按已确认方案处理跨进程启动工作区基线和 token/cost 计量；
- `__init__.py` 的稳定公共导出；
- 新增 `tests/test_harness_loop.py`，并扩展指定测试文件；
- WP-10 规格、计划、决策、索引和后续验收报告。

### 3.2 非目标

- 不实现 CLI、`run/resume/approvals/inspect` 参数和退出码，这些属于 WP-11；
- 不实现同一进程内的 `--wait-for-approval` 轮询；
- 不实现真实模型 Provider、API Key、网络 smoke test 或模型质量评估；
- 不复制、继承并重写整个 Agent 类，不修改 site-packages，不 monkey patch；
- 不在 adapter 中复制 Gate 的 schema、路径、权限、审批、重复调用或预算检查；
- 不让 `GovernedTool` 持有或调用真实工具 handler；
- 不把 `step_callbacks` 当作唯一安全门禁；
- 不重新实现 WP-07 的恢复动作选择或 WP-08/WP-09 的验收判定；
- 不自动把 `UNKNOWN_RESULT` 改为成功，不自动重放已开始或结果不确定的副作用；
- 不序列化旧 Agent memory、模型隐藏推理、完整 Audit 流或完整工具输出；
- 不承诺真实模型、分布式执行或容器级隔离。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。WP-10 是指定的 Agent
运行闭包，因此负责在 Fake Model 驱动下证明 P0 场景能够端到端成立；底层能力仍分别由 WP-03、
WP-04、WP-06、WP-07、WP-08、WP-09 实现。

| 编号 | 优先级 | 飞书通过标准 | WP-10 责任 | 实现归属 |
| --- | --- | --- | --- | --- |
| G1 | P0 | 参数缺字段或类型错误时返回明确错误，handler 调用次数为 0 | 让 Fake Model 的真实候选调用经过 Gate，并断言拒绝 observation 与零 handler 调用 | WP-04 / WP-10 |
| G2 | P0 | 连续三次相同工具和参数，第三次在执行前拦截，handler 只执行两次 | 保留每次原始调用 ID 和顺序，Gate 重复预算生效，Run 不绕过门禁自行放行 | WP-04 / WP-10 |
| G3 | P0 | 未审批写操作进入 `WAITING_APPROVAL`，目标文件不变 | Agent 在 Gate 返回等待后停止模型循环，保存 Checkpoint 和待执行调用 | WP-02 / WP-04 / WP-10 |
| G4 | P0 | 审批后重复执行同一调用，第一次只执行一次，第二次拒绝，不重复写入 | resume 使用原调用事实重新过 Gate，replay suppression 返回原结果且不触达 handler | WP-02 / WP-07 / WP-10 |
| S1 | P0 | `artifacts/../../.env` 路径逃逸时 Sandbox 拒绝，handler 不执行 | Agent 提交的路径必须原样送入 Gate/Sandbox，adapter 不做放宽或预执行 | WP-03 / WP-04 / WP-10 |
| S2 | P1 | 软链接逃逸或敏感目录访问被真实落点校验拒绝并审计 | Agent 闭环复验该拒绝；P0 自动化不把 P1 场景作为 WP-10 单独验收门禁 | WP-03 / WP-12 |
| S3 | P0 | `run_test` 配置外命令不在允许范围时不执行 | 模型只能看到受控 `command_id` schema，adapter 不接收或拼接任意 argv | WP-06 / WP-10 |
| S4 | P1 | 命令超时或用户取消时真实终止进程组 | Agent 遇未知结果立即停止后续执行；进程组实现由 WP-03/WP-06 验收 | WP-03 / WP-06 / WP-10 |
| C1 | P0 | 每个安全步骤后保存 Checkpoint，包含 Run、step、计划、证据、预算和待审批动作 | Harness 在 Run 启动、工具结果、ActionStep 完成、审批请求和验收等边界触发 WP-07 | WP-07 / WP-10 |
| C2 | P0 | 写入完成后结果未保存时恢复标记 `UNKNOWN_RESULT`，不得自动重放 | Harness 遇到未知执行或恢复计划要求人工核对时停止 Agent，不重新提案原写操作 | WP-07 / WP-10 |
| C3 | P0 | 正常中断后换进程恢复，找回进度，已完成写入不重复，计数不清零 | `Harness.run(RunRequest(mode=RESUME))` 重建 Agent 和上下文，预算与步骤来自 StateStore | WP-07 / WP-10 |
| C4 | P0 | 恢复时工作区已变化时旧证据或审批失效并停止等待核对 | Harness 只消费 WP-07 的恢复计划；`MANUAL_REVIEW` 时不创建 Agent、不执行工具 | WP-07 / WP-10 |
| A1 | P0 | 被拒绝调用审计包含 rule、decision、actor、toolCallId | 保留原始 `tool_call_id`，Harness 不吞掉 Gate 拒绝，并验证 Audit 关联 | WP-01 / WP-04 / WP-10 |
| A2 | P1 | 完整任务可关联 Gate、Sandbox、State、Approval 和执行结果 | `RunResult` 只报告关联 ID 和验收事实；P1 全链演示留给 WP-12 | WP-10 / WP-12 |
| H1 | P0 | 模型说完成但无交付物时 final answer 被拒绝，不能进入完成态 | `final_answer` 只写 `candidate_final_answer`；正常停止后必须执行 Completion | WP-08 / WP-10 |
| H2 | P0 | 测试通过后又修改源码时旧测试证据失效，必须重跑当前版本 | Harness 不在预算/异常停止时验收；正常停止后交给 WP-09 当前工作区验收 | WP-09 / WP-10 |
| H3 | P0 | 达到最大轮数或预算时明确 StopReason 停止，不伪装成功 | 任一预算耗尽固定写 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，禁止继续模型、工具或验收 | WP-01 / WP-10 |
| T1 | P0 | 所有 P0 场景都有确定性测试且可重复通过 | `tests/test_harness_loop.py` 使用临时 Git 仓库、SQLite、Fake Model，无网络和关键 skip | WP-10 / WP-12 |
| T2 | P1 | 用真实模型跑通一类任务，产物、Diff、测试记录和审计能相互印证 | P0 不实现；保留 `smolagents.Model` 注入缝，真实 Provider 属于 D-07 明确的 P1 | WP-12 / P1 |

跨工作包条目只有在 WP-12 端到端验收后才能声明项目级关闭。WP-10 通过只能证明 Fake Model
驱动下的 Agent 运行闭包，不证明真实模型能力、CLI 交互或全部 P1 项目。

## 5. 组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `harness.py` | 唯一 Run 编排入口，装配配置、模型工具、Gate、State、Checkpoint、Audit 和 Completion | 实现具体工具 handler、复制 Gate 规则、决定验收业务规则 |
| `agent_adapter.py` | `Tool` 包装、原始调用 ID 分派、流式暂停和 `ToolCallingAgent` 公开适配 | 参数授权、文件/进程访问、审批持久化 |
| `tests/fake_model.py` | 脚本化模型回合、工具调用、最终回答、异常、token 用量和多调用序列 | 生产运行时、真实 Provider、安全判断 |
| `registry.py` | 提供模型可见 `ToolSpec` 和隐藏 handler | 把 handler 暴露给模型 |
| `gate.py` | 每次工具调用的唯一授权与执行入口 | Agent 决策、消息拼装、最终验收判定 |
| `checkpoint.py` | 安全快照、恢复计划和 continuation context | 启动 Agent、调用 handler |
| `completion.py` | Agent 停止后的程序化验收 | 驱动模型或修改产物 |
| `state_store.py` / `audit.py` | Run、Step、预算、审批、Evidence、Completion 和事实审计 | Agent memory、模型隐藏推理 |
| `sandbox.py` | 路径、Git、命令和进程边界 | 决定模型可调用哪个工具 |
| `config.py` | 严格配置、任务预算、批准和验收策略 | 接受运行时模型覆盖 |

依赖方向：

```text
config.py + registry.py + sandbox.py + state/audit + approval
                              │
                              ▼
domain.py <── gate.py <── agent_adapter.py <── harness.py
                    ▲                              │
                    │                              ├──> checkpoint.py
                    └── completion.py <────────────┘

tests/fake_model.py ───> harness.py / ToolCallingAgent
```

禁止：

- `agent_adapter.py` 读取 Agent memory 私有字段来决定授权；
- `GovernedTool` 保存 `ToolHandler`、真实文件路径、审批通道或 Completion 私有能力；
- `harness.py` 直接执行 handler、直接改 State/Audit SQL、直接调用 Sandbox 工具操作；
- 通过 `step_callbacks` 之外的回调或日志替代 Gate；
- 为不同类型任务在 Harness 中写 `understand`、`fix_tests` 分支；任务差异只来自已校验配置、
  已注册工具和 WP-08/WP-09 验收策略；
- 使用模型最终回答、工具 description 或提示词决定成功。

## 6. 公共接口与运行契约

### 6.1 RunMode、RunRequest 和 RunResult

候选稳定类型如下，最终名称和字段需在 WP10-Q 决策后冻结：

```text
RunMode =
    START
    RESUME

RunRequest(
    schema_version=1,
    mode: RunMode,
    run_id: RunId | None,
)

RunResult(
    schema_version=1,
    run_id,
    trace_id,
    status: RunStatus,
    stop_reason: StopReason | None,
    acceptance_status: AcceptanceStatus,
    completion: CompletionCheck | None,
    candidate_final_answer: str | None,
    approval_id: ApprovalId | None,
    last_checkpoint_id: CheckpointId | None,
    last_audit_sequence: int,
    error_code: StableErrorCode | None,
)
```

约束：

- `START` 的 `run_id` 必须为 `null`，由 Harness 新建；`RESUME` 必须提供现有 `run_id`；
- 调用方不能通过 `RunRequest` 覆盖任务、工作区、工具、权限、预算、审批角色或配置指纹；
- 任务说明来自已校验的 `task.description`，任务类型来自 `config.config.task.kind`；
- `RunResult` 是不可变、严格、可序列化的对外结果，不包含 handler、lease token、审批 token、
  完整模型消息、完整 observation 或未脱敏异常；
- `WAITING_APPROVAL` 是正常可恢复结果，不是异常或成功；
- `COMPLETED` 只表示运行流程结束，`acceptance_status` 才表示验收结论。

### 6.2 Harness 构造与生命周期

候选可信构造入口：

```text
Harness(
    config_path: Path,
    model: smolagents.Model,
    tool_factory: Callable[
        [ResolvedHarnessConfig, Sandbox],
        ToolRegistry,
    ],
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
    command_policy: CommandPolicy | None = None,
    fault_injector: FaultInjector | None = None,
)

Harness.run(request: RunRequest) -> RunResult
```

实现要求：

- 每次 `run` 重新加载并校验配置，重新验证配置指纹与恢复 Run 一致；
- 每次创建当前 Run 专属的 `Sandbox`、`Gate`、`CompletionChecker`、
  `CheckpointService` 和 Agent，不复用旧进程中的 Agent、Gate 或模型 memory；
- `tool_factory` 只能由可信程序提供，返回的 Registry 不向模型暴露 handler；
- Harness 从 Registry 已注册工具与任务的 `allowed_tools` 计算交集；配置要求的工具缺失、重复
  或不可见时失败关闭；
- Agent actor、角色和权限由 Harness 固定装配，不从模型参数或 RunRequest 接收；
- Harness 是创建 Agent、启动模型流、选择恢复动作和调用 Completion 的唯一现有入口；
- `step_callbacks` 只用于收集公开步骤事实和触发预算观测；任何 checkpoint、最终状态和
  acceptance 决定还必须由 Harness 主循环和持久化事实确认。

### 6.3 Agent 身份与权限装配

候选固定语义：

- Agent actor 为 `agent`；
- Agent role 为 `developer`；
- `developer` 的权限集合是本次任务允许工具所声明权限的并集；
- 实际工具可见性仍由 `task.allowed_tools`、Registry 和 Policy 再次约束；
- 审批角色只从 `task.approval.approver_roles` 读取，审批 actor 不得与 `requested_by` 相同；
- Completion 使用 Gate 已冻结的独立系统 actor 和内部 Registry，不继承 Agent 权限。

该映射不增加 YAML 角色字段，符合 D-43。不得把 Agent 角色直接授予全部权限类别；权限并集只
反映当前允许工具的最小声明集合。

### 6.4 GovernedTool

`GovernedTool` 是 `smolagents.Tool` 的薄包装器。每个可见 `ToolSpec` 对应一个实例，并且只
持有：

- 公开 `name`、`description`、`inputs`、`output_type`；
- 对 Gate 的受控引用；
- 当前 Run 的受信任执行上下文提供者；
- 无 handler、无 Sandbox 私有路径、无审批明文、无 Completion 入口。

行为要求：

- 模型看到的输入 schema 来自 Registry 的模型安全快照；
- `forward` 不生成新的工具调用 ID，只使用 adapter 提供的原始 `tool_call_id`；
- 每次调用构造 `ToolCallRequest` 并调用 `Gate.invoke`，不在 wrapper 内预检查或执行 handler；
- 返回有界、脱敏的 `ToolObservation`；拒绝、审批等待和未知结果不得伪装成功；
- 没有活动 adapter 调用上下文时直接失败关闭，防止测试或模型路径绕过调用 ID 绑定；
- `final_answer` 是 smolagents 内置工具，不是 GovernedTool，也不进入业务 Registry。

如果 `Tool` 的公开 schema 表示能力不足以完整表达 `ToolSpec`，adapter 必须验证模型侧 schema
仍然只声明相同参数和类型，并始终由 Gate 用原始 draft 2020-12 schema 做最终校验。不得为了
通过包装而放宽 Gate schema。

### 6.5 HarnessToolCallingAgent

`HarnessToolCallingAgent` 是 `ToolCallingAgent` 的轻量子类，只覆写公开的
`process_tool_calls(chat_message, memory_step)` 分派边界。

固定行为：

1. 在产生任何 handler 副作用前检查本回合调用 ID 非空、唯一；
2. 按模型声明顺序逐个 yield `ToolCall`；
3. 对每个非 `final_answer` 调用，携带原始调用 ID 进入 Gate；
4. 对 `final_answer` 调用，走 smolagents 原生工具，不做业务 Gate 包装；
5. 每次调用产生对应 `ToolOutput`，其 ID 与原始 `tool_call_id` 完全一致；
6. 按声明顺序写回 `memory_step.tool_calls` 和 observations；
7. 同一回合的多个调用默认串行。Gate 返回 `WAITING_APPROVAL` 或未知结果后不进执行同批剩余
   调用；
8. adapter 不直接解释审批结果、不消费审批、不修改 Run 状态；
9. 对 Gate 已拒绝的调用仍产生结构化 observation，让模型有机会在预算内纠正非安全错误；
10. 对不可恢复的暂停或未知结果抛出 Harness 私有控制异常/停止信号，由 Harness 转为公开
    Run 状态。

构造 Agent 时固定：

```text
ToolCallingAgent(
    tools=<model-visible GovernedTool list>,
    model=<injected smolagents.Model>,
    max_tool_threads=1,
    step_callbacks=<observation callbacks>,
    final_answer_checks=<bounded structural checks, optional>,
)
```

`run(..., stream=True, reset=True)` 必须由 Harness 消费。不得调用 `step_callbacks` 直接执行
任务验收，也不得依赖 `final_answer_checks` 证明仓库任务完成。

### 6.6 FakeScriptedModel

`tests/fake_model.py` 必须实现 `smolagents.Model` 的公开接口并支持确定性脚本：

```text
ScriptedToolCall(
    id,
    name,
    arguments: dict,
)

ScriptedResponse =
    AssistantToolCalls(content, tool_calls, token_usage)
    AssistantFinalAnswer(content, token_usage)
    AssistantError(exception)
```

要求：

- 产生合法 `ChatMessage`、`ChatMessageToolCall`、`ChatMessageToolCallFunction` 和
  `MessageRole.ASSISTANT`；
- token usage 使用公开 `TokenUsage`，未声明时默认零；
- 支持一次响应包含零个、一个或多个工具调用；
- 严格按脚本消费；脚本耗尽抛出测试异常，不隐式返回成功或模拟最终答案；
- 可记录收到的消息和 `tools_to_call_from`，供“schema 不含 handler/内部 Tool”断言；
- 不访问网络、文件系统、环境凭据或真实模型；
- 不内置任何对 Gate、Harness 或任务类型的特殊放行逻辑；
- 提供脚本构造 helper 和确定性异常注入，但不成为生产依赖。

### 6.7 候选最终回答

- `final_answer` 工具输出只在当前 Run 尚为 `RUNNING` 时接受；
- Harness 将脱敏、类型受限且有界的文本写入 `candidate_final_answer`，并写独立 Audit 事实；
- 写入失败时不得进入 `VERIFYING`；
- 最终回答不进入 Checkpoint 的授权事实，不改变 Evidence validity、审批或验收结果；
- 模型未产生最终回答、模型异常或预算耗尽时不得伪造最终回答；
- 最终回答超限或不是可接受文本时的策略由 WP10-Q03 确认。

## 7. `smolagents==1.26.0` 公开适配边界

### 7.1 允许依赖的公开接口

WP-10 只依赖已核对的以下接口：

- `smolagents.Model` 及其公开 `generate` 契约；
- `smolagents.Tool` 的名称、描述、inputs、输出类型、调用和工具 schema 表示；
- `smolagents.ToolCallingAgent(tools, model, ..., max_tool_threads=1)`；
- `ToolCallingAgent.run(..., stream=True, reset=True)`；
- `ToolCallingAgent.process_tool_calls(chat_message, memory_step)`；
- `ChatMessage`、`ChatMessageToolCall`、`ChatMessageToolCallFunction`、`MessageRole`；
- `TokenUsage`；
- `ActionStep`、`PlanningStep`、`FinalAnswerStep`、`ToolCall`、`ToolOutput`、
  `ActionOutput` 的公开数据；
- `step_callbacks` 和可选 `final_answer_checks`。

具体导入路径、签名和行为 DTO 必须由兼容测试锁定。只使用上述公开结构，不通过
`__dict__`、名字改写属性、site-packages 内部函数或字段完成安全分派。

### 7.2 版本与契约检查

Harness 启动时必须：

1. 读取 `smolagents.__version__` 并要求精确等于 `1.26.0`；
2. 校验 `Model.generate`、`ToolCallingAgent.run`、`process_tool_calls` 的目标签名；
3. 校验本规格依赖的公开 ChatMessage、ToolCall、ToolOutput、ActionStep 字段；
4. 校验 `max_tool_threads` 与 streaming 参数存在；
5. 任一检查失败返回 `SMOLAGENTS_*` 稳定错误，不启动模型或工具，不降级为“尽力兼容”。

升级依赖版本时必须先运行适配契约测试、更新本文和 `docs/decisions.md`，再调整代码。

### 7.3 明确不得依赖

- 不覆写 `_run_stream`、`_step_stream`、`_finalize_step` 等私有执行方法；
- 不读取私有 memory、monitor、logger 或 callback registry 字段作为安全事实；
- 不修改 smolagents 的 `ToolCallingAgent`、`Tool`、`Model`、`ToolCall` 或 `ToolOutput`；
- 不用 monkey patch 改变 `execute_tool_call` 或其他框架方法；
- 不依赖并发 `as_completed` 的完成顺序决定审计或写操作顺序；
- 不依赖模型自带的解析/重试来补足缺失的工具 ID；
- 不把 smolagents `RunResult.state=="success"` 映射为 `ACCEPTED`。

## 8. 唯一执行入口与调用分派

### 8.1 单次调用

```text
Model emits ChatMessageToolCall(id, name, args)
  -> HarnessToolCallingAgent validates IDs/order
  -> yields public ToolCall(id, name, args)
  -> Harness persists/associates current Step
  -> GovernedTool or unknown-tool shim constructs ToolCallRequest
  -> Gate.invoke(request)
       -> schema / semantic / permission / risk / approval / repeat / budget
       -> Sandbox + handler only after allow
       -> State/Audit/Evidence
  -> bounded structured ToolObservation
  -> ToolOutput(id, observation)
  -> model receives observation or Harness stops
```

模型可见工具白名单只决定“模型看到了什么”，不构成执行授权。未知工具也必须进入 Gate 并产生
`GATE_TOOL_NOT_FOUND` Audit，而不是被 adapter 静默忽略。

### 8.2 多工具调用

- P0 全部串行，`max_tool_threads=1`；
- 执行顺序等于模型声明的 `tool_calls` 顺序；
- 第一个审批等待、未知结果或不可继续的 Gate 错误出现后，停止当前 Agent 流；
- 同批后续调用没有进入 handler，也不得预先写 `STARTED`；
- 如果前序只读调用已经形成确定结果，保留其事实和 Checkpoint，不删除；
- 如果重复调用 ID 在同一响应中出现，整个响应在第一次副作用前失败关闭；
- 审计按稳定调用顺序追加，不能由线程调度或 `as_completed` 改变顺序。

## 9. Run 生命周期

### 9.1 新建 Run

```text
load and validate config
  -> verify smolagents version/contracts
  -> construct Sandbox and capture startup WorkspaceBaseline
  -> initialize/reopen SQLite without changing workspace
  -> create Run + lease + RUN_STARTED audit
  -> construct Registry/Policy/Gate/Completion/Checkpoint and Agent
  -> persist startup WorkspaceBaseline
  -> NEW -> RUNNING
  -> [test_repair] capture internal test baseline through Gate
  -> create RUN_STARTED safety checkpoint
  -> run native Agent stream
```

顺序约束：

- 测试修复基线必须在 Run 进入 RUNNING、Agent 首次模型/工具调用前完成；
- 启动工作区和测试基线无法持久化时，不启动模型；
- Run、配置指纹、工作区身份、预算和 Agent 装配必须来自同一份已校验配置；
- 任何装配失败都不能留下可被误认为可恢复的 `RUNNING` Run。

### 9.2 Agent 正常停止

正常停止条件是 smolagents 产生 `final_answer` 并结束流。

```text
persist candidate_final_answer and final-answer audit
  -> create STEP_COMPLETED safety checkpoint
  -> RUNNING -> VERIFYING
  -> CompletionChecker strategy selected only by validated task acceptance
  -> record CompletionCheck + audit
  -> create COMPLETION_RECORDED checkpoint
  -> VERIFYING -> COMPLETED
  -> return RunResult
```

`COMPLETED` 不等于 `ACCEPTED`。Completion 返回 `REJECTED` 或 `UNVERIFIABLE` 时 Run 仍可
`COMPLETED`，但必须保留对应 acceptance status 与失败检查项。

### 9.3 审批等待

- Gate 已在同一事务写审批、待执行 Invocation、Run `WAITING_APPROVAL` 和 Audit；
- Harness 捕获暂停后创建 `APPROVAL_REQUESTED` Checkpoint；
- Harness 关闭 Agent 流，不继续调用模型或同批后续工具；
- 默认释放当前进程的 Run 租约，进程可安全退出；
- `RunResult` 返回 `WAITING_APPROVAL`、`APPROVAL_REQUIRED` 和 `approval_id`；
- 目标文件或其他副作用不得在该阶段发生。

### 9.4 预算或异常停止

- 任一轮数、工具调用、token/cost 或墙钟预算耗尽：
  `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，不进入 Completion；
- Gate 返回确定未知结果：Run 进入 `BLOCKED/UNKNOWN_RESULT`，不自动恢复或重放；
- 恢复计划要求人工核对：保持/进入 `BLOCKED`，不创建 Agent；
- 模型、消息结构、公开适配或 Harness 内部错误：`FAILED/INTERNAL_ERROR`；
- Audit 或关键状态无法持久化：失败关闭，不返回 assumed-success；
- 任何异常都不得生成伪最终回答、伪成功 ToolObservation 或 `ACCEPTED`。

## 10. 预算、步骤与公开观测

### 10.1 预算检查

- Gate 在每次工具调用前再次检查预算；
- Harness 在每次完成的 ActionStep 后检查 step、tool、token/cost 和墙钟预算；
- Harness 在恢复后从持久化 usage 计算剩余预算，不重置计数；
- 预算记录使用 `BudgetDeltaCommand` 的稳定 source ID，重复 resume 不重复扣费；
- 达到上限后不发起下一次模型或工具调用；若当前 step 已产生最终回答，可进入验收；
- `step_callbacks` 只用于观察 token usage 和步骤边界；即使 callback 未触发，Harness 也必须用
  流事件和持久化状态执行预算检查。

### 10.2 Step 事实

每个 smolagents ActionStep 映射为至少一个持久化 `Step`：

- ordinal 单调、稳定；
- summary 只来自有界公开模型输出/工具摘要，不保存隐藏推理和完整 prompt；
- 在工具调用前创建或关联 Step，使 Gate 有合法 `step_id`；
- ActionStep 结束后转为 `COMPLETED` 或 `FAILED`；
- 重复消费同一 stream event 不重复创建 Step；
- PlanningStep 若未启用则不产生；未来启用时必须先补充预算和持久化语义。

### 10.3 token/cost 口径

飞书 H3 要求达到预算后明确停止，但当前配置字段名为 `max_token_cost`，Fake Model 提供的是
`TokenUsage`。WP10-Q01 必须确认以下一种口径：

1. 注入 `TokenCostPolicy`，将输入/输出 token 转为 Decimal cost；
2. P0 简化定义 `token_cost = input_tokens + output_tokens`，字段作为通用资源单位；
3. 只在测试中使用固定模型单价，并把单价写入配置指纹。

未确认前，不得以“只记录 token 数但不参与预算”的方式通过 H3。

## 11. Checkpoint 与恢复

### 11.1 Checkpoint 边界

Harness 至少处理：

- Run 启动后的初始工作区/基线安全点；
- ToolInvocation 确定成功、明确失败、超时或未知结果；
- ActionStep 完成；
- 审批请求已持久化；
- 审批恢复并重新通过 Gate；
- Completion 已记录。

每个 Checkpoint 通过 `CheckpointService.create_checkpoint` 创建，并验证当前 Run 租约和配置
指纹。Checkpoint 写失败时：

- 在工具副作用前失败：不执行工具；
- 在工具已确定完成后失败：保留既知结果并停止后续 Agent 自动推进；
- 不得删除或覆盖旧 Checkpoint 来“重试成功”。

### 11.2 恢复动作

`Harness.run(RESUME)` 先获取新进程 lease，再调用 `prepare_recovery`。只按持久化计划处理：

| RecoveryAction | Harness 行为 |
| --- | --- |
| `CONTINUE_AGENT` | 使用 continuation context 创建新 Agent 并继续 |
| `WAIT_FOR_APPROVAL` | 不创建 Agent；返回 `WAITING_APPROVAL/APPROVAL_REQUIRED` |
| `REENTER_GATE` | 先用持久化调用事实调用 `Gate.reenter_prepared`，再根据结果继续或停止 |
| `RESUME_CLAIM_THEN_REENTER_GATE` | 通过 Gate 的恢复入口完成 claim 续接和重新门禁，不直接执行 handler |
| `REBUILD_CHECKPOINT_THEN_CONTINUE` | 只消费 WP-07 已完成的可归因重建结果，再创建新 Agent |
| `MARK_UNKNOWN_RESULT` | 返回/保持 `BLOCKED/UNKNOWN_RESULT`，不创建 Agent |
| `MANUAL_REVIEW` | 返回阻塞事实和关联 ID，不创建 Agent |
| `ALREADY_TERMINAL` | 读取并返回既有终态，不再验收或写新业务事实 |

约束：

- continuation prompt 只能来自 `render_continuation` 的确定性结构事实；
- 不恢复旧 Agent memory 或旧 `ToolCallingAgent` 对象；
- 不自动重新提案已完成的写操作；如模型再次提案，由 Gate replay suppression 拦截；
- 每次 resume 的最终作答仍必须经过 Completion，不继承旧 acceptance；
- 恢复期间 lease、配置、工作区、审批或审计任一变化均失败关闭。

### 11.3 跨进程工作区基线

WP-08 Completion 需要启动时完整 `WorkspaceBaseline`。D-74 追加 migration v7 表
`run_workspace_baselines`，按 Run 保存一次规范化 JSON、摘要、配置指纹和创建时间；表为
append-only。两类任务均从该表读取启动基线，测试修复既有的报告/节点基线继续保留。

Harness 在 Run 进入 `RUNNING` 前写入启动基线，并在测试基线捕获后、首次模型调用前创建
`RUN_STARTED` Checkpoint。`RESUME` 必须读取并复核该基线；缺失、摘要不符、配置指纹不符或身份
不符时进入 `BLOCKED`，不创建 Agent。

## 12. Completion 与终态

- Harness 仅根据 `task.acceptance.strategy` 选择 WP-08 或 WP-09 的公开 Completion 入口；
- `RUNNING -> VERIFYING` 后 Agent 和模型工具不再可调用；
- Completion 使用已经持久化的启动基线和当前工作区事实；
- `ACCEPTED`、`REJECTED`、`UNVERIFIABLE` 原样写回 Run 和 `RunResult`；
- 预算停止不进入 `VERIFYING`，验收固定为 `NOT_EVALUATED`；
- Completion 基础设施异常不得降级为 `REJECTED` 后返回成功；应返回失败/不可验证事实并失败关闭；
- Completion 记录与 Audit 必须成功提交，否则不得进入 `COMPLETED`；
- `final_answer_checks` 通过不改变验收；即使它失败，也只作为 smolagents 风格的结构检查，
  真正仓库结论仍由 Completion 产生。

## 13. 状态、持久化与审计

### 13.1 状态机

WP-10 只使用既有合法转移：

```text
NEW -> RUNNING
RUNNING -> WAITING_APPROVAL | VERIFYING | STOPPED | FAILED | BLOCKED
WAITING_APPROVAL -> RUNNING | STOPPED | FAILED | BLOCKED
VERIFYING -> COMPLETED | FAILED | BLOCKED
```

所有转移使用 expected status、revision 和 lease token 条件更新。并发 resume 只能有一个所有者。

### 13.2 追加式事实

WP-10 至少产生以下审计事实：

- Run 启动、恢复、等待审批、停止、失败、阻塞和完成；
- 模型 Step 完成、候选最终回答记录；
- Gate 每次 allow/deny 及审批生命周期（由 Gate）；
- 工具执行 Start/Result/Unknown（由 Gate）；
- Checkpoint 写入与恢复动作；
- Completion 开始、逐项结果、最终结论。

`RunResult` 中的 ID 必须能定位对应 State 和 Audit 事实。记录先脱敏再写入，不包含模型密钥、
lease token、审批 token、完整文件内容或隐藏推理。

### 13.3 候选最终回答持久化

当前 WP-00/WP-01 没有公开的“更新 candidate_final_answer”接口。WP-10 需要增加窄范围
StateStore 方法，候选接口：

```text
set_candidate_final_answer(
    run_id,
    text: str,
    *,
    expected_run_revision: int,
    lease_token: str,
) -> Run
```

该方法必须：

- 只允许 `RUNNING` Run；
- 原子校验 revision/lease，拒绝覆盖不同文本；
- 在 StateStore 与 Audit 同事务提交；
- 脱敏、限制长度、写稳定审计事件；
- 失败时不允许 Harness 进入验收。

## 14. 错误契约

WP-10 使用以下稳定错误族：

```text
HARNESS_RUN_REQUEST_INVALID
HARNESS_RUN_MODE_INVALID
HARNESS_CONFIG_RELOAD_FAILED
HARNESS_TOOL_ASSEMBLY_FAILED
HARNESS_STEP_PERSIST_FAILED
HARNESS_BUDGET_ACCOUNTING_FAILED
HARNESS_FINAL_ANSWER_INVALID
HARNESS_COMPLETION_FAILED
HARNESS_RESUME_NOT_ALLOWED
HARNESS_INTERNAL_ERROR

SMOLAGENTS_VERSION_MISMATCH
SMOLAGENTS_ADAPTER_INCOMPATIBLE
SMOLAGENTS_STREAM_CONTRACT_INVALID
SMOLAGENTS_DUPLICATE_TOOL_CALL_ID
```

现有 `GATE_*`、`STATE_*`、`CHECKPOINT_*`、`RECOVERY_*`、`COMPLETION_*`、`APPROVAL_*`
错误码继续使用，不包装成模糊的 Harness 错误。面向调用方的消息有界、脱敏并包含稳定 code；
完整内部异常只进入受控日志，不进入模型上下文。

## 15. 验收场景

WP10-AC 至少覆盖以下场景：

| ID | 场景 | 必须证据 |
| --- | --- | --- |
| WP10-AC-001 | Fake Model 驱动原生 `ToolCallingAgent` 完成只读工具调用 | 模型收到的 tools 不含 handler；Gate allow；ToolOutput ID 原样 |
| WP10-AC-002 | 单工具调用 | Call ID、tool name、observation、Step 和 Audit 一一对应 |
| WP10-AC-003 | 同回合多工具调用 | 声明顺序、执行顺序、memory observation 顺序稳定 |
| WP10-AC-004 | 重复调用 ID | 副作用前拒绝，handler 次数为 0 |
| WP10-AC-005 | 未知工具 | Gate deny/audit，handler 不可达 |
| WP10-AC-006 | schema 注入 | Gate 拒绝，handler 为 0，ToolOutput 保留原 ID |
| WP10-AC-007 | 路径逃逸 | Sandbox 拒绝，handler 为 0，模型得到结构化拒绝 |
| WP10-AC-008 | 未审批写 | `WAITING_APPROVAL`，文件不变，Checkpoint 含待执行调用 |
| WP10-AC-009 | 审批后 resume | 新 Harness/Agent 过 Gate，写入执行一次，重复提案被抑制 |
| WP10-AC-010 | 步骤预算耗尽 | `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，无 Completion 调用 |
| WP10-AC-011 | token/cost 预算耗尽 | 按已确认口径停止，usage 持久化和恢复不重置 |
| WP10-AC-012 | 墙钟预算耗尽 | 不再发起模型/工具调用，返回明确 StopReason |
| WP10-AC-013 | Fake Model 异常 | `FAILED/INTERNAL_ERROR`，不伪造最终回答或 accepted |
| WP10-AC-014 | 每个安全步骤写 Checkpoint | Run/step/plan/evidence/budget/pending approval 可恢复 |
| WP10-AC-015 | `PREPARED` 重启 | 新进程重新过 Gate，不重复预算或 Invocation |
| WP10-AC-016 | `STARTED` 无结果 | `UNKNOWN_RESULT`/BLOCKED，不自动重放 |
| WP10-AC-017 | 完成写入但 Checkpoint 未写 | 只消费 WP-07 安全恢复计划，不直接重放 |
| WP10-AC-018 | 恢复工作区变化 | 旧证据/审批失效，BLOCKED/MANUAL_REVIEW，不创建 Agent |
| WP10-AC-019 | 预算停止却模型声称完成 | 不进入 Completion，仍为 NOT_EVALUATED |
| WP10-AC-020 | 模型 final answer 无交付物 | candidate 被记录，Completion REJECTED，Run 不能 ACCEPTED |
| WP10-AC-021 | Completion 写 State/Audit 失败 | 不返回 accepted，不进入伪成功终态 |
| WP10-AC-022 | 两个并发 resume | 只允许一个 lease owner，副作用最多一次 |
| WP10-AC-023 | `smolagents` 版本/签名不兼容 | 启动前返回稳定 SMOLAGENTS 错误，未访问仓库 |
| WP10-AC-024 | 测试修复闭环 | Agent 修复后由 WP-09 最终报告验收；H2 旧证据不通过 |
| WP10-AC-025 | 全量确定性 | 测试无需网络、真实模型、凭据或本机绝对路径 |

所有拒绝场景除错误码外还必须断言 handler、文件写入和进程启动计数符合预期，并验证 Audit
关联；仅检查返回字符串不算通过。

## 16. 已完成决策与待确认问题

### 16.1 已由上位文档确定

- D-07：P0 只用 Fake Model，不接入真实 Provider；
- D-08：恢复使用结构化 continuation context，不恢复旧 Agent memory；
- D-09：预算停止固定 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`；
- D-17：预算增量由幂等 ledger 记录；
- D-19：Run 创建和 lease 获取原子完成；
- D-37 至 D-44：Gate、observation、重复执行、超时和拒绝持久化语义；
- D-53 至 D-58：PREPARED 重入门、未知结果、Evidence 失效和审批 Checkpoint；
- D-59 至 D-72：Completion 内部控制面、启动基线注入、测试修复验收和跨进程测试基线。

### 16.2 已确认的 WP10 决策

| ID | 问题 | 冻结方案 | 决策 |
| --- | --- | --- | --- |
| WP10-Q01 | `TokenUsage` 如何计入 `max_token_cost` | 注入 `TokenCostPolicy`；默认每 token `0.000001` Decimal 资源单位，策略版本写审计 | D-73 |
| WP10-Q02 | 代码理解跨进程启动基线如何持久化 | migration v7 通用保存 Run 启动 `WorkspaceBaseline`；新增 `RUN_STARTED` Checkpoint boundary | D-74 |
| WP10-Q03 | 最终回答超 16,000 字符时如何处理 | `HARNESS_FINAL_ANSWER_INVALID` 失败关闭，不静默截断 | D-75 |
| WP10-Q04 | 多工具批次遇审批/未知后是否保留后续调用 | 不执行、不预写 STARTED；已完成的确定事实和 Checkpoint 保留 | D-76 |
| WP10-Q05 | Agent actor/role 的默认映射 | 固定 `agent/developer`，权限取允许工具声明权限并集，不接受 RunRequest 覆盖 | D-77 |

## 17. 来源与追溯

| 来源 | 使用方式 |
| --- | --- |
| `requirements.md` 6.1、6.5、6.7、6.8 和整体验收 | Agent 唯一入口、恢复、审计和最终验收 |
| `design.md` 5、6、12、13 | Run 流程、公开适配、错误和测试映射 |
| `decisions.md` D-07 至 D-09、D-17 至 D-20、D-43、D-53 至 D-72 | Fake Model、预算、Gate、恢复和 Completion 约束 |
| `docs/task-breakdown.md` WP-10 与 M3 | 文件范围、退出条件和验收命令 |
| WP-04 spec | Gate 唯一执行入口和 ToolObservation |
| WP-07 spec | Checkpoint、恢复动作和 continuation context |
| WP-08/WP-09 spec | 两类任务的最终 Completion 入口 |
| 飞书文档 revision 14 内嵌“核心验收”工作簿 revision 11 | G1 至 G4、S1 至 S4、C1 至 C4、A1/A2、H1 至 H3、T1/T2 |

## 18. 当前结论

WP-10 规格已冻结。D-73 至 D-77 关闭全部 WP10 设计问题，`design.md` 和决策记录已同步。
WP-08/WP-09 依赖、25 个 AC、目标测试、全量测试、Ruff 和 `acceptance-report.md` 均已完成，
工作包当前为 `DONE`。

后续改变 Agent 适配、基线持久化、预算、最终回答、多调用终止或权限语义时必须提升规格版本，
同步决策、契约测试和下游 WP-11 计划。
