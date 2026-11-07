# WP-11 CLI 与人工恢复工作包实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-11 CLI 与人工恢复 |
| 文档状态 | done |
| 计划版本 | 1.1.0 |
| 创建日期 | 2026-11-07 |
| 批准日期 | 2026-11-07 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |
| 前置依赖 | WP-07 accepted；WP-10 accepted |
| 当前依赖状态 | WP-07 accepted；WP-10 VERIFYING，验收报告 review |
| 间接依赖 | WP-01、WP-02、WP-04、WP-07 的 State/Audit/Approval/Checkpoint 公共契约 |
| 下游影响 | WP-12 的跨进程 E2E、退出码和运维文档 |
| 当前结论 | 实现和包内测试完成；验收报告 review，等待 WP-10 accepted |

本计划不重新定义规格。WP11-Q01 至 Q06 已按 D-78 至 D-83 关闭；后续若改变命令、模型来源、
身份参数、只读查询、退出码或等待语义，必须先更新 `spec.md` 和必要的设计/决策文档。

## 2. 计划目标与完成定义

WP-11 施工完成后必须通过公共 CLI 入口证明：

1. `guarded-harness` 与 `python -m guarded_harness` 调用同一 `main`；
2. `run/resume` 只调用 Harness，不在 CLI 内拼装 Gate、Sandbox、Completion 或 handler；
3. 默认审批等待安全退出，文件不变且 lease 释放；
4. `approvals approve/reject` 只改变授权与规定状态，不执行 handler；
5. 新进程 `resume` 能重新校验并至多执行一次；
6. 等待模式在审批批准后继续，在本地超时后保持 WAITING；
7. `approvals` 和 `inspect` 在不启动模型/handler 的情况下返回有界事实；
8. 退出码严格由 Run/Completion/控制命令结果映射；
9. 所有输出和错误先脱敏、限长，再进入 stdout/stderr/JSON；
10. WP11-AC-001 至 WP11-AC-025 有自动化或明确的人工证据；
11. 目标测试、全量测试、Ruff lint 和格式检查通过；
12. `acceptance-report.md` 由实际命令和证据产生。

## 3. 计划范围与文件布局

### 3.1 计划文件布局

```text
src/guarded_harness/
├── cli.py             # 新增：参数解析、命令编排、输出和退出码
├── __main__.py        # 新增：python -m 入口
├── control.py         # 新增：CLI 专用审批/只读检查服务
├── domain.py          # 窄扩展：CLI 稳定错误码（按 Q05）
├── approval.py        # 可选窄扩展：按 approval_id 定位 Run 的公共只读接口
├── storage.py         # 可选窄扩展：无副作用只读事务/查询
├── harness.py         # 尽量复用；只按 Q01 增加模型工厂适配，不在 CLI 中包装
└── __init__.py        # 导出必要的稳定 CLI 契约

pyproject.toml         # 注册 guarded-harness console script

tests/
├── test_cli.py                 # 新增：命令、输出、退出码、审批、inspect
├── cli_model_runner.py         # 可选新增：子进程注入 Fake Model 的测试入口
├── test_harness_loop.py        # 扩展：CLI 跨进程 run -> approve -> resume
└── test_state_checkpoint.py    # 扩展：CLI 恢复、并发和未知结果

docs/work-packages/wp-11-cli-manual-recovery/
├── spec.md
├── plan.md
└── acceptance-report.md        # 实现与实测后创建
```

文件布局是实现建议，规格以外部行为为准。P0 不提供受控脚本模型；不能把
`tests/fake_model.py` 直接作为生产依赖。

### 3.2 依赖与准入

```text
WP-07 accepted ────────────────┐
WP-10 accepted ────────────────┼──> WP-11
WP-02 ApprovalStore ───────────┤
WP-01 State/Audit/Storage ─────┘
```

准入条件：

1. WP-07 保持 accepted；
2. WP-10 目标测试、全量测试、Ruff 和验收报告达到 accepted；
3. `Harness.run()` 的 START/RESUME、`WAITING_APPROVAL`、BLOCKED/UNKNOWN 和 ALREADY_TERMINAL
   行为仍是已冻结契约；
