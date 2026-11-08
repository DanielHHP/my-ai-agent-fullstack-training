# WP-09 测试修复验收工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-09 测试修复验收 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-07 |
| 冻结日期 | 2026-11-07 |
| 依赖 | WP-06、WP-08 均已 accepted |
| 间接依赖 | WP-01 State/Audit、WP-03 Sandbox、WP-07 Evidence 失效与恢复语义 |
| 下游工作包 | WP-10、WP-12 |
| 对应需求 | `REQ-GATE-*`、`REQ-STATE-*`、`REQ-AUDIT-*`、`REQ-TASK-003`、`REQ-TASK-004`、`REQ-COMPLETE-*` |
| 对应设计 | `design.md` 第 5、8、9、10、11.3、13 节 |
| 已确认决策 | D-05、D-06、D-13、D-14、D-20、D-22、D-29 至 D-36、D-48 至 D-52、D-56、D-59 至 D-72 |
| 飞书输入 | 作业文档 `SvsPd37Zpo6v1ExX73Dc54H5nSf` revision 14；“核心验收”内嵌工作簿 revision 11 |
| 原文件任务 | P4-01、P4-02 的测试修复部分 |

本文是 WP-09 已冻结的行为与验收规格。WP-08 全场景验收和 WP-09 `acceptance-report.md` 均已
完成并 accepted。

文档冲突时按 `requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 的顺序处理。
WP09-Q01 至 WP09-Q08 已由 D-65 至 D-72 关闭，本文为已冻结规格。

## 2. 目标

WP-09 必须在模型之外建立测试修复任务的程序化验收闭环：

1. Agent 启动前，通过可信内部控制面执行任务配置中的唯一验收测试命令，捕获基线报告；
2. 记录声明 argv、有效 argv、固定 cwd、退出码、配置指纹、工作区前后状态和报告摘要；
3. 将基线测试节点持久化，使审批等待、进程退出和恢复后仍能比较原始测试集合；
4. Agent 停止后，在 `VERIFYING` 状态用同一基础命令复跑当前工作区；
5. 解析完整测试报告，证明最终测试集合没有丢失基线节点、没有新增失败或跳过；
6. 校验最终测试运行前后工作区未变化，且当前工作区仍与测试报告绑定版本一致；
7. 校验工作区累计 diff 仅包含任务 `allow_write` 允许的源码和测试目录；
8. 识别既有测试文件在目录外被修改、删除、重命名或替换，并拒绝验收；
9. 将测试报告、节点摘要、Evidence、逐项检查和最终结论关联到同一 Run；
10. 模型最终回答、模型自行调用 `run_test` 的观察结果或自然语言“测试已通过”均不能替代
    内部控制面产生的基线、最终报告和工作区事实；
11. 在基线缺失、报告不完整、超时、结果未知、旧证据失效或持久化失败时失败关闭；
12. 不执行任务配置之外的隐藏测试，也不向模型暴露验收器内部的报告路径和节点选择能力。

## 3. 范围与非目标

### 3.1 范围内

- 扩展 `completion.py` 的测试修复策略；
- 新增测试报告解析和节点标识模块，建议命名为 `test_reports.py`；
- `config.py` 中测试报告格式和命名的严格配置契约；
- `gate.py`/`registry.py` 中仅供可信 Harness 使用的固定测试验收执行定义；
- `state_store.py`/`storage.py` 中基线、测试执行和节点事实的追加式持久化；
- `domain.py` 中测试状态、基线 DTO、错误码和必要审计事件；
- 扩展 `tests/test_completion.py`，并按需扩展 `test_config.py`、`test_domain.py`、
  `test_gate.py`、`test_audit.py`、`test_state_checkpoint.py` 和 `test_tools.py`；
- WP-09 工作包文档、索引、决策记录和验收报告。

### 3.2 非目标

- 不实现 Agent、Fake Model、Harness 主循环、CLI、审批等待或跨进程 `resume`；
- 不实现代码理解策略、Markdown 解析或通用 Completion 内部控制面，这些属于 WP-08；
- 不自动修复测试、修改源码、重新运行模型或改写验收失败；
- 不运行 Agent 不可见的隐藏测试、私有测试夹具或未声明命令；
- 不证明测试断言没有被语义弱化；WP-09 只能机械检查节点、状态、文件版本和 diff；
- 不把测试 exit code 为 0 等同于报告完整、测试集合未减少或工作区未越界；
- 不把模型自行执行的 `run_test` 结果当作基线或最终验收证据；
- 不承诺容器、虚拟机或 OS 级隔离；
- 不需要真实模型、网络或开发者本机凭据。

## 4. 飞书“核心验收”吸收

来源为指定飞书文档 revision 14 中“核心验收”内嵌工作簿 revision 11。WP-09 直接负责测试修复
任务中的新旧测试证据和测试节点一致性，并为 WP-10 的最终 Run 状态提供程序化验收结果。

| 编号 | 优先级 | 飞书通过标准 | WP-09 责任 | 最终归属 |
| --- | --- | --- | --- | --- |
| H1 | P0 | 模型说完成但无交付物时，final answer 被拒绝，不能进入完成态 | 直接部分：测试修复不采信模型最终回答或模型自行运行测试的自述；必须有内部控制面基线和最终报告 | WP-09 / WP-10 / WP-12 |
| H2 | P0 | 测试通过后又修改源码，旧测试证据失效，必须重跑当前版本 | 直接：测试报告绑定运行前后工作区指纹；当前工作区变化时旧 `TEST_REPORT` Evidence 失效并拒绝接受 | WP-09 / WP-12 |
| H3 | P0 | 达到最大轮数或预算时以明确 StopReason 停止，不伪装成功 | 直接部分：非 `VERIFYING`、预算停止或 `NOT_EVALUATED` Run 不得产生 `ACCEPTED`；终态编排由 WP-10 完成 | WP-09 / WP-10 / WP-12 |
| A2 | P1 | 完整任务可关联 Gate、Sandbox、State、Approval 和执行结果 | 直接部分：基线/最终测试执行、ToolCall、Evidence、报告摘要和 CompletionItem 通过 `run_id` 与稳定 ID 可重建 | WP-09 / WP-10 / WP-12 |
| T1 | P0 | 所有 P0 场景都有确定性测试，且可重复通过 | 直接：WP09-AC 全部离线、确定、无关键 skip，并使用临时 Git 仓库和 SQLite | WP-09 / WP-12 |

跨工作包验收只有在最终归属工作包完成端到端证据后才可关闭。WP-09 通过不能单独证明真实模型、
Agent 主循环、CLI 恢复或整个项目已经验收。

## 5. 参与组件与依赖方向

| 组件 | 职责 | 不承担 |
| --- | --- | --- |
| `completion.py` | 测试修复基线捕获、最终节点比较、检查项编排和 Completion 结果 | Agent 循环、源码修复、测试命令生成 |
| `test_reports.py` | 安全解析 JUnit XML、规范节点标识、状态分类和报告摘要 | 判断 Run 状态、执行命令或写数据库 |
| `gate.py` | 提供 WP-09 专用的固定内部测试执行入口和事务生命周期 | 决定节点丢失或覆盖减少 |
| `registry.py` | 保存不可由模型选择的内部测试报告工具定义 | 暴露 handler、临时报告路径或任意 argv |
| `sandbox.py` | 固定 cwd、最小环境、进程组、超时和输出边界 | 解析测试报告或决定验收规则 |
| `config.py` | 严格声明测试命令和报告格式 | 接受模型提供的命令参数 |
| `state_store.py` | 持久化基线、测试执行、节点、Evidence 和 Completion | 决定验收业务规则 |
| `audit.py` | 记录测试执行、报告解析、节点比较和最终验收事实 | 保存测试报告原文或作为第二套状态 |
| `domain.py` | 严格 DTO、枚举和稳定错误码 | 访问文件、进程或数据库 |

依赖方向：

```text
domain.py <──── completion.py ────> config.py
                         │
                         ├────────> test_reports.py（纯解析/比较）
                         ├────────> gate.py（固定内部测试执行）
                         ├────────> sandbox.py（工作区比较公共契约）
                         └────────> state_store.py + audit.py

