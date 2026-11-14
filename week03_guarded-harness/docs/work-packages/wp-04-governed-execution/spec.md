# WP-04 治理执行内核规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-04 治理执行内核 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 冻结日期 | 2026-11-05 |
| 依赖 | WP-00 v1.3.0、WP-01 v0.4.0、WP-02 v0.2.0、WP-03 v1.0.0 |
| 下游工作包 | WP-05、WP-06、WP-07、WP-08、WP-10、WP-11、WP-12 |
| 对应需求 | `REQ-HARNESS-003` 至 `REQ-HARNESS-005`、`REQ-GATE-001` 至 `REQ-GATE-005`、`REQ-STATE-001` 至 `REQ-STATE-003`、`REQ-AUDIT-001` 至 `REQ-AUDIT-004`、`REQ-CHECKPOINT-003` |
| 对应设计 | `design.md` 第 5.2 至 5.4、6.2、7、9.2、9.3、10、13 节 |
| 已确认决策 | D-01、D-09、D-11、D-14、D-16 至 D-28、D-30、D-32、D-37 至 D-44、D-66、D-67 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌表 revision 11 |

本文是 WP-04 已冻结的行为契约。实现是否完成仍由自动化测试、故障证据、lint、格式检查和
验收报告决定。

文档冲突时按
`requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。

## 2. 目标

WP-04 必须交付一个由程序控制、可审计、失败关闭的唯一工具执行内核：

1. 可信组装代码把工具元数据和 handler 注册到 Registry，模型只提交候选工具名和参数；
2. Policy 根据可信调用上下文、任务可见性、角色权限、模式和风险决定是否允许继续；
3. Gate 在每次执行前按固定顺序重新完成全部检查，不信任模型可见工具列表；
4. 参数经过严格 schema、语义和路径规范化，未声明字段、路径逃逸和命令注入在 handler 前拒绝；
5. 需要审批的调用进入 `WAITING_APPROVAL`，审批前 handler 调用次数必须为 0；
6. 批准后的同一调用重新经过 Gate，审批只能 claim/consume 一次，参数或上下文变化使旧审批失效；
7. 执行意图、`PREPARED`、`STARTED`、确定结果和 `UNKNOWN_RESULT` 可持久化并审计；
8. 重复调用、已完成写操作的 replay、工具调用预算和状态/审计故障均失败关闭；
9. 返回模型的是有界、脱敏、结构化的 `ToolObservation`；
10. 使用测试 handler 独立证明正常路径、拒绝路径和故障路径，不依赖真实工具、Agent 或网络。

WP-04 完成的是治理执行内核，不完成具体文件/进程工具、Agent 适配、Checkpoint、CLI、
Completion 或端到端任务成功判定。

D-66/D-67 在 WP-09 中为唯一执行入口增加一个受控例外：可信 Harness 可通过固定
`invoke_test_verification(BASELINE|FINAL)` 执行任务配置中的验收命令。它不暴露给模型、
不接受任意 argv，并继续使用 Gate 的 Invocation、Evidence、Audit、Sandbox 和租约边界；
WP-04 本身不实现测试报告解析或验收规则。

## 3. 范围与非目标

### 3.1 范围内

- 新增 `src/guarded_harness/registry.py`、`policy.py`、`gate.py`；
- `domain.py` 中 Gate/Registry/Policy 所需的公共类型、枚举和稳定错误码；
- 包根公共导出；
- 工具注册快照、工具 handler 与可信元数据的装配契约；
- 角色权限、任务工具白名单、执行模式、风险和审批策略；
- JSON schema、语义、规范化参数摘要、路径参数和命令 ID 校验；
- Gate 固定检查链与结构化 `ToolObservation`；
- ApprovalStore、StateStore、AuditStore 和 Sandbox 的编排；
- 执行意图、审批等待、审批 claim、handler 开始、确定结果和未知结果生命周期；
- 重复调用、写操作 replay suppression、工具调用预算和并发串行化；
- 输出大小限制、脱敏和异常映射；
- 新增 `tests/test_registry_policy.py`、`tests/test_gate.py`，并按需扩展公共测试 Fixture；
- WP-04 文档、工作包索引和最终验收报告。

### 3.2 非目标

- 不实现 `read_file`、`search_code`、`list_directory`、`git_status`、`git_diff`、
  `write_file`、`apply_patch` 或 `run_test` 等具体工具，这些属于 WP-05/WP-06；
- 不实现 Gate 内部控制面之外的 Completion 验收策略；
- 不实现 Checkpoint 快照、continuation prompt、恢复动作或租约接管，这些属于 WP-07；
- 不构造 `ToolCallingAgent`、不覆写 `smolagents`、不实现 Fake Model 或 Harness 主循环；
- 不实现 CLI、审批列表、approve/reject 命令或 `--wait-for-approval`；
- 不引入真实模型、网络、用户凭据或端到端仓库任务；
- 不把测试 handler 当作真实仓库工具，也不宣称 G3/G4/S1/S3 已完成端到端验收；
- 不通过提示词、`step_callbacks`、模型自述或工具白名单替代执行时授权；
- 不实现进程级强制终止；WP-04 只消费 WP-03/WP-06 提供的结构化进程结果。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌表 revision 11。下表只列 WP-04 直接或部分
负责的条目；整条跨包验收仍由最终归属工作包完成。

| 编号 | 优先级 | 飞书通过标准 | WP-04 责任 | 最终归属 |
| --- | --- | --- | --- | --- |
| G1 | P0 | 参数缺字段或类型错误时返回明确错误，handler 调用次数为 0 | 直接：严格 schema 校验和结构化拒绝 observation | WP-04 / WP-12 |
| G2 | P0 | 连续三次相同工具和参数，第三次在执行前拦截，handler 只执行两次 | 直接：稳定重复键、可信重复上限、拒绝不触达 handler | WP-04 / WP-12 |
| G3 | P0 | 未审批写操作进入 `WAITING_APPROVAL`，目标文件不变 | 直接：审批创建、Run 等待状态和安全返回；文件证据由 WP-06 验证 | WP-04 / WP-06 / WP-11 / WP-12 |
| G4 | P0 | 审批后重复执行同一调用，第一次只执行一次，第二次拒绝且不重复写入 | 直接：绑定复核、一次性 claim/consume、replay suppression | WP-04 / WP-06 / WP-11 / WP-12 |
| S1 | P0 | `artifacts/../../.env` 路径逃逸由 Sandbox 拒绝，handler 不执行 | 部分：Gate 在语义阶段调用 Sandbox，并把拒绝转成结构化 observation | WP-03 / WP-04 / WP-05 / WP-12 |
| S3 | P0 | `run_test` 配置外命令不执行 | 部分：Gate 只允许可信 `command_id`，拒绝 argv/env/cwd 注入 | WP-03 / WP-04 / WP-06 / WP-12 |
| C2 | P0 | 写入完成后结果未保存时恢复，标记 `UNKNOWN_RESULT` 且不自动重放 | 部分：Gate 在 handler 异常或结果不明确时持久化/暴露未知结果 | WP-04 / WP-06 / WP-07 / WP-12 |
| C3 | P0 | 换进程恢复后查找进度，已完成写入不重复且计数不清零 | 部分：Gate replay suppression 和预算幂等；恢复编排归 WP-07 | WP-04 / WP-07 / WP-11 |
| A1 | P0 | 被拒绝调用的审计记录含 rule、decision、actor、toolCallId | 直接：每个 Gate 决定写稳定审计事实 | WP-04 / WP-12 |
| A2 | P1 | 完整任务能关联 Gate、Sandbox、State、Approval 和执行结果 | 部分：WP-04 产生稳定关联字段和 Gate 事实 | WP-12 |
| H3 | P0 | 达到最大轮数或预算时以明确 StopReason 停止，不伪装成功 | 部分：Gate 在工具预算不足时拒绝；Harness 写最终 `BUDGET_EXHAUSTED` | WP-04 / WP-10 / WP-12 |
| T1 | P0 | 所有 P0 场景有确定性测试且可重复通过 | 直接：WP-04 目标测试离线、确定、无关键 skip | WP-12 |

WP-04 不得用本表把 G3/G4/S1/S3/C2/C3/H3/A2 的跨包场景声明为已端到端完成。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `registry.py` | 保存可信 `ToolSpec`、handler 和规范化钩子；注册冲突检查；生成模型可见快照 | 不执行 handler，不判断业务成功 |
| `policy.py` | 可见性、角色权限、执行模式、风险和审批需求策略；不可变执行上下文 | 不校验 schema、不写状态、不调用 handler |
| `gate.py` | 唯一工具执行入口；固定检查链、审批编排、执行意图、结果和 observation | 不实现具体仓库工具，不自行复制 Sandbox 规则 |
| `domain.py` | 冻结 Gate/Registry/Policy 公共类型、枚举和错误码 | 不编排事务，不执行 I/O |
| `state_store.py` | 持久化 Run、Invocation、预算和结果 | 不选择是否执行工具 |
| `approval.py` | 审批绑定、claim、consume 和失效 | 不调用 handler，不创建第二套审批状态机 |
| `audit.py` | append-only Gate 决策和执行事实 | 不替代 StateStore |
| `sandbox.py` | 真实路径和命令边界；返回稳定拒绝/结果码 | 不决定业务授权 |

依赖方向：

```text
domain.py
   ^
   |
