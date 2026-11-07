# WP-10 Agent 与 Harness 工作包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-10 Agent 与 Harness |
| 文档状态 | in-progress |
| 计划版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 批准日期 | 2026-11-07 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-07 accepted；WP-08、WP-09 的实现与验收 |
| 间接依赖 | WP-01、WP-02、WP-03、WP-04、WP-05、WP-06 的既有公共契约 |
| 当前结论 | WP-10 实现与目标测试已落地，正在完成全量复验和验收报告 |
| 下游影响 | WP-11 CLI/恢复、WP-12 两类任务端到端与最终交付 |

本计划不重新定义规格。若评审改变公开接口、Run 状态、错误码、持久化格式或安全语义，必须先
更新 `spec.md` 和必要的设计/决策文档，再调整任务卡。

## 2. 计划目标与完成定义

WP-10 施工完成后必须通过公共接口证明：

1. Fake Model 驱动原生 `ToolCallingAgent`，而不是第二套 Agent Loop；
2. 模型只能看到 `GovernedTool` 和内置 `final_answer`，不能触达 handler；
3. 单工具、多工具和未知工具的原始 `tool_call_id`、顺序和 observation 正确；
4. 所有非 `final_answer` 候选调用都经过 Gate，拒绝发生在 handler 之前；
5. 未审批写操作产生 `WAITING_APPROVAL`，停止模型流且目标文件不变；
6. 审批后新进程 resume 只执行一次写入，预算、Step 和 Invocation 计数不清零；
7. 每类安全边界都可由 WP-07 生成可恢复 Checkpoint；
8. 预算耗尽固定 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，不继续验收；
9. 未知结果和人工复核条件不自动重放，不创建绕过恢复计划的 Agent；
10. Agent 正常停止后，由 WP-08/WP-09 Completion 独立决定验收；
11. `COMPLETED` 与 `ACCEPTED` 分离，模型自述不能改变结论；
12. 公开适配在 `smolagents` 版本或契约变化时显式失败；
13. 目标测试、全量测试、Ruff lint 和格式检查通过；
14. `acceptance-report.md` 由实际命令和证据产生，不以本计划预期值代替。

## 3. 计划范围与依赖方向

### 3.1 计划文件布局

```text
src/guarded_harness/
├── harness.py          # 新增：唯一 Run 编排入口
├── agent_adapter.py    # 新增：GovernedTool 和 HarnessToolCallingAgent
├── domain.py           # 扩展：RunMode/RunRequest/RunResult、错误码
├── state_store.py      # 扩展：候选最终回答；按 Q02 处理通用基线
├── storage.py          # 仅当 Q02 选择通用基线持久化时追加 migration
├── checkpoint.py       # 复用：安全快照、恢复计划、continuation context
├── completion.py       # 复用：WP-08/WP-09 验收入口
├── gate.py             # 复用：唯一工具执行入口和内部控制面
├── registry.py         # 复用：模型安全工具快照
├── policy.py           # 复用/窄扩展：可信 Agent 权限装配
├── audit.py            # 复用/窄扩展：候选回答、Run 编排事实
└── __init__.py         # 导出稳定公共类型

tests/
├── fake_model.py              # 新增：确定性脚本化 Model
├── test_harness_loop.py       # 新增：WP10-AC 主测试矩阵
├── test_gate.py               # 扩展：adapter 穿透 Gate、拒绝零 handler
├── test_state_checkpoint.py   # 扩展：Step、预算、恢复和跨进程
├── test_completion.py         # 扩展：Agent 停止后调用 Completion
├── test_domain.py             # 扩展：RunRequest/RunResult/错误码
└── test_tools.py              # 按需扩展：tool schema/handler 隔离

docs/work-packages/wp-10-agent-harness/
├── spec.md
├── plan.md
└── acceptance-report.md       # 施工与实测完成后创建
```

WP-10 不创建 `cli.py`、`__main__.py` 或 CLI 测试，这些属于 WP-11。

### 3.2 依赖与准入

