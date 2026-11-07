# Guarded Coding Harness 工作包开发拆解

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档状态 | P0 工作包计划基线，作为详细设计、施工和验收报告输入 |
| 对应需求 | [requirements.md](requirements.md) |
| 对应设计 | [design.md](design.md) |
| 已确认决策 | [decisions.md](decisions.md) |
| 开发资源 | 单开发者；默认串行施工，但按实际依赖而非文件顺序安排 |
| 组织单位 | 可独立实现、测试、审查和回滚的能力工作包 |

本文按“里程碑 -> 工作包 -> 实施任务”组织 P0 开发。工作包是计划和验收单位，文件是包内的
实施对象；同一个文件可以由多个工作包分阶段扩展。本文描述计划，不代表相关能力已经实现。

## 2. 工作包定义

### 2.1 什么是一个工作包

一个工作包必须形成一个可从外部验证的能力闭环，并同时包含：

- 明确的输入、前置契约和输出公共接口；
- 包内源码、配置、迁移、测试和必要文档；
- 正常路径、失败路径和安全边界测试；
- 可供复核的测试命令、工作区证据或审计证据；
- 明确的非目标和剩余风险。

“创建某个文件”或“写完某个类”不是工作包完成条件。例如，`gate.py` 的第一阶段扩展属于
WP-04，后续真实工具接入和生命周期扩展仍可在 WP-05、WP-06 中继续完成。

### 2.2 与文件任务的关系

原 `P0-*`、`P1-*` 等文件任务 ID 继续保留，作为工作包内的实施项和需求追溯锚点。它们不再各自
代表一个可独立验收的里程碑，也不要求测试文件单独成为一个任务。

原则如下：

- 工作包可以修改多个文件，但每个文件只承担清晰、单一的职责；
- 一个文件可以被多个工作包按能力增量扩展；
- 测试、配置和文档在验证该能力的工作包内同步完成；
- 工作包通过验收时生成 `acceptance-report.md`，用实际运行证据记录完成情况、限制和结论；
- 跨模块契约先在所属工作包中确定，再允许下游工作包依赖；
- 每个公共契约变化都必须同步契约测试、设计说明和错误码。

### 2.3 统一完成定义

工作包只有同时满足以下条件才可标记完成：

1. 工作包声明的能力可通过公共接口或明确的内部验收入口执行；
2. 正常路径、拒绝路径和关键故障路径测试存在并通过；
3. 安全拒绝发生在 handler、I/O 或进程副作用之前；
4. 无法确认副作用时产生 `UNKNOWN_RESULT`，不自动重放；
5. State、Audit、Approval 和 Checkpoint 的关联符合概要设计；
6. Ruff lint、Ruff format check 和相关 pytest 通过；
7. 新增公共 API、错误码、持久化格式或安全语义已同步文档；
8. 没有把未完成能力写成“已实现”；
9. `acceptance-report.md` 已生成、复核并标记为 `accepted`；
10. 工作包可以单独审查，失败时可以回滚到前一安全状态。

## 3. 组织原则

### 3.1 垂直能力优先

工作包优先覆盖“输入 -> 门禁 -> 执行 -> 状态/审计 -> 可验证结果”的完整路径，避免先写完所有
接口再集中联调。首个安全执行里程碑必须在接入 Agent 之前完成。

### 3.2 先冻结契约，再扩展实现

以下高风险工作包进入施工前必须先完成详细设计评审：

- WP-01：SQLite schema、事务组合和状态机；
- WP-02：审批摘要、状态机和一次性消费；
- WP-04：Gate 固定检查链和执行生命周期；
- WP-07：Checkpoint 快照、恢复判定和租约；
- WP-09：测试报告解析和测试节点证据；
- WP-10：`smolagents==1.26.0` 公开适配边界。

详细设计至少明确公共类型、错误码、事务边界、并发行为、迁移方式和失败关闭规则。评审未通过时
不进入施工。

### 3.3 测试随能力交付

不设置独立“补测试”工作包。`tests/test_gate.py` 等六个必须测试文件可以在多个工作包中逐步扩展，
但每次扩展都必须与被验证能力一起提交。WP-12 只做全局故障矩阵和最终验收，不替代包内测试。

### 3.4 文件级施工规则

- 公共 API、复杂安全判定和恢复逻辑使用类型标注及清晰 docstring；
- 跨模块对象使用 Pydantic 模型或 dataclass，不使用裸字典漂移；
- SQLite migration 只追加，不修改已经发布或已被测试使用的 migration；
- 写文件、进程和审批相关操作默认串行；
- 不使用 `step_callbacks` 作为唯一安全门禁；
- 不复制、修改或 monkey patch `smolagents` 核心；
- 不通过提示词、模型最终回答或审计日志替代程序化验收。

## 4. 工作包总览

| 工作包 | 能力目标 | 依赖 | 原文件任务 | 主要退出证据 |
| --- | --- | --- | --- | --- |
| WP-00 工程与契约内核 | 工程可安装，公共模型、配置和测试基础设施可用 | 无 | P0-01 至 P0-09 | 安装、导入、严格配置校验和契约测试通过 |
| WP-01 持久化与审计底座 | Run 状态、预算、脱敏和 append-only 审计形成事务闭环 | WP-00 | P1-01 至 P1-05 | 重开数据库可读，非法转换和审计篡改被拒绝 |
| WP-02 审批生命周期 | 审批绑定、过期、认领和一次性消费可独立验证 | WP-01 | P1-06，P1-08 审批部分 | 篡改、重放、跨运行和过期审批全部拒绝 |
| WP-03 Sandbox 边界 | 路径、Git、环境、命令和进程边界可独立验证 | WP-00 | P2-01、P2-02 | 越界发生在真实 I/O 或进程副作用前 |
| WP-04 治理执行内核 | Registry、Policy、Gate 和执行生命周期形成安全闭环 | WP-01、WP-02、WP-03 | P3-01 至 P3-03、P3-09、P3-11 核心部分 | 使用测试 handler 证明拒绝不触达、允许最多执行一次 |
| WP-05 只读仓库能力包 | 文件读取、搜索、目录读取和 Git 查看经过 Gate 产生证据 | WP-04 | P3-04、P3-06、P3-08 前半、P3-10 前半 | 只读工具输入有界，读取证据可关联文件和内容版本 |
| WP-06 写入与命令能力包 | 写入、patch 和受控测试命令接入审批、超时与未知结果 | WP-04、WP-05 | P3-05、P3-07、P3-08 后半、P3-10、P3-11 后半 | 审批前无写入，命令进程组可终止，未知结果不重放 |
| WP-07 Checkpoint 与恢复 | 安全快照、租约和崩溃窗口判定形成恢复闭环 | WP-01、WP-02、WP-06 | P1-07、P1-08 恢复部分 | 各崩溃窗口得到确定恢复动作，未知结果停止自动恢复 |
| WP-08 验收引擎与代码理解 | 程序化验收框架和代码理解策略可用 | WP-04、WP-05 | P4-01、P4-02 代码理解部分 | 文档、引用、读取证据和 diff 可机械验收 |
| WP-09 测试修复验收 | 基线、最终测试节点、报告和 diff 证据形成闭环 | WP-06、WP-08 | P4-01、P4-02 测试修复部分 | 旧证据、测试丢失、跳过和结果未知均被拒绝 |
| WP-10 Agent 与 Harness | Fake Model、公开适配和 Harness 编排形成 Agent 闭环 | WP-07、WP-08、WP-09 | P5-01 至 P5-03、P5-06 | 原生 ToolCallingAgent 循环保留，所有工具调用经过 Gate |
| WP-11 CLI 与人工恢复 | run、resume、approvals、inspect 共享受控运行链 | WP-07、WP-10 | P5-04、P5-05、P5-07 | 审批后新进程恢复，写操作只执行一次 |
| WP-12 故障加固与交付 | 全局故障矩阵、样例配置、测试与运维文档形成最终交付 | WP-00 至 WP-11 | P6-01 至 P6-07 | 两类任务端到端通过，完整证据链可复验 |

## 5. 依赖关系与施工顺序

### 5.1 能力依赖图