registry.py     policy.py
       \          /
        \        /
          gate.py
         /  |  |  \
        v   v  v   v
 state store audit approval sandbox

tests/test_registry_policy.py -> registry.py + policy.py
tests/test_gate.py            -> gate.py + 上游公共接口
```

`gate.py` 只能通过公共接口消费 WP-01/02/03。不得导入私有 `_` 方法、直接读取 SQLite 行、
复制审批状态机或在 handler 内绕过 Sandbox。

## 6. 公共接口

以下接口是 WP-04 冻结契约。

### 6.1 执行上下文与调用请求

```text
ExecutionMode: PLAN | EXECUTE

ToolExecutionContext(
    run_id,
    step_id,
    task_id,
    task_kind,
    mode,
    actor,
    roles,
)

ToolCallRequest(
    tool_call_id,
    tool_name,
    args,
    context: ToolExecutionContext,
    lease_token,
)

Gate.invoke(request: ToolCallRequest) -> ToolObservation
```

约束：

- `tool_call_id`、模型工具名和参数来自 Agent 边界，但仍须严格校验；
- `actor`、`roles`、`mode`、`task_id` 和 `lease_token` 来自可信 Harness 上下文，模型不能注入；
- `lease_token` 只存在于 Gate 请求，不传给 handler；
- Gate 从 StateStore 读取 Run，并核对 `run_id`、`task_id`、`task_kind`、租约和配置指纹；
- 同一 Run 的 Gate 调用默认全串行；首期不开放只读并发；
- `lease_token` 只在内存中传给可信 Store，不写审计、不写错误消息、不返回模型。

### 6.2 Registry 与 handler

```text
ToolDefinition(
    spec: ToolSpec,
    handler: ToolHandler,
    normalize_args: Callable[[dict, ToolExecutionContext], dict] | None,
)