4. ApprovalStore 的 approve/reject、到期物化和 lease 行为没有待修接口；
5. WP11-Q01 至 Q06 已确认并登记；
6. `spec.md` 已冻结，`plan.md` 已批准；
7. 当前全量测试有可复现基线。

依赖不满足时，只允许完成文档、只读接口探查和无副作用的测试骨架；不得提前把 CLI 写成
“已可运行”。

## 4. 详细设计

### 4.1 决策基线

以下决策已关闭：

```text
WP11-Q01 P0 模型来源
WP11-Q02 入口名、config 位置和命令形态
WP11-Q03 approve/reject actor/role 输入
WP11-Q04 approvals list 范围与到期物化
WP11-Q05 退出码和 JSON envelope
WP11-Q06 wait-for-approval 超时形式
        -> spec.md frozen
        -> decisions.md D-78 至 D-83
        -> design.md 12.5
        -> tests/test_cli.py 契约测试
```

任务卡 T01 起只能按冻结值实现，不得在代码中重新引入隐式默认或兼容旁路。

### 4.2 CLI 与 `main`

`cli.py` 使用标准库 `argparse` 或等价的严格解析器。解析器只建立不可变命令对象，业务行为由
独立 handler 调用 Harness/控制服务。

公共入口：

```python
def main(
    argv: Sequence[str] | None = None,
    *,
    model_factory: ModelFactory | None = None,
    clock: Clock | None = None,
) -> int:
    """Execute one CLI command and return a stable process exit code."""
```

要求：

- 不在 import 时执行数据库、模型、文件或网络操作；
- `argparse` usage failure 统一映射为 `CLI_USAGE_ERROR` 和退出码 2；
- 不调用 `sys.exit` 隐藏在业务 handler 中；由 wrapper 转换为 `SystemExit(main())`；
- `--json` 只改变呈现，不改变授权、状态或退出码；
- `stdout` 输出命令结果，`stderr` 输出错误诊断；
- `--json` 错误也输出单一 JSON object，不能混入帮助文本或 traceback；
- 所有位置 ID 在进入控制服务前做格式校验，但不自行推断前缀；
- 命令 handler 只依赖可信注入对象，不在 CLI 模块创建 executable 或子进程。

`__main__.py` 固定：

```python
raise SystemExit(main())
```

`pyproject.toml` 固定：

```toml
[project.scripts]
guarded-harness = "guarded_harness.cli:main"
```

两者指向同一 Python callable；help、usage 和退出码测试必须覆盖这一点。

### 4.3 模型工厂

固定接口：

```python
ModelFactory = Callable[[Path], smolagents.Model]
```

命令流程：

```text
main -> model_factory(config_path) -> Harness(
    config_path=config_path,
    model=model,
    ...
) -> harness.run(RunRequest(...))
```

约束：

- 工厂只由可信调用方注入；CLI argv 不提供 import path、Python code 或 shell command；
- 工厂每次返回的模型必须是 `smolagents.Model` 实例；
- 每次独立 Harness 调用创建单独实例，避免旧进程 memory 或脚本状态被复用；
- 等待审批后重新 `RESUME` 时创建新 Harness 和模型；
- 工厂异常映射到 `CLI_MODEL_UNAVAILABLE`/`HARNESS_INTERNAL_ERROR`，在仓库副作用前失败；
- P0 默认 console 若没有受控模型来源，必须给出明确说明并可重复失败，不能转入真实网络 Provider。

P0 不增加受控脚本模型格式或真实 Provider。测试进程通过测试专用 runner 调用同一 `main` 并注入
`FakeScriptedModel`；生产默认 factory 返回 `CLI_MODEL_UNAVAILABLE`。

### 4.4 控制服务

新增 `control.py`，把 CLI 的审批和只读事实查询封装在可独立测试的服务中。

服务类型：

```python
class ApprovalControlService:
    def list(...): ...
    def approve(...): ...
    def reject(...): ...
    def refresh(approval_id): ...

class InspectionService:
    def inspect_run(...): ...
    def inspect_tool_call(...): ...
    def inspect_approval(...): ...
```

