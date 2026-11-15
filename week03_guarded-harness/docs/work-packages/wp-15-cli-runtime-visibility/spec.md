# WP-15 CLI 运行可观测性工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-15 CLI 运行可观测性 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-15 |
| 冻结日期 | 2026-11-15 |
| 依赖 | WP-10 accepted；WP-11 accepted；WP-14 accepted |
| 对应设计 | `design.md` 12.3、12.5 |
| 相关决策 | D-75、D-82、D-83、D-111 |

本文定义 `run/resume` 的人类可读运行进度和最终回答展示。它不改变 Gate、审批、执行、验收、
状态机、持久化 schema 或 `smolagents` 决策循环。

## 2. 目标

1. 新 Run 持久化并进入 `RUNNING` 后、首次模型调用前，CLI 立即可见 `run_id` 和 `trace_id`；
2. `--wait-for-approval` 遇到待审批调用时，立即可见审批操作所需信息；
3. 文本模式将模型 `final_answer` 作为独立纯文本区块展示，避免 JSON 转义影响阅读；
4. `--json` 的 stdout 继续严格保持单个 JSON envelope；
5. 所有新增输出继续脱敏、有界并区分“模型候选答案”和“程序验收结果”。

## 3. 输出契约

### 3.1 输出通道

- 运行进度写入 stderr，包括 Run 创建和审批等待；
- 默认文本模式的最终摘要和 `final_answer` 写入 stdout；
- `--json` 模式只在 stdout 写一个 JSON envelope，进度仍可写入 stderr；
- `--json` 的 `data.candidate_final_answer` 保持兼容，不由 CLI 重新拼接原始模型响应。

### 3.2 Run 创建

Harness 在 Run、工作区基线和 `RUNNING` 状态事务提交后发出只读 `RunCreatedEvent`。事件在首次
模型调用前交付给可选观察器，且不参与任何授权或验收。

默认进度格式：

```text
run created: run_id=RUN_ID trace_id=TRACE_ID
```

观察器故障不得改变 Run 状态、审批、预算、工具执行或 Completion 结果。

### 3.3 审批提示

`run/resume --wait-for-approval` 对每条审批立即输出一次摘要，同一进程按 `approval_id` 去重：

```text
approval required
  run_id=RUN_ID
  approval_id=APPROVAL_ID
  tool=TOOL_NAME
  tool_call_id=TOOL_CALL_ID
  status=EFFECTIVE_STATUS
  expires_at=UTC_TIME
  roles=APPROVER_ROLES
```

不展示规范化工具参数、完整文件内容、lease token 或审批绑定摘要。提示只用于观察，不代表批准。

### 3.4 最终回答

文本模式直接读取已经持久化、脱敏和限长的 `RunResult.candidate_final_answer`，输出为：

```text
final_answer (model candidate; not acceptance evidence):
<原始换行和 Markdown>
```

- 没有候选答案时不输出空区块；
- 不通过 `json.dumps` 展示，因此不产生普通换行和引号转义；
- 展示前移除 ANSI escape，将除换行/制表符外的控制字符替换为可见替换符；
- 候选答案存在不改变退出码，也不表示 `ACCEPTED`。

## 4. 安全与兼容边界

- `RunCreatedEvent` 只包含 `run_id` 和 `trace_id`，不包含任务描述、工作区内容或模型输出；
- 进度观察器不得成为 `step_callbacks` 安全门禁的替代品；
- 新增 stderr 内容不得污染 `--json` stdout；
- 输出失败不得伪造 Run 或审批状态；观察器故障只记录固定非敏感日志；
- 既有退出码、审批生命周期、`--wait-for-approval` 超时语义和 JSON data 字段保持兼容。

## 5. 验收条件

| 编号 | 条件 |
| --- | --- |
| WP15-AC-001 | Run 创建事件在 Run 已持久化为 `RUNNING` 后、模型调用前触发 |
| WP15-AC-002 | 默认运行在 stderr 显示正确的 `run_id` 和 `trace_id` |
| WP15-AC-003 | `--json` stdout 仍可完整解析为单个 envelope，进度只出现在 stderr |
| WP15-AC-004 | 等待审批时显示 Run、审批、工具、调用、状态、有效期和角色摘要 |
| WP15-AC-005 | 同一审批在单次等待命令中只提示一次 |
| WP15-AC-006 | 文本模式以独立区块展示多行 `final_answer`，无 JSON 普通字符转义 |
| WP15-AC-007 | `final_answer` 展示使用持久化脱敏值，并清理 ANSI/控制字符 |
| WP15-AC-008 | 没有候选最终答案时不输出空区块 |
| WP15-AC-009 | 观察器故障不改变治理或验收结果 |
| WP15-AC-010 | 目标测试、全量测试、Ruff lint 和格式检查通过 |

## 6. 非目标

- 不增加流式 token、模型隐藏推理或完整工具参数展示；
- 不改变审批命令、退出码、等待超时或 JSON envelope schema；
- 不提供 TUI、Web、颜色主题、日志文件轮转或交互式审批；
- 不改变 `smolagents`、Gate、Sandbox、ApprovalStore、Completion 或 SQLite migration。