```text
WP-07 accepted
      │
WP-08 Completion/Evidence ─────┐
WP-09 test baseline/final ─────┼──> WP-10
WP-04 Gate + WP-05/06 tools ───┘
```

准入条件：

1. WP-08、WP-09 的公共入口与 spec 一致，工作包达到可作为依赖的状态；
2. `smolagents==1.26.0` 在项目 `.venv` 中可导入；
3. WP10-Q01 至 Q05 已确认并登记决策；
4. `spec.md` 已冻结，`plan.md` 已批准；
5. 现有全量测试在施工前有可复现基线。

依赖不满足时允许完成的只有兼容性探针、测试骨架和本文档更新，不得提前宣称 Agent 闭环可运行。

## 4. 详细设计

### 4.1 决策门禁

施工前必须关闭：

```text
WP10-Q01 token/cost 计量
WP10-Q02 跨进程启动工作区基线（已选择 migration v7）
WP10-Q03 最终回答超限策略
WP10-Q04 多工具批次审批/未知终止语义
WP10-Q05 Agent actor/role 与权限并集
        -> decisions.md
        -> design.md
        -> spec.md frozen
        -> test matrix 更新
```

Q01 至 Q05 已全部关闭：migration v7、固定 TokenCostPolicy、最终回答失败关闭、批次停止顺序和
固定 Agent 身份已进入实现。

### 4.2 公共契约与 Harness 构造

先建立严格类型测试，再实现行为：

- `RunMode` 只允许 `START/RESUME`；
- `RunRequest` 使用 `extra="forbid"`，START 无 run_id，RESUME 必有 run_id；
- `RunResult` 为冻结模型，只含脱敏 ID、状态和结论；
- Harness 每次 run 重新加载配置并对恢复 Run 校验指纹；
- Harness 不接收模型提供的 workspace、工具列表、角色、预算或审批角色；
- 每个 run 新建 Run 专属安全组件和公开 Agent，不跨进程重用旧对象；
- 公共导出和错误码补契约测试。

候选内部结构：

```text
_RunContext(
    config,
    storage,
    sandbox,
    registry,
    policy,
    gate,
    completion,
    checkpoints,
    lease,
    startup_workspace,
    current_step_id,
)
```

内部结构不包含模型可读取的 handler、审批明文或隐藏路径。

### 4.3 模型可见工具与 GovernedTool

实现顺序：

1. 用 `ToolSpec.input_schema` 生成 `smolagents.Tool.inputs` 的公开描述；
2. 保留 `ToolSpec` 原始 schema 供 Gate 最终校验；
3. 包装器的 `forward` 只读取 adapter 注入的原始 `tool_call_id`、name、args；
4. 构造 `ToolCallRequest` 并调用 `Gate.invoke`；
5. 将 observation 做二次大小检查和 JSON 序列化；
6. 无活动 adapter 上下文时抛稳定内部错误；
7. 断言 wrapper、模型 schema 和提示中都不出现 handler、私有路径或内部 Completion 工具。

不得通过字符串拼接生成任意 Python 执行代码。若 `smolagents.Tool` 要求专用 signature，使用
其公开 wrapper 支持能力并增加精确版本契约测试；不能关闭 Gate schema 校验。

### 4.4 HarnessToolCallingAgent 分派

`process_tool_calls` 的实现应保持尽量薄：

```text
validate chat_message.tool_calls and unique IDs
for each call in declared order:
    yield public ToolCall
    if final_answer:
        use native final-answer execution
    elif visible GovernedTool:
        set active call context and invoke through the public tool path
    else:
        submit candidate directly to Gate for tool-not-found audit/denial
    convert result to bounded ToolOutput with original ID
    if WAITING/UNKNOWN:
        signal Harness to stop; do not run later calls
update memory_step tool_calls and observations in declared order
```

要点：