它们只依赖已注入的 `SQLiteStorage`、`ApprovalStore`、`StateStore`、`AuditStore` 和
`CheckpointService`，不依赖 Harness 私有字段。

审批定位：

- `approve/reject APPROVAL_ID` 先从持久化数据解析所属 Run；
- 不在 CLI 中猜测 Run ID 或直接 SQL 更新；
- 推荐在 `ApprovalStore` 增加窄只读方法 `locate(approval_id) -> ApprovalRef`，返回 Run ID、
  tool call ID、状态、revision 和过期时间；
- `ApprovalRef` 不包含明文参数、lease token 或未脱敏值；
- `locate` 同时返回当前持久化事实和由当前时间计算的 effective status，不物化状态；
- 之后在同一/后续安全事务中获取 Run lease，并调用现有 `load/approve/reject/expire_due`。

只读事务：

- `inspect` 和跨 Run 列表优先使用只读查询，不启动 `BEGIN IMMEDIATE` 写事务；
- 若现有 `SQLiteStorage` 没有公共 read transaction，可增加窄公共 `read_transaction()`；
- read transaction 不得暴露底层 connection、允许写 SQL、执行 migration 或持有 Run lease；
- 所有返回数量、事件数、字段长度和 Audit 摘要都有上限。

### 4.5 `run` 实现

```text
parse run
  -> validate no run_id override
  -> load config for early binding errors
  -> model_factory(config_path)
  -> Harness(config_path, model)
  -> Harness.run(RunRequest(mode=START))
  -> render RunResult
  -> exit mapping
```

禁止：

- 直接调用 `Gate.invoke`、`Sandbox`、tool factory 或 `CompletionChecker`；
- 读取 handler registry 绕过 Harness；
- 根据 `candidate_final_answer` 判断成功；
- 在 `WAITING_APPROVAL` 时自行 approve 或继续模型。

失败关闭点：

- CLI 参数/配置失败：不创建模型，不初始化 Run；
- model factory 失败：不访问 workspace；
- Harness 装配/初始基线/Checkpoint/Audit 失败：不显示成功；
- approve 前中断：目标文件和进程副作用计数必须为 0。

### 4.6 `approvals` 实现

#### list

固定行为：

```text
load config
  -> resolve state database and workspace identity
  -> bounded read query
  -> optional run/status filters
  -> stable sort by requested_at, approval_id
  -> output persisted/effective status
```

无模型、无 Gate 业务调用、无 handler、无进程。`limit` 建议默认 100、最大 1000；非法值返回
`CLI_USAGE_ERROR`。

数据库持久化状态不因 list 改变。输出同时标记 `persisted_status` 和 `effective_status`；
wait/approve/reject/resume 仍按 ApprovalStore 规则物化到期。

#### approve

```text
load config
  -> locate approval/run
  -> acquire run lease
  -> load approval with expiry materialization
  -> validate status=PENDING, revision, actor, roles, not self
  -> ApprovalStore.approve
  -> release lease
  -> format approval + current run status
```

关键测试：

- 正确 actor/role：PENDING -> APPROVED；
- 缺 role、大小写重复、无交集角色和 self approval：失败，状态不变；
- 过期、CLAIMED、CONSUMED、REJECTED、EXPIRED、INVALIDATED：失败；
- revision 冲突：失败；
- approve 期间 handler、进程、文件写入计数均为 0；
- Audit 与 approval 同事务。

#### reject

与 approve 相同，但调用 `ApprovalStore.reject`，使用固定 reason code。成功必须原子产生：

```text
Approval REJECTED
Run STOPPED/APPROVAL_REJECTED/NOT_EVALUATED
Audit
```

reject 不执行 handler，也不把 Run 变成可恢复状态。

### 4.7 `resume` 实现

```text
parse resume + run_id
  -> load config
  -> model_factory(config_path)
  -> Harness(config_path, model)
  -> Harness.run(RunRequest(mode=RESUME, run_id=run_id))
  -> render RunResult
  -> exit mapping
```

