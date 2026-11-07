# WP-11 CLI 与人工恢复工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-11 CLI 与人工恢复 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 冻结日期 | 2026-11-07 |
| 依赖 | WP-07 accepted；WP-10 accepted |
| 当前依赖状态 | WP-07 已 accepted；WP-10 为 VERIFYING，验收报告仍为 review |
| 下游工作包 | WP-12 |
| 对应需求 | `REQ-HARNESS-001/002/005`、`REQ-GATE-004`、`REQ-STATE-002`、`REQ-CHECKPOINT-002/003/004`、`REQ-AUDIT-001/002/003`、`REQ-COMPLETE-001/002/005` |
| 对应设计 | `design.md` 6.1、6.3、6.4、9.3、12.4、13、14 |
| 相关决策 | D-01、D-08、D-09、D-19、D-21、D-23、D-25、D-27、D-28、D-53、D-54 |
| 飞书输入 | `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |
| 原文件任务 | P5-04、P5-05、P5-07；P5-06 的跨进程部分 |

本文定义 WP-11 必须表现出的外部行为、CLI 契约和验收要求。需求方已明确要求在 WP-10 验收仍为
`review` 时并行开始施工；实现和测试结果可以记录，但该状态不满足 WP-10 accepted 依赖，本包在
上游闭环前不能标记 `DONE`。

文档冲突时按 `requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 处理。
WP11-Q01 至 WP11-Q06 已按 D-78 至 D-83 关闭，本文为冻结规格。

## 2. 目标

WP-11 必须在已有 Harness、Approval、State、Audit 和 Checkpoint 公共契约之上，提供一个可操作、
可脚本化且不能绕过治理链的本地入口：

1. 通过同一 `main` 提供 console script 和 `python -m guarded_harness`；
2. `run` 只调用 Harness，由 Harness 装配模型、Gate、Sandbox、State、Checkpoint、Audit 和
   Completion；
3. 默认在 `WAITING_APPROVAL` 安全退出，进程退出后目标文件和其他仓库副作用均未发生；
4. `approvals approve/reject` 只改变持久化授权及其规定的 Run 终态，不执行 handler；
5. `resume` 在新进程中重建 Harness 和 Agent，重新校验配置、工作区、调用参数和审批；
6. 可选等待模式只改变同一命令是否轮询审批，不改变审批绑定、一次性消费和失败关闭语义；
7. `approvals` 和 `inspect` 提供有界只读查询，不启动模型、Gate 业务调用、handler 或子进程；
8. 所有结果都有稳定退出码；只有程序化 Completion 产生 `ACCEPTED` 才是成功；
9. `STOPPED`、`BLOCKED`、`FAILED`、`REJECTED` 和 `UNVERIFIABLE` 可由退出码和结构化输出区分；
10. stdout/stderr 不泄露密钥、租约 token、完整模型消息、完整文件内容或敏感环境变量；
11. 两类任务都可由 CLI 驱动 Fake Model 完成审批、跨进程恢复和最终验收；
12. CLI 测试离线、确定、可重复，不依赖真实 Provider、网络、用户凭据或本机绝对路径。

## 3. 范围与非目标

### 3.1 范围内

- 新增 `src/guarded_harness/cli.py` 和 `src/guarded_harness/__main__.py`；
- `pyproject.toml` 注册 console script；
- 提供 `run`、`resume`、`approvals list/approve/reject`、`inspect`；
- 提供受控、只读的审批查询和 Run 事实查询服务；
- 提供稳定退出码、人类可读输出、`--json` 输出和稳定错误码；
- `run/resume` 的模型受控注入边界；
- `run/resume` 的 `--wait-for-approval` 轮询语义；
- 新增 `tests/test_cli.py`；
- 扩展 `tests/test_harness_loop.py` 和 `tests/test_state_checkpoint.py` 的跨进程 CLI 恢复覆盖；
- 同步 `docs/decisions.md`、`docs/design.md`、工作包索引和后续验收报告。