```text
WP-00 ───────────────> WP-03 ────────────────┐
  │                                          │
  └──> WP-01 ───> WP-02 ─────────────────────┼──> WP-04
                                               │
                                               └──> WP-05
                                                      │
                                                      └──> WP-06
                                                             │
WP-01 + WP-02 + WP-06 ──────────────────────────────────────> WP-07

WP-04 + WP-05 ─────────────────────────────────────────────> WP-08
WP-06 + WP-08 ─────────────────────────────────────────────> WP-09
WP-07 + WP-08 + WP-09 ─────────────────────────────────────> WP-10
WP-07 + WP-10 ─────────────────────────────────────────────> WP-11
WP-00 至 WP-11 ────────────────────────────────────────────> WP-12
```

### 5.2 推荐施工顺序

单开发者默认按以下顺序施工：

```text
WP-00 -> WP-01 -> WP-02 -> WP-03 -> WP-04 -> WP-05
      -> WP-06 -> WP-07 -> WP-08 -> WP-09
      -> WP-10 -> WP-11 -> WP-12
```

WP-02 与 WP-03 在 WP-00 完成后没有直接依赖，可在多人协作时并行。WP-08 的代码理解验收可以在
WP-06 完成前开始，因为它只依赖 Gate 和只读工具；但 WP-09 必须等待 WP-06。

### 5.3 里程碑

| 里程碑 | 工作包 | 可验证结果 |
| --- | --- | --- |
| M0 契约基线 | WP-00 | 工程可复现，模型和配置契约冻结 |
| M1 可信无 Agent 执行 | WP-01 至 WP-06 | Gate、Sandbox、审批和真实工具可安全执行 |
| M2 可恢复验收闭环 | WP-07 至 WP-09 | 崩溃恢复判定和两类任务程序化验收可用 |
| M3 Agent 运行闭环 | WP-10、WP-11 | Fake Model 驱动原生 Agent，CLI 支持审批和恢复 |
| M4 P0 最终交付 | WP-12 | 全量测试、故障注入、样例和文档通过 |

### 5.4 里程碑执行规程

每个工作包按以下顺序推进，不得把包级测试、故障注入和文档同步全部推迟到里程碑末：

1. **准入检查**：依赖工作包已验收，上游公共契约和 migration 已冻结。
2. **详细设计**：高风险工作包完成 WP 定义中要求的详细设计评审和开放问题收口。
3. **施工拆分**：把工作包拆成可单独测试、审查和回滚的增量任务。
4. **测试先行**：先建立正常路径、拒绝路径和故障路径测试，再实现对应能力。
5. **增量验证**：每个增量运行目标测试；工作包结束前运行全量测试、Ruff lint 和格式检查。
6. **编写验收报告**：汇总实际命令、输出、规格场景、任务卡、安全边界、限制和结论，形成
   `acceptance-report.md`。
7. **包级封版**：验收报告复核为 `accepted` 后，记录公共接口、错误码、migration、测试证据和
   剩余风险，再进入下游。

单开发者一次只允许一个工作包处于实现状态。WP-02 与 WP-03 等没有直接依赖的包可以在多人协作时
并行，但接口或 migration 冲突未解决前不得同时合并。

工作包执行状态使用 `READY -> DESIGNED -> IN_PROGRESS -> VERIFYING -> DONE`。只有验收报告
状态为 `accepted`，工作包才允许进入 `DONE`。依赖未满足、详细设计未评审、验收报告为
`rejected` 或验收证据不足时，状态最多为 `READY`、`VERIFYING` 或 `BLOCKED`。动态状态由
工作看板维护，不回写为本文的“已完成功能”声明。

每个增量提交应尽量形成一个可测试行为，提交信息标明工作包和对应 P 任务，例如：

```text
feat(wp-01): add versioned SQLite migrations
feat(wp-01): add append-only audit store
test(wp-01): cover audit rollback and concurrent writes
```

工作包关闭时至少保存以下证据：

- `acceptance-report.md`，包含代码基线、环境和结论；
- 目标测试和全量测试结果；
- Ruff lint 与格式检查结果；
- 正常、拒绝和故障场景清单；
- 拒绝发生时 handler、I/O 或进程未执行的证据；
- 公共契约、migration、错误码和文档变化；
- 未解决问题、风险承受者和是否阻塞下游。

### 5.5 M0 契约基线

**范围**：WP-00。

**准入条件**：工作包计划完成评审，关键技术栈和依赖边界无未决项。

**包内施工顺序**：

1. 工程脚手架、依赖、构建和测试入口；
2. 枚举、ID、错误码、领域模型和模块协议；
3. 临时 Git 仓库、SQLite、时间和故障注入 fixture；
4. YAML 严格加载、路径归一化和配置指纹；
5. 模型 round-trip、非法字段、非法预算和路径逃逸测试。

**完成演示**：

- 新环境可通过 `python -m pip install -e '.[dev]'` 一次完成项目、运行时依赖和开发工具安装；
- 模块可导入，领域对象可稳定 round-trip；
- 两份任务配置 Fixture 可加载，非法配置全部失败关闭；
- 配置字段变化会改变指纹，等价配置得到相同指纹；
- 测试不使用真实模型、网络、用户凭据或本机绝对路径。

**验收命令**：

```bash
python -m pip install -e '.[dev]'
python -m pytest tests/test_domain.py tests/test_config.py -q
python -m ruff check .
python -m ruff format --check .
```

**封版内容**：公共领域模型、配置 schema、配置指纹规则、测试 fixture 契约。

**进入 M1 的条件**：上述验收全部通过，WP-00 验收报告已标记 `accepted`，公共字段、默认值、
ID 类型和序列化格式没有未决项。M0 不提前实现 Gate、Sandbox、真实仓库工具或 Agent。

### 5.6 M1 可信无 Agent 执行

**范围**：WP-01、WP-02、WP-03、WP-04、WP-05、WP-06。

**准入条件**：M0 已验收，WP-00 公共契约已封版。

**包内施工顺序**：

```text
WP-01 -> WP-02 -> WP-03 -> WP-04 -> WP-05 -> WP-06
```

主要原因：

- 先稳定 SQLite 事务、状态和审计，审批与后续 Gate 才能建立一致事实；
- Sandbox 在 Gate 前完成，使 Gate 可以消费真实路径和命令边界；
- WP-04 先使用测试 handler 验证固定检查链，不等待真实工具；
- WP-05 先接只读能力，WP-06 再接写入和进程能力，降低定位副作用的复杂度。

**完成演示**：

1. 合法只读调用经过 Gate 并产生路径、行范围和内容版本证据；
2. 未授权工具、越界路径、敏感文件和危险命令在 handler 前被拒绝；
3. 写操作首次运行进入 WAITING_APPROVAL，文件不变；
4. 批准后写入只执行一次，重复提案被 replay suppression 拦截；
5. 审批参数篡改、过期、重放和跨 Run 使用被拒绝；
6. 测试命令超时后进程组终止，输出有界截断，非幂等超时进入 `UNKNOWN_RESULT`；
7. State、Audit、Approval 和 ToolCall 记录可通过关联 ID 重建该执行链。

**验收命令**：

```bash
python -m pytest tests/test_audit.py -q
python -m pytest tests/test_state_checkpoint.py -q
python -m pytest tests/test_sandbox.py -q
python -m pytest tests/test_registry_policy.py tests/test_tools.py -q
python -m pytest tests/test_gate.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

**封版内容**：ToolSpec、Gate 调用契约、ToolObservation、Sandbox 结果模型、审批摘要和事务参与接口。

**进入 M2 的条件**：M1 演示全部通过，WP-01 至 WP-06 均已关闭，Gate 拒绝链和真实工具集成
测试没有跳过项。不得以“Agent 尚未接入”为由延迟 Gate 或 Sandbox 的安全测试。

### 5.7 M2 可恢复验收闭环

**范围**：WP-07、WP-08、WP-09。

**准入条件**：M1 已验收，Gate 执行生命周期、真实工具和审批消费语义稳定。

**包内施工顺序**：

```text
WP-07 -> WP-08 -> WP-09
```

WP-08 的 Completion 基础框架在依赖满足后可以独立推进，但测试修复策略必须等待 WP-06 的受控命令
和 WP-08 的验收框架完成。

**完成演示**：

1. PREPARED 且未 STARTED 的调用恢复时必须重新经过 Gate；
2. STARTED 后无确定结果的调用进入 `UNKNOWN_RESULT`，不得自动重放；
3. SUCCEEDED/FAILED 调用使用已有结果，跨连接 reopen 后预算不清零；
4. 配置、工作区指纹、外围脏路径、审批、租约或 schema 变化时恢复进入 BLOCKED；
5. 代码理解文档可通过，缺少文档、无事实引用、引用未读或越界 diff 被拒绝；
6. 测试修复记录基线、最终报告和测试节点，基线节点丢失、新增跳过或旧证据被拒绝；
7. Completion 结果无法写入 StateStore 或 Audit 时不得 ACCEPTED。

**验收命令**：

```bash
python -m pytest tests/test_state_checkpoint.py -q
python -m pytest tests/test_completion.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

