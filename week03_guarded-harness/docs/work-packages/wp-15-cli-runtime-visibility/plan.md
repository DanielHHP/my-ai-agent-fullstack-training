# WP-15 CLI 运行可观测性实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-15 CLI 运行可观测性 |
| 计划状态 | done |
| 计划版本 | 1.0.0 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |

## 2. 实施原则

- Run 创建通知使用 Harness 可选只读观察器，不改变 Agent 决策循环；
- 审批信息和最终回答格式化留在 CLI；
- stderr 承载运行进度，`--json` stdout 保持单一 envelope；
- 只展示已经校验、脱敏和有界的事实；
- 不增加数据库 migration、Gate 分支或第二套执行入口。

## 3. 任务卡

| 任务 | 内容 | 状态 |
| --- | --- | --- |
| T01 | 新增 `RunCreatedEvent`/`RunEventSink`，在 Run 提交后、模型调用前通知 | DONE |
| T02 | CLI 将 Run 创建信息写入 stderr 并立即 flush | DONE |
| T03 | 等待审批按审批 ID 去重输出结构化摘要 | DONE |
| T04 | 文本模式独立渲染持久化 `final_answer` 并清理终端控制字符 | DONE |
| T05 | 覆盖事件顺序、JSON 隔离、审批提示、最终文本和观察器故障测试 | DONE |
| T06 | 同步设计、决策、运维、测试计划和工作包索引 | DONE |

## 4. 测试矩阵

| 场景 | 覆盖 |
| --- | --- |
| Run 创建 | 数据库中 Run 已为 `RUNNING`，模型尚未调用，事件 ID 与 RunResult 一致 |
| JSON 模式 | stdout 为单个 envelope；`run_id` 进度仅出现在 stderr |
| 审批等待 | 显示绑定摘要；轮询期间同一审批只打印一次 |
| 最终回答 | 多行原文、脱敏值、ANSI 清理、无 JSON 换行转义 |
| 观察器故障 | 显示失败不影响 Run 终态和 Completion |
| 回归 | 既有退出码、审批、恢复、并发和输出测试继续通过 |

## 5. 回滚

移除 CLI 观察器和新增文本区块即可回滚展示行为；`RunCreatedEvent` 是可选公开接缝，不影响持久化
格式。回滚不要求数据迁移，也不改变既有 Run、审批、Checkpoint 或 Audit。