gate.py ───> registry.py + policy.py + sandbox.py + state/audit

WP-10/WP-12 ───> CompletionChecker + Gate + State + Audit
```

禁止：

- `completion.py` 或 `test_reports.py` 直接调用 `subprocess`、shell 或测试 handler；
- 把内部报告工具注册到模型可见 `ToolRegistry.snapshot()`；
- 接受模型提交 argv、可执行文件、cwd、报告路径、环境变量、超时或测试节点选择；
- 仅凭模型调用 `run_test` 产生的 `COMMAND` Evidence 充当基线或最终测试证据；
- 只解析 stdout 文本中的“passed/failed”字符串；
- 在失败后自动删除测试、跳过测试、修改断言或重新执行非幂等命令；
- 把测试报告原文、完整异常栈或敏感参数写入 Audit、Completion 或模型上下文；
- 用最终 exit code 为 0 替代报告节点和 diff 检查。

## 6. 公共接口与配置契约

### 6.1 候选可信入口

WP-09 扩展 WP-08 已冻结的 `CompletionChecker`，候选公共接口如下：

```text
CompletionChecker.capture_test_baseline(
    run_id: RunId,
    lease_token: str,
    *,
    expected_run_revision: int,
    startup_workspace: WorkspaceBaseline,
) -> TestBaseline