ToolRegistry.register(definition: ToolDefinition) -> None
ToolRegistry.get(tool_name: ToolName) -> ToolDefinition
ToolRegistry.snapshot(
    context: ToolExecutionContext,
    policy: PolicyEngine,
) -> tuple[ToolSpec, ...]
```

`ToolDefinition` 是不可变可信装配对象，只允许 Harness/测试装配代码注册。快照只包含模型可安全
看到的 `ToolSpec` 字段，不包含 handler、实现路径、可执行文件路径或内部闭包。

handler 拟采用窄同步协议：

```text
ToolHandler(
    normalized_args: dict[str, JsonValue],
    context: ToolExecutionContext,
) -> HandlerResult

HandlerResult(
    status: SUCCEEDED | FAILED | TIMED_OUT | UNKNOWN_RESULT,
    output: JsonValue | None,
    summary: str,
    error_code: StableErrorCode | None,
    evidence: tuple[EvidenceDraft, ...] = (),
)

EvidenceDraft(
    kind: EvidenceKind,
    path: RelativePath | None,
    command_id: str | None,
    line_start: int | None,
    line_end: int | None,
    content_digest: Sha256Digest,
)
```

- handler 不接收原始参数，只接收经 Gate 规范化的参数；
- handler 不直接写 State/Audit/Approval；证据草稿由 Gate 在结果事务中写入；
- `TIMED_OUT` 只能用于 WP-03 或幂等 handler 已确认进程/操作已停止的情况；
- handler 无法确定副作用是否发生时必须返回 `UNKNOWN_RESULT`，不能猜成功或失败；
- `HandlerResult` 的最终持久化状态仍由 Gate 重新校验风险和幂等性后决定。

### 6.3 Policy

```text
PolicyEngine(
    allowed_tool_names: frozenset[ToolName],
    role_permissions: Mapping[str, frozenset[ToolPermission]],
    task_approval_tools: frozenset[ToolName],
)