不向 Harness 传递 CLI 提供的额外授权。Resume 的恢复校验完全沿用 WP-07/WP-10：

- config fingerprint；
- Git identity/HEAD/index、workspace_diff/status、外围保护；
- Audit chain/schema；
- Checkpoint/latest recovery plan；
- approval status/expiry/binding；
- Evidence validity；
- budget usage 和 lease。

任何新 workspace、新 task 或新 approval 只能在新的 Run 中发生。

### 4.8 `--wait-for-approval`

状态机：

```text
RUN/RESUME result
  -> if status != WAITING_APPROVAL: exit
  -> poll bounded refresh(approval_id)
       PENDING/APPROVED?
         PENDING: wait
         APPROVED: create NEW Harness/model; Harness.run(RESUME)
       REJECTED/EXPIRED: read terminal Run facts and exit non-success
       timeout: exit WAITING
       error: fail closed
```

实现要点：

- `approval_id` 可来自 `RunResult.approval_id`，缺失时通过受控快照查找待审批单；
- 每轮通过只读 control service 读取状态；到期状态需要按已确认 Q04 规则处理；
- poll interval 建议 0.5 秒，单次 sleep 不超过 1 秒；总次数由上界计算；
- 不无限等待；不把本地等待超时写成审批过期；
- timeout 后保留 WAITING_APPROVAL，输出继续/批准所需 ID；
- 收到 APPROVED 后必须重新进入 Harness，不能直接执行 invocation；
- 新 Harness 获取 lease；若另一个进程已恢复，返回其冻结结果或 lease 冲突；
- 进程中断只留下安全等待状态，不留下内存授权。
- `SECONDS` 必须显式为 `1..86400` 的整数，不接受默认等待值。

### 4.9 `inspect`

输出 DTO：

```text
RunInspection(
    run: summary,
    budget: summary,
    usage: summary,
    checkpoint: summary | null,
    recovery: summary | null,
    approvals: bounded summaries,
    invocations: bounded summaries,
    completion: summary | null,
    audit: AuditInspection,
)
```

只能读取持久化事实，不调用 Harness。`--tool-call-id`、`--approval-id` 和 `--audit-limit`
使用固定查询，不接受任意 SQL 或过滤表达式。

A1 展示：

- `rule_id`；
- `decision/status`；
- `actor`；
- `tool_call_id`；
- Gate phase/error code；
- event sequence 和关联 approval/checkpoint ID。

C4 展示：

- `BLOCKED`/`MANUAL_REVIEW`；
- recovery reason/code；
- config/workspace/audit/checkpoint 哪个校验失败；
- 可安全公开的关联 ID；
- 不提供自动重放按钮或命令参数。

### 4.10 退出码与输出映射

退出码不读取 stdout 文本或模型自述，只读取 `RunResult`、控制服务结果和稳定错误码。

映射集中在两个纯函数：

```text
exit_code_for_run_result(result) -> int
exit_code_for_control_result(result, error) -> int
```

要求：

- 纯函数可单测；
- 同一 status/acceptance/stop reason 组合始终得到同一 code；
- 未知组合失败关闭，不能默认为 0；
- `COMPLETED/ACCEPTED` 是 `run/resume` 唯一成功；
- `approvals` 控制操作的成功与任务 acceptance 分离；
- `--json` 的 `ok` 与退出码一致；
- 文本输出不承诺机器解析，脚本应使用 `--json`。

### 4.11 脱敏与有界输出

复用 `Redactor` 和领域模型长度上限。CLI 输出层还要：

- 限制单行、总文本、列表条数和 Audit 事件数；
- 对路径只展示 workspace 相对路径或已授权的绝对 root；
- 不输出 lease token、SQL、完整 `model_input_messages`、完整工具结果或完整异常；
- 对审批参数只展示规范化摘要和脱敏摘要；
- 明确标记截断，不能静默截断关键安全字段；
- JSON 序列化失败返回 `CLI_OUTPUT_INVALID`，不得退回 repr 输出。

### 4.12 并发与故障

CLI 必须验证：