CompletionChecker.evaluate_test_repair(
    run_id: RunId,
    lease_token: str,
    *,
    expected_run_revision: int,
    startup_workspace: WorkspaceBaseline,
) -> CompletionCheck
```

前置条件：

- 只能由可信 Harness/测试装配代码调用，模型不能调用或注入参数；
- 配置、Sandbox 和 Run 绑定同一规范工作区，任务 `kind=test_repair`；
- `capture_test_baseline` 仅允许 Run 已进入 `RUNNING`、租约有效、尚无 Agent 副作用；
- `evaluate_test_repair` 仅允许 Run 已进入 `VERIFYING`、租约有效；
- 配置指纹、Run workspace 身份和注入的 `WorkspaceBaseline` 精确匹配；
- 基线只能捕获一次，存在有效基线后重复调用返回同一事实或稳定冲突；
- 最终验收不得重新计算或缩短已持久化基线。

候选内部执行入口：

```text
Gate.invoke_test_verification(
    run_id: RunId,
    tool_call_id: ToolCallId,
    *,
    phase: TestExecutionPhase,
    lease_token: str,
    expected_run_revision: int,
) -> ToolObservation
```

`phase` 只能是 `BASELINE` 或 `FINAL`。调用方不能传入 argv、报告路径、环境或任意工具名。
工具定义、基础命令、限制和内部控制面身份均来自可信装配。该入口是否直接复用
`Gate.invoke_internal`，还是作为独立窄接口实现，由 WP09-Q02 决定。

### 6.2 配置契约

`TestRepairAcceptance` 需要新增经校验并进入配置指纹的声明，候选为：

```yaml
acceptance:
  strategy: test_repair
  test_command:
    - python
    - -m
    - pytest
    - -q
  baseline_required: true
  report_format: pytest_junit_xml
```

规则：

- P0 只支持 `pytest_junit_xml`，未知格式在配置加载阶段失败关闭；
- `test_command` 仍是唯一基础命令，模型和 CLI 不能覆盖；
- 基础命令必须能够由固定适配器追加 `-o junit_family=xunit2` 和受控
  `--junitxml=<临时绝对路径>`；
- 基础命令中已有 JUnit 输出覆盖项、shell 包装、重定向或不兼容解释器时拒绝；
- 记录声明 argv 与 P0 适配器生成的有效 argv 摘要；有效 argv 仅允许差异为固定报告参数；
- 报告路径由验收器创建在独立私有临时目录，不与 State 数据库目录相邻，也不使用工作区路径；
- 配置加载不得接受模型提供任意报告格式、插件参数或报告模板。

若最终选择“配置内固定报告占位符”等其他方案，必须提升 WP-00/WP-06 配置契约并重新冻结本规格。

## 7. 测试报告协议

### 7.1 P0 格式

P0 固定支持 pytest 生成的 JUnit XML（`xunit2` 语义）：

- 根元素必须为 `<testsuites>` 或 `<testsuite>`；
- 至少包含一个 `<testcase>`；
- 每个测试用例必须有 `name`；为可验证测试文件范围，候选要求 `file` 也非空；
- `file` 必须是工作区相对 POSIX 路径，规范化后不得越界；
- `classname` 可缺失；存在时参与节点身份；
- 测试状态只从 `<failure>`、`<error>`、`<skipped>` 子元素推导；
- 一个 `<testcase>` 同时含多个终态子元素时报告无效；
- suite 声明的 `tests/failures/errors/skipped` 计数若存在，必须与解析计数一致；
- 计数不一致、空报告、解析截断或报告被其他进程覆盖时不得用于验收。

### 7.2 节点身份

候选规范节点键为：

```text
sha256(
  "pytest-junit-v1\0" +
  normalized_file + "\0" +
  normalized_classname + "\0" +
  name
)
```

规则：

- `name` 保留 pytest 参数化 ID，用于区分参数用例；
- `file`、`classname` 和 `name` 经过 Unicode 规范化、NUL 拒绝和有界裁剪前长度检查；
- 同一报告内节点键重复时报告无效，不能按集合静默去重；
- 持久化和 Audit 只保存节点键、脱敏后的有界标签、文件路径和状态，不保存参数化值原文；
- 标签仅用于人工诊断，节点守恒判断必须使用节点键；
- 文件重命名或模块 `classname` 改变会表现为旧节点丢失，必须按既有测试变更策略拒绝。

### 7.3 状态分类

| 持久化状态 | 来源 | 最终验收含义 |
| --- | --- | --- |
| `PASSED` | 无终态子元素 | 通过 |
| `FAILED` | `<failure>` | 失败 |
| `ERROR` | `<error>` | 错误 |
| `SKIPPED` | 普通 skip | 跳过 |
| `XFAIL` | pytest xfail skip 类型 | 预期失败 |
| `XPASS` | 明确识别的 xpass 标记 | 非失败但必须作为状态变化审计 |
| `UNKNOWN` | 无法稳定分类的终态 | 不可验证，不得按通过处理 |

### 7.4 报告安全上限

候选固定上限：

- 报告文件最大 8 MiB；
- 最多 100,000 个测试节点；
- 单字段原始长度最多 4,096 字符；
- 持久化节点标签最多 512 字符；
- 失败摘要最多 8 KiB，合并后写入 Completion 前再裁剪到既有字段上限；
- XML 深度、suite 数和节点数均必须显式限制；
- 禁止 `DOCTYPE`、`ENTITY` 和任何外部实体；检测到即 `UNVERIFIABLE`；
- 解析器只使用 Python 标准库的安全子集，不访问网络或解析外部资源。

超过任一上限、解析异常或未读全报告时，结果只能是 `UNVERIFIABLE`。

### 7.5 原文保留

候选 P0 策略为“临时读取、解析后删除”：

- 原始报告创建时权限为 `0600`，父目录为私有临时目录；
- 完成解析和摘要后删除原文，不把报告正文写入 State、Audit 或模型 observation；
- 持久化报告 SHA-256、字节数、parser 版本、计数和脱敏失败摘要；
- `TEST_REPORT` Evidence 的 `content_digest` 使用原始报告字节摘要；
- 需要人工复验时使用同一配置命令与工作区版本重新执行，不依赖保留敏感报告原文。

## 8. 基线捕获

### 8.1 启动顺序

```text
可信 Harness 创建/取得 Run 和租约
  -> 校验任务 kind、配置、报告格式和测试命令
  -> 注入启动 WorkspaceBaseline
  -> Run 进入 RUNNING 且尚无 Agent 副作用
  -> 通过固定内部测试入口执行 BASELINE
  -> 检查进程结果、报告完整性和测试前后工作区
  -> 解析并持久化基线运行、节点、Evidence 和 Audit
  -> 只有基线完整时才允许启动 Agent