PolicyEngine.is_visible(context, tool_name) -> bool
PolicyEngine.check_permission(context, spec) -> PolicyDecision
PolicyEngine.check_risk(context, spec) -> PolicyDecision
PolicyEngine.requires_approval(context, spec) -> bool
```

策略输入必须来自已校验配置或可信代码装配，不从模型参数临时构造。显式任务审批工具可与
`ToolSpec.approval_requirement=REQUIRED` 取并集，不能把 REQUIRED 降级为 NEVER。

### 6.4 ToolObservation

`ToolObservation` 使用已确认的 schema v2：

```text
schema_version
status: ALLOWED | DENIED | WAITING_APPROVAL | FAILED | UNKNOWN_RESULT
code
tool_call_id
phase
message
retryable
approval_id: ApprovalId | null
evidence_ids: tuple[EvidenceId, ...]
truncated: bool
output: JsonValue | null
```

保留现有单数 `evidence_id=None` 作为兼容只读属性，或在 v2 迁移中由 `evidence_ids[0]` 派生；
不得让两个字段表达不同事实。该兼容修订已由 D-38 确认。

### 6.5 Gate 依赖

```text
Gate(
    storage: SQLiteStorage,
    registry: ToolRegistry,
    policy: PolicyEngine,
    sandbox: Sandbox,
    clock: Clock,
    id_factory: IdFactory,
)
```

Gate 可以自行开启 UnitOfWork，但不得在 handler 执行期间持有数据库写事务。

## 7. 固定检查链

Gate 必须按以下顺序处理每个候选调用。前一阶段拒绝后立即返回，不运行后续阶段，不触达 handler。

| 阶段 | 行为 | 失败码示例 |
| --- | --- | --- |
| 1. 可见性 | 校验请求 envelope；工具已注册；在当前任务/Agent 可见 | `GATE_TOOL_NOT_FOUND`、`GATE_TOOL_NOT_VISIBLE` |
| 2. Schema | `additionalProperties=false`；类型、必填、边界和 JSON 值合法 | `GATE_INVALID_ARGUMENT` |
| 3. 语义/规范化 | JSON canonicalization；工具 normalizer；路径参数经 Sandbox；脱敏可恢复性；命令只接受可信 ID；摘要 | `GATE_SEMANTIC_INVALID`、`TOOL_ARGUMENTS_NOT_RECOVERABLE`、Sandbox/Gate 稳定错误 |
| 4. 权限 | 可信角色的权限并集覆盖工具全部 `required_permissions` | `GATE_PERMISSION_DENIED` |
| 5. 模式/风险 | `PLAN` 禁止写/执行；风险和执行模式匹配可信任务策略 | `GATE_MODE_DENIED`、`GATE_RISK_DENIED` |
| 6. 审批 | 缺失审批进入等待；已有审批校验绑定、有效期和一次性语义 | `APPROVAL_REQUIRED`、`APPROVAL_*` |
| 7. 重复 | 规范化工具名 + 参数摘要的重复上限；已完成写 replay | `GATE_REPEATED_CALL`、`GATE_REPLAY_SUPPRESSED` |
| 8. 预算 | 工具调用、步骤、时间、Token/成本仍有余量 | `GATE_BUDGET_EXHAUSTED` |
| 9. 执行意图 | 持久化 PREPARED/预算增量；批准路径原子 claim 并恢复 Run | `GATE_INTENT_WRITE_FAILED`、`STATE_*`、`APPROVAL_*` |
| 10. 开始 | handler 前独立持久化 STARTED 和审计 | `GATE_START_WRITE_FAILED` |
| 11. 执行 | 调用 handler；状态和审计不可写时失败关闭 | `GATE_RESULT_UNKNOWN` 等 |
| 12. 结果 | 写证据、确定结果或未知结果；确定结果消费审批 | `GATE_RESULT_WRITE_FAILED` |

### 7.1 等待审批时的特殊顺序

审批创建必须引用一个已持久化的 PREPARED Invocation。首次发现需要审批时，Gate 在同一个
UnitOfWork 内完成：

```text
create PREPARED invocation
  -> create PENDING approval
  -> Run RUNNING -> WAITING_APPROVAL
  -> Gate/approval audit
  -> commit
  -> return WAITING_APPROVAL
```

该事务不得调用 handler。已有同一 `(run_id, tool_call_id)` 的 PENDING/APPROVED 审批时返回原审批，
不重复创建；Resume 后仍从阶段 1 开始重新校验，再执行阶段 7、8 和批准 claim。

审批创建前的可恢复性检查使用与 StateStore 相同的 Redactor。脱敏输出必须与规范化参数完全一致
且未截断；否则在语义阶段返回 `DENIED/TOOL_ARGUMENTS_NOT_RECOVERABLE`，设置
`retryable=true`，不创建 Invocation 或审批。模型可在同一 Run 内使用新 `tool_call_id` 改写。
该检查不得保存或返回原始敏感值，也不得为了恢复保存未脱敏参数。

## 8. 参数、语义与摘要

### 8.1 Schema

- 工具输入 schema 必须可被标准 JSON Schema 2020-12 校验器解释；
- 根必须是 object，且必须显式拒绝未声明字段；
- 必填字段缺失、类型错误、枚举越界、额外字段、非有限数字和非 JSON 值均拒绝；
- 缺失或无效 schema 属于可信装配错误，Registry 注册失败，不能留到执行时才解释；
- 模型不能通过 `__proto__`、重复键、类型混淆或自定义对象绕过 schema。

运行时依赖固定 `jsonschema>=4.23,<5`，Registry 和 Gate 使用同一 draft 2020-12 validator。

### 8.2 规范参数

```text
validate schema
  -> reject non-JSON / non-finite / excessive depth
  -> canonicalize object keys
  -> apply trusted tool normalizer
  -> Sandbox-check every declared path argument
  -> validate command_id and trusted token shape
  -> produce normalized_args
  -> calculate domain-separated SHA-256(tool_name, normalized_args)
