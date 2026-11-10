# WP-12 正式样例

本目录提供两类 P0 任务的最小、自包含样例：

- `config/code-understanding.yaml`：阅读 `workspaces/code-understanding/src/app.py`，生成带代码依据的
  Markdown 文档。
- `config/test-repair.yaml`：修复 `workspaces/test-repair` 中的失败测试，不修改既有测试。
- `config/model-openai-chat-completions.yaml`：version 2 真实模型配置，使用固定模型快照、
  `OPENAI_API_KEY` 引用和低预算代码理解任务。

配置使用相对路径，状态目录位于目标工作区外。测试会把整个 `examples/` 复制到临时目录并初始化
Git，因此不会在项目仓库中生成状态数据库或任务产物。

默认自动化验收通过测试专用 Fake Model 和本地协议仿真完成，不连接外部模型服务。真实服务
使用 `OPENAI_API_KEY` 显式运行，见 WP-13 测试计划。