```

基线命令可以因测试失败而返回非零退出码；这不是基础设施失败，只要进程确定结束且报告完整，
就必须记录该基线。启动失败、超时、取消、结果未知、报告缺失或报告无效时，基线未建立，
Agent 不得启动，Run 应按 WP-10 映射为 `BLOCKED` 或 `UNKNOWN_RESULT`。

### 8.2 必记事实

每条基线记录至少包含：

- `run_id`、基线记录 ID、配置指纹和 Run revision；
- `command_id=acceptance_test`、声明 argv、有效 argv 摘要和固定 cwd；
- 内部 ToolCall ID、命令退出码、状态、耗时和是否超时/取消；
- 报告 SHA-256、字节数、parser 版本和节点计数；
- 测试前后工作区 status/diff、身份、HEAD/index 和外围保护摘要；
- 全部规范测试节点键、脱敏标签、文件路径和状态；
- 脱敏且有界的失败摘要；
- 命令 Evidence、报告 Evidence、Audit 序号和创建时间。

### 8.3 基线不变量

- 基线只在 Agent 开始前捕获，不允许用 Agent 运行后的结果倒推；
- 同一 Run 只有一份有效基线；
- 基线报告节点不得被后续最终报告覆盖；
- 基线测试前后 Git-visible 工作区必须一致；测试命令修改 tracked/untracked Git-visible 文件时
  基线失败关闭；
- 基线持久化失败时不得继续启动 Agent；
- 恢复时只复用已持久化且未失效的基线，不自动重跑非幂等测试命令；
- 基线记录、节点和报告 Evidence 为追加式历史，不能改写为通过。

## 9. 最终验收

### 9.1 固定处理链

```text
验证 config/run/task kind/lease/VERIFYING/startup workspace
  -> 加载唯一有效 BASELINE 和基线节点
  -> 记录最终测试前工作区状态
  -> 通过固定内部测试入口执行 FINAL
  -> 记录最终测试后工作区状态，要求测试前后相等
  -> 安全解析最终 JUnit 报告并持久化测试执行/节点事实
  -> 比较基线节点、失败修复、新增节点和新增跳过
  -> 比较启动基线到当前工作区 diff 与 task.paths.allow_write
  -> 校验既有测试文件修改/删除/重命名边界
  -> 再次检查当前工作区仍等于最终测试后状态
  -> 检查 Audit 链、Evidence 有效性和 Run revision
  -> 生成稳定排序的 CompletionItem
  -> 原子写入 Completion-only Evidence、CompletionCheck 和 Audit
  -> 返回已持久化 CompletionCheck