```

路径参数规范化使用 `ToolSpec.path_arguments`：

- `READ` 风险调用 `Sandbox.check_read`；
- `WRITE`/`EXECUTE` 风险调用 `Sandbox.check_write`；命令类工具由 WP-06 handler 复用 Sandbox 执行；
- 规范化参数保存工作区相对 POSIX 路径，避免审批后工作区迁移；
- 输入路径逃逸、敏感路径、禁写路径和符号链接逃逸直接拒绝，handler 调用次数为 0。

`args_digest` 至少绑定工具名和规范化参数，并与 WP-02 的
`canonical_args_digest` 使用相同 canonical JSON 规则。完整 Run、工作区、任务、配置和有效期
绑定继续由 Approval 的 `binding_digest` 负责。

## 9. 审批、重复与预算

### 9.1 审批

- 审批请求的 `requested_by` 等于可信事务 actor；
- 允许审批角色来自任务配置；不存在合格角色时审批不可用并失败关闭；
- 真实 approve/reject 由 WP-11 CLI 完成，只改审批状态，不执行 handler；
- 批准后的执行事务必须在同一 UnitOfWork 内完成 approval claim、
  `WAITING_APPROVAL -> RUNNING` 和必要预算/意图记录；
- claim 成功后单独提交 `PREPARED -> STARTED`，再调用 handler；
- 参数、工具名、Run、任务、工作区、配置指纹或有效期任一变化均拒绝；
- `UNKNOWN_RESULT` 不 consume 审批，审批保持 `CLAIMED`；
- 确定 `SUCCEEDED/FAILED/TIMED_OUT` 在结果事务中 consume 审批；consume 失败时不得返回成功。

### 9.2 重复与 replay

重复键为：

```text
(run_id, normalized_tool_name, canonical_args_digest)
```

拟采用以下默认策略：

| 工具类别 | 已进入 handler 的执行上限 | 超限结果 |
| --- | --- | --- |
| 只读工具 | 2 | `GATE_REPEATED_CALL`，handler 不再调用 |
| 写/执行工具 | 1 | `GATE_REPLAY_SUPPRESSED`，返回原证据摘要 |

只读上限使飞书 G2 的第三次调用在执行前被拦截且前两次 handler 恰好执行。具体上限和工具覆盖方式
见 D-40。

replay suppression 规则：

- 已成功完成的非幂等 Invocation 再次以同一 `tool_call_id` 提交时，不重新审批、不重新开始；
- 返回 `DENIED/GATE_REPLAY_SUPPRESSED`，`evidence_ids` 指向原 Invocation 的证据；
- 相同 `tool_call_id` 携带不同工具名或参数返回 `GATE_TOOL_CALL_ID_CONFLICT`；
- 仅 `PREPARED` 且从未 STARTED 的调用可按 WP-07 恢复规则重新过 Gate；
- `STARTED`、`UNKNOWN_RESULT` 永不自动重放。

### 9.3 预算

- 阶段 8 读取持久化 usage 和 Run budget，不采用内存计数作为唯一事实；
- 每次真正进入执行意图的调用使用 `BudgetCategory.TOOL_CALLS` 和
  `source_id=tool_call_id` 幂等记录一次增量；
- 预算已耗尽时返回 `GATE_BUDGET_EXHAUSTED`，handler 不执行；
- Gate 不伪造 Run 终态；Harness 根据该结果停止 Run 并写
  `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`；
- 预算写入失败时不执行 handler，不能先执行再补账。

## 10. 执行生命周期与事务边界

### 10.1 无审批路径

```text
preflight phases 1..8
  -> transaction A:
       create PREPARED invocation
       record TOOL_CALLS budget delta once
       append Gate ALLOW decision
  -> transaction B:
       transition PREPARED -> STARTED
       append TOOL_STARTED
  -> call handler outside any DB transaction
  -> transaction C:
       add immutable evidence drafts
       transition STARTED -> SUCCEEDED/FAILED/TIMED_OUT/UNKNOWN_RESULT
       append result audit
  -> return bounded observation
```

### 10.2 审批路径

```text
preflight phases 1..5
  -> transaction A:
       create PREPARED invocation
       create approval
       transition RUNNING -> WAITING_APPROVAL
       append Gate/approval audit
  -> return WAITING_APPROVAL without handler