- `max_tool_threads=1`，不在 P0 打开只读并发；
- 不复制 ChatMessage 生成、ReAct 提示、Model.generate 或最终答案解析；
- 不覆盖 `_run_stream`、`_step_stream` 或 `_finalize_step`；
- 对同回合重复 ID 在第一次调用前失败关闭；
- 不吞掉 Gate 拒绝；拒绝继续以 observation 返回模型；
- 暂停/未知使用 Harness 能识别的显式信号，不伪装成普通成功文本；
- 测试 ToolOutput ID、顺序和 memory observations 逐项一致。

### 4.5 FakeScriptedModel

`tests/fake_model.py` 首先按公开类型实现：

- 连续脚本式 `ChatMessage` 响应；
- 单/多工具调用、最终回答、空调用错误、模型异常；
- 每回合 `TokenUsage`；
- 记录收到的 message list 和工具 schema；
- 脚本耗尽显式失败；
- 可断言 `final_answer` 是唯一未包装工具；
- 不接触网络、真实凭据或生产状态目录。

测试 helper 应让场景写成稳定脚本，例如“读文件 -> 写文件 -> 最终回答”，而不是在测试中直接
调用 Gate 伪造 Agent 已完成。

### 4.6 新建 Run 编排

```text
Harness.run(START)
  -> load config
  -> assert smolagents version/contracts
  -> construct Sandbox; capture startup WorkspaceBaseline
  -> initialize SQLite
  -> create Run/lease/RUN_STARTED
  -> build Registry/Policy/Gate/Completion/Checkpoint
  -> validate allowed tools and assemble agent role permissions
  -> persist startup baseline via approved Q02 design
  -> NEW -> RUNNING
  -> [test_repair] capture baseline before Agent model/tool calls
  -> create RUN_STARTED safety checkpoint
  -> _drive_agent
```

失败关闭点：

- 配置/版本/工具装配失败前不得创建 Agent；
- SQLite/Run/租约失败不得访问仓库副作用；
- 基线或初始 Checkpoint 写失败不得启动模型；
- 测试修复基线失败不得让 Agent 开始修复；
- 已创建 Run 无法完成安全启动时写入明确的 STOPPED/BLOCKED/FAILED 事实，若状态设施也损坏则
  抛错，不伪造安全。

### 4.7 流式驱动与步骤

Harness 通过公开 generator 逐项消费：

- `ToolCall`：确保当前 Step 已持久化，向 adapter 提供 `step_id`/Run context；
- `ToolOutput`：读取结构化 observation，按结果创建工具边界 Checkpoint；
- `ActionStep`：写有界摘要、结束 Step、记录 step token usage、检查预算；
- `FinalAnswerStep`：只用于确认 Agent 自然结束，不能代替 final ToolOutput/Completion；
- 其他公开 planning/delta 事件：P0 不启用 planning；遇到未声明事件类型时失败关闭。

禁止把完整 `model_input_messages`、prompt、`raw` 响应或工具大输出写入 State/Audit。摘要截断
必须有稳定标记并先脱敏。

### 4.8 预算与停止

预算更新来源：

| 类别 | source kind | source ID |
| --- | --- | --- |
| Step | `STEP` | `step_id` |
| Tool call | `TOOL_CALL` | `tool_call_id` |
| Token/cost | `HARNESS` | 稳定 Agent 模型调用 ID |
| Wall time | `HARNESS` | 启动/恢复阶段稳定 ID |

规则：

- Gate 仍执行调用前预算检查；
- Harness 在每一步完成后检查所有预算；
- 达到上限时不请求下一模型回合或下一工具；
- 通过公开 interrupt/停止流机制收口，紧急路径可关闭当前 generator，但只在已持久化安全结论后；
- 预算停止不调用 Completion；
- 历史 usage 读取与本次增量使用同一事务/幂等键，resume 不重复扣费；
- 墙钟应按持久化 started_at 与 UTC 时间计算，不以进程内累计唯一事实；
- Q01 确认前不实现 token/cost 映射。

### 4.9 Checkpoint 设计

Harness 需要生成以下边界：

