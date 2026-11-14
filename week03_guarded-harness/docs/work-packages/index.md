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
| [WP-00 工程与契约内核](wp-00-engineering-contracts/spec.md) | 工程可安装，公共模型、配置和测试基础设施可用 | `spec.md` v1.4.0 / frozen | `plan.md` v1.1.0 / done | [acceptance-report.md](wp-00-engineering-contracts/acceptance-report.md) / accepted（后续兼容修订由 WP-04/WP-09 验证） | DONE |
| [WP-01 持久化与审计底座](wp-01-persistence-audit/spec.md) | Run 状态、预算、脱敏和 append-only 审计形成事务闭环 | [spec.md](wp-01-persistence-audit/spec.md) v0.4.0 / frozen | [plan.md](wp-01-persistence-audit/plan.md) v0.3.0 / done | [acceptance-report.md](wp-01-persistence-audit/acceptance-report.md) / accepted（v0.3.0 基线 + WP-04 只读接口） | DONE |
| [WP-02 审批生命周期](wp-02-approval-lifecycle/spec.md) | 审批绑定、过期、角色校验、一次性 claim/consume 可独立验证 | [spec.md](wp-02-approval-lifecycle/spec.md) v0.2.0 / frozen | [plan.md](wp-02-approval-lifecycle/plan.md) v0.2.0 / done | [acceptance-report.md](wp-02-approval-lifecycle/acceptance-report.md) / accepted | DONE |
| [WP-03 Sandbox 边界](wp-03-sandbox-boundary/spec.md) | 路径、安全 I/O、安全枚举、受限 pathspec、Git、环境、命令和进程边界 | [spec.md](wp-03-sandbox-boundary/spec.md) v1.1.0 / frozen | [plan.md](wp-03-sandbox-boundary/plan.md) v1.1.0 / done | [acceptance-report.md](wp-03-sandbox-boundary/acceptance-report.md) / accepted（兼容修订） | DONE |
| [WP-04 治理执行内核](wp-04-governed-execution/spec.md) | Registry、Policy、Gate 和工具调用生命周期形成唯一执行入口 | [spec.md](wp-04-governed-execution/spec.md) v1.0.0 / frozen | [plan.md](wp-04-governed-execution/plan.md) v1.0.0 / done | [acceptance-report.md](wp-04-governed-execution/acceptance-report.md) / accepted | DONE |
| [WP-05 只读仓库能力包](wp-05-readonly-repository-tools/spec.md) | 文件读取、搜索、目录读取和 Git 查看经过 Gate 产生可追溯证据 | [spec.md](wp-05-readonly-repository-tools/spec.md) v1.0.0 / frozen | [plan.md](wp-05-readonly-repository-tools/plan.md) v1.0.0 / done | [acceptance-report.md](wp-05-readonly-repository-tools/acceptance-report.md) / accepted | DONE |
| [WP-06 写入与命令能力包](wp-06-write-and-command/spec.md) | 单文件写入、patch、受控测试命令、审批、超时与未知结果 | [spec.md](wp-06-write-and-command/spec.md) v1.0.0 / frozen | [plan.md](wp-06-write-and-command/plan.md) v1.0.0 / done | [acceptance-report.md](wp-06-write-and-command/acceptance-report.md) / accepted | DONE |
| [WP-07 Checkpoint 与恢复](wp-07-checkpoint-recovery/spec.md) | 安全快照、崩溃窗口恢复判定、租约协作、旧证据/审批失效和 continuation context | [spec.md](wp-07-checkpoint-recovery/spec.md) v1.1.0 / frozen | [plan.md](wp-07-checkpoint-recovery/plan.md) v1.1.0 / done | [acceptance-report.md](wp-07-checkpoint-recovery/acceptance-report.md) / accepted | DONE |
| [WP-08 验收引擎与代码理解](wp-08-completion-code-understanding/spec.md) | 程序化验收框架、内部只读控制面、Markdown/证据校验、工作区范围和代码理解策略 | [spec.md](wp-08-completion-code-understanding/spec.md) v1.0.0 / frozen | [plan.md](wp-08-completion-code-understanding/plan.md) v1.0.0 / done | [acceptance-report.md](wp-08-completion-code-understanding/acceptance-report.md) v1.2 / accepted | DONE（24/24 AC、自动化和人工语义复核通过） |
| [WP-09 测试修复验收](wp-09-test-repair-acceptance/spec.md) | 测试基线、最终 JUnit 报告、节点守恒、工作区范围、H2 证据失效和跨进程基线 | [spec.md](wp-09-test-repair-acceptance/spec.md) v1.0.0 / frozen | [plan.md](wp-09-test-repair-acceptance/plan.md) v1.0.0 / done | [acceptance-report.md](wp-09-test-repair-acceptance/acceptance-report.md) v1.2 / accepted | DONE（32/32 AC、自动化和人工测试质量复核通过） |
| [WP-10 Agent 与 Harness](wp-10-agent-harness/spec.md) | Fake Model、`smolagents==1.26.0` 公开适配、唯一 Harness Run 编排、预算/Checkpoint/恢复/Completion 闭环 | [spec.md](wp-10-agent-harness/spec.md) v1.0.0 / frozen | [plan.md](wp-10-agent-harness/plan.md) v1.0.0 / done | [acceptance-report.md](wp-10-agent-harness/acceptance-report.md) v1.2 / accepted | DONE（25/25 AC、依赖和全量复验通过） |
| [WP-11 CLI 与人工恢复](wp-11-cli-manual-recovery/spec.md) | run/resume/approvals/inspect、等待审批、退出码和新进程恢复 | [spec.md](wp-11-cli-manual-recovery/spec.md) v1.0.0 / frozen | [plan.md](wp-11-cli-manual-recovery/plan.md) v1.1.0 / done | [acceptance-report.md](wp-11-cli-manual-recovery/acceptance-report.md) v1.2 / accepted | DONE（25/25 AC、CLI 和跨进程人工验收通过） |
| [WP-12 故障加固与交付](wp-12-fault-hardening-delivery/spec.md) | 项目级故障矩阵、正式样例、两类任务 E2E、最终质量门禁和交付证据 | [spec.md](wp-12-fault-hardening-delivery/spec.md) v1.0.0 / frozen | [plan.md](wp-12-fault-hardening-delivery/plan.md) v1.0.0 / done | [acceptance-report.md](wp-12-fault-hardening-delivery/acceptance-report.md) v1.1 / accepted | DONE（9 项目标、372 项全量和最终人工验收通过） |
| [WP-13 真实模型 Provider 与模型调用治理](wp-13-real-model-provider/spec.md) | version 2 模型配置、OpenAI Chat Completions v1 Provider、ModelCallGateway、预算预留、审计、失败分类、恢复和 live smoke | [spec.md](wp-13-real-model-provider/spec.md) v1.1.0 / frozen | [plan.md](wp-13-real-model-provider/plan.md) v1.0.0 / done | [acceptance-report.md](wp-13-real-model-provider/acceptance-report.md) v1.3 / accepted | DONE（离线回归、SDK 协议、DeepSeek live smoke 和失败分类审计通过） |
| [WP-14 CLI Run 指令输入](wp-14-cli-run-instruction/spec.md) | `run --instruction`、START-only 覆盖、有效指令持久化和恢复 | [spec.md](wp-14-cli-run-instruction/spec.md) v1.0.0 / frozen | [plan.md](wp-14-cli-run-instruction/plan.md) v1.0.0 / done | [acceptance-report.md](wp-14-cli-run-instruction/acceptance-report.md) v1.0.0 / accepted | DONE（11/11 AC，目标和全量门禁通过） |