### 3.2 非目标

- 不实现真实模型 Provider、API Key 管理、网络 smoke test 或模型质量评估；
- 不实现 Web、HTTP 审批服务、TUI 或多用户远程控制面；
- 不在 CLI 中重新装配 Gate、Sandbox、Policy、Completion 或 Checkpoint；
- 不允许 CLI 直接调用 handler、绕过 Gate 或消费审批后自行执行工具；
- 不实现审批后的自动执行命令；审批和执行必须由 `approve/reject` 与 `resume` 分开完成；
- 不提供 `UNKNOWN_RESULT` 人工改写、强制成功、释放 CLAIMED 审批或同 Run 强制重放入口；
- 不新增跨 Run 自动恢复、分布式租约或 Windows 进程组支持；
- 不修改已发布 migration 或 append-only Audit 事实。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。WP-11 负责其中的本地操作
入口和跨进程可见结果；底层安全判定仍由上游工作包负责，只有 WP-12 能声明项目级闭环完成。

| 编号 | 优先级 | 飞书通过标准 | WP-11 责任 | 实现归属 |
| --- | --- | --- | --- | --- |
| G3 | P0 | 未审批写操作进入 `WAITING_APPROVAL`，目标文件不变 | `run` 返回 `WAITING_APPROVAL` 并以非成功退出码安全退出；`inspect` 可看到审批 ID | WP-02 / WP-04 / WP-10 / WP-11 |
| G4 | P0 | 审批后重复执行同一调用，第一次只执行一次，第二次拒绝 | `approve` 不执行；新进程 `resume` 至多执行一次；再次 `resume` 不重放 | WP-02 / WP-07 / WP-10 / WP-11 |
| C3 | P0 | 正常中断后换进程恢复，进度和计数保留，已完成写入不重复 | `resume` 在新进程重建 Agent，预算、Step 和调用计数不重置 | WP-07 / WP-10 / WP-11 |
| C4 | P0 | 恢复时工作区已变化时停止并等待核对 | `resume` 呈现 `BLOCKED/MANUAL_REVIEW` 原因和关联 ID；`inspect` 不提供强放行入口 | WP-07 / WP-10 / WP-11 |
| A1 | P0 | 被拒绝调用的审计含 rule、decision、actor、toolCallId | `inspect --tool-call-id` 展示有界审计关联，不伪造缺失字段 | WP-01 / WP-04 / WP-11 |
| H1 | P0 | 模型声称完成但无交付物时不能成功 | CLI 只以 `RunResult.acceptance_status=ACCEPTED` 返回成功；`REJECTED` 使用独立退出码 | WP-08 / WP-10 / WP-11 |
| H3 | P0 | 达到轮数或预算时以明确 StopReason 停止 | `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED` 返回独立退出码 | WP-01 / WP-10 / WP-11 |

G1、G2、S1 至 S4、C1/C2、A2、H2、T1/T2 的全量场景关闭仍由 WP-12 统一复核。WP-11 不把
`inspect` 能看到审批、工具和审计 ID 误写成整个项目已经具备可证明的任务完成能力。

## 5. 组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `cli.py` | 严格解析参数，调用 Harness/控制面，映射退出码和输出 | 直接访问 Gate、handler、Sandbox 或 SQL |
| `__main__.py` | 以 `SystemExit(main())` 调用与 console script 相同的 `main` | 独立业务逻辑 |
| `Harness` | `START/RESUME`、模型和 Agent 生命周期、审批等待结果、Completion | CLI 参数解析和人工查询格式 |
| 审批控制服务 | 按审批 ID 定位 Run、列审批、通过 ApprovalStore 执行 approve/reject | handler 执行、恢复和策略判断 |
| 只读查询服务 | 读取 RunSnapshot、Approval、Checkpoint、Audit 的有界投影 | 修改 Run、释放审批、启动模型或进程 |
| `ApprovalStore` | 绑定校验、到期物化、approve/reject 和审计 | CLI 输出、模型选择、恢复 Agent |
| `CheckpointService` | 恢复计划和 continuation context | 新进程 CLI 参数、退出码 |
| WP-08/WP-09 Completion | 程序化验收 | CLI 自行判定成功 |

