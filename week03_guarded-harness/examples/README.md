# WP-12 正式样例

本目录提供两类 P0 任务的最小、自包含样例：

- `config/code-understanding.yaml`：阅读 `workspaces/code-understanding/src/app.py`，生成带代码依据的
  Markdown 文档。
- `config/test-repair.yaml`：修复 `workspaces/test-repair` 中的失败测试，不修改既有测试。

配置使用相对路径，状态目录位于目标工作区外。测试会把整个 `examples/` 复制到临时目录并初始化
Git，因此不会在项目仓库中生成状态数据库或任务产物。

自动化验收通过测试专用 Fake Model 注入完成，不连接真实模型或网络。
