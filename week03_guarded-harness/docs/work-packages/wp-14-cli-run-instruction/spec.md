# WP-14 CLI Run 指令输入工作包规格

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-14 CLI Run 指令输入 |
| 文档状态 | frozen |
| 规格版本 | 1.0.0 |
| 创建日期 | 2026-11-11 |
| 冻结日期 | 2026-11-11 |
| 依赖 | WP-10 accepted；WP-11 accepted；WP-13 accepted |
| 上游接口修订 | `RunRequest.instruction`、`Harness` 有效任务描述装配 |
| 下游影响 | CLI `run`、Run 持久化任务描述、审批后恢复和 continuation context |
| 对应设计 | `design.md` 12.4、12.5 |
| 相关决策 | D-08、D-77、D-79、D-101 |

本文定义通过命令行为新 Run 提供任务指令的行为和安全边界。它是 WP-10/WP-11 冻结交付后的
窄幅功能修订，不重新定义 Gate、Sandbox、审批、Completion 或模型 Provider。

文档冲突时按 `requirements.md / design.md / decisions.md > 本文 > plan.md > 任务卡` 处理。

## 2. 目标

1. 用户可在不改写 YAML 的情况下，为新 `run` 提供一次性任务指令；
2. CLI 指令只替换本次 Run 的有效任务描述，不改变任务配置、权限或验收；
3. 有效指令在 Run 创建时持久化，审批后新进程 `resume` 不依赖原 shell 命令；
4. `RESUME` 禁止重新提供指令，防止已经审批的执行上下文被重新解释；
5. 输入校验、持久化或恢复失败时继续失败关闭。

## 3. 公共契约

命令面新增：

```text
guarded-harness --config PATH run [--instruction TEXT] \
  [--wait-for-approval SECONDS] [--json]
```

`--instruction`：

- 只允许用于 `run`；
- 必须包含非空白文本；
- 最大 4000 字符；
- 拒绝 NUL 字符；
- 与原 Run 指令和 YAML 描述均不拼接；
- 不进入配置指纹。

`RunRequest` 增加可选字段：

```text
instruction: str | None
```

约束：

- `START` 可携带指令；
- `RESUME` 必须为 `None`；
- 字段为 `None` 时，Harness 使用 `config.config.task.description`；
- 字段非空时，Harness 将其作为本 Run 的有效任务描述并持久化为
  `Run.task_description`。

## 4. 生效与恢复语义

初始 Agent 任务来源：

```text
effective_task_description =
    instruction, if instruction is not None
    else config.config.task.description
```

恢复语义：

- `RESUME` 从 `Run.task_description` 读取原有效指令；
- continuation prompt 的前置任务文本必须使用该持久化值；
- 恢复不要求也不接受再次传入 `--instruction`；
- YAML 中的其他字段和配置指纹继续按原规则重新校验；
- 运行期间修改 YAML `task.description` 即使原 Run 使用了 CLI 指令，也会因配置指纹变化而拒绝恢复。

## 5. 安全边界

- CLI 指令是任务输入，不是授权。它不能覆盖 `task.id/kind`、workspace、工具 allowlist、路径、
  预算、审批、测试命令、交付物或验收策略。
- 提示要求访问未授权文件或执行未授权工具时，仍由 Gate 和 Sandbox 拒绝。
- 提示与程序化验收冲突时，由 Completion 拒绝，不能由模型最终回答改变结论。
- `approvals` 和 `inspect` 不接受 `--instruction`，不构造模型。
- 有效指令会写入持久化 Run 状态；调用者不得在命令行中提交 API Key、密码或令牌。
- 指令内容不得进入 stdout/stderr 的未脱敏错误输出。

## 6. 验收条件

| 编号 | 条件 |
| --- | --- |
| WP14-AC-001 | `run --instruction TEXT` 将 TEXT 作为初始 Agent 任务 |
| WP14-AC-002 | 提供指令时不把 YAML `task.description` 拼入初始任务 |
| WP14-AC-003 | 未提供指令时保持原行为，使用 YAML `task.description` |
| WP14-AC-004 | `Run.task_description` 持久化本次有效指令 |
| WP14-AC-005 | 审批后跨进程 `resume` 使用持久化指令和 continuation context |
| WP14-AC-006 | `RESUME` 携带指令时失败关闭 |
| WP14-AC-007 | 纯空白、NUL 和超过 4000 字符的指令返回 CLI usage error |
| WP14-AC-008 | 指令不能扩大工具、路径、审批或验收权限 |
| WP14-AC-009 | CLI 指令不改变配置指纹；配置变化仍阻止恢复 |
| WP14-AC-010 | `approvals` 和 `inspect` 不获得指令输入路径，也不创建模型 |
| WP14-AC-011 | 目标测试、全量测试、Ruff lint 和格式检查通过 |

## 7. 非目标

- 不提供 `--system-prompt` 或覆盖 smolagents Agent 系统提示的入口；
- 不支持从环境变量、`.env`、模型输出或任意脚本加载指令；
- 不支持在 `RESUME` 时追加、替换或修正任务目标；
- 不提供交互式 TUI、Web 输入、多轮会话或跨 Run 指令记忆；
- 不改变 SQLite schema、Audit schema、Gate 或 Completion 策略。
