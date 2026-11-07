# 文档索引

本目录统一维护 Guarded Coding Harness 的需求、设计、决策、任务拆解和验收文档。

## 当前文档

| 文档 | 定位 |
| --- | --- |
| [requirements.md](requirements.md) | 项目整体需求、职责边界、必须交付物与整体验收标准 |
| [design.md](design.md) | 系统概要设计：模块划分、运行链路、安全边界、状态与恢复、验收策略 |
| [decisions.md](decisions.md) | 已确认的 D-01 至 D-52 设计决策及约束 |
| [task-breakdown.md](task-breakdown.md) | 按里程碑和能力工作包组织的 P0 开发计划、执行规程、`spec.md`/`plan.md`/`acceptance-report.md` 包内文档模式、依赖、验收条件与文件任务追溯 |
| [work-packages/index.md](work-packages/index.md) | 已进入详细设计的工作包规格、计划、当前状态和冻结版本索引 |
| [WP-00 验收报告](work-packages/wp-00-engineering-contracts/acceptance-report.md) | WP-00 工程与契约内核的实测证据、场景覆盖、限制和验收结论 |
| [WP-01 规格](work-packages/wp-01-persistence-audit/spec.md) | 持久化与审计底座已冻结的行为、接口、状态机、飞书验收映射和决策追溯 |
| [WP-01 计划](work-packages/wp-01-persistence-audit/plan.md) | WP-01 已批准的 schema、事务设计、任务卡、测试矩阵、风险和回滚计划 |
| [WP-01 验收报告](work-packages/wp-01-persistence-audit/acceptance-report.md) | WP-01 的实现、并发/故障证据、场景映射、限制和验收结论 |
| [WP-02 规格](work-packages/wp-02-approval-lifecycle/spec.md) | 审批生命周期已冻结规格：绑定、角色、过期、一次性 claim/consume、恢复和飞书验收映射 |
| [WP-02 计划](work-packages/wp-02-approval-lifecycle/plan.md) | WP-02 已完成的实施计划：详细设计、migration、任务卡、测试矩阵、风险和回滚 |
| [WP-02 验收报告](work-packages/wp-02-approval-lifecycle/acceptance-report.md) | WP-02 的实现、并发/故障证据、场景映射、限制和验收结论 |
| [WP-03 规格](work-packages/wp-03-sandbox-boundary/spec.md) | Sandbox 路径、安全 I/O、Git 基线、受信命令、环境、进程组、飞书验收映射和已冻结决策 |
| [WP-03 计划](work-packages/wp-03-sandbox-boundary/plan.md) | WP-03 已完成的实施计划：详细设计、任务卡、测试矩阵、风险、回滚和决策追溯 |
| [WP-03 验收报告](work-packages/wp-03-sandbox-boundary/acceptance-report.md) | Sandbox 实现、52 项目标测试、145 项全量测试、场景映射、限制和验收结论 |
| [WP-04 规格](work-packages/wp-04-governed-execution/spec.md) | Registry、Policy、Gate 固定检查链、审批/执行生命周期、飞书验收映射和 D-37 至 D-44 |
| [WP-04 计划](work-packages/wp-04-governed-execution/plan.md) | WP-04 详细设计、事务边界、任务卡、测试矩阵、风险和回滚计划 |
| [WP-04 验收报告](work-packages/wp-04-governed-execution/acceptance-report.md) | Gate 实现、目标/全量测试、故障/并发证据、限制和验收结论 |
| [WP-05 规格](work-packages/wp-05-readonly-repository-tools/spec.md) | 已冻结的只读文件、搜索、目录与 Git 工具行为、证据语义和飞书验收映射 |
| [WP-05 计划](work-packages/wp-05-readonly-repository-tools/plan.md) | WP-05 详细设计、兼容前置、任务卡、测试矩阵、风险和回滚计划 |
| [WP-05 验收报告](work-packages/wp-05-readonly-repository-tools/acceptance-report.md) | 六工具实现、196 项全量测试、安全/截断/Evidence 证据、限制和验收结论 |
| [WP-06 规格](work-packages/wp-06-write-and-command/spec.md) | 已冻结的写入、单文件 patch、受控测试命令、审批、超时、未知结果和飞书验收映射 |
| [WP-06 计划](work-packages/wp-06-write-and-command/plan.md) | WP-06 已完成详细设计、D-48 至 D-52、任务卡、测试矩阵、风险和回滚计划 |
| [WP-06 验收报告](work-packages/wp-06-write-and-command/acceptance-report.md) | 三工具实现、124 项目标测试、221 项全量测试、审批/超时/Evidence 证据和验收结论 |
| [WP-07 规格](work-packages/wp-07-checkpoint-recovery/spec.md) | 已冻结的 Checkpoint 快照、恢复窗口、租约、续接上下文、飞书 C1-C4 映射和 D-53 至 D-58 |
| [WP-07 计划](work-packages/wp-07-checkpoint-recovery/plan.md) | WP-07 migration v5、恢复设计、任务卡、测试矩阵、风险和回滚计划 |
| [WP-07 验收报告](work-packages/wp-07-checkpoint-recovery/acceptance-report.md) | Checkpoint/恢复实现、101 项目标测试、249 项全量测试、场景映射和验收结论 |
| [WP-08 规格](work-packages/wp-08-completion-code-understanding/spec.md) | 已冻结的验收引擎、内部只读控制面、代码理解文档结构、Evidence 追溯、工作区 diff、飞书 H1/H2/H3/A2/T1 映射和 D-59 至 D-64 |
| [WP-08 计划](work-packages/wp-08-completion-code-understanding/plan.md) | Completion 核心实现与独立 review 报告已落地；待补报告中的 GAP/PARTIAL 自动化证据 |
| [WP-08 验收报告](work-packages/wp-08-completion-code-understanding/acceptance-report.md) | 独立 review：合法接受、陈旧引用和主要 SQLite/Audit 证据已通过；部分 WP08-AC 仍缺直接测试 |
| [WP-09 规格](work-packages/wp-09-test-repair-acceptance/spec.md) | 已冻结的测试修复基线、JUnit 节点守恒、H2 证据失效、飞书核心验收映射和 D-65 至 D-72 |
| [WP-09 计划](work-packages/wp-09-test-repair-acceptance/plan.md) | 测试报告、内部测试执行、migration v6、基线/最终验收和独立 review 报告已落地；待补 GAP/PARTIAL |
| [WP-09 验收报告](work-packages/wp-09-test-repair-acceptance/acceptance-report.md) | 独立 review：失败基线、最终 JUnit、节点守恒和 H2 失效已通过；部分 WP09-AC 仍缺直接测试 |
| [WP-10 规格](work-packages/wp-10-agent-harness/spec.md) | 已冻结：原生 ToolCallingAgent 公开适配、Fake Model、唯一 Harness 入口、Run 生命周期、飞书核心验收映射和 D-73 至 D-77 |
| [WP-10 计划](work-packages/wp-10-agent-harness/plan.md) | 已实施：Agent 适配、Harness 编排、预算/Checkpoint/恢复/Completion 任务卡、测试矩阵和风险清单 |
| [WP-10 验收报告](work-packages/wp-10-agent-harness/acceptance-report.md) | 23 项目标测试、293 项全量测试、审批/恢复/预算/Completion 实际证据；状态 review，等待上游验收 |
| [WP-11 规格](work-packages/wp-11-cli-manual-recovery/spec.md) | 已冻结：CLI run/resume/approvals/inspect、审批后跨进程恢复、等待模式、退出码、飞书核心验收映射和 D-78 至 D-83 |
| [WP-11 计划](work-packages/wp-11-cli-manual-recovery/plan.md) | 已完成：入口、模型注入、控制面、运行链、任务卡、测试矩阵、风险和回滚计划 |
| [WP-11 验收报告](work-packages/wp-11-cli-manual-recovery/acceptance-report.md) | 21 项 CLI 目标测试、314 项全量测试、跨进程恢复、只读查询和退出码证据；状态 review，等待 WP-10 accepted |

新增设计、决策或测试文档后，应同步更新本索引。