**封版内容**：Checkpoint schema、恢复动作结果、continuation context schema、Completion 结果和
测试报告证据格式。

**进入 M3 的条件**：M2 演示全部通过，崩溃矩阵和两类任务验收策略均可由程序复验。
`UNKNOWN_RESULT` 不得通过人工确认或其他旁路被当作自动恢复成功。

### 5.8 M3 Agent 运行闭环

**范围**：WP-10、WP-11。

**准入条件**：M2 已验收，恢复动作和 Completion 结果契约稳定。

**包内施工顺序**：

1. 使用 Fake Model 验证脚本化工具调用、异常、预算和并行调用；
2. 完成 `smolagents==1.26.0` 公开适配契约测试；
3. 通过 GovernedTool 和 Gate 完成首次 Agent 工具调用；
4. 完成 Harness 新建 Run、流式停止、预算、Checkpoint 和 Completion；
5. 完成 CLI run、approvals、inspect 和 resume；
6. 完成审批后跨进程恢复演示。

**完成演示**：

1. Fake Model 驱动原生 `ToolCallingAgent`，模型提示和工具 schema 中不暴露 handler；
2. 单工具和多工具调用保留原始 `tool_call_id`，所有调用经过 Gate；
3. 默认运行在审批点返回 WAITING_APPROVAL，进程退出而文件不变；
4. 审批命令只改变授权，不执行 handler；
5. 新进程 `resume` 重新校验配置、工作区、参数摘要和审批，只执行一次写入；
6. 预算耗尽固定得到 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`；
7. final answer 只是候选结果，ACCEPTED 只能由 Completion 产生。

**验收命令**：

```bash
python -m pytest tests/test_harness_loop.py -q
python -m pytest tests/test_cli.py -q
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

**封版内容**：Harness 公共入口、RunRequest/RunResult、Agent 适配契约、CLI 命令和退出码。

**进入 M4 的条件**：M3 演示全部通过，跨进程恢复、审批和预算终态没有未解决缺陷。

### 5.9 M4 P0 最终交付

**范围**：WP-12。

**准入条件**：M0 至 M3 已验收，全部工作包状态为 DONE。

**包内施工顺序**：

1. 建立跨事务边界的进程退出、审计失败、DB 锁和 Checkpoint 损坏注入；
2. 完成 `UNKNOWN_RESULT`、已确定写入和预算恢复故障矩阵；
3. 使用正式配置加载器校验两类任务样例；
4. 建立需求、决策、测试文件和验收证据的完整映射；
5. 完成运行、审批、恢复、日志、退出码和禁止操作文档；
6. 执行全量测试、lint、格式和两类任务端到端验收。

**完成演示**：

1. 代码理解任务由 Fake Model 完成并通过程序化验收；
2. 测试修复任务记录基线、复跑最终测试并检查节点和 diff；
3. 完成一次审批后跨进程 resume；
4. 在 STARTED 后注入崩溃并证明不会自动重放；
5. 六个指定测试文件全部存在并通过，没有替代或跳过；
6. 文档明确区分计划、已实现能力、剩余风险和未完成项。

**验收命令**：

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

**最终出口**：生成最终验收记录，包含命令、结果、覆盖场景、证据位置、剩余风险和明确未实现项。
M4 不增加新的 P0 功能；发现问题时回到对应工作包修复，并通过该工作包和受影响下游里程碑重新验收。

## 6. 工作包详细定义

### WP-00 工程与契约内核

**目标**：建立可安装、可测试、可严格校验的工程基线，并冻结跨模块共同契约。

**依赖**：无。

**包内文档**：[冻结规格](work-packages/wp-00-engineering-contracts/spec.md)（v1.2.0 / frozen；
Approval/config 修订已由 WP-02 T01 实现）、
[实施计划](work-packages/wp-00-engineering-contracts/plan.md)（v1.1.0 / done）、
[验收报告](work-packages/wp-00-engineering-contracts/acceptance-report.md)（accepted）。

**主要范围**：

- `.gitignore`、`.python-version`、`pyproject.toml`；
- `src/guarded_harness/__init__.py`、`domain.py`、`config.py`；
- `tests/conftest.py`、`tests/test_domain.py`、`tests/test_config.py`；
- 详细设计所需的错误码、ID、时间和序列化约束。

**交付内容**：

- 固定 Python、`smolagents==1.26.0`、Pydantic 2、pytest 和 Ruff 的依赖边界；
- 定义 Run、Step、ToolInvocation、ToolObservation、ToolSpec、Approval、Evidence 和 Completion
  等公共模型，以及枚举、稳定错误码和模块协议；
- 严格加载 YAML，拒绝未知字段，完成相对路径归一化和配置指纹；
- 提供临时 Git 仓库、工作子目录、SQLite、可控 ID/时间及故障注入 fixture；
- 用合法与非法配置验证字段、路径、预算、交付物和指纹稳定性。
- 生成 `acceptance-report.md`，记录安装、测试、Ruff、格式、依赖一致性、场景映射和剩余风险。

**退出条件**：

- `pip install -e '.[dev]'`、模块导入、领域 round-trip 和配置测试可复现；
- 未知字段、缺失字段、路径逃逸、重复交付物和非法预算全部拒绝；
- 两份 Fixture 任务配置可独立加载，配置变化会改变指纹；
- 公共字段、默认值、ID 类型和序列化格式没有未决项；
- 测试不依赖网络、真实模型、用户凭据或本机绝对路径；
- 验收报告状态为 `accepted`，且所有命令结果为实际复跑证据而非计划声明。

**不在本包内**：SQLite 业务表、Gate、Sandbox 执行逻辑、真实工具和最终示例配置。

### WP-01 持久化与审计底座

**目标**：建立 StateStore、Audit 和共享事务边界的可信持久化底座。

**依赖**：WP-00。

**包内文档**：[冻结规格](work-packages/wp-01-persistence-audit/spec.md)（v0.3.0 / frozen）、
[实施计划](work-packages/wp-01-persistence-audit/plan.md)（v0.3.0 / done）、
[验收报告](work-packages/wp-01-persistence-audit/acceptance-report.md)（accepted）。

**主要范围**：

- `storage.py`、`state_store.py`、`redaction.py`、`audit.py`；
- `tests/test_audit.py` 和 `tests/test_state_checkpoint.py` 的状态持久化部分；
- State、Audit 和后续 Approval、Checkpoint 共用的 migration 与事务 API。

**交付内容**：

- SQLite 连接生命周期、版本化 migration、WAL、外键、busy timeout 和
  `BEGIN IMMEDIATE` 事务；
- Run、Step、ToolCall、预算、证据和 Completion 状态模型的条件更新与合法状态机；
- Run 级租约和并发写冲突处理；
- append-only 审计事件、稳定序号、canonical JSON、`prev_hash/event_hash` 和关联 ID；
- 统一脱敏、循环引用保护、有界裁剪和敏感值检测；
- 可让状态与审计在同一事务中提交或共同回滚的事务协作接口。

**退出条件**：

- 关闭并重开数据库后可读取完整状态、预算和审计；
- 非法状态转换、版本冲突、失效租约和直接 SQL 修改/删除审计均被拒绝；
- 审计写入失败时业务状态回滚；状态写入失败时审计不得单独提交；
- 敏感环境变量、Authorization 和已知凭据形态不会进入状态或审计；
- 并发写不会产生重复序号或状态覆盖。

**不在本包内**：审批决定、Checkpoint 恢复动作、Gate 执行意图和 Agent 运行。

### WP-02 审批生命周期

**目标**：完成与工具执行解耦的审批对象、绑定摘要和一次性消费语义。

**依赖**：WP-01。

**包内文档**：[冻结规格](work-packages/wp-02-approval-lifecycle/spec.md)（v0.2.0 / frozen）、
[完成计划](work-packages/wp-02-approval-lifecycle/plan.md)（v0.2.0 / done）、
[验收报告](work-packages/wp-02-approval-lifecycle/acceptance-report.md)（accepted）。D-23 至
D-28 已关闭全部审批契约问题；T01-T08 已完成。

