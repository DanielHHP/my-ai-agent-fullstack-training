# RAG 工具任务拆解

## 阶段 1：项目骨架

- [x] 使用 `pyproject.toml` 管理包、依赖和 console script。
- [x] 建立 `rag/`、`docs/`、`tests/` 与语料目录。
- [x] 提供 `.env.example` 和本地忽略规则。

## 阶段 2：语料处理

- [x] 支持 Markdown、Python、HTML、CSV、PDF 和纯文本。
- [x] 清洗文本并保留文件路径、类型、标题层级。
- [x] 实现 Markdown 标题切分和长节二次切分。

## 阶段 3：索引后端

- [x] 实现 jieba 分词和 BM25 检索。
- [x] 将 BM25 节点以 JSON 安全落盘。
- [x] 接入 LlamaIndex + DashScope embedding。
- [x] 使用 `SimpleVectorStore` 持久化向量索引。
- [x] 增加 manifest 和后端完整性检查。
- [x] 使用临时目录构建并安全替换旧索引。

## 阶段 4：混合检索

- [x] 实现向量与 BM25 双路召回。
- [x] 实现可配置权重的 RRF 融合。
- [x] 支持 `doc_type` 元数据过滤。

## 阶段 5：CLI 与交付

- [x] 实现 `rag build`、`rag search`、文本与 JSON 输出。
- [x] 保留 `ingest.py` 与 `query.py` 兼容入口。
- [x] 增加需要的测试和静态检查配置。
- [x] 完成 README、需求、设计和任务文档。

## 后续可扩展

- [ ] 增量更新和按文件删除。
- [ ] 索引统计与健康检查命令。
- [ ] 可切换 embedding provider 与批量重试策略。
- [ ] 召回评估集和 nDCG/MRR 指标。