依赖方向固定为：

```text
argv
  -> cli.py
       -> Harness.run(START|RESUME)
       -> ApprovalControlService
       -> ReadOnlyInspectionService

Harness -> Gate / Checkpoint / Completion / State / Audit
Control services -> ApprovalStore / StateStore / AuditStore
```

禁止：

- CLI 读取 handler 注册表后自行调用工具；
- CLI 直接 SQL 更新 `runs`、`approvals`、`checkpoints` 或 `audit_events`；
- 通过 `step_callbacks`、stdout 或模型最终回答决定退出码；
- 在 `approvals approve` 中附带 `resume`，或在 `inspect` 中提供“继续执行”副作用；
- 使用模型或 CLI 参数覆盖工作区、预算、工具 allowlist、测试命令或身份；
- 以 shell 拼接命令；CLI 自身只解析 argv。

## 6. 公共 CLI 契约

### 6.1 入口身份

WP11-CLI-001：项目必须同时支持以下两个入口，并调用同一个 `main`：

```text
guarded-harness <command> ...
python -m guarded_harness <command> ...
```

`main(argv=None, *, model_factory=None, ...) -> int` 是可直接测试的公共入口，不得在导入模块时
启动模型、数据库迁移或仓库操作。console wrapper 和 `__main__.py` 只负责把返回值转换为进程
退出状态。

`guarded-harness` 是 canonical 名称；`--config PATH` 是全局参数。

### 6.2 配置边界

所有命令都通过显式配置路径定位任务和状态库：

```text
--config PATH
```

CLI 不提供覆盖以下字段的参数：

- `workspace.root`、`require_clean_workspace`、`protect_other_git_changes`；
- `state_dir`；
- `budget`；
- `task.id/kind/description/allowed_tools/paths/acceptance`；
- `approval.required_tools/approver_roles/ttl_seconds/channel`。

配置由现有 `load_config` 严格加载；扩展字段、路径逃逸、缺失文件和非法值失败关闭。审批命令和
`inspect` 也必须加载同一配置，以定位 SQLite 和校验 Run 绑定，不得依赖当前目录猜测状态库。

### 6.3 命令面

命令面固定如下：

```text
guarded-harness --config PATH run [--wait-for-approval SECONDS] [--json]
guarded-harness --config PATH resume RUN_ID [--wait-for-approval SECONDS] [--json]

guarded-harness --config PATH approvals list
    [--run-id RUN_ID] [--status STATUS]... [--limit N] [--json]
guarded-harness --config PATH approvals approve APPROVAL_ID
    --actor ACTOR --role ROLE [--role ROLE]... [--json]
guarded-harness --config PATH approvals reject APPROVAL_ID
    --actor ACTOR --role ROLE [--role ROLE]... [--json]

guarded-harness --config PATH inspect RUN_ID
    [--tool-call-id TOOL_CALL_ID] [--approval-id APPROVAL_ID]
    [--audit-limit N] [--json]
```

约束：

- `run` 不允许传 `run_id`；
- `resume` 必须传完整 `run_id`，不允许携带 task、workspace、tool、budget 或 approval 覆盖；
- `approvals` 不提供 `execute` 子命令；
- `approve/reject` 至少显式提供一个角色，不默认读取环境变量、用户名或配置中的唯一角色；
- `--role` 可重复；角色和 actor 规范化后必须满足 `Approver` 模型和任务 allowlist；
- 审批 actor 不得等于审批的 `requested_by`；
- `--limit` 和 `--audit-limit` 必须有上限，默认和边界在计划中冻结；
- 未声明的命令、flag、多余位置参数一律以 usage error 失败，不得被忽略。