**主要范围**：

- `approval.py`；
- `tests/test_state_checkpoint.py` 的审批状态部分；
- 审批 schema/migration、摘要 canonicalization 和审计事件。

**交付内容**：

- 创建、批准、拒绝、过期、撤销、claim 和 consume 操作；
- 审批摘要绑定 `run_id`、任务、`git_root`、`workspace_root`、`workspace_prefix`、
  工具名、规范参数、配置指纹和有效期；
- 条件更新和租约保护的 `PENDING/APPROVED/CLAIMED/CONSUMED` 状态机；
- 审批变化与审计事件的原子提交；
- 为本地 CLI 和未来外部通道保留 `ApprovalChannel` 接口。

**退出条件**：

- 参数、工作区、任务、配置、运行或有效期变化会使旧审批失效；
- 重放、跨 Run、跨工作区、过期、撤销和已消费审批均被拒绝；
- claim 只能成功一次，consume 只能由确定结果触发；
- 审批 API 本身不执行任何 handler、文件或进程操作；
- 审批与审计可通过 `approval_id` 完整关联。

**不在本包内**：CLI 审批命令、Gate 审批检查、等待模式和应用执行。

### WP-03 Sandbox 边界

详细规格与计划见 [spec.md](work-packages/wp-03-sandbox-boundary/spec.md) 和
[plan.md](work-packages/wp-03-sandbox-boundary/plan.md)；实测证据见
[acceptance-report.md](work-packages/wp-03-sandbox-boundary/acceptance-report.md)。

**目标**：形成可复用的路径、Git 和进程安全边界，拒绝行为在副作用前发生。

**依赖**：WP-00。

**主要范围**：

- `sandbox.py`、`tests/test_sandbox.py`；
- 路径判定、10 MiB 有界安全 I/O、Git 基线、命令规格、环境过滤、输出和 POSIX 进程结果模型。

**交付内容**：

- 解析 `git_root`、`workspace_root` 和 `workspace_prefix`，处理尚不存在路径和符号链接；
- 使用 `Path.relative_to()` 等路径关系判断，不使用字符串前缀；
- 平台级敏感读规则，任务级 `deny_read`、`deny_write` 和窄范围 `allow_write`；
- 由 Sandbox 提供整文件有界读取和原子写入，具体工具 handler 不自行打开目标仓库文件；
- 记录工作区 status/diff、仓库外围 Git-visible 脏路径哈希、HEAD 和 index；
- 仅接受可信 argv，固定 `cwd`，使用 `shell=False` 和最小环境变量；
- 独立进程组、超时终止、宽限后强杀、stdout/stderr 上限和截断标记；
- 将超时或不可确认结果映射为 `UNKNOWN_RESULT` 输入。

**退出条件**：

- `..`、绝对路径越界、符号链接逃逸、大小写/分隔符绕过和敏感读写测试通过；
- 超限读取、写摘要冲突和原子写故障均失败关闭，不返回部分内容或留下部分目标；
- 未授权命令、自定义解释器、shell 包装、重定向和环境注入被拒绝；
- Linux/macOS 超时后 POSIX 进程组确实终止，其他平台在命令能力构造时失败关闭；
- stdout/stderr 各以 1 MiB 为上限，截断被显式标记且不能作为完整验收报告；
- 拒绝用例验证真实 handler、I/O 或进程未执行；
- 工作区外已有脏文件变化、HEAD/index 或工作区身份变化可被检测。

**不在本包内**：Gate 检查顺序、审批、业务状态、具体仓库工具和 Agent。

### WP-04 治理执行内核

**目标**：让 Registry、Policy 和 Gate 组成唯一可验证的执行入口。

**依赖**：WP-01、WP-02、WP-03。

**主要范围**：

- `registry.py`、`policy.py`、`gate.py`；
- `tests/test_registry_policy.py`、`tests/test_gate.py` 的核心部分；
- 工具注册快照、固定检查链、执行意图和结果生命周期。

**交付内容**：

- 工具元数据：名称、描述、输入模型、风险、权限、超时、重试、幂等性、路径参数、
  输出上限和并发类别；
- 任务模式、角色、风险和模型可见性策略；
- Gate 固定执行顺序：可见性、schema、语义规范化、权限、风险、审批、重复和预算；
- 每次调用重新校验，不信任模型可见工具白名单；
- 执行前持久化意图和审计，审批 claim，写 `STARTED`，调用 handler，再写确定结果或
  `UNKNOWN_RESULT`；
- 已完成写调用的 replay suppression；
- 有界、脱敏、结构化的 ToolObservation。

**退出条件**：

- 每个门禁阶段都有独立拒绝测试，且 handler 执行次数为 0；
- 未知字段、错误类型、越权工具、plan 模式写入和非法路径均被拒绝；
- 审批参数篡改、重放、过期和跨 Run 使用均被拒绝；
- 同一写操作最多成功执行一次，恢复时重复提案返回原证据；
- PREPARED、STARTED、SUCCEEDED、FAILED、TIMED_OUT 和 UNKNOWN_RESULT 的状态转换可验证；
- 审计或执行意图无法写入时不执行新的副作用。

**施工前详细设计**：必须给出每阶段伪代码、事务边界、Run 互斥方式、错误码和未知结果处理。

**不在本包内**：具体文件/进程 handler、Agent、Completion 和 CLI。

### WP-05 只读仓库能力包

**包内文档**：[规格](work-packages/wp-05-readonly-repository-tools/spec.md)（v1.0.0 / frozen）、
[完成计划](work-packages/wp-05-readonly-repository-tools/plan.md)（v1.0.0 / done）、
[验收报告](work-packages/wp-05-readonly-repository-tools/acceptance-report.md)（v1.0.0 / accepted）。

**目标**：将只读仓库操作接入 Gate，并产生可供完成验收使用的读取证据。

**依赖**：WP-04。

**主要范围**：

- `tools/fs_read.py`、`tools/git.py` 和 `tools/__init__.py` 的只读工具注册；
- `tests/test_tools.py` 和 `tests/test_gate.py` 的只读集成部分。

**交付内容**：

- `list_files`、`search_code`、`read_file`、`read_directory`；
- `git_status`、`git_diff`；
- 严格输入 schema、分页/结果上限、pathspec 和输出结构；
- 文件相对路径、完整行范围、文件内容哈希和读取时的 Run/步骤关联；
- 二进制、大文件和敏感文件处理；
- 稳定的任务级工具注册顺序和模型可见描述。

**退出条件**：

- 所有只读执行都通过 Gate，handler 和内部路径不暴露给模型；
- 读取结果能证明文件路径、行范围、内容版本、`run_id` 和 `tool_call_id`；
- 单次读取和搜索有明确结果上限，截断状态可见；
- Git 工具只执行受控只读操作，不修改 index 或工作区；
- 越界、敏感和非法 pathspec 调用不会触达 handler。

**不在本包内**：任何写入、patch、测试命令或最终验收策略。

### WP-06 写入与命令能力包

**包内文档**：[规格](work-packages/wp-06-write-and-command/spec.md)（v1.0.0 / frozen）、
[完成计划](work-packages/wp-06-write-and-command/plan.md)（v1.0.0 / done）、
[验收报告](work-packages/wp-06-write-and-command/acceptance-report.md)（v1.0.0 / accepted）。

**目标**：完成真实写操作和受控命令执行，并验证审批、超时和未知结果语义。

**依赖**：WP-04、WP-05。

**主要范围**：

- `tools/fs_write.py`、`tools/exec.py`、`tools/__init__.py` 的完整注册；
- `tests/test_tools.py`、`tests/test_gate.py` 的写入和执行部分。

**交付内容**：

- `write_file`、`apply_patch` 和 `run_test`；
- 交付物及 `allow_write` 精确路径检查，审批前不得产生文件变化；
- patch 空内容、上下文缺失和歧义拒绝；
- 测试命令只接受任务配置或固定 allowlist 的 argv；
- 写入、进程和审批相关调用串行执行；
- 退出码、信号、超时、截断、耗时和已采集输出的结构化结果；
- 写入或进程结果不确定时的不可重放状态。

**退出条件**：

- 未审批和未授权路径的写入在主文件系统上不产生变化；
- 已批准写入只执行一次，参数变化要求重新审批；
- `apply_patch` 的失败不会产生部分未审计修改；
- 非白名单可执行文件、解释器包装、shell 分隔符和重定向被拒绝；
- 超时后进程组终止，输出有上限；非幂等超时进入 `UNKNOWN_RESULT`；
- 命令结果、审计和工具调用记录可关联。