| 触发点 | boundary kind | boundary_ref |
| --- | --- | --- |
| Run 启动完成 | `RUN_STARTED` | `run-start` |
| Tool succeeded | `TOOL_SUCCEEDED` | `tool_call_id` |
| Tool failed | `TOOL_FAILED` | `tool_call_id` |
| Tool timed out | `TOOL_TIMED_OUT` | `tool_call_id` |
| Tool unknown | `TOOL_UNKNOWN_RESULT` | `tool_call_id` |
| ActionStep 完成 | `STEP_COMPLETED` | `step_id` |
| 审批请求 | `APPROVAL_REQUESTED` | `approval_id` |
| 审批恢复 | `APPROVAL_SETTLED` | `approval_id` |
| Completion 已写 | `COMPLETION_RECORDED` | `run_id` |

`RUN_STARTED` 已作为稳定枚举加入；它不依赖 step/tool/approval，只允许在 `RUNNING` Run 上创建。

Checkpoint 写入先于继续 Agent 的下一步。若工具结果已确定但 Checkpoint 写失败，停止后续自动
执行并保留可诊断事实；恢复时依赖 WP-07 的 W6 规则，不在 Harness 中私自定义归因。

### 4.10 恢复编排

```text
Harness.run(RESUME)
  -> reload config and verify fingerprint
  -> reopen SQLite and integrity/audit checks
  -> acquire lease
  -> CheckpointService.prepare_recovery
  -> switch recovery action
  -> [WAIT/MANUAL/TERMINAL] return persisted RunResult
  -> [reenter] Gate.reenter_prepared(...)
  -> create approval-settled checkpoint on success
  -> build continuation context and render bounded prompt
  -> construct a new Agent with current model/tools
  -> continue through the same _drive_agent path
```

约束：

- 每次 resume 都可能是不同模型对象或进程；
- 旧 Agent memory 不参与恢复；
- 不把 continuation context 中的 summary 当作授权或验收证据；
- `REENTER_GATE` 结果仍可能 deny/wait/unknown，按 Gate 事实收口；
- 已完成写入再次提案时返回 `REPLAY_SUPPRESSED` 和原证据；
- 恢复后 Step ordinal、tool call 数和 token/cost 从 StateStore 继续；
- 并发 resume 通过 lease 和 expected revision 互斥，失败方不执行模型；
- `ALREADY_TERMINAL` 只读返回，不追加新的 Completion 或执行 Checkpoint。

### 4.11 正常结束与 Completion

```text
final ToolOutput
  -> persist candidate_final_answer via new StateStore method
  -> complete/finalize current Step
  -> create STEP_COMPLETED checkpoint
  -> RUNNING -> VERIFYING
  -> choose Completion entry from validated config
  -> call WP-08/WP-09
  -> persist completion (Completion itself)
  -> create COMPLETION_RECORDED checkpoint
  -> VERIFYING -> COMPLETED
  -> return RunResult
```

异常分类：

- Completion 正常返回 `REJECTED/UNVERIFIABLE`：Run 可 `COMPLETED`，保持该 acceptance；
- Completion 执行基础设施失败：`FAILED/INTERNAL_ERROR` 或 BLOCKED，按失败关闭语义；
- Final answer 写入失败：不进入 VERIFYING；
- Completion 写 State/Audit 失败：不进入 COMPLETED、不返回 accepted；
- 预算停止/未知结果：不进入 VERIFYING。

### 4.12 并发与幂等

- 一个 Run 任一时刻最多一个有效 lease owner；
- Harness 内每个 Run 使用稳定锁，但数据库 lease/revision 是最终事实；
- 同一 `tool_call_id` 不重复预算、Invocation 或 handler；
- 同一 Step/安全边界重复提交需要幂等：返回原事实或稳定冲突；
- Audit 顺序与模型声明顺序一致，不依赖线程完成顺序；
- 审批等待和后续 Agent 不并发；
- Completion 与模型 Agent 不并发；
- 同一进程中断后重新调用 Harness 仍按 RESUME 语义处理，不依赖内存标记。