### 6.4 模型注入

`Harness` 要求调用方注入 `smolagents.Model`。P0 不实现真实 Provider，CLI 只支持可信
`model_factory` 注入。

固定安全边界：

```text
ModelFactory = Callable[[Path], smolagents.Model]
main(..., model_factory: ModelFactory | None = None) -> int
```

- 测试和程序化调用可注入可信 `model_factory`；
- CLI 不得从配置、模型参数或环境变量选择任意可执行 Python class/function；
- 每次进入 `Harness.run()` 或等待审批后的再次 `RESUME` 时，可创建新的模型对象；
- model factory 失败时在访问仓库副作用前返回稳定错误；
- 默认 console 入口若没有 Provider 或可信脚本模型，必须显式失败，不能静默使用测试 fake 或
  退回规则 Agent。

标准 console/module 入口没有受控模型来源时返回 `CLI_MODEL_UNAVAILABLE`；自动化验收通过注入
Fake Model 的同一 `main` 完成。不得从 argv 或环境加载任意 Python 实现。

### 6.5 输出契约

默认输出为有界、稳定的人类可读摘要。`--json` 输出单个 UTF-8 JSON envelope：

```text
{
  "ok": bool,
  "command": "run|resume|approvals.list|approvals.approve|approvals.reject|inspect",
  "data": object | null,
  "error": {
    "code": StableErrorCode | "CLI_USAGE_ERROR",
    "message": string
  } | null
}
```

要求：

- `run/resume` 的 `data` 至少包含 `run_id`、`trace_id`、`status`、`stop_reason`、
  `acceptance_status`、`approval_id`、`last_checkpoint_id` 和 `last_audit_sequence`；
- `approvals list` 使用 `(requested_at, approval_id)` 稳定排序；
- `inspect` 使用稳定字段顺序，不输出完整模型消息、完整文件内容、隐藏推理或 Audit 大对象；
- JSON 和文本输出都只包含脱敏后的有界摘要；
- 面向用户的消息不能包含 SQL、traceback、租约 token、完整异常对象或敏感环境变量；
- 默认文本输出可以省略 `candidate_final_answer`；JSON 如包含该字段，只能使用已由 WP-10
  脱敏和限制的事实，不得重新拼接原始模型响应。

### 6.6 退出码

退出码固定如下：

| 退出码 | 条件 |
| --- | --- |
| `0` | `run/resume` 的 `acceptance_status=ACCEPTED`；只读或审批控制命令成功完成 |
| `2` | CLI usage、配置、参数或目标 ID 格式错误 |
| `3` | `WAITING_APPROVAL/APPROVAL_REQUIRED/NOT_EVALUATED` |
| `4` | `STOPPED`，包括预算、审批拒绝、审批过期和明确停止原因 |
| `5` | `BLOCKED`，包括 `UNKNOWN_RESULT` 和人工复核状态 |
| `6` | `FAILED` 或 Harness 基础设施失败 |
| `7` | `COMPLETED/REJECTED` |
| `8` | `COMPLETED/UNVERIFIABLE` |
| `9` | Audit/State/解析结果不可安全读取的 CLI 内部失败 |

`approvals approve/reject` 的命令成功不等于任务成功。它们成功改变授权时返回 `0`，但输出必须
包含审批和 Run 的新状态；reject 后 Run 为 `STOPPED` 这一事实仍需在 `data` 中可见。

## 7. 行为契约

### 7.1 `run`

WP11-BEH-001：`run` 只构造 `RunRequest(schema_version=1, mode=START, run_id=None)`，通过注入的
模型工厂构造模型，然后调用 `Harness.run()`。

执行顺序：

```text
parse args
  -> load config for CLI/service binding
  -> construct trusted model via model factory
  -> Harness.run(START)
  -> format RunResult
  -> map acceptance/status to exit code
```