**不在本包内**：测试修复验收、Harness 基线编排、CLI 和跨进程恢复。

### WP-07 Checkpoint 与恢复

**目标**：建立从持久化事实生成确定恢复动作的安全闭环。

**依赖**：WP-01、WP-02、WP-06。

**包内文档**：[规格](work-packages/wp-07-checkpoint-recovery/spec.md)（v1.1.0 / frozen）、
[计划](work-packages/wp-07-checkpoint-recovery/plan.md)（v1.1.0 / done）、
[验收报告](work-packages/wp-07-checkpoint-recovery/acceptance-report.md)（accepted）。

**主要范围**：

- `checkpoint.py`；
- `tests/test_state_checkpoint.py` 的崩溃和恢复部分；
- 快照 schema、恢复校验器、恢复计划器和租约协作。

**交付内容**：

- 安全快照：Run、当前步骤、计划、工具结果、预算、审批、停止原因、最后审计序号、
  配置指纹、工作区证据和 schema 版本；
- 快照 canonical 哈希和读取时重新校验；
- 恢复前配置、工作区身份、外围保护基线、审批和租约复核；
- 对执行意图前、STARTED 前、STARTED 后、handler 返回后、结果提交后和
  Checkpoint 索引未提交等窗口给出确定动作；
- 有界、确定性的 continuation context 数据模型，不序列化旧 Agent memory。

**退出条件**：

- PREPARED 且未 STARTED 只能重新经过 Gate，不能直接调用 handler；
- STARTED 但无确定结果进入不可自动恢复的 `UNKNOWN_RESULT`；
- SUCCEEDED/FAILED 使用已有结果，不重复执行；
- 配置、工作区、外围脏路径、审批、租约或 schema 不匹配时进入 BLOCKED；
- 关闭并重开数据库、跨连接恢复时预算不清零、审批不复活；
- continuation context 不包含旧 Agent、隐藏推理或完整 Audit 事件流。

**施工前详细设计**：必须明确快照 schema、六类崩溃窗口、恢复状态表和人工处理入口。

**不在本包内**：重新创建 Agent、CLI `resume` 和完整端到端恢复。

### WP-08 验收引擎与代码理解

**目标**：让程序不受模型自述影响，按代码理解任务契约独立判定交付。

**依赖**：WP-04、WP-05。

**包内文档**：[规格](work-packages/wp-08-completion-code-understanding/spec.md) ·
[计划](work-packages/wp-08-completion-code-understanding/plan.md)。规格已冻结、计划已批准，D-59
至 D-64 已关闭；Completion 核心实现已落地，当前状态为 `IN_PROGRESS`，全场景验收报告待完成。

**主要范围**：

- `completion.py` 的验收框架和代码理解策略；
- `tests/test_completion.py` 的代码理解部分；
- 验收结果、证据 DTO、程序化 diff/status 检查。

**交付内容**：

- 通过 Gate 内部控制面执行验收，使用独立系统 actor，不向模型暴露验收工具；
- 解析“事实”“推断”“代码依据”三个区；
- 校验事实引用、证据编号、相对路径、行范围和读取证据版本；
- 比较配置交付物、磁盘内容、工作区 diff/status 和仓库外围保护基线；
- 检测 `claim_without_evidence`；
- 将验收项、证据、最终结论、时间和关联 ID 原子写入 StateStore 和 Audit。

**退出条件**：

- 合法文档可得到 ACCEPTED，缺少文档、内容为空、事实无引用或引用未定义得到 REJECTED；
- 引用路径越界、行号不存在、完整范围未读、文件读取后变化均被拒绝；
- 交付物列表之外出现新增、修改、删除或 HEAD/index 变化时不得 ACCEPTED；
- 验收结果无法写入 StateStore 或 Audit 时不得返回 ACCEPTED；
- 测试只使用程序化工作区证据，不采信 Fake Model 的最终回答。

**不在本包内**：测试修复策略、测试命令基线、Agent 和 CLI。

### WP-09 测试修复验收

**包内文档**：[冻结规格](work-packages/wp-09-test-repair-acceptance/spec.md)（v1.0.0 / frozen）、
[实施计划](work-packages/wp-09-test-repair-acceptance/plan.md)（v1.0.0 / in-progress）。
WP09-Q01 至 Q08 已由 D-65 至 D-72 关闭；先完成 WP-08 依赖，再继续运行时施工，状态为
`IN_PROGRESS`。

**目标**：建立基线测试、修复后复跑、测试节点和 diff 范围的程序化验收闭环。

**依赖**：WP-06、WP-08。

**主要范围**：

- `completion.py` 的测试修复策略；
- `tests/test_completion.py` 的测试修复部分；
- 基线执行、测试报告解析和测试节点证据。

**交付内容**：

- 通过 Gate 内部控制面执行基线及最终验收命令；
- 记录 argv、cwd、开始 status/diff、退出码、报告、失败摘要和配置指纹；
- 解析最终测试集合，比较基线节点、新增节点、丢失节点和跳过状态；
- 校验最终退出码、测试报告、工作区指纹前后来一致；
- 校验 `allow_write`、既有测试只读和最终 diff 范围；
- 旧基线、旧报告和文件版本变化后的证据失效。

**退出条件**：

- 基线缺失、命令不可执行、超时、报告无法解析或结果未知均不得 ACCEPTED；
- 基线节点丢失、新增跳过、测试覆盖减少和越界 diff 被拒绝；
- 最终测试通过后再次修改工作区会使结果失效；
- 同一验收命令只从任务配置读取，模型不能替换命令或解释器；
- 不执行 P0 范围外的隐藏测试，不向模型暴露未来隐藏用例。

**施工前详细设计**：必须确定测试报告格式、节点提取方式、无输出但退出码异常的处理和
报告原文上限。该结论应同步到设计和测试文档。

**不在本包内**：Agent 决策、审批等待和跨进程恢复。

### WP-10 Agent 与 Harness

**目标**：在保留 `ToolCallingAgent` 原生决策循环的前提下，完成唯一 Agent Run 编排入口。

**依赖**：WP-07、WP-08、WP-09。

**主要范围**：

- `tests/fake_model.py`、`agent_adapter.py`、`harness.py`；
- `tests/test_harness_loop.py` 的正常、拒绝、预算、异常和恢复部分。

**交付内容**：

- 脚本化 Fake Model，支持 tool calls、最终回答、异常、token 用量、预算和并行调用；
- 使用公开 `Tool`、`ToolCallingAgent(...)`、`max_tool_threads=1`、流式 `run` 和
  `step_callbacks`；
- 极薄的 `HarnessToolCallingAgent` 公开分派适配，保留原始 `tool_call_id`；
- GovernedTool 只调用 Gate，不携带实际 handler 或隐藏路径给模型；
- `Harness.run(RunRequest)` 的任务装配、新建 Run、基线执行、流式停止、预算、
  Checkpoint、验收和异常收口；
- `final_answer` 仅作为候选回答，不能形成 ACCEPTED。

**退出条件**：

- 单工具和多工具调用的 ID、顺序和 observation 正确，原生 ToolCallingAgent 仍负责决策；
- 正常完成、门禁拒绝、审批等待、预算停止和异常停止均有确定性测试；
- 任何工具调用都经过 Gate，测试无法从模型侧触达 handler；
- 预算耗尽固定为 `STOPPED/BUDGET_EXHAUSTED/NOT_EVALUATED`，且不再验收；
- Completion 在 Agent 停止后独立执行，模型自述不能改变验收结论；
- `smolagents` 适配契约失败时显式报错，不静默丢失安全关联。

**施工前详细设计**：必须记录 `smolagents==1.26.0` 的公开适配点、流式停止、final answer、
升级兼容测试和不得依赖的私有字段。

**不在本包内**：CLI 命令、默认退出与 `--wait-for-approval` 交互、最终运维文档。

### WP-11 CLI 与人工恢复

**目标**：提供可操作但不绕过安全链的本地运行、审批、查询和恢复入口。

**依赖**：WP-07、WP-10。

**包内文档**：[规格](work-packages/wp-11-cli-manual-recovery/spec.md) ·
[计划](work-packages/wp-11-cli-manual-recovery/plan.md) ·
[验收报告](work-packages/wp-11-cli-manual-recovery/acceptance-report.md)。

**主要范围**：