### 4.13 持久化与审计

优先使用现有 StateStore/Audit 公共 API；新增公共写操作必须：

- 带 expected revision 和 lease；
- 同事务写状态与 Audit；
- 非法状态/冲突失败关闭；
- 使用稳定 source ID 保证幂等；
- 可跨 SQLite reopen 重建。

候选审计事件：

```text
FINAL_ANSWER_RECORDED
AGENT_STEP_RECORDED
HARNESS_BUDGET_STOPPED
HARNESS_RESUME_STARTED
HARNESS_RUN_BLOCKED
```

若现有 `AuditEventType` 已有等价语义，优先复用。不得把同一个事实写两套不同语义事件。

## 5. 施工任务卡

| 任务 | 实现内容 | 对应规格 | 依赖 | 主要文件/测试 |
| --- | --- | --- | --- | --- |
| T00 | 关闭 WP10-Q01 至 Q05，更新决策/设计并冻结 spec | 16 | 无 | `spec.md`、`decisions.md`、`design.md` |
| T01 | 建立 RunRequest/RunResult、错误码和公共导出 | 6.1、14 | T00 | `domain.py`、`__init__.py`、`test_domain.py` |
| T02 | 实现候选最终回答写操作与 Audit | 6.7、13.3 | T01 | `state_store.py`、`audit.py`、`test_state_checkpoint.py` |
| T03 | 实现通用启动基线持久化和 migration v7 | 11.3 | T01 | `storage.py`、`state_store.py`、`workspace_baseline.py`、`test_state_checkpoint.py` |
| T04 | 实现 FakeScriptedModel 和 script helpers | 6.6 | T01 | `tests/fake_model.py`、`test_harness_loop.py` |
| T05 | 实现 GovernedTool schema 包装和无 handler 隔离 | 6.4 | T01 | `agent_adapter.py`、`test_tools.py` |
| T06 | 实现原始 ID/顺序分派和暂停控制 | 6.5、8 | T05 | `agent_adapter.py`、`test_harness_loop.py` |
| T07 | 实现 Harness START 装配与首次 Run | 6.2、9.1 | T02 至 T06 | `harness.py`、`test_harness_loop.py` |
| T08 | 实现流式 Step、Checkpoint 和预算收口 | 9、10、11 | T07 | `harness.py`、`test_state_checkpoint.py` |
| T09 | 实现 RESUME 动作和 continuation Agent | 11.2 | T08 | `harness.py`、`test_harness_loop.py`、`test_state_checkpoint.py` |
| T10 | 接入 WP-08/WP-09 Completion 与 Run 终态 | 12 | T09 | `harness.py`、`test_completion.py` |
| T11 | 故障、并发、安全和审计全矩阵 | 15 | T10 | 指定测试文件 |
| T12 | 运行全量验收并生成 acceptance-report 和索引状态 | 完成定义 | T11 | `acceptance-report.md`、索引 |

### 5.1 T00 决策冻结

已完成。WP10-Q01 至 Q05 已按 D-73 至 D-77 冻结，规格为 v1.0.0 / frozen。

完成条件：

1. 需求方确认 Q01 至 Q05；
2. 每条结论登记 `D-73` 及后续决策 ID；
3. `design.md` 的 RunRequest、基线持久化、预算口径和 adapter 行为同步；
4. `spec.md` 升版并标记 `frozen`；
5. 依赖工作包没有未解决的接口变化；
6. 本计划升版并标记 `approved`。

### 5.2 T01 契约

先写失败测试：

- START/RESUME 非法组合；
- extra field、错误枚举和错误 ID；
- RunResult 序列化/反序列化稳定；
- smolagents 错误码存在且脱敏。

### 5.3 T02 至 T03 持久化

T02：

- 只在 RUNNING 写候选回答；
- 相同文本幂等，不同文本冲突；
- State/Audit 故障回滚；
- 超限/脱敏。

T03：

