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
| [WP-00 工程与契约内核](wp-00-engineering-contracts/spec.md) | 工程可安装，公共模型、配置和测试基础设施可用 | `spec.md` v1.3.0 / frozen | `plan.md` v1.1.0 / done | [acceptance-report.md](wp-00-engineering-contracts/acceptance-report.md) / accepted（v1.1.0 基线 + WP-02/WP-04 兼容修订） | DONE |
| [WP-01 持久化与审计底座](wp-01-persistence-audit/spec.md) | Run 状态、预算、脱敏和 append-only 审计形成事务闭环 | [spec.md](wp-01-persistence-audit/spec.md) v0.4.0 / frozen | [plan.md](wp-01-persistence-audit/plan.md) v0.3.0 / done | [acceptance-report.md](wp-01-persistence-audit/acceptance-report.md) / accepted（v0.3.0 基线 + WP-04 只读接口） | DONE |
| [WP-02 审批生命周期](wp-02-approval-lifecycle/spec.md) | 审批绑定、过期、角色校验、一次性 claim/consume 可独立验证 | [spec.md](wp-02-approval-lifecycle/spec.md) v0.2.0 / frozen | [plan.md](wp-02-approval-lifecycle/plan.md) v0.2.0 / done | [acceptance-report.md](wp-02-approval-lifecycle/acceptance-report.md) / accepted | DONE |
| [WP-03 Sandbox 边界](wp-03-sandbox-boundary/spec.md) | 路径、安全 I/O、安全枚举、受限 pathspec、Git、环境、命令和进程边界 | [spec.md](wp-03-sandbox-boundary/spec.md) v1.1.0 / frozen | [plan.md](wp-03-sandbox-boundary/plan.md) v1.1.0 / done | [acceptance-report.md](wp-03-sandbox-boundary/acceptance-report.md) / accepted（兼容修订） | DONE |
| [WP-04 治理执行内核](wp-04-governed-execution/spec.md) | Registry、Policy、Gate 和工具调用生命周期形成唯一执行入口 | [spec.md](wp-04-governed-execution/spec.md) v1.0.0 / frozen | [plan.md](wp-04-governed-execution/plan.md) v1.0.0 / done | [acceptance-report.md](wp-04-governed-execution/acceptance-report.md) / accepted | DONE |
| [WP-05 只读仓库能力包](wp-05-readonly-repository-tools/spec.md) | 文件读取、搜索、目录读取和 Git 查看经过 Gate 产生可追溯证据 | [spec.md](wp-05-readonly-repository-tools/spec.md) v1.0.0 / frozen | [plan.md](wp-05-readonly-repository-tools/plan.md) v1.0.0 / done | [acceptance-report.md](wp-05-readonly-repository-tools/acceptance-report.md) / accepted | DONE |
| [WP-06 写入与命令能力包](wp-06-write-and-command/spec.md) | 单文件写入、patch、受控测试命令、审批、超时与未知结果 | [spec.md](wp-06-write-and-command/spec.md) v1.0.0 / frozen | [plan.md](wp-06-write-and-command/plan.md) v1.0.0 / done | [acceptance-report.md](wp-06-write-and-command/acceptance-report.md) / accepted | DONE |
| [WP-07 Checkpoint 与恢复](wp-07-checkpoint-recovery/spec.md) | 安全快照、崩溃窗口恢复判定、租约协作、旧证据/审批失效和 continuation context | [spec.md](wp-07-checkpoint-recovery/spec.md) v1.1.0 / frozen | [plan.md](wp-07-checkpoint-recovery/plan.md) v1.1.0 / done | [acceptance-report.md](wp-07-checkpoint-recovery/acceptance-report.md) / accepted | DONE |
| [WP-08 验收引擎与代码理解](wp-08-completion-code-understanding/spec.md) | 程序化验收框架、内部只读控制面、Markdown/证据校验、工作区范围和代码理解策略 | [spec.md](wp-08-completion-code-understanding/spec.md) v1.0.0 / frozen | [plan.md](wp-08-completion-code-understanding/plan.md) v1.0.0 / approved | 尚未创建 | DESIGNED |