- 两个 approve 同时执行：一个成功，另一个 revision/state 冲突；
- 两个 reject 同时执行：一个成功，另一个失败；
- approve/reject 与 resume 竞争：没有批准时 resume 保持 WAITING；批准后最多一个执行 owner；
- 两个 resume：一个 lease owner，一个失败；
- wait 与手动 resume：最多一个执行写入；
- lease release 失败：命令非成功并保留原事实，不伪称等待可恢复；
- Audit/State 写失败：控制命令失败关闭，审批不变成半授权；
- 数据库 busy：不自动重放 handler，只按安全只读/事务规则处理；
- 进程退出在 run 等待、wait 轮询、approve 前后等点：由 State/Checkpoint 事实决定，不靠 CLI
  内存推断。

## 5. 施工任务卡

| 任务 | 实现内容 | 对应规格 | 依赖 | 主要文件/测试 |
| --- | --- | --- | --- | --- |
| T00 | 关闭 WP11-Q01 至 Q06，更新 spec/decision/design 并冻结 | 12 | 无 | 已完成 |
| T01 | CLI DTO、严格 parser、输出 envelope、exit code 纯函数 | WP11-CLI-001、6.3 至 6.6 | T00 | `cli.py`、`test_cli.py`、`test_domain.py` |
| T02 | `main`、`__main__.py`、console script、同入口契约 | WP11-AC-001、WP11-CLI-001 | T01 | `cli.py`、`__main__.py`、`pyproject.toml` |
| T03 | 模型工厂边界、默认不可用 Provider 失败关闭 | 6.4、WP11-AC-002 | T00/T02 | `cli.py`、`test_cli.py` |
| T04 | ApprovalControlService/InspectionService 与必要时公共只读接口 | 7.2 至 7.4、7.7、11 | T01 | `control.py`、`approval.py`、`storage.py`、`test_cli.py` |
| T05 | `run` 命令接 Harness | 7.1、WP11-AC-002/003 | T02/T03/T04 | `cli.py`、`test_cli.py` |
| T06 | `approvals list/approve/reject` | 7.2 至 7.4、WP11-AC-004 至 008 | T04 | `control.py`、`test_cli.py`、`test_state_checkpoint.py` |
| T07 | `resume` 新进程入口 | 7.5、WP11-AC-009 至 011/014 | T05 | `cli.py`、`test_cli.py`、`test_harness_loop.py` |
| T08 | `--wait-for-approval` | 7.6、WP11-AC-012/013 | T06/T07 | `cli.py`、`test_cli.py` |
| T09 | `inspect` 与 A1/C4 视图 | 7.7、WP11-AC-019/020 | T04 | `control.py`、`test_cli.py` |
| T10 | cross-process tests/fixture/exit mapping | WP11-AC-009 至 013 | T05 至 T09 | `tests/test_cli.py`、`tests/cli_model_runner.py` |
| T11 | 并发、故障、脱敏、输出边界和离线矩阵 | 8、9、10、11 | T10 | 指定测试文件 |
| T12 | 全量验收、acceptance-report、索引和下游交接 | 13 | T11 | 验收报告、索引、docs |

### 5.1 T00 决策冻结（已完成）

完成证据：

1. 需求方确认 Q01 至 Q06 全部按建议；
2. 每条结论登记到 `docs/decisions.md`，分配 D 编号；
3. 若模型来源、退出码、approval 查询或 wait 改变外部行为，同步 `design.md`；
4. `spec.md` 升版并标记 frozen；
5. 依赖 WP-10 accepted；
6. 本计划对应版本标记 approved；
7. `tests/test_cli.py` 先写契约失败测试的用例清单。

### 5.2 T01 至 T02 入口与契约

先写失败测试：

- 无参数、`--help`、未知命令/flag、多余参数；
- 非法 run ID、approval ID、limit；
- `--json` 单对象和稳定 key；
- console script 与 module 入口调用同一 `main`；
- import `guarded_harness.cli` 不产生 DB/model/workspace 副作用；
- exit mapper 对未知状态失败关闭。

实现顺序：

1. 纯 DTO 和 parser；
2. JSON/text renderer；
3. exit mapper；
4. `main` dispatch table；
5. `__main__.py`；
6. `pyproject.toml` console script。