- migration 只追加，旧数据升级保留；
- 启动基线只写一次并可跨 reopen 读取；
- 工作区/config/run 不匹配失败；
- 不把 handler、lease token 或完整文件内容存入基线。

### 5.4 T04 Fake Model

- 正常 tool call 和 final answer；
- 一次多调用、声明顺序、重复 ID；
- token usage、脚本耗尽、模型异常；
- 消息/schema 记录；
- 无网络、无真实 Provider、无敏感环境依赖。

### 5.5 T05 至 T06 Adapter

T05：

- 每个 visible ToolSpec 生成一个 GovernedTool；
- 工具 schema 与 Task `allowed_tools` 一致；
- wrapper/handler 隔离；
- 无 adapter context 失败关闭。

T06：

- 原始 ID、顺序、ToolOutput 和 memory observation；
- 未知工具进入 Gate；
- schema/路径/命令拒绝零 handler；
- 审批/未知后不执行后续调用；
- `final_answer` 仍由原生 Agent 执行。

### 5.6 T07 至 T08 Harness 主循环

先测试装配失败零副作用，再测试：

- 正常读工具 -> final answer -> VERIFYING -> Completion；
- 未审批写 -> WAITING_APPROVAL；
- 预算停止 -> STOPPED/NOT_EVALUATED；
- 模型异常 -> FAILED；
- Step、usage、Checkpoint 和 Audit 关联；
- Step/Checkpoint/Audit 故障失败关闭。

### 5.7 T09 恢复

- PREPARED 重新过 Gate；
- CLAIMED/PREPARED resume claim；
- STARTED 无结果 -> UNKNOWN_RESULT；
- 工作区/config/审批/审计变化 -> BLOCKED/MANUAL_REVIEW；
- ALREADY_TERMINAL 只读；
- 新进程/新对象不依赖旧 memory；
- 计数和预算不重置；
- 并发 resume 单一 owner。

### 5.8 T10 Completion

- 代码理解真实闭环；
- 测试修复真实闭环；
- H1 无交付物；
- H2 测试后源码变化；
- Completion 固件失败；
- 预算停止不调用 Completion；
- final answer 只是 candidate；
- `COMPLETED` 与 `ACCEPTED` 分离。

### 5.9 T11 至 T12 加固与验收

T11 完成 WP10-AC 全矩阵，重点包含故障注入、并发和安全负例。T12 运行目标测试、全量测试、
Ruff，生成 `acceptance-report.md`，记录实际环境、测试数量、失败/skip、场景映射、限制和
是否可进入 WP-11。报告 `accepted` 前不得标记 WP-10 `DONE`。

## 6. 测试计划

### 6.1 单元测试

| 文件/主题 | 关键断言 |
| --- | --- |
| `test_domain.py` | RunMode/Request/Result 严格字段和序列化 |
| `fake_model.py` 自测或 loop | 脚本耗尽、异常、token usage、调用 ID 原样 |
| `agent_adapter.py` | wrapper schema、ID、顺序、暂停信号、无 handler |
| `harness.py` | START/RESUME 分支、配置重载、Run 终态 |
| `test_state_checkpoint.py` | candidate answer、Step、预算、Checkpoint、跨 reopen |

### 6.2 安全与集成

| 主题 | 关键断言 |
| --- | --- |
| 未知/越权工具 | Gate 记录 deny，handler/PoPEN 为 0 |
| schema/路径/命令注入 | adapter 不放宽，Sandbox/Gate 拒绝 |
| 未审批写 | WAITING_APPROVAL，文件内容和 mtime 不变 |
| 审批恢复 | 新 Agent 过 Gate，handler 恰好一次 |
| 预算 | 不请求下一模型/工具，不进入 Completion |
| 模型自述 | candidate 与 accepted 分离 |
| Completion 故障 | 不伪成功、不返回 accepted |

### 6.3 故障与并发

