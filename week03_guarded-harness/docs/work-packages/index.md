# 工作包文档索引

本目录维护已进入详细设计阶段的工作包文档。`docs/task-breakdown.md` 仍是跨工作包总计划和依赖
关系的权威来源；这里只记录已经创建的包内规格、计划及其当前状态。

## 状态说明

- `spec.md`：`draft -> review -> frozen -> superseded`。
- `plan.md`：`draft -> approved -> in-progress -> done -> superseded`。
- `blocked` 表示存在必须由需求方确认的规格问题，不允许进入施工。

## 当前工作包

| 工作包 | 能力目标 | 规格 | 计划 | 当前状态 | 入口条件 |
| --- | --- | --- | --- | --- | --- |
| [WP-00 工程与契约内核](wp-00-engineering-contracts/spec.md) | 工程可安装，公共模型、配置和测试基础设施可用 | `spec.md` v1.0.2 / frozen | `plan.md` v1.0.2 / done | DONE | T01 至 T08 和 WP-00 验收命令全部通过 |