- `cli.py`、`__main__.py`；
- `tests/test_cli.py` 和 `tests/test_harness_loop.py` 的跨进程恢复部分。

**交付内容**：

- `run`、`resume`、`approvals list/approve/reject` 和 `inspect`；
- 默认审批等待时安全退出，批准后通过 `resume` 重建 Agent；
- 可选 `--wait-for-approval`，超时保持 WAITING_APPROVAL；
- 稳定退出码，ACCEPTED 才是成功；STOPPED、BLOCKED、FAILED 和 REJECTED 明确区分；
- CLI 只调用 Harness 或审批/只读查询服务，不自行拼装安全组件；
- 审批命令只改变授权，不执行 handler。

**退出条件**：

- 写操作首次运行进入 WAITING_APPROVAL，文件不变；
- 新进程 `resume` 后重新校验配置、工作区、参数摘要和审批，只执行一次；
- 审批重放、参数篡改、过期、跨 Run 和不可用通道均返回稳定错误；
- `approvals` 和 `inspect` 为只读，不可触发仓库操作；
- `python -m guarded_harness` 与 console script 调用同一 `main`。

**不在本包内**：真实模型 Provider、网络调用和 Web 管理界面。

### WP-12 故障加固与交付

**目标**：完成跨模块故障矩阵、样例、全量验收和可复现交付材料。

**依赖**：WP-00 至 WP-11。

**包内文档**：[冻结规格](work-packages/wp-12-fault-hardening-delivery/spec.md)（v1.0.0 / frozen）、
[实施计划](work-packages/wp-12-fault-hardening-delivery/plan.md)（v1.0.0 / approved）、
[验收报告](work-packages/wp-12-fault-hardening-delivery/acceptance-report.md)（blocked，等待上游
WP-08 至 WP-11 accepted）。
跨包人工操作步骤见 [WP-08 至 WP-11 手动验收说明](manual-acceptance-wp08-wp11.md)。

**主要范围**：

- `tests/fault_injection.py`、`tests/test_fault_recovery.py`；
- `examples/config/code-understanding.yaml`、`examples/config/test-repair.yaml`；
- `docs/test-plan.md`、`docs/operations.md`、根目录 `README.md` 和文档索引；
- 全量测试、lint、格式、故障注入和最终证据核对。

**交付内容**：

- 可定位到具体事务边界的进程退出、审计失败、DB 锁、Checkpoint 损坏和租约冲突注入；
- 两类任务的完整样例配置，并由正式配置加载器校验；
- P0 需求、决策、测试用例、验收命令和证据映射；
- 运行、审批、恢复、状态库位置、日志、退出码和安全操作说明；
- 根目录只保留入口概览，详细内容链接回 `docs/`。

**退出条件**：

- 两类任务均通过 Fake Model 完整运行；
- 完成审批后跨进程 resume 和 `STARTED` 后崩溃恢复；
- `UNKNOWN_RESULT` 不重放，已确定写入不重复，预算不清零；
- 六个指定测试文件存在并覆盖设计映射，全部 pytest 通过；
- Ruff lint 和 Ruff format check 通过；
- README、需求、设计、决策、任务和运维文档之间无冲突；
- 最终报告明确记录命令、结果、剩余风险和未实现项。

**不在本包内**：新增 P0 功能、真实 Provider、隐藏验收、多仓库或分布式能力。

## 7. 工作包文档拆解模式

每个工作包进入详细设计后，默认形成两份规范性文档；通过验收时再形成一份证据文档：

- `spec.md`：规定工作包必须表现出的行为、公共接口、数据约束和验收契约；
- `plan.md`：规定工作包的详细设计、施工任务、测试安排、实施顺序和回滚方式；
- `acceptance-report.md`：记录实际交付物、复跑命令和结果、规格场景与任务映射、限制和验收结论。

文档名称按文件名语义只使用小写字母，统一为 `spec.md`、`plan.md` 和
`acceptance-report.md`。工作包目录、可选设计文档和其他配套文档也使用小写 kebab-case，避免
不同文件系统对大小写的处理差异。

### 7.1 文档权威与边界

文档发生冲突时按以下顺序处理：

```text
requirements.md / design.md / decisions.md
        >
工作包 spec.md
        >
工作包 plan.md
        >
具体任务卡
```

| 文档 | 负责回答 | 不负责 |
| --- | --- | --- |
| `requirements.md` | 项目为什么做、全局必须满足什么 | 单个工作包的接口细节 |
| `design.md` | 系统总体结构、运行链路和跨包安全语义 | 单个工作包的全部施工步骤 |
| `decisions.md` | 已确认的跨阶段技术决策 | 临时任务顺序 |
| 包内 `spec.md` | 本包行为、接口、状态、错误和验收契约 | 具体施工排期和任务切分 |
| 包内 `plan.md` | 本包详细设计、任务、测试实施、风险和回滚 | 重新定义已经冻结的行为契约 |
| 包内 `acceptance-report.md` | 汇总实际交付物、复跑结果、场景映射、限制和结论 | 重新定义已冻结规格或计划 |
| 测试与运行证据 | 用可执行事实证明契约成立 | 替代规格中的验收定义 |

`plan.md` 不得静默改变 `spec.md` 的行为。实施过程中发现规格不合理时，必须先停止施工，
更新 `spec.md` 和对应契约测试并完成评审，再修改 `plan.md`。涉及跨工作包接口、系统安全边界或
已确认决策时，还必须先更新 `design.md` 或 `decisions.md`。

### 7.2 目录结构

工作包文档按需创建，不为尚未进入详细设计的包批量生成空文件。建议目录如下：

```text
docs/work-packages/
├── index.md
├── wp-00-engineering-contracts/
│   ├── spec.md
│   ├── plan.md
│   └── acceptance-report.md
├── wp-01-persistence-audit/
│   ├── spec.md
│   ├── plan.md
│   └── acceptance-report.md
└── wp-04-governed-execution/
    ├── spec.md
    ├── plan.md
    ├── acceptance-report.md
    └── design.md              # 仅在需要时创建
```

`docs/work-packages/index.md` 维护工作包文档索引、当前规格版本和状态。`task-breakdown.md`
继续作为跨工作包总计划，只链接已经创建的包内文档，不复制其中的全部接口和任务细节。

### 7.3 `spec.md` 内容

`spec.md` 是工作包的规范性契约，建议包含：

1. 文档信息：工作包、状态、版本、依赖、对应需求和设计章节；
2. 目标、范围和非目标；
3. 参与组件、调用方、输入和输出；
4. 公共接口：类型、函数、schema、前置条件和后置条件；
5. 行为契约：正常流程、拒绝流程和固定处理顺序；
6. 状态与不变量：合法状态、禁止转移和必须保持的系统事实；
7. 安全与故障语义：审批、超时、重试、幂等、结果未知和失败关闭；
8. 错误契约：稳定错误码、触发条件、可重试性和模型可见信息；
9. 兼容与迁移：schema 版本、公共契约升级和旧数据行为；
10. 验收场景：使用稳定编号描述可执行场景；
11. 未决问题：规格冻结时必须为空，或明确记录经批准的非阻塞项。

关键条目使用稳定 ID，例如：

```text
WP04-BEH-001：所有拒绝必须发生在 handler 调用前。
WP04-ERR-003：已消费审批不得再次 claim。
WP04-AC-005：写操作参数变化后旧审批返回 APPROVAL_INVALID。
```

`spec.md` 不依赖具体文件布局。它应描述调用方可以观察到的行为，而不是实现者方便的内部结构。

### 7.4 `plan.md` 内容

`plan.md` 是执行计划，建议包含：

1. 对应的 `spec.md` 版本和验收场景；
2. 包内组件划分、依赖方向和详细设计；
3. 关键调用链、伪代码、状态转换和事务边界；
4. 数据表、migration、索引、兼容和回滚方式；
5. 施工任务 ID、目标、依赖、涉及文件和完成条件；
6. 单元、集成、安全和故障测试计划；
7. `spec.md` 场景到测试文件、测试函数和验收证据的映射；
8. 推荐施工顺序和可独立提交的增量；
9. 已知风险、阻塞条件、回滚点和人工处理入口；
10. 实际完成情况、偏差、遗留风险和下游影响。

包内任务应直接引用规格条目，例如：

