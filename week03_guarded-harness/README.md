# Guarded Coding Harness

这是 Guarded Coding Harness 的工程入口。项目旨在保留
`smolagents.ToolCallingAgent` 的原生决策循环，同时由程序负责参数门禁、执行边界、状态、审计、
恢复和最终验收。

WP-00 工程与契约内核已完成并通过测试，当前具备可安装工程、严格领域模型、配置加载与指纹
能力。Gate、Sandbox、恢复和端到端任务能力仍属于后续工作包，尚未实现。需求、设计、决策、
任务计划和工作包规格统一维护在 [docs/README.md](docs/README.md)。

## 本地开发

依赖由 `pyproject.toml` 统一声明，不再维护 `requirements.txt`：

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```