- SQLite 在 Run/Step/Tool result/Checkpoint/Completion 提交点故障；
- Audit 写失败；
- 进程在 Gate PREPARED、STARTED、结果提交、Checkpoint 前后退出；
- 两个 Harness 并发 resume；
- 同回合重复 call ID 和等价参数；
- `UNKNOWN_RESULT` 不自动重试；
- 配置/工作区/证据/审批变化；
- model/stream 事件缺失或未知类型；
- smolagents 版本/签名契约不匹配。

### 6.4 结束命令

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_domain.py tests/test_state_checkpoint.py -q
python -m pytest tests/test_harness_loop.py -q
python -m pytest tests/test_gate.py tests/test_completion.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

额外可单独运行 `tests/test_tools.py`、`tests/test_audit.py`、`tests/test_sandbox.py` 定位回归，
但不能替代必需测试文件。

## 7. 场景到测试映射

| 规格场景 | 主测试 |
| --- | --- |
| WP10-AC-001 至 004 | `test_harness_loop.py` adapter/tool dispatch |
| WP10-AC-005 至 007 | `test_harness_loop.py` + `test_gate.py` + `test_tools.py` |
| WP10-AC-008 至 009 | `test_harness_loop.py` + `test_state_checkpoint.py` |
| WP10-AC-010 至 012 | `test_harness_loop.py` budget matrix |
| WP10-AC-013 | `test_harness_loop.py` model failure |
| WP10-AC-014 至 018 | `test_state_checkpoint.py` recovery matrix |
| WP10-AC-019 至 021 | `test_completion.py` terminal matrix |
| WP10-AC-022 | `test_state_checkpoint.py` concurrent resume |
| WP10-AC-023 | `test_harness_loop.py` version/signature compatibility |
| WP10-AC-024 | `test_completion.py` test-repair E2E |
| WP10-AC-025 | 全量离线/重复运行 |

## 8. 推荐施工顺序与可回滚增量

```text
T00 decisions/spec
  -> T01 DTO/errors
  -> T02 final-answer persistence
  -> T03 baseline persistence (conditional)
  -> T04 Fake Model
  -> T05 GovernedTool
  -> T06 ID-aware dispatch
  -> T07 START Harness
  -> T08 budget/checkpoint
  -> T09 RESUME
  -> T10 Completion
  -> T11 fault/concurrency
  -> T12 acceptance report
```

可独立提交/回滚的增量：

1. DTO 和错误码；
2. Fake Model 测试工具；
3. 无状态 GovernedTool；
4. Agent 分派 adapter；
5. START 主循环；
6. 预算和 Checkpoint；
7. RESUME；
8. Completion 终态；
9. 故障加固。

每个增量先写失败测试，再实现对应能力；不能等到 T12 才补安全负例。migration 和公共错误码一旦
被其他工作包使用，只能追加修订，不能改写已发布 migration。

## 9. 风险、阻塞与人工处理

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `process_tool_calls` 在 1.26.0 是公开方法但实现细节可能变化 | 丢失 ID 或顺序 | 精确版本锁定、启动签名检查、升级契约测试 |
| Tool schema 无法完整表达 Registry JSON Schema | 模型侧提示略宽松 | Gate 保留原始 schema 最终校验，测试拒绝路径 |
| 使用私有属性快速取 ID | 升级即失效、违反架构边界 | 只使用公开消息和 process 分派，禁止私有字段 |
| 恢复时重建 Agent 丢失对话 | 模型可能重复提案 | continuation context + Gate replay suppression |
| 启动基线未跨进程持久化 | 代码理解 resume 无法安全 Completion | Q02 优先选择通用 migration |
| token/cost 口径含糊 | H3 无法机械验收 | Q01 冻结 Decimal policy |
| adapter 遇到审批后继续模型 | 目标可能被绕过 | 显式暂停信号、主循环停止和 Checkpoint |
| Checkpoint 写失败后继续 | 崩溃窗口不可恢复 | 失败关闭、停止后续自动推进 |
| 模型 final answer 很长或含敏感内容 | 状态超限/泄露 | Q03 + 脱敏 + 有界失败关闭 |
| 并发多调用写顺序不稳定 | 审批重复或副作用冲突 | max_tool_threads=1，声明顺序，Run lease |