| 任务 | 实现内容 | 对应规格 | 主要测试 |
| --- | --- | --- | --- |
| WP04-T01 | Registry 与 ToolSpec | WP04-API-001 | `tests/test_registry_policy.py` |
| WP04-T02 | Policy 判定 | WP04-BEH-002 | `tests/test_registry_policy.py` |
| WP04-T03 | Gate 固定拒绝链 | WP04-BEH-001 | `tests/test_gate.py` |
| WP04-T04 | 执行意图与结果生命周期 | WP04-STATE-001 | `tests/test_gate.py` |

任务完成必须以对应规格场景和测试证据满足为准，不能只以文件创建或代码合并为准。

### 7.5 `acceptance-report.md` 内容

`acceptance-report.md` 是工作包验收证据的唯一汇总入口，不重新定义规格或计划。报告至少包含：

1. 文档信息：工作包、报告状态、报告版本、验收日期、对应 spec/plan 版本、代码基线提交、
   生成报告前的工作区状态、操作系统、Python、pip 和关键依赖版本；
2. 验收范围：明确已验收能力与非目标，防止把工作包完成解释为项目整体完成；
3. 交付物清单：列出源码、配置、测试、文档及其他可审查产物，并逐项给出 PASS/FAIL；
4. 执行证据：记录实际执行的安装、目标测试、全量测试、lint、格式和依赖一致性命令，以及
   退出状态、测试数量、失败/skip 数和关键输出；
5. 规格场景映射：逐条覆盖 `WPxx-AC-*`，说明对应测试、证据和结果；
6. 任务卡映射：逐条记录 `WPxx-T*` 的完成状态和验收依据；
7. 安全与故障矩阵：列出注入、越界、审批、超时、恢复、脱敏或失败关闭等边界及其证据；
8. 来源验收映射：引用飞书核心验收或其他上游要求时，只标记本包实际负责的部分，并写清
   下游责任工作包；
9. 限制与剩余风险：记录未运行环境、未覆盖路径、已知缺陷、风险承受者和下游影响；
10. 验收结论：明确 `accepted`、`rejected` 或 `blocked`，给出进入下游的前置条件。

报告必须使用实际复跑结果，不能复制计划中的预期值或模型自述。命令失败、存在未解释 skip、
关键规格场景无证据或测试数量异常时，结论不得为 `accepted`。报告中的命令、路径和关联 ID
必须可追溯；不得包含密钥、令牌、用户凭据或完整敏感环境变量。

建议使用以下报告状态：

- `draft -> review -> accepted -> superseded`；
- 验收失败时从 `review` 进入 `rejected`，修复后提升报告版本并重新评审；
- 因环境、依赖或外部证据不可用时从 `review` 进入 `blocked`，解除阻塞后重新评审；
- 被后续实现取代时保留最终历史报告，以 `superseded` 标记，不覆盖既有结论。

### 7.6 可选 `design.md`

默认把详细设计放在 `plan.md` 中。出现以下情况时，再拆分出包内 `design.md`：

- 一个工作包包含多个相互关联的状态机；
- 存在复杂的跨进程事务、崩溃恢复或并发场景；
- 同一公共 schema 被三个以上下游工作包依赖；
- 包含框架兼容层，例如 `smolagents` 公开适配；
- 设计部分已经影响 `plan.md` 中任务计划的可读性；
- 需要单独召开设计评审，而任务拆分尚不是当前重点。

增加 `design.md` 后，`spec.md` 仍是行为与验收的唯一权威，`plan.md` 负责引用设计并安排施工。

### 7.7 生命周期与变更流程

工作包文档按以下流程推进：

```text
创建 spec.md 草案
  -> 规格评审
  -> spec.md 冻结
  -> 编写 plan.md
  -> 拆分增量任务
  -> 实现与测试
  -> 包级验收
  -> 编写并评审 acceptance-report.md
  -> 记录偏差和下游影响
```

建议使用以下文档状态：

- `spec.md`：`draft -> review -> frozen -> superseded`；
- `plan.md`：`draft -> approved -> in-progress -> done -> superseded`；
- `acceptance-report.md`：`draft -> review -> accepted -> superseded`；
- 可选 `design.md`：`draft -> review -> approved -> superseded`。

变更处理规则：

- 仅调整施工顺序或任务粒度：修改 `plan.md`；
- 修改本包公共行为、接口、状态、错误码或验收条件：先修改 `spec.md`；
- 修改跨工作包契约或安全语义：先修改 `design.md` 或 `decisions.md`；
- 已冻结规格发生变化：提升版本，更新契约测试和受影响下游计划；
- `plan.md` 与 `spec.md` 冲突：立即停止施工，以 `spec.md` 为当前权威并完成变更评审；
- 验收证据变化：提升验收报告版本并重新评审，不静默修改历史结论；
- 工作包完成后：保留文档及最终状态，不删除历史规格，用 `superseded` 记录被替代版本。

每个 `spec.md` 验收场景最终至少映射到一个自动化测试或明确的人工验收步骤。无法测试的场景必须
说明原因、限制和剩余风险，不能仅以自然语言声明完成。

### 7.8 推进顺序

每个工作包进入实施前按以下顺序完成：

1. 从本文读取工作包目标、依赖、退出条件和风险；
2. 读取 `design.md`、`decisions.md` 中与该包相关的约束；
3. 编写 `spec.md` 草案并完成行为、接口和验收评审；
4. 冻结 `spec.md` 版本；
5. 根据冻结规格编写 `plan.md`；
6. 将 `plan.md` 拆成可测试、可审查和可回滚的施工任务；
7. 按任务实施并持续验证规格场景；
8. 包级验收后编写并评审 `acceptance-report.md`，记录实际偏差、证据和下游影响；
9. 验收报告状态为 `accepted` 后，将工作包标记为 `DONE`；
10. 下游工作包只能依赖已冻结的规格版本和已验收的公共契约。

简单工作包的 `spec.md`、`plan.md` 和 `acceptance-report.md` 可以保持简短，但不能取消行为
契约、实施顺序、实际证据和验收映射。复杂工作包可以增加 `design.md`，但不应把同一事实在
多份文档中重复维护。

## 8. 工作包施工卡格式

进入详细设计或施工时，每个工作包应拆成可执行任务卡。任务卡至少包含：

```text
任务 ID 与工作包
任务目标
前置契约
涉及文件
公共接口或 schema 变化
正常路径
拒绝与故障路径
测试用例
验收命令
需要同步的文档
验收报告需要更新的章节和状态
风险和回滚方式
```

任务卡不按“每文件一张卡”机械拆分。跨文件但服务于同一能力的实现可以放在同一任务卡中；相反，
一个复杂文件中的不同安全能力可以在不同工作包中分阶段完成。

## 9. 跨工作包验收矩阵

| 验收主题 | 首个验证工作包 | 后续扩展 | 最小证据 |
| --- | --- | --- | --- |
| 严格配置和契约 | WP-00 | 每个修改公共契约的 WP | 合法/非法模型与指纹测试 |
| 状态与审计原子性 | WP-01 | WP-04、WP-07、WP-10 | 故障注入后共同回滚或提交 |
| 审批绑定与一次性 | WP-02 | WP-04、WP-06、WP-11 | 篡改、重放、过期和消费测试 |
| 路径与进程隔离 | WP-03 | WP-05、WP-06 | 拒绝发生时副作用计数为 0 |
| Gate 固定检查链 | WP-04 | WP-05、WP-06、WP-10 | 各阶段拒绝 observation 和审计 |
| 读取证据可追溯 | WP-05 | WP-08 | 路径、行范围、内容哈希和工具调用关联 |
| 副作用未知结果 | WP-06 | WP-07、WP-12 | 超时、进程终止和不可重放状态 |
| 崩溃恢复判定 | WP-07 | WP-10、WP-11、WP-12 | 跨数据库重开和进程恢复证据 |
| 代码理解验收 | WP-08 | WP-10、WP-12 | 文档、引用、读取版本和 diff 证据 |
| 测试修复验收 | WP-09 | WP-10、WP-12 | 基线、最终报告、测试节点和 diff |
| Agent 统一入口 | WP-10 | WP-11、WP-12 | Fake Model 下所有调用经过 Gate |
| 人工审批与恢复 | WP-11 | WP-12 | 新进程 resume 和一次性执行 |
| 全量故障加固 | WP-12 | 无 | 完整测试、lint、格式和交付记录 |
| 工作包验收报告 | WP-00 | 每个 WP | `acceptance-report.md` 为 accepted，命令可复跑且场景/任务/限制完整 |

## 10. 风险与详细设计待确认项

