# WP-14 CLI Run 指令输入实施计划

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 工作包 | WP-14 CLI Run 指令输入 |
| 计划状态 | done |
| 计划版本 | 1.0.0 |
| 对应规格 | [spec.md](spec.md) v1.0.0 / frozen |

## 2. 实施原则

- 复用 `Run.task_description` 作为唯一有效任务描述事实，不增加 migration；
- CLI 只扩大 START 的任务输入，不扩大执行权限；
- 所有恢复路径从 StateStore 读取有效描述，不依赖当前 YAML 描述、旧 shell 或内存；
- 不在 CLI 层执行 Gate、工具或验收逻辑。

## 3. 任务卡

| 任务 | 内容 | 状态 |
| --- | --- | --- |
| T01 | `RunRequest.instruction` 严格校验和 START/RESUME 模式约束 | DONE |
| T02 | `run --instruction` parser、长度/空白/NUL 校验和 CLI 接线 | DONE |
| T03 | Harness 有效描述选择、Run 持久化和初始 Agent 任务 | DONE |
| T04 | resume continuation prompt 从 Run 持久化描述重建 | DONE |
| T05 | CLI/Harness 正向、边界、恢复和配置指纹测试 | DONE |
| T06 | 设计、决策、运维、测试计划和工作包索引同步 | DONE |

## 4. 测试矩阵

| 场景 | 覆盖 |
| --- | --- |
| START 有指令 | 指令进入 Fake Model 首次消息，Run 状态保存同一文本 |
| START 无指令 | YAML 描述继续进入首次消息 |
| 等待审批后 RESUME | continuation 使用持久化指令且验收通过 |
| RESUME 带指令 | CLI usage error，不创建 Run/模型 |
| 空、NUL、超长 | usage error，无工作区副作用 |
| 配置变化 | 即使 Run 使用 CLI 指令，fingerprint 不匹配时仍拒绝恢复 |
| 权限/验收 | 指令不改变 YAML 工具、路径、审批或 Completion |

## 5. 回滚

回滚 CLI 和领域字段后，新版本创建的 Run 仍可在 State 中读取 `task_description`；当前没有新表、
新 migration 或 Audit schema 变化。回滚后的旧版本会忽略 RunRequest 的新字段，但不应与新版 CLI
并发运行同一状态库。
