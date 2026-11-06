# 文档索引

本目录统一维护 Guarded Coding Harness 的需求、设计、决策、任务拆解和验收文档。

## 当前文档

| 文档 | 定位 |
| --- | --- |
| [requirements.md](requirements.md) | 项目整体需求、职责边界、必须交付物与整体验收标准 |
| [design.md](design.md) | 系统概要设计：模块划分、运行链路、安全边界、状态与恢复、验收策略 |
| [decisions.md](decisions.md) | 已确认的 D-01 至 D-36 设计决策及约束 |
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

新增设计、决策或测试文档后，应同步更新本索引。
