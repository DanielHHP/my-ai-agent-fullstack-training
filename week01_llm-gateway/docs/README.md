# docs 目录说明

本目录用于沉淀 LLM 统一模型调用服务在设计、决策和拆解阶段的文档。当前共四个文档，建议按“需求 → 设计 → 决策 → 任务拆解”的顺序阅读。

| 文档 | 定位 |
| --- | --- |
| [requirements.md](./requirements.md) | 整体需求。描述网关要支持的三类协议、统一抽象层、流式输出、结构化输出、Prompt 版本管理、可观测性与韧性要求，并给出验收标准。 |
| [design.md](./design.md) | 阶段 0 设计细化。锁定对外 API 面、协议与适配器、统一数据模型、路由/流式/结构化/用量等核心契约，是后续实现的设计基线。 |
| [decisions.md](./decisions.md) | 设计决策记录。记录阶段 0 已确认的决策点、结论、影响，以及选择理由和关联约束。 |
| [task-breakdown.md](./task-breakdown.md) | 需求拆解与实施计划。把整体需求拆成可执行、可验证的阶段，并说明参考项目模块的复用或调整方式。 |

## 阅读顺序

1. 先看 [requirements.md](./requirements.md)，明确“要做什么”。
2. 再看 [design.md](./design.md)，理解“系统长什么样”。
3. 遇到取舍问题时看 [decisions.md](./decisions.md)，了解“为什么这样定”。
4. 实际开发时看 [task-breakdown.md](./task-breakdown.md)，确认“按什么顺序做”。

随着接口和实现逐步稳定，本目录再按实际需要补充架构、API、配置、部署和开发文档。