### 5.3 T03 模型工厂

先测试：

- `None` factory 默认路径的行为；
- factory 返回非 Model；
- factory 抛异常；
- factory 调用次数：START 一次，wait 后 APPROVED 再 RESUME 一次；
- factory 不接收 argv 中任意模块路径；
- CLI 不访问网络。

若 P0 只允许测试注入，则 console 无 factory 的 run/resume 必须有稳定错误和测试；`approvals` 和
`inspect` 仍可正常工作。

### 5.4 T04 控制服务

先建立通过 `SQLiteStorage` 的干净临时 DB 和 Git workspace。测试：

- approval 定位只返回必要摘要；
- 读取不启动模型/Gate/handler/子进程；
- 不存在/损坏 approval 失败关闭；
- read query 有界、稳定排序；
- 只读查询不写 Audit；如选择 expired effective，不改持久化状态；
- 若新增 `ApprovalStore.locate` 或 storage read transaction，补 WP02/WP01 兼容测试。

实施后检查没有 CLI 模块直接执行 SQL。

### 5.5 T05 `run`

固定场景：

- 正常 code-understanding Fake Model；
- 非法配置；
- 模型工厂失败；
- 写工具等待审批；
- README/report 等目标文件在等待后不存在或 mtime 不变；
- lease 已释放；
- RunResult JSON 与文本一致；
- 退出码 3。

### 5.6 T06 审批命令

固定场景：

- list 默认/筛选/limit 排序；
- approve 正确角色；
- 无角色、无交集角色、self approval；
- reject；
- 已过期/已消费/已 claim/跨 Run/revision 冲突；
- DB 写入失败回滚；
- approve/reject 前后文件系统和 handler 计数不变。

每次成功/拒绝都检查 Audit chain。

### 5.7 T07 `resume`

固定场景：

- 原进程退出；
- 新 Python 进程、新 Harness、新 Model；
- 未 approve 时返回 WAITING，model call 为 0、handler 为 0；
- approve 后返回 ACCEPTED 或 REJECTED，写入恰好一次；
- 再次 resume 使用既有终态或 replay suppression，写入不增加；
- config/workspace 变化返回 BLOCKED；
- UNKNOWN_RESULT 不重放；
- budget/usage/step 不重置。

### 5.8 T08 wait

固定场景：

- wait 遇到 pending，批准后继续；
- wait 超时保持 WAITING；
- wait 时 reject/expire；
- wait 与另一个 resume 并发；
- wait 轮询错误；
- 任何路径 handler 不重复。

进程测试必须用有界 helper 等状态，不使用无界 sleep；测试结束清理子进程。

### 5.9 T09 inspect

固定场景：

- 一次 Gate denial 的 A1 字段；
- 一次完整 Run 的 Run/step/tool/approval/checkpoint/completion/audit 关联；
- C3 恢复 Run 的恢复动作、checkpoint 和预算；
- C4 workspace changed 的 BLOCKED/MANUAL_REVIEW；
- inspect 期间 handler/model 调用为 0；
- audit limit 截断标记；
- JSON 不含明文敏感参数。

### 5.10 T10 至 T12 测试、加固与验收

T10 完成跨进程真实 CLI 证据。T11 完成并发、故障、输出和离线矩阵。T12 运行目标测试、全量测试、
Ruff，生成 `acceptance-report.md`，更新 `docs/README.md`、`docs/work-packages/index.md` 和
`docs/task-breakdown.md` 链接。

## 6. 测试计划

### 6.1 单元测试

| 主题 | 关键断言 |
| --- | --- |
| parser | 缺参数、未知 flag、非法 ID/limit、无副作用 |
| output | JSON envelope、稳定 key、截断标记、脱敏 |
| exit mapping | status/acceptance/stop reason 的完整矩阵 |
| approval control | 状态、revision、role/self/binding、审计和回滚 |
| inspect projection | 字段有界、ID 关联、不触发执行 |
| wait policy | 超时/批准/拒绝/错误状态机 |

### 6.2 CLI 集成测试