阻塞处理：

- 依赖与规格不一致：停止施工，修上游契约或更新 WP-10 规格；
- `UNKNOWN_RESULT`：只保留事实和关联 ID，人工核对；不在同 Run 自动恢复；
- 工作区/config/审批变化：BLOCKED/MANUAL_REVIEW，新 Run 继续；
- smolagents 升级：先补兼容评审和测试，不临时私有适配。

## 10. 回滚计划

- T01 契约：回滚公共 DTO/错误码导出，不影响既有 Gate/Completion；
- T02 final-answer 写操作：回滚 Harness 调用；若已写数据库事实，不回写删除 Audit；
- T03 migration：禁止降级删除列/表；代码回滚后保留新 schema，旧版本必须显式拒绝；
- T04 Fake Model：测试专用，可直接回滚，不入生产；
- T05/T06 adapter：回滚后不得回退到无 Gate 的 Tool 路径；保持功能禁用并失败关闭；
- T07 至 T10：按增量回滚到前一安全终态，已执行副作用和 Audit 只能追加诊断，不伪造未发生；
- T11/T12：保留测试与验收事实，修复后重新运行全量命令。

任何回滚都不得：

- 修改/删除 append-only Audit；
- 把 UNKNOWN_RESULT 改成成功；
- 重放已开始/未知 side effect；
- 改写已执行 migration；
- 通过提示词或人工说明替代验收。

## 11. 下游交接

WP-10 accepted 后向 WP-11 提供：

- 稳定 `RunRequest/RunResult`；
- `WAITING_APPROVAL`、BLOCKED/UNKNOWN 和 ALREADY_TERMINAL 的返回语义；
- 新进程 `RESUME` 公共入口；
- 关联的 approval/checkpoint/audit ID；
- 明确的非成功返回条件；
- CLI 不得重新装配 Gate 或直接执行 handler 的约束。

向 WP-12 提供：

- Agent 原生循环保留和安全适配证据；
- Fake Model 全部 P0 场景；
- 两类任务完整 Run 证据；
- smolagents 版本兼容基线和已接受限制。

WP-11 负责 CLI 参数、退出码、审批命令和 `--wait-for-approval`；WP-12 负责隐藏测试之外的项目级
故障矩阵、样例配置和最终运维文档。

## 12. 实际完成情况

截至 2026-11-07，已完成：

- `RunMode`、`RunRequest`、`RunResult`、WP-10 稳定错误码和 `RUN_STARTED` boundary；
- migration v7、通用 `WorkspaceBaseline` 持久化、候选最终回答原子写入；
- `GovernedTool`、`HarnessToolCallingAgent`、公开接口兼容检查和原始调用 ID 分派；
- `Harness` 的 START、审批等待、RESUME、预算、Checkpoint、Completion 和失败收口；
- `tests/fake_model.py` 和 `tests/test_harness_loop.py` 的代码理解、测试修复、审批、恢复、
  预算、安全拒绝、未知结果和故障注入场景；
- v6 -> v7 migration 和 append-only 基线测试。

当前目标测试与全量测试已通过，但 WP-08/WP-09 的独立验收报告仍未完成，因此 WP-10 保持
`VERIFYING`，不能进入 `DONE`。实际复验结果：

- `python -m pip install -e '.[dev]'`：成功；
- `tests/test_harness_loop.py`：23 passed；
- `python -m pytest -q`：293 passed；
- `python -m ruff check .`：通过；
- `python -m ruff format --check .`：79 files formatted。

基于上述结果的 [acceptance-report.md](acceptance-report.md) 已生成，状态为 `review`，等待上游
WP-08/WP-09 验收与 WP-11 下游接口评审。

后续状态必须由实际代码、测试输出和 `acceptance-report.md` 更新；本文的计划性描述不得被当作
实现完成证据。