```

### 9.2 通过条件

最终验收至少满足：

1. 基线存在、完整、未失效，声明命令摘要与最终执行一致；
2. 最终命令 exit code 为 0；
3. 最终报告完整、非空、可解析，且至少有一个测试节点实际执行；
4. 除基线已存在且最终保持的 `SKIPPED/XFAIL` 外，所有最终测试节点均为 `PASSED`；任何
   `FAILED`、`ERROR` 或 `UNKNOWN` 均拒绝；
5. 基线节点键集合是最终节点键集合的子集；
6. 基线中失败/错误的节点在最终报告中为通过；
7. 基线中从未跳过的节点，最终不得变为 `SKIPPED`、`XFAIL` 或 `UNKNOWN`；
8. 最终新增节点不得失败、错误或跳过；
9. 最终测试命令前后 Git-visible 工作区完全相同；
10. 最终测试完成到 Completion 提交前，工作区、HEAD/index、身份和配置指纹未变化；
11. 启动工作区到最终工作区的变化全部命中 `task.paths.allow_write`；
12. 基线测试文件不得删除、重命名或在未命中 `allow_write` 时修改；
13. 外围 Git 保护开启时没有外围变化；关闭时必须显式记录“未监控”；
14. Audit 链可验证，所有 Completion 引用只属于本 Run 且 Evidence 有效；
15. 最终测试报告、测试节点、Completion 和 Audit 全部成功持久化。

### 9.3 Completion 检查项

通过项使用稳定代码 `OK`；失败项使用第 13 节的 `COMPLETION_TEST_*` 错误码。

| `item_id` | 通过条件 |
| --- | --- |
| `test_baseline_present` | 存在唯一、完整、未失效的基线记录 |
| `test_command_consistent` | 基线与最终有效命令、配置指纹和 cwd 一致 |
| `baseline_workspace_stable` | 基线执行前后工作区一致，且启动身份有效 |
| `final_command_executed` | 最终内部测试已确定结束且 exit code 为 0 |
| `final_report_valid` | 最终报告完整、安全解析且包含测试节点 |
| `baseline_nodes_preserved` | 所有基线节点键存在于最终报告 |
| `baseline_failures_repaired` | 基线失败/错误节点最终通过 |
| `final_nodes_passed` | 所有最终节点均为通过状态 |
| `no_new_skips` | 无原本执行的节点变为跳过或预期失败 |
| `test_workspace_stable` | 最终测试前后工作区完全相同 |
| `workspace_scope` | 累计 diff 仅命中 `allow_write`，HEAD/index/身份和外围保护符合规则 |
| `test_files_scope` | 既有测试未被越权修改、删除或重命名 |
| `evidence_current` | 最终报告 Evidence 仍对应当前工作区版本且未失效 |
| `audit_chain` | 本 Run 的 Audit 链和关联事实可验证 |
| `result_persisted` | 测试事实、Evidence、Completion 和 Audit 已原子提交 |

`ACCEPTED` 当且仅当所有必需检查项 `passed=true`。已确定违规使用 `REJECTED`；报告缺失、截断、
解析器无法可靠取得事实或基础设施不可用时使用 `UNVERIFIABLE`。调用入口不得为预算停止的 Run
生成 `NOT_EVALUATED` Completion 记录。

### 9.4 测试文件边界

测试文件集合由基线报告中的规范化 `file` 字段确定，并补充任务配置中可识别的测试路径：

- 基线测试文件是只读资产，默认不允许修改；
- 只有路径命中任务 `allow_write` 的窄范围例外时，才允许修改或覆盖既有测试文件；
- 即使命中 `allow_write`，也不允许删除或重命名基线测试文件；
- `deny_write` 且未命中更窄 `allow_write` 的测试文件修改必须拒绝；
- 新增测试必须位于 `allow_write` 范围并出现在最终报告中；
- 符号链接、目录替换、大小写或 Unicode 归一化别名不能绕过路径判断；
- P0 无法证明断言强度未下降；节点保留但断言被弱化属于必须记录的限制。

## 10. 工作区与旧证据失效

### 10.1 工作区版本

测试报告必须与执行时的工作区版本绑定：

- 基线必须在执行前后工作区完全相同的条件下捕获；
- 最终报告必须绑定最终测试前和最终测试后相同的 Git-visible 工作区摘要；
- 当前状态与最终测试后摘要不同，对应报告不能支撑 `ACCEPTED`；
- 工作区身份、HEAD/index、外围保护基线或配置指纹变化时不得接受；
- Git-visible 之外的 ignored 文件不进入 P0 强保证；测试命令仍必须不依赖这些文件才能通过；
- diff/status 输出截断或无法解析时不得把未见路径当作无变化。

### 10.2 H2 陈旧证据

最终测试完成后、Completion 提交前发现源码或工作区变化时：

1. 在有效租约下调用 WP-07 的单向 Evidence 失效操作；
2. 将最终 `TEST_REPORT` Evidence 标记为失效并追加 Audit；
3. 对应检查项判为 `REJECTED`，整体不得 `ACCEPTED`；
4. 不自动重跑最终测试，非幂等命令只能在新 Run 或明确新审批/执行生命周期中重新开始；
5. 失效操作或审计失败时整体失败关闭。

跨进程恢复或后续审阅发现报告绑定的工作区版本已经变化时，同样不得把旧报告恢复为有效。

## 11. 状态、事务、并发与恢复

### 11.1 状态

- WP-09 不新增 Run 业务状态；
- 基线只允许在 `RUNNING` 且 Agent 尚未产生副作用的受控时点执行；
- 最终验收只允许在 `VERIFYING` 执行；
- `STOPPED/BUDGET_EXHAUSTED`、`FAILED`、`BLOCKED` 或 `COMPLETED` Run 不得进入最终验收；
- `AcceptanceStatus` 继续使用 `NOT_EVALUATED/ACCEPTED/REJECTED/UNVERIFIABLE`；
- Run 最终终态与 StopReason 由 WP-10 编排，WP-09 只产生验收事实。

### 11.2 持久化事务

候选拆分：

1. Gate 内部测试执行的意图、`STARTED`、handler 返回和确定结果按 WP-04/WP-06 规则提交；
2. 报告解析成功后，在新的 `BEGIN IMMEDIATE` 事务中提交测试执行、节点和
   `TEST_REPORT`/`COMMAND` Evidence；
3. 最终 Completion 事务提交 Completion-only Evidence、`CompletionCheck`、
   逐项 Audit 和最终 Audit；
4. 任一步报告解析或持久化失败时，不得伪造完整测试事实；
5. 如果命令已经执行但报告/节点未成功持久化，结果视为不可确认，不得自动重跑测试命令；
6. 已提交的只读/命令审计事实不回滚，不能用删除历史来“修复”状态。

基线记录必须在启动 Agent 前可见；最终测试执行记录必须在写 Completion 前可见。
Completion 的 Evidence ID、节点计数和报告摘要必须来自同一已提交测试执行。

### 11.3 并发与幂等

- 同一 Run 的基线捕获和最终验收串行化；
- 并发基线捕获最多一个成功，其余返回同一事实或稳定冲突；
- 并发最终验收不得执行两个非幂等测试命令；
- 已存在基线时不得重跑基线；
- 已存在最终测试执行时不得通过重试隐式产生第二份报告；
- 相同报告和相同工作区版本在同一 Run 重复验收时返回原结果或稳定冲突；
- 工作区、Run revision、租约、配置或 Evidence 版本变化时不得复用旧结论；
- 内部测试工具不进入模型工具快照，模型调用同一命令只能用 `run_test`，不能被识别为验收执行。

### 11.4 恢复

- 审批后新进程恢复时，从 StateStore 读取基线和节点，不在新进程重新执行基线；
- 读取时验证基线 schema、canonical digest、Run/config/workspace 身份和报告 Evidence 有效性；
- 基线缺失、损坏或跨 Run 时进入 `BLOCKED/UNVERIFIABLE`；
- 最终执行处于 `PREPARED` 且确定未开始时，只能重新经过 Gate；
- 最终执行已经 `STARTED` 但无确定结果时进入 `UNKNOWN_RESULT`，不得自动重放；
- 最终执行有完整持久化报告和节点、但 Completion 未提交时，可在工作区仍匹配的前提下完成验收，
  不重新运行测试；
- 最终测试完成后工作区变化时，旧报告失效并要求人工核对或新 Run。

## 12. 持久化设计候选

为支持跨进程比较，候选追加 schema migration v6。不得修改既有 migration。

### 12.1 `test_repair_baselines`

每个 Run 一行，至少包含：

- `run_id`、基线 ID、`schema_version`；
- `command_id`、declared argv/有效 argv 摘要、cwd、配置指纹；
- `workspace_baseline_json`、canonical digest、报告 Evidence ID 和解释器版本；
- `created_at`。

`workspace_baseline_json` 采用有界 canonical JSON，超限失败关闭。它仅用于 WP-09 测试修复任务，
不改变 D-61 对代码理解 Completion 的通用启动基线策略。

### 12.2 `test_executions`

记录基线和最终测试执行，至少包含：

- `test_execution_id`、`run_id`、`phase`、`command_id`；
- `tool_call_id`、`command_evidence_id`、`report_evidence_id`；
- declared argv、有效 argv digest、cwd、配置指纹；
- 测试前后工作区 digest、exit code、执行状态、耗时、超时/取消标记；
- 报告 digest、字节数、parser 版本、节点/失败/错误/跳过计数；
- 脱敏失败摘要、创建时间；
- `UNIQUE(run_id, phase)`。

### 12.3 `test_nodes`

记录每个测试节点，至少包含：

- `test_execution_id`、`node_key`、脱敏标签、规范化文件路径；
- 状态、脱敏失败类别和必要的有界诊断；
- 主键或唯一约束 `(test_execution_id, node_key)`。

`test_repair_baselines`、`test_executions` 和 `test_nodes` 均采用追加式写入，禁止 UPDATE/DELETE
触发器；节点事实不能通过覆盖改变。若审计后确认错误，应追加新的执行或新 Run 事实。

是否新增 migration、是否持久化完整工作区基线由 WP09-Q04 决定。若采用其他方案，必须仍满足
审批后跨进程恢复时不重跑基线和 H2 版本一致性要求。

## 13. 错误契约

候选新增稳定错误码，最终在 WP-09 设计冻结时写入 `domain.ErrorCode`：

```text
COMPLETION_TEST_BASELINE_MISSING
COMPLETION_TEST_BASELINE_INVALID
COMPLETION_TEST_BASELINE_STALE
COMPLETION_TEST_BASELINE_NOT_FAILING
COMPLETION_TEST_COMMAND_MISSING
COMPLETION_TEST_COMMAND_MISMATCH
COMPLETION_TEST_START_FAILED
COMPLETION_TEST_TIMEOUT
COMPLETION_TEST_RESULT_UNKNOWN
COMPLETION_TEST_EXIT_NONZERO
COMPLETION_TEST_REPORT_MISSING
COMPLETION_TEST_REPORT_EMPTY
COMPLETION_TEST_REPORT_TOO_LARGE
COMPLETION_TEST_REPORT_MALFORMED
COMPLETION_TEST_REPORT_UNSAFE_XML
COMPLETION_TEST_REPORT_TRUNCATED
COMPLETION_TEST_NODE_DUPLICATE
COMPLETION_TEST_NODE_LOST
COMPLETION_TEST_BASELINE_FAILURE_REMAINS
COMPLETION_TEST_NEW_NODE_FAILED
COMPLETION_TEST_NEW_SKIP
COMPLETION_TEST_FILE_MODIFIED
COMPLETION_TEST_FILE_DELETED
COMPLETION_TEST_WORKSPACE_UNSTABLE
COMPLETION_TEST_WORKSPACE_OUT_OF_SCOPE
COMPLETION_TEST_EVIDENCE_STALE
COMPLETION_TEST_RESULT_WRITE_FAILED
```

| 错误类别 | 触发 | 结论/重试 | 模型可见信息 |
| --- | --- | --- | --- |
| `*_BASELINE_*` | 基线缺失、损坏或版本失效 | `UNVERIFIABLE`/`REJECTED`，新 Run | 阶段、基线 ID 和原因，不回显工作区绝对路径 |
| `*_COMMAND_*`/`*_START_*`/`*_TIMEOUT` | 命令不可用、未执行或未知 | `UNVERIFIABLE`；不自动重试 | 稳定错误码和有界说明 |
| `*_REPORT_*` | 报告缺失、空、超限、XML 不安全或无法解析 | `UNVERIFIABLE` | 报告类别、大小上限，不回显原文 |
| `*_NODE_*`/`*_SKIP` | 节点丢失、失败未修复或新增跳过 | `REJECTED` | 有界节点标签/哈希和计数 |
| `*_FILE_*`/`*_WORKSPACE_*` | 越界修改、删除、工作区前后变化 | `REJECTED` | 规范化相对路径和失败类别 |
| `*_EVIDENCE_STALE` | 最终报告不再对应当前工作区 | `REJECTED`，失效并审计 | Evidence ID 和原因 |
| `*_RESULT_WRITE_FAILED` | State/Audit/Completion 写入失败 | 整体回滚；不得返回 `ACCEPTED` | 稳定基础设施错误，不泄露数据库内部信息 |

错误文本必须脱敏、有界，不包含绝对 workspace 路径、完整测试报告、源码断言、完整异常栈、
API Key、令牌、密码或敏感环境变量。

## 14. 验收场景

`WP09-AC-001` 至 `WP09-AC-032` 是候选最小验收集合。每个场景必须映射到离线自动化测试；未解决
的开放问题不能通过测试固化后绕过评审。

| ID | 场景 | 预期 |
| --- | --- | --- |
| WP09-AC-001 | 合法失败基线，修复后相同节点全部通过 | `ACCEPTED`，报告和节点可重开读取 |
| WP09-AC-002 | 未捕获基线即请求最终验收 | 不得 `ACCEPTED`，输出 `BASELINE_MISSING/UNVERIFIABLE` |
| WP09-AC-003 | 基线记录损坏、跨 Run 或 Evidence 已失效 | 拒绝，不重新计算基线 |
| WP09-AC-004 | 最终命令与基线命令/配置指纹不同 | `REJECTED` |
| WP09-AC-005 | 测试命令启动失败 | 无 Agent/验收成功；稳定失败关闭 |
| WP09-AC-006 | 基线或最终命令超时、取消或结果未知 | 不入库为完整报告，不自动重试 |
| WP09-AC-007 | 基线测试失败但报告完整 | 正确记录基线失败节点，允许 Agent 开始 |
| WP09-AC-008 | 基线报告缺失、空、超 8 MiB 或节点超限 | 基线不可用，失败关闭 |
| WP09-AC-009 | 报告含 `DOCTYPE`、`ENTITY` 或格式歧义 | `UNVERIFIABLE`，不解析外部资源 |
| WP09-AC-010 | 基线执行前后 Git-visible 工作区变化 | 基线失败关闭 |
| WP09-AC-011 | 最终 exit code 非零 | `REJECTED` |
| WP09-AC-012 | 最终 exit code 为 0 但报告缺失或为空 | 不得 `ACCEPTED` |
| WP09-AC-013 | 基线节点在最终报告消失 | `NODE_LOST`，拒绝 |
| WP09-AC-014 | 基线失败节点在最终仍失败或错误 | 拒绝 |
| WP09-AC-015 | 最终新增测试失败 | 拒绝 |
| WP09-AC-016 | 原本执行节点在最终变为 skip/xfail | `NEW_SKIP`，拒绝 |
| WP09-AC-017 | 基线已存在且最终保持的 skip | 按 WP09-Q06 决议；候选允许并显式记录 |
| WP09-AC-018 | 参数化用例减少或参数 ID 改变 | 按节点键识别为丢失，拒绝 |
| WP09-AC-019 | 基线测试文件在 `allow_write` 外修改 | 拒绝且 diff 证据可追溯 |
| WP09-AC-020 | 基线测试文件被删除、重命名或替换为符号链接 | 拒绝 |
| WP09-AC-021 | 在允许目录新增测试并全部通过 | 允许，最终节点应为基线的超集 |
| WP09-AC-022 | 源码修改命中 `allow_write` | 允许 |
| WP09-AC-023 | 源码或未跟踪文件修改未命中 `allow_write` | 拒绝 |
| WP09-AC-024 | HEAD/index/身份或外围保护变化 | 拒绝/不可验证 |
| WP09-AC-025 | 测试命令在运行中修改工作区 | `WORKSPACE_UNSTABLE`，拒绝 |
| WP09-AC-026 | 最终测试通过后、Completion 前源码再变化 | 报告 Evidence 失效并拒绝 |
| WP09-AC-027 | stdout/stderr 截断但报告文件完整 | 报告可验证，截断输出不得作为完整证据 |
| WP09-AC-028 | 模型自行调用 `run_test` 或声称测试通过 | 不产生有效基线/最终报告，不影响程序验收 |
| WP09-AC-029 | Run 不在 `VERIFYING` 或因预算停止 | 不产生 `ACCEPTED`，保持 `NOT_EVALUATED` |
| WP09-AC-030 | State/Audit/Completion 写入故障 | 事务回滚，不得返回 `ACCEPTED` |
| WP09-AC-031 | 内部测试工具出现在模型 snapshot 或被模型调用 | Registry/Gate 拒绝，snapshot 不包含内部工具 |
| WP09-AC-032 | 同一 Run 重复/并发验收 | 不重复执行非幂等测试；返回原结果或稳定冲突 |

验收还必须覆盖：

- 两阶段测试均使用临时 Git 仓库、临时 SQLite、确定性 ID/时钟和可控 fake report/command；
- 测试不使用真实网络、真实模型、用户凭据或开发者本机工作区；
- `tests/test_completion.py` 存在并通过；
- 目标测试、全量 pytest、Ruff lint 和 Ruff format check 通过；
- 所有 P0 场景均可重复运行，没有未解释的关键 skip。

## 15. 已确认决策

2026-11-07 经需求方确认，WP09-Q01 至 WP09-Q08 全部采用推荐方案：

| 原问题 | 决策 | 冻结结论 |
| --- | --- | --- |
| WP09-Q01 报告格式与命令适配 | D-65 | P0 仅支持 pytest JUnit XML；固定适配器追加 JUnit 参数并进入配置指纹 |
| WP09-Q02 内部测试执行控制面 | D-66 | 新增固定 `invoke_test_verification`；基线 `RUNNING`、最终 `VERIFYING` |
| WP09-Q03 内部测试审批 | D-67 | 可信配置命令不额外要求模型审批；模型 `run_test` 策略不变 |
| WP09-Q04 跨进程基线 | D-68 | migration v6 持久化 WorkspaceBaseline、测试执行和节点 |
| WP09-Q05 全绿基线 | D-69 | 基线必须至少有一个失败/错误节点 |
| WP09-Q06 skip/xfail | D-70 | 不变的既有 skip/xfail 允许；新增跳过拒绝 |
| WP09-Q07 报告原文 | D-71 | 解析后删除原文，只保存摘要、计数和脱敏事实 |
| WP09-Q08 资源上限 | D-72 | 报告 8 MiB、100,000 节点及固定字段上限 |

后续如改变上述结论，必须提升规格版本并同步决策、设计和下游契约测试。

## 16. 来源与追溯

| 来源 | 使用方式 |
| --- | --- |
| `requirements.md` `REQ-TASK-003`、`REQ-TASK-004`、`REQ-COMPLETE-*` | 测试修复、模型自述边界和最终验收 |
| `design.md` 11.3 | 基线、最终复跑、节点守恒、工作区前后一致和公开命令 |
| `task-breakdown.md` WP-09 | 工作包范围、退出条件、高风险管理入口和下游 |
| `decisions.md` D-05、D-06、D-13、D-22、D-48 至 D-52、D-56、D-59 至 D-72 | 测试目录、Evidence、非幂等命令、未知结果、内部控制、测试报告和陈旧事实 |
| WP-05/WP-06 冻结规格 | `READ`/`COMMAND` Evidence、受控命令、输出上限和 `UNKNOWN_RESULT` |
| WP-07 冻结规格 | Evidence 单向失效和恢复不重放语义 |
| WP-08 冻结规格 | Completion 框架、内部访问、原子结果和基线注入 |
| 飞书文档 revision 14 | 只吸收内嵌“核心验收”表 revision 11 的 H1、H2、H3、A2、T1 相关要求 |

## 17. 当前结论

WP-09 规格已冻结，WP09-Q01 至 Q08 已关闭，工作包当前为 `DONE`。`completion.py` 测试修复策略、
JUnit 解析器、基线持久化、Gate 内部测试执行、全场景自动化矩阵和 `acceptance-report.md` 均已完成
并 accepted。