- Harness 装配和权限检查失败时，CLI 不直接访问工作区或执行工具；
- `WAITING_APPROVAL` 是正常但非成功的命令结果；
- 默认模式立即退出，不在本进程等待；
- 默认模式退出后必须没有待审批 handler 副作用；
- 模型 `final_answer` 不改变退出码；只使用持久化 Completion 结果。

### 7.2 `approvals list`

WP11-BEH-002：`approvals list` 是有界、稳定的查询，不启动模型、不调用 Gate 业务路径、不执行
handler、不创建进程、不修改工作区。

可选过滤：

- `--run-id`；
- 一个或多个 `--status`；
- `--limit`，建议默认 100、最大 1000。

查阅到期审批时必须同时展示持久化状态和按当前 UTC 时间计算的 effective status。列表本身不
物化 `EXPIRED`，也不终止 Run；wait、approve、reject 和 resume 才通过 ApprovalStore 受控物化。
列出审批不得把它变成可执行授权。

### 7.3 `approvals approve`

WP11-BEH-003：`approve` 只允许 `PENDING -> APPROVED`，必须通过 `ApprovalStore.approve` 完成。

前置条件：

- 配置和目标数据库可安全读取并初始化；
- approval 存在，且当前持久化状态为 `PENDING`；
- approval 未过期；
- actor/roles 构成的 `Approver` 至少命中一个 allowed role；
- actor 不等于 `requested_by`；
- 当前进程可获得该 Run 的租约；
- expected approval revision 与持久化 revision 一致。

成功时：

- 审批状态、revision 和审计在同一事务提交；
- 不执行 handler，不改变目标文件；
- 返回 approval ID、Run ID、新审批状态和 Run 当前状态；
- lease 被释放。

重复 approve、过期、已 claim、revision 冲突、无权限或绑定损坏必须失败关闭，不得执行 handler。

### 7.4 `approvals reject`

WP11-BEH-004：`reject` 只允许 `PENDING -> REJECTED`，必须通过 `ApprovalStore.reject` 完成。

- 成功时 Approval、Run `STOPPED/APPROVAL_REJECTED/NOT_EVALUATED` 和 Audit 在同一事务提交；
- reject 后模型不能换工具继续同一 Run；
- 不执行 handler；
- reason code 使用稳定值；若将来允许附加备注，备注必须脱敏、限长且只作为审计摘要；
- 重复 reject、过期、已 claim、跨 Run、revision 冲突或无权限必须失败关闭。

### 7.5 `resume`

WP11-BEH-005：`resume` 只构造 `RunRequest(mode=RESUME, run_id=RUN_ID)`，不得从 CLI 重新提供
工具参数或执行审批。

固定行为：

- 在调用者的新进程或新 Harness 实例中执行；
- 重新加载配置并校验配置指纹、工作区、schema、Audit、Checkpoint、审批和预算；
- 为本次调用创建新的模型和 Agent；
- `CONTINUE_AGENT` 使用结构化 continuation context 继续；
- `WAIT_FOR_APPROVAL` 返回 `WAITING_APPROVAL`，不执行、不自动批准；
- `REENTER_GATE` 和 `RESUME_CLAIM_THEN_REENTER_GATE` 只通过 Gate 恢复入口；
- `MANUAL_REVIEW`、`MARK_UNKNOWN_RESULT` 返回 BLOCKED 事实，不启动模型执行副作用；
- `ALREADY_TERMINAL` 只读返回既有终态；
- 已完成写操作再次被模型提案时由 Gate replay suppression 拦截，不重复副作用。

### 7.6 `--wait-for-approval`

WP11-BEH-006：可选等待模式只改变当前 CLI 进程是否继续轮询审批，不改变授权事实。

状态流程：