| 文件/主题 | 关键断言 |
| --- | --- |
| `test_cli.py::run` | Harness 是唯一执行入口；WAITING 安全退出；文件不变 |
| `test_cli.py::approvals` | 只授权、不执行；reject 收口；错误不复活 |
| `test_cli.py::wait` | 同命令批准后恢复；超时保持等待 |
| `test_cli.py::inspect` | 只读、脱敏、A1/C4 事实 |
| `test_harness_loop.py` | 新进程 START -> WAIT -> approve -> RESUME |
| `test_state_checkpoint.py` | lease、revision、checkpoint、budget 跨进程一致 |

### 6.3 安全与故障

- 参数注入：未知 flag/多余位置参数不进入命令；
- 身份注入：actor/role 不是配置覆盖，只能作为审批人身份；
- 路径注入：所有路径来自 config，不接受 CLI workspace 覆盖；
- SQL 注入：查询只使用参数化语句，不接受 SQL 片段；
- 输出注入：错误和原因先脱敏、限长，不输出 traceback；
- DB 锁、只读失败、Audit 失败、审批写入失败；
- lease 竞争、并发 approve/reject/resume；
- 进程在等待/批准/恢复前后退出；
- 目标文件在等待阶段不变；
- unknown/blocked 不能通过 CLI 绕过。

### 6.4 场景到测试映射

| 规格场景 | 主测试 |
| --- | --- |
| WP11-AC-001 | `test_cli.py` entrypoint/import |
| WP11-AC-002 至 003 | `test_cli.py::run`、`test_harness_loop.py` |
| WP11-AC-004 至 008 | `test_cli.py::approvals`、`test_state_checkpoint.py` |
| WP11-AC-009 至 013 | `test_cli.py::resume_wait`、`test_harness_loop.py` |
| WP11-AC-014 至 016 | `test_harness_loop.py`、`test_cli.py` |
| WP11-AC-017 至 018 | `test_completion.py` + `test_cli.py` exit mapping |
| WP11-AC-019 至 020 | `test_cli.py::inspect` |
| WP11-AC-021 | `test_cli.py` output/redaction |
| WP11-AC-022 至 024 | `test_cli.py` concurrency/fault |
| WP11-AC-025 | 全量离线重复运行 |

### 6.5 验收命令

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest tests/test_cli.py -q
python -m pytest tests/test_harness_loop.py tests/test_state_checkpoint.py -q
python -m pytest tests/test_gate.py tests/test_audit.py tests/test_completion.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

在安装后还至少检查：

```bash
guarded-harness --help
python -m guarded_harness --help
```

正式验收报告必须记录实际命令、退出码、测试数量、skip/失败数、关键 stdout/stderr、环境版本和
工作区状态，不得复制计划中的预期值。

## 7. 推荐施工顺序与可回滚增量

```text
T00 decisions/spec
  -> T01 CLI DTO/parser/output/exit
  -> T02 main/module/console
  -> T03 model boundary
  -> T04 control services
  -> T05 run
  -> T06 approvals
  -> T07 resume
  -> T08 wait
  -> T09 inspect
  -> T10 cross-process
  -> T11 hardening
  -> T12 acceptance
```

可独立提交/回滚的增量：

1. CLI parser、DTO、输出和 exit mapper；
2. 模块入口和 console script；
3. model factory 边界；
4. 审批控制服务；
5. `run`；
6. `approvals`；
7. `resume`；
8. wait；
9. inspect；
10. cross-process 和故障测试。

每个增量先写失败测试，再实现；不得把安全负例全部推迟到最后。

