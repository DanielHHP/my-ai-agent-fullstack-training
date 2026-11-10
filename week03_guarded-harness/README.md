# Guarded Coding Harness

Guarded Coding Harness 在保留 `smolagents.ToolCallingAgent` 原生决策循环的前提下，为代码理解和
测试修复增加程序化的参数/权限门禁、Sandbox、审批、状态、审计、Checkpoint、恢复和最终验收。

模型只负责理解与选择工具；成功必须由工作区、文件 Evidence、测试报告和 append-only Audit 证明。

## 当前范围

P0 已完成：六个核心模块、Fake Model 驱动的 Agent/CLI 闭环、审批后跨进程恢复、程序化
Completion、离线安全测试、项目级故障矩阵和人工验收均已通过。WP-08 至 WP-12 的验收报告状态
均为 `accepted`。

WP-13 P1 已验收：version 2 模型配置、OpenAI Chat Completions SDK、GovernedModel、
模型调用 usage/成本/Audit、migration v8、显式重试、超时和 unknown 恢复均已通过离线测试、
本地 SDK 协议仿真和 DeepSeek live smoke。

首期支持：

- 单个本地 Git 工作区；
- 代码理解与测试修复；
- Linux/macOS POSIX 进程组；
- 本地 SQLite 状态与本地 CLI 审批；
- Fake Model 离线验收；
- version 2 OpenAI Chat Completions 真实模型配置。

不包含多 Provider 路由、自动 fallback、Web/TUI、多仓库、分布式执行、隐藏验收或容器级隔离。

## 安装与验证

依赖由 `pyproject.toml` 统一声明：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

## 使用入口

```bash
guarded-harness --config examples/config/code-understanding.yaml run --json
guarded-harness --config examples/config/test-repair.yaml run --json
guarded-harness --config examples/config/model-openai-chat-completions.yaml run --json
```

version 1 配置仍只使用可信 `model_factory` 注入；标准入口没有来源时返回
`CLI_MODEL_UNAVAILABLE`。version 2 配置通过默认安装的 OpenAI SDK 和固定 tokenizer 策略
构造真实模型。

正式样例位于 `examples/`，并以临时 Git 副本离线执行。审批、恢复和运维说明见
[operations.md](docs/operations.md)。

## 文档

- [文档索引](docs/README.md)
- [需求](docs/requirements.md)
- [设计](docs/design.md)
- [已确认决策](docs/decisions.md)
- [测试计划](docs/test-plan.md)
- [运维说明](docs/operations.md)
- [WP-08 至 WP-11 手动验收](docs/manual-acceptance-wp08-wp11.md)
- [任务拆解](docs/task-breakdown.md)