```text
run/resume -> WAITING_APPROVAL
  -> bounded poll approval state
       -> APPROVED: construct fresh Harness/model and Harness.run(RESUME)
       -> REJECTED/EXPIRED: return persisted STOPPED result
       -> local wait timeout: leave Run WAITING_APPROVAL and exit code 3
       -> approval/state read failure: fail closed
```

要求：

- 轮询间隔、总超时和最大轮询次数有界；
- 同一命令不得因超时自行 reject、approve、修改 Run 或消费审批；
- 轮询读取不得执行 handler；
- 等待后的恢复仍调用同一个 `Harness.run(RESUME)`，不直接执行待审批调用；
- 新 Harness 必须重新加载配置并获取 lease；并发 resume 只有一个 owner；
- 等待过程中进程崩溃与默认模式一致，外部可再次显式执行 `resume`；
- 秒数必须显式提供，范围为 `1..86400`。

### 7.7 `inspect`

WP11-BEH-007：`inspect` 是有界只读事实视图，不启动模型、Gate 业务调用、handler 或子进程。

默认视图包含：

- Run 的 task、workspace identity 摘要、config fingerprint；
- `status`、`stop_reason`、`acceptance_status`；
- budget 和 usage；
- 最近 Checkpoint ID、最新恢复动作/原因（若存在）；
- approvals、tool invocations 和 completion checks 的有界摘要；
- Audit head sequence 和 chain 验证状态。

过滤：

- `--tool-call-id`：展示该调用的 Gate/Sandbox/Execution/Audit 关联事实；拒绝场景至少可看到
  rule、decision、actor 和 tool call ID；
- `--approval-id`：展示审批生命周期和绑定摘要，不展示明文敏感参数；
- `--audit-limit`：限制关联 Audit 事件数，必须标记是否截断。

`inspect` 不提供批准、拒绝、恢复、释放 claim、修改 unknown 或强制完成入口。对于
`BLOCKED/MANUAL_REVIEW`，只展示原因、关联 ID、允许的人工核对边界和“需启动新 Run”的提示。

## 8. 状态、审计与安全不变量

WP11-INV-001：CLI 和只读控制面不能创建除 ApprovalStore 规定状态转移之外的审批变更。

WP11-INV-002：任何可能执行 handler 的路径只能由 `Harness.run()` 进入；CLI、审批服务和检查
服务均不得直接调用 handler。

WP11-INV-003：`approve/reject` 与对应 Audit 必须同事务提交；写入失败时整体回滚。

WP11-INV-004：`resume` 的模型、Agent、Gate 和 Sandbox 上下文必须来自本次调用；不得恢复旧
Agent memory 或复用旧进程内存授权。

WP11-INV-005：审批、Run、工具调用、Checkpoint 和 Audit 的关联 ID 必须在 CLI 输出中保持原值，
不得改写、截断 ID 或生成替代 ID。

WP11-INV-006：没有 `ACCEPTED` Completion 时，退出码不得为 `0`，除非当前命令是成功的审批或
只读控制命令。

WP11-INV-007：CLI 的失败和解析中断不得留下 `RUNNING` 假象、伪审批、伪终态或未审计状态。

WP11-INV-008：`WAITING_APPROVAL` 退出后 lease 必须已释放或可安全过期；CLI 不保留后台线程、
子进程或文件锁。

WP11-INV-009：所有 stdout/stderr/JSON 内容先脱敏再输出；完整异常和 traceback 只进入受控本地
日志（如已有日志设施），不直接展示。

WP11-INV-010：所有查询和等待都有时间/数量/字符上限，不能无界读取 Audit 或无限轮询。

## 9. 错误契约

现有 `CONFIG_*`、`HARNESS_*`、`APPROVAL_*`、`STATE_*`、`CHECKPOINT_*`、`AUDIT_*` 和
`SMOLAGENTS_*` 错误码保持原语义，不包装成模糊字符串。

新增稳定 CLI 错误码：

