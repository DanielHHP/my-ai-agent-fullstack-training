# 工作包文档索引

本目录维护已进入详细设计阶段的工作包文档。`docs/task-breakdown.md` 仍是跨工作包总计划和依赖
关系的权威来源；这里只记录已经创建的包内规格、计划及其当前状态。

## 状态说明

- `spec.md`：`draft -> review -> frozen -> superseded`。
- `plan.md`：`draft -> approved -> in-progress -> done -> superseded`。
- `acceptance-report.md`：`draft -> review -> accepted -> superseded`；验收失败时进入 `rejected`，
  外部证据不可用时进入 `blocked`。
- `blocked` 表示存在必须由需求方确认的规格问题，不允许进入施工。

## 当前工作包

| 工作包 | 能力目标 | 规格 | 计划 | 验收报告 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| [WP-00 工程与契约内核](wp-00-engineering-contracts/spec.md) | 工程可安装，公共模型、配置和测试基础设施可用 | `spec.md` v1.0.2 / frozen | `plan.md` v1.0.2 / done | [acceptance-report.md](wp-00-engineering-contracts/acceptance-report.md) / accepted | DONE |