resume with APPROVED approval
  -> re-run phases 1..8
  -> transaction B:
       claim approval
       transition WAITING_APPROVAL -> RUNNING
       record budget delta once
       append Gate ALLOW decision
  -> transaction C:
       transition PREPARED -> STARTED
       append TOOL_STARTED
  -> call handler outside any DB transaction
  -> transaction D:
       add evidence
       transition to deterministic result or UNKNOWN_RESULT
       consume approval only for deterministic result
       append result audit
```

### 10.3 状态转换

允许的执行状态链：

```text
PREPARED -> STARTED -> SUCCEEDED
                    -> FAILED
                    -> TIMED_OUT       # 仅幂等且已确认停止
                    -> UNKNOWN_RESULT
```

禁止：

- 从任意终态再次进入 `STARTED`；
- 在 STARTED 写失败时调用 handler；
- 对非幂等超时写 `TIMED_OUT`；
- 对 `UNKNOWN_RESULT` 自动重试或 consume 审批；
- 在结果事务失败后返回成功；
- 将已确认成功的写操作伪装成新的执行授权。

### 10.4 失败窗口

| 窗口 | 持久化事实 | Gate 行为 |
| --- | --- | --- |
| 意图事务前失败 | 无本次 Invocation | 不触达 handler，返回结构化拒绝/失败 |
| PREPARED 已提交、STARTED 前失败 | 明确未开始 | 不调用 handler；由 WP-07 决定恢复 |
| STARTED 已提交、handler 前失败 | 保守视为可能已开始 | 标记 `UNKNOWN_RESULT`，不自动重放 |
| handler 已返回、结果事务未提交 | 持久化仍为 STARTED | 返回未知结果；恢复不得按成功处理 |
| 结果事务已提交 | 确定结果 | 使用持久化结果，不重复执行 |

## 11. 输出、脱敏与观测

- handler 输出在写 State/Audit 和返回模型前统一脱敏；
- `ToolSpec.output_limit_bytes` 是单次工具输出硬上限，装配时不得超过平台全局上限；
- 超限时只保留确定性预览，并设置 `truncated=true`；不得返回无限字符串或伪造完整结果；
- `output_kind=NONE` 要求 `output=null`；JSON/TEXT 输出必须经过对应类型校验；
- `message`、summary、异常和审计字段均有字符/节点上限；
- 错误消息包含可修正方向和拒绝阶段，但不包含 API Key、令牌、密码、完整环境变量或未脱敏参数；
- `ToolObservation.code` 始终是稳定字符串，`retryable` 默认 false；
- 模型只能看到结构化 observation，不看到 handler、SQL、内部异常栈或审批 token。

## 12. 审计与并发

### 12.1 审计

每个候选调用至少产生一条 `GATE_DECISION`：

- Run、工具调用、工具名、actor；
- `phase`：拒绝阶段；ALLOW 使用 `EXECUTION`，WAITING 使用 `APPROVAL`；
- `decision=ALLOW/DENY`；
- 稳定的 `rule_id`；
- 脱敏后的参数摘要和结果摘要；
- 适用时关联 step、approval 和 evidence。

执行阶段继续由 StateStore/ApprovalStore 自动产生 `TOOL_PREPARED`、`TOOL_STARTED`、
`TOOL_SUCCEEDED/FAILED/TIMED_OUT/UNKNOWN_RESULT`、审批和预算事件。

审计失败语义：

- ALLOW 审计或意图写入失败：不调用 handler，返回失败关闭 observation；
- DENY 审计写入失败：handler 仍不得执行；返回 `AUDIT_WRITE_FAILED` 类失败关闭结果；
- handler 已返回但结果/审计无法写入：不得返回成功，保留 STARTED 事实供恢复识别未知；
- Gate 不吞掉审计异常，也不重试到可能产生重复决策事件。

### 12.2 并发

- 本次 WP-04 所有 Gate 调用默认串行；
- Gate 使用 Run 级进程内互斥锁保护“preflight -> intent -> STARTED”以及“handler -> result”的决策顺序；
- 跨进程安全依赖 Run lease、SQLite `BEGIN IMMEDIATE`、条件更新和审批 revision；
- 进程内锁不能替代租约和数据库约束，租约也不能替代同一进程内锁；
- 审批只能被一个执行意图成功 claim；并发第二方得到稳定审批冲突；
- 审计顺序由事务提交顺序决定，同一 Run 的 Gate 决策不得出现不确定并行顺序。

## 13. 错误契约

WP-04 至少追加以下稳定错误码：

```text
GATE_TOOL_NOT_FOUND
GATE_TOOL_NOT_VISIBLE
GATE_INVALID_ARGUMENT
TOOL_ARGUMENTS_NOT_RECOVERABLE
GATE_SEMANTIC_INVALID
GATE_PERMISSION_DENIED
GATE_RISK_DENIED
GATE_MODE_DENIED
GATE_REPEATED_CALL
GATE_REPLAY_SUPPRESSED
GATE_TOOL_CALL_ID_CONFLICT
GATE_BUDGET_EXHAUSTED
GATE_INTENT_WRITE_FAILED
GATE_START_WRITE_FAILED
GATE_HANDLER_FAILED
GATE_HANDLER_TIMEOUT
GATE_RESULT_UNKNOWN
GATE_RESULT_INVALID
GATE_RESULT_WRITE_FAILED
GATE_OUTPUT_INVALID
```

既有 `APPROVAL_*`、`STATE_*`、`AUDIT_*`、`SANDBOX_*` 错误继续直接使用，不包装成含义更模糊的
Gate 通用错误。错误码只追加，不复用为不同含义。

错误映射：

| 来源 | Gate 行为 |
| --- | --- |
| schema/语义/路径/权限/风险 | DENIED，handler=0 |
| 审批缺失 | WAITING_APPROVAL，handler=0 |
| 审批过期/篡改/重放 | DENIED，handler=0 |
| 重复/replay/budget | DENIED，handler=0 |
| handler 明确失败 | FAILED，按策略决定是否 retryable |
| handler 超时但已确认停止 | TIMED_OUT；非幂等映射 UNKNOWN_RESULT |
| handler/结果无法确认 | UNKNOWN_RESULT，不重试、不自动恢复 |
| State/Audit 不可写 | 不执行或返回失败；不得成功 |

## 14. 不变量与验收场景

### 14.1 不变量

| ID | 不变量 |
| --- | --- |
| WP04-INV-001 | 所有候选工具调用只能经 Gate 进入 handler。 |
| WP04-INV-002 | 拒绝发生在 handler、真实 I/O 和进程启动之前。 |
| WP04-INV-003 | 每次执行重新解析工具、参数、权限、风险、审批、重复和预算，不信任快照。 |
| WP04-INV-004 | Registry 快照不暴露 handler 或内部可执行路径。 |
| WP04-INV-005 | 前置拒绝不能被后续 allow 规则覆盖。 |
| WP04-INV-006 | 审批绑定不可变；approval 只可成功 claim/consume 一次。 |
| WP04-INV-007 | `UNKNOWN_RESULT` 不自动重试、不 consume 审批、不判成功。 |
| WP04-INV-008 | 已确认写操作不重复执行；replay 返回原证据而不是新结果。 |
| WP04-INV-009 | State、Audit 和 Approval 的关联 ID 可重建一次调用链。 |
| WP04-INV-010 | Gate 不在 handler 执行期间持有 SQLite 写事务。 |
| WP04-INV-011 | 错误和 observation 有界、脱敏且不伪造成功。 |
| WP04-INV-012 | 同一 Run 的写、进程和审批调用默认串行。 |

### 14.2 验收场景

所有场景必须映射到自动化测试；关键 P0 场景不得用 skip/xfail 替代。

| ID | 场景 | 通过判据 |
| --- | --- | --- |
| WP04-AC-001 | Registry 正常注册与快照 | 快照包含安全元数据，不含 handler 和内部路径 |
| WP04-AC-002 | Registry 冲突 | 重名、重绑定、非法 schema/handler 失败关闭 |
| WP04-AC-003 | 工具存在但不可见 | `GATE_TOOL_NOT_VISIBLE`，handler=0 |
| WP04-AC-004 | 工具不存在 | `GATE_TOOL_NOT_FOUND`，handler=0 |
| WP04-AC-005 | G1 参数缺失/类型错误/额外字段 | 稳定错误，handler=0 |
| WP04-AC-006 | 语义非法与空 patch/路径参数 | 在 handler 前拒绝 |
| WP04-AC-007 | S1 路径逃逸 | Sandbox 错误映射为结构化 DENIED，handler=0，目标未读 |
| WP04-AC-008 | S3 命令注入 | argv/env/cwd/shell 片段被拒绝，handler/进程=0 |
| WP04-AC-009 | 角色权限不足 | 权限阶段拒绝，handler=0 |
| WP04-AC-010 | plan 模式写/执行 | `GATE_MODE_DENIED`，handler=0 |
| WP04-AC-011 | 风险不允许 | `GATE_RISK_DENIED`，handler=0 |
| WP04-AC-012 | G3 首次未审批写 | Run=`WAITING_APPROVAL`，PENDING 审批存在，handler=0 |
| WP04-AC-013 | 审批参数/上下文篡改 | 旧审批不执行，绑定错误稳定可见 |
| WP04-AC-014 | G4 审批后重放 | 第一次 handler=1；第二次拒绝且仍为 1 |
| WP04-AC-015 | G2 相同只读调用三次 | 前两次 handler=2，第三次拒绝，总计仍为 2 |
| WP04-AC-016 | 已完成写 replay suppression | 不调用 handler，返回零个新写入和原证据 |
| WP04-AC-017 | 同 tool_call_id 不同参数 | `GATE_TOOL_CALL_ID_CONFLICT`，handler=0 |
| WP04-AC-018 | H3 工具预算耗尽 | Gate 拒绝，handler=0，结果可供 Harness 停止 |
| WP04-AC-019 | PREPARED/STARTED 顺序 | handler 被调用前 STARTED 已持久化并可从重开连接读取 |
| WP04-AC-020 | 成功/确定失败生命周期 | 状态转换、结果摘要和审计一致 |
| WP04-AC-021 | 非幂等超时/异常 | 进入 `UNKNOWN_RESULT`，handler 不自动重试，审批不 consume |
| WP04-AC-022 | 结果事务失败 | 不返回成功；STARTED 事实保留 |
| WP04-AC-023 | Gate ALLOW 审计/意图失败 | handler=0，返回失败关闭结果 |
| WP04-AC-024 | Gate DENY 审计失败 | handler=0，不降级为允许 |
| WP04-AC-025 | A1 拒绝审计字段 | 含 rule、decision、actor、tool_call_id、phase |
| WP04-AC-026 | 输出有界与脱敏 | 超限显式截断；密钥不出现在 observation/State/Audit |
| WP04-AC-027 | 同 Run 并发调用 | 写/审批串行；只有一个 claim/execution；审计顺序稳定 |
| WP04-AC-028 | handler 返回非法结果 | `GATE_RESULT_INVALID`，不伪造成功 |
| WP04-AC-029 | 模型可见白名单不等于执行授权 | 快照可见后仍重新做权限/风险/审批检查 |
| WP04-AC-030 | 离线确定性 | fake handler/clock/fault，无真实模型、网络和用户凭据 |
| WP04-AC-031 | 参数经脱敏后不可原样恢复 | 审批和 Invocation 为 0、handler=0，返回可重试 |

## 15. 已确认决策

2026-11-05 经需求方确认，以下决策进入 `decisions.md`，不再是待确认项：

| 决策 | 结论 |
| --- | --- |
| D-37 | WP-01 提供 `get_run_revision`、`get_invocation_revision` 和 `verify_lease` 公共读取接口。 |
| D-38 | `ToolObservation` 升级 schema v2，增加 `approval_id`、`evidence_ids`、`truncated`。 |
| D-39 | 引入 `jsonschema>=4.23,<5`，统一使用 draft 2020-12。 |
| D-40 | 只读默认执行上限 2，写/执行默认 1；等价已完成写先 replay suppression。 |
| D-41 | WP-04 使用协作 deadline；硬终止由 Sandbox 的进程边界负责。 |
| D-42 | WP-04 不自动重试 handler，重试资格留给 WP-06 重新过 Gate。 |
| D-43 | 角色权限由可信代码装配的 PolicyEngine 提供，YAML 接线属于 WP-10。 |
| D-44 | 前置拒绝只写 Gate 审计，不创建 ToolInvocation。 |

## 16. 兼容与迁移

- `ToolSpec` 现有字段足以描述首期工具；WP-04 原则上不修改其持久化形状；
- 新增错误码和 Gate 类型使用追加式兼容，既有错误码不得改义；
- WP-04 不新增 SQLite migration；WP-01 v0.4.0 只新增只读接口；
- `ToolObservation` v2 提供旧 `evidence_id` 兼容派生和 round-trip 测试；
- `smolagents` 适配仍由 WP-10 负责；WP-04 不读取其私有属性；
- 下游只能依赖冻结后的公共接口，不得复制 Gate 检查逻辑或直接调用 Registry handler。

## 17. 完成定义

WP-04 只有在以下条件全部满足后才能标记 `DONE`：

1. 第 15 节决策已关闭，规格 v1.0.0 已冻结；
2. Registry、Policy 和 Gate 正常、拒绝、故障测试全部通过；
3. G1、G2、G3、G4 的包内 handler 调用次数证据成立；
4. S1/S3 的 Gate 阶段拒绝发生在 handler 前，并保留 wp05/wp06 后续真实工具责任；
5. 审批参数篡改、过期、重放、跨 Run 和并发 claim 全部拒绝；
6. PREPARED、STARTED、SUCCEEDED、FAILED、TIMED_OUT、UNKNOWN_RESULT 的合法/非法转换可验证；
7. State/Audit/Approval 不可写入时不执行新副作用，结果不伪装成功；
8. replay suppression 不重复写并返回原证据；
9. 目标 pytest、全量 pytest、Ruff lint、Ruff format check 和 `pip check` 通过；
10. 文档、配置、公共契约和实现一致；
11. `acceptance-report.md` 基于实际证据标记 `accepted`。