## 8. 风险、阻塞与人工处理

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| P0 没有生产 Model Provider | console `run/resume` 可能没有可执行模型 | Q01 明确范围；可在 P0 只支持注入 factory，console 显式失败 |
| 只读 list 需要跨 Run 定位 | 现有 ApprovalStore 仅按 Run 列出 | 增加窄只读 locate/query，不直接写表；补 WP02 契约 |
| 查询物化审批到期会改变 Run | “只读”语义含糊 | Q04 明确 effective-only 或受控物化；文档与测试同步 |
| approve/reject 需要 lease | 用户可能认为审批只是更新一行 | 服务内部获取/释放 lease，失败不自动重试 |
| wait 与外部 resume 竞争 | 可能重复执行 | Lease/revision/Gate claim/replay suppression 为主要边界 |
| 退出码被脚本依赖 | 后续修改破坏自动化 | D-82 后按稳定 API 维护，变更提升版本 |
| CLI 输出泄露敏感事实 | 违反审计/安全要求 | 统一 redactor、长度限制、JSON 结构化，不输出 traceback |
| 跨进程测试不稳定 | 验收不可重复 | 明确状态轮询、上界超时、隔离临时目录和无网络 fixture |
| inspect 被误用为恢复入口 | 仍可能阻止修 bug | 只提供查看事实；恢复必须显式 `resume` |

阻塞处理：

- Q01 至 Q06 已关闭；实现必须遵循 D-78 至 D-83；
- WP-10 未 accepted：不得进入 WP-11 实现；
- ApprovalStore 公共接口不足以安全定位审批：先补上游窄接口和测试，不在 CLI 私有访问；
- 任何状态/审计不一致：停止，保留事实，转人工核对或新 Run；
- Workspace/config/approval 变化：BLOCKED，不自动批准或重放。

## 9. 回滚计划

- parser/output/exit mapper：回滚 CLI 文件，不影响现有 Harness；
- module/console script：移除 `[project.scripts]` 和 `__main__.py`，已有数据库与 Audit 保留；
- control services：回滚只读查询，不回写删除已生成 Audit；
- approve/reject：已提交的审批和 Audit 是 append-only 事实，不能删除或改写；回滚代码后新 CLI
  禁止继续操作该数据库；
- resume/wait：回滚到显式 run/approve/resume 的旧流程，不把已执行副作用当作未发生；
- inspect：只影响展示，回滚不得删除历史 Run；
- 任何回滚都不得修改 migration、重写审批绑定、伪造 unknown 成功或重放已开始写操作。

## 10. 下游交接

WP-11 accepted 后向 WP-12 提供：

- `main`、console script 和 module 入口契约；
- run/resume 的 status 到退出码映射；
- WAITING_APPROVAL 的安全退出与 wait 语义；
- approve/reject/list/inspect 的只读/授权边界；
- 新进程恢复的实际证据；
- CLI 环境变量、配置路径、状态库和日志限制；
- 已知 P0 限制，特别是模型来源、真实 Provider、无 Web UI 和平台边界。

WP-12 负责：

- 两类任务的正式样例配置；
- 真实的项目级 E2E 和故障矩阵；
- 运维文档、根 README 和最终交付报告；
- 不得把 WP-11 的单个 CLI 入口写成整个 P0 已完成。

## 11. 实际完成情况

截至 2026-11-07，决策、详细设计、实现和包内验证已完成：

- 从 `docs/task-breakdown.md` 读取 WP-11 目标、依赖、退出条件和 P5 任务；
- 从 `requirements.md`、`design.md`、`decisions.md`、WP-02、WP-07、WP-10 读取相关约束；
- 从飞书 revision 14 的“核心验收”内嵌工作簿 revision 11 读取验收条目；
- 初始化工作包目录并编写规格和计划；
- WP11-Q01 至 Q06 按推荐方案确认，登记 D-78 至 D-83；
- `spec.md` v1.0.0 冻结，本计划 v1.0.0 批准；
- 新增 `cli.py`、`__main__.py`、`control.py`、console script 和 `tests/test_cli.py`；
- 增加 ApprovalStore 无 lease locate 和 effective-only 跨 Run 查询；
- 完成 run/resume/approvals/inspect、wait、退出码和跨进程恢复；
- `tests/test_cli.py`：`21 passed`；
- 全量测试：`314 passed`；
- Ruff lint 通过，Ruff format check 为 `89 files already formatted`；
- 已生成 `acceptance-report.md`，状态为 review。

WP-11 实现阶段结束，当前进入 VERIFYING。WP-10 accepted 前不能把验收报告转为 accepted 或把
工作包标记为 DONE。