以下事项必须在对应工作包施工前确定，不应在实现中临时猜测：

| 风险/待确认项 | 所属工作包 | 约束 |
| --- | --- | --- |
| SQLite 事务协作和 migration 顺序 | WP-01 | State、Audit 及后续审批/Checkpoint 必须能共同提交或回滚 |
| Run 租约和并发写冲突 | WP-01、WP-07 | 不依赖内存锁完成跨进程安全 |
| 审批摘要兼容维护 | WP-02 | D-28 已冻结 canonical 输入；字段顺序、路径和时间格式变化必须提升契约版本 |
| `apply_patch` 上下文和原子失败语义 | WP-06 | 不产生部分未审计写入，不接受空 patch |
| 测试报告格式和节点提取 | WP-09 | 必须可从公开命令和结构化报告中机械验证 |
| continuation context 的有界格式 | WP-07、WP-10 | 不序列化旧 Agent，不包含隐藏推理 |
| smolagents 公开适配稳定性 | WP-10 | 只依赖固定版本的公开接口，升级测试必须显式失败 |
| Git-ignored 外围文件不可见 | WP-03 | 只能作为剩余风险，不宣称文件系统级强隔离 |
| 自然语言语义正确性 | WP-08 | P0 只验证引用和程序化事实，不宣称能自动证明代码解释语义 |

## 11. 原文件任务追溯附录

| 原任务 | 所属工作包 | 在新计划中的含义 |
| --- | --- | --- |
| P0-01 至 P0-03 | WP-00 | 工程、依赖和构建基线 |
| P0-04 | WP-00 | 包入口和最小公开导出 |
| P0-05 | WP-00 | 公共领域模型、错误码和协议 |
| P0-06、P0-07 | WP-00 | 测试基础设施和领域契约测试 |
| P0-08、P0-09 | WP-00 | 配置加载、指纹和配置测试 |
| P1-01 | WP-01 | SQLite、migration 和事务基础设施 |
| P1-02 | WP-01 | 状态、预算、证据、状态机和租约 |
| P1-03 | WP-01 | 脱敏和有界裁剪 |
| P1-04、P1-05 | WP-01 | append-only 审计及测试 |
| P1-06 | WP-02 | 审批生命周期、绑定和一次性消费 |
| P1-07 | WP-07 | Checkpoint、恢复校验和动作选择 |
| P1-08 | WP-01、WP-02、WP-07 | 状态、审批和恢复测试分阶段扩展 |
| P2-01、P2-02 | WP-03 | Sandbox 边界及测试 |
| P3-01、P3-02 | WP-04 | Registry、Policy 和测试 |
| P3-03 | WP-04 | Gate 核心、固定检查链和执行生命周期 |
| P3-04、P3-06 | WP-05 | 只读文件与 Git 工具 |
| P3-05、P3-07 | WP-06 | 写入与受控命令工具 |
| P3-08 | WP-05、WP-06 | 工具工厂分阶段注册只读和副作用工具 |
| P3-09 | WP-04 | Registry/Policy 测试 |
| P3-10 | WP-05、WP-06 | 工具测试按只读和副作用能力扩展 |
| P3-11 | WP-04、WP-06 | Gate 核心测试后接真实工具集成测试 |
| P4-01、P4-02 | WP-08、WP-09 | Completion 框架与两类策略分阶段实现 |
| P5-01 | WP-10 | Fake Model 和测试控制能力 |
| P5-02、P5-03 | WP-10 | smolagents 适配和 Harness 编排 |
| P5-04、P5-05 | WP-11 | CLI 和模块入口 |
| P5-06 | WP-10、WP-11 | Agent Loop 测试及跨进程恢复扩展 |
| P5-07 | WP-11 | CLI 测试 |
| P6-01、P6-02 | WP-12 | 故障注入与恢复加固 |
| P6-03、P6-04 | WP-12 | 两类任务的最终示例配置 |
| P6-05、P6-06 | WP-12 | 测试计划和运维说明 |
| P6-07 | WP-12 | 根 README 和最终交付入口 |

## 12. 必须测试文件映射

| 测试文件 | 创建/首次扩展 | 后续扩展 | 主要工作包 |
| --- | --- | --- | --- |
| `tests/test_gate.py` | WP-04 | WP-06、WP-10、WP-12 | 治理执行内核 |
| `tests/test_sandbox.py` | WP-03 | WP-06、WP-12 | Sandbox 边界 |
| `tests/test_state_checkpoint.py` | WP-01 | WP-02、WP-07、WP-10、WP-11、WP-12 | 持久化与恢复 |
| `tests/test_audit.py` | WP-01 | WP-04、WP-12 | 审计与事务 |
| `tests/test_harness_loop.py` | WP-10 | WP-11、WP-12 | Agent 运行与恢复 |
| `tests/test_completion.py` | WP-08 | WP-09、WP-10、WP-12 | 程序化最终验收 |

允许增加测试文件，但不得以额外测试替代上述六份指定文件。

## 13. 需求与决策追溯

| 来源 | 主要工作包 |
| --- | --- |
| `REQ-HARNESS-*` | WP-04、WP-10、WP-11 |
| `REQ-GATE-*` | WP-01、WP-02、WP-04、WP-05、WP-06 |
| `REQ-SANDBOX-*` | WP-00、WP-03、WP-06 |
| `REQ-STATE-*` | WP-01、WP-07、WP-10 |
| `REQ-AUDIT-*` | WP-01、WP-04、WP-07、WP-08 |
| `REQ-CHECKPOINT-*` | WP-07、WP-10、WP-11、WP-12 |
| `REQ-TASK-*` | WP-05、WP-06、WP-08、WP-09、WP-10 |
| `REQ-COMPLETE-*` | WP-08、WP-09、WP-10、WP-12 |
| D-01 审批与恢复 | WP-02、WP-07、WP-10、WP-11 |
| D-02 工作区范围 | WP-00、WP-03、WP-08 |
| D-03 交付物路径 | WP-00、WP-06、WP-08 |
| D-04 文档引用强度 | WP-00、WP-05、WP-08 |
| D-05 测试目录策略 | WP-00、WP-03、WP-06、WP-09 |
| D-06 无隐藏验收 | WP-09、WP-12 |
| D-07 Fake Model | WP-10、WP-12 |
| D-08 恢复上下文 | WP-07、WP-10、WP-11 |
| D-09 预算终态 | WP-01、WP-04、WP-10、WP-11 |
| D-16 状态目录隔离 | WP-00、WP-01、WP-03、WP-04、WP-07 |
| D-17 预算幂等 ledger | WP-01、WP-04、WP-06、WP-10 |
| D-18 当前计划建模 | WP-01、WP-07、WP-10 |
| D-19 Run 租约与心跳 | WP-01、WP-07、WP-10、WP-11 |
| D-20 状态变更强制审计 | WP-01、WP-04、WP-07、WP-08 |
| D-21 状态文件权限 | WP-01、WP-03、WP-11 |
| D-22 Evidence 单一生产者 | WP-01、WP-04、WP-05、WP-08、WP-09 |
| D-29 至 D-36 Sandbox 边界 | WP-03、WP-04、WP-05、WP-06、WP-07、WP-09、WP-12 |
| D-48 至 D-52 WP-06 写入与命令语义 | WP-06、WP-07、WP-09、WP-10、WP-12 |

## 14. 全局完成定义

P0 交付必须同时满足：

1. WP-00 至 WP-12 的全部退出条件满足；
2. 六个必须模块通过公共入口完成端到端装配；
3. `ToolCallingAgent` 原生决策循环保留，所有仓库副作用经过 Gate；
4. 参数、权限、风险、审批、重复和预算拒绝测试证明 handler 未执行；
5. Sandbox 在真实 I/O 和进程层拦截越界，并清理敏感环境变量；
6. State、Audit、Approval 和 Checkpoint 在事务及恢复语义上保持一致；
7. 代码理解和测试修复均由程序根据工作区、测试和审计证据验收；
8. 六个指定测试文件存在并覆盖对应边界，全部 pytest 通过；
9. Ruff lint 和 Ruff format check 通过；
10. 每个工作包的验收报告均为 `accepted`，记录实际命令、证据、限制和下游影响；
11. 文档明确区分计划、已实现能力、剩余风险和未完成项。

工作包完成不自动等于项目完成。只有 WP-12 的端到端、安全边界和交付证据全部通过后，
项目才可以标记为 P0 完成。