```text
CLI_USAGE_ERROR
CLI_CONFIG_REQUIRED
CLI_MODEL_UNAVAILABLE
CLI_APPROVAL_NOT_FOUND
CLI_APPROVAL_ACTION_REJECTED
CLI_INSPECTION_UNAVAILABLE
CLI_OUTPUT_INVALID
CLI_INTERNAL_ERROR
```

要求：

- 参数缺失、未知 flag、非法 ID 和非法 limit 不初始化数据库、不访问模型、不执行 handler；
- 审批不存在、已过期、已消费、跨 Run 或绑定不匹配使用现有 `APPROVAL_*` 错误码；
- 配置指纹或工作区不匹配使用现有 `HARNESS_CONFIG_RELOAD_FAILED`、`RECOVERY_*` 或
  `SANDBOX_*`；
- 审计链或解析失败不得降级为“部分成功”；
- 所有错误均包含稳定 code、有界 message、命令名和可安全公开的关联 ID；
- 退出码与错误类别必须稳定对应，不能用模型文本决定。

## 10. 并发、恢复与人工处理

- 两个进程同时 approve/reject 同一审批：只允许一个 revision 条件更新成功；
- approve 与 resume 并发：只有成功 approve 后 resume 才能进入可执行授权；
- 两个 resume 并发：State lease/revision 只允许一个 owner，另一个失败关闭；
- wait 模式与另一个 resume 并发：副作用最多一次；
- 已 `CLAIMED`、`CONSUMED`、`REJECTED`、`EXPIRED` 或 `INVALIDATED` 的审批不因 CLI 重试复活；
- `UNKNOWN_RESULT` 永远返回 BLOCKED 非成功状态；需继续工作必须启动新 Run；
- `BLOCKED/MANUAL_REVIEW` 只提供查看事实和关联 ID，不提供同 Run 强放行；
- SQLite busy、lease 冲突和临时不可用不得通过自动重复执行来“修复”；只允许安全只读重读或
  明确重试策略。

## 11. 验收场景

| ID | 场景 | 必须证据 |
| --- | --- | --- |
| WP11-AC-001 | console script 与 `python -m` 共用 `main` | 两入口 help/非法参数行为一致；导入无副作用 |
| WP11-AC-002 | `run` 只调用 Harness | 测试替换 Harness/model factory，CLI 不直接访问 handler |
| WP11-AC-003 | 未审批写默认退出 | `WAITING_APPROVAL`、退出码 3、文件内容和 mtime 不变、lease 已释放 |
| WP11-AC-004 | `approvals list` 只读 | 无模型/handler/进程调用，不改变工作区；排序和 limit 稳定 |
| WP11-AC-005 | `approvals approve` | 只将授权改为 APPROVED 并审计；handler 计数为 0 |
| WP11-AC-006 | `approvals reject` | Approval REJECTED、Run STOPPED/APPROVAL_REJECTED/NOT_EVALUATED；handler 为 0 |
| WP11-AC-007 | 审批权限错误 | 未授权 actor/role、自批和重复决定均失败，状态和审计原子回滚 |
| WP11-AC-008 | 审批过期/篡改/跨 Run | 返回稳定 APPROVAL_* 错误，不能变成有效授权 |
| WP11-AC-009 | 新进程 resume | 写入执行一次，Step/usage 保留，已有写不重复 |
| WP11-AC-010 | replay suppression | 已完成调用再次提案只返回原结果/拒绝事实，handler 次数不增加 |
| WP11-AC-011 | `resume` 无 approval | 返回 WAITING_APPROVAL，不创建模型执行或目标副作用 |
| WP11-AC-012 | wait 后批准 | 同一命令在批准后通过 New Harness/Agent 恢复并只执行一次 |
| WP11-AC-013 | wait 超时 | 超时后 Run 保持 WAITING_APPROVAL，退出码 3，审批不被 CLI 修改 |
| WP11-AC-014 | 工作区变化恢复 | `BLOCKED`/人工核对原因可见，模型和 handler 均未执行 |
| WP11-AC-015 | UNKNOWN_RESULT inspect/resume | 不提供强放行；重复 resume 保持 BLOCKED，handler 为 0 |
| WP11-AC-016 | 预算停止 | STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED，退出码 4，不调用 Completion |
| WP11-AC-017 | Completion REJECTED | 模型声称完成但无交付物时退出码 7，不能返回成功 |
| WP11-AC-018 | Completion UNVERIFIABLE | 独立退出码 8，不伪装成功 |
| WP11-AC-019 | inspect 拒绝调用 | `--tool-call-id` 可见 rule、decision、actor、toolCallId 和审计关联 |
| WP11-AC-020 | inspect 完整 Run | 可关联 Run、step、tool call、approval、checkpoint、completion 和 Audit head |
| WP11-AC-021 | JSON/文本错误脱敏 | 不泄露 token、密码、API Key、完整文件内容或环境原值，输出有界 |
| WP11-AC-022 | 并发 approve/reject/resume | 条件更新和 lease 保证最多一个成功执行 owner |
| WP11-AC-023 | Audit/State 查询失败 | 返回非成功内部失败，不显示部分成功或伪造终态 |
| WP11-AC-024 | 跨平台入口 | POSIX 下 subprocess 测试封装稳定；不支持平台由现有 Sandbox 失败关闭 |
| WP11-AC-025 | 离线确定性 | 无真实模型、网络、凭据、本机绝对路径和关键 skip |

每个场景都必须断言退出码、结构化输出、worker/handler 调用次数、文件状态和关联 Audit；只检查
帮助文本或 stdout 字符串不算通过。

## 12. 已确认决策

| ID | 问题 | 冻结方案 | 决策 |
| --- | --- | --- | --- |
| WP11-Q01 | P0 CLI 如何获得 `smolagents.Model` | 只支持可信 `model_factory` 注入；console 默认无 Provider 时显式失败 | D-78 |
| WP11-Q02 | 入口名、`--config` 位置和命令形态 | canonical `guarded-harness`；全局 `--config`；固定 6.3 命令面 | D-79 |
| WP11-Q03 | approve/reject 的身份参数 | 必须显式 `--actor` 和至少一个 `--role` | D-80 |
| WP11-Q04 | `approvals list` 默认范围与到期物化 | 默认跨 Run、有界排序；effective-only，不物化 | D-81 |
| WP11-Q05 | 退出码与 `--json` envelope | 采用 6.5/6.6 稳定表和统一 envelope | D-82 |
| WP11-Q06 | `--wait-for-approval` 超时形式 | 必须显式提供 `1..86400` 秒；超时保持等待 | D-83 |

## 13. 完成定义

WP-11 只有在以下条件全部满足时才可标记 `DONE`：

1. WP11-Q01 至 WP11-Q06 已按 D-78 至 D-83 决策并登记，`spec.md` 为 `frozen`；
2. WP-07 和 WP-10 依赖已 accepted；
3. console script 与 `python -m` 共用 `main`，CLI 不自行装配安全组件；
4. `run` 默认审批等待安全退出，批准前文件不变；
5. 新进程 `resume` 重新校验并最多执行一次，预算和计数不清零；
6. approve/reject 只改变授权，不执行 handler；
7. `approvals` 和 `inspect` 只读且不能触达 handler、模型或进程；
8. 退出码与异常分类稳定，`ACCEPTED` 是唯一任务成功退出；
9. WP11-AC-001 至 WP11-AC-025 都有可执行证据；
10. 目标测试、全量 pytest、Ruff lint 和格式检查通过；
11. `acceptance-report.md` 状态为 `accepted`，记录实际命令、测试数量、限制和下游影响；
12. 文档索引、任务拆解链接、实现契约和测试证据一致。
