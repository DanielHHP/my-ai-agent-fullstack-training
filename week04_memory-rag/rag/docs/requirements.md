# RAG 工具需求文档

## 1. 目标

为本地语料提供可重复执行的 RAG 索引、检索和问答 CLI。使用方可以独立建库，
通过 BM25、向量或混合模式取回证据，并可选地使用真实模型生成带来源引用的回答。

## 2. 范围

### 2.1 建库

- 递归读取语料目录中的 Markdown、Python、HTML、CSV、PDF 和纯文本文件。
- 解析内容、清洗文本，并保留来源路径、文档类型和标题层级。
- 将文档切分成可检索节点。
- 生成 BM25 索引，持久化到目标存储目录。
- 使用 LlamaIndex + DashScope embedding 生成向量，并持久化到同一存储目录。
- 支持只建立一种索引，便于隔离依赖故障。
- 默认同时建立两种索引。

### 2.2 检索

- BM25：支持中文分词和代码符号、英文关键词命中。
- 向量：通过 DashScope embedding 和 LlamaIndex 进行语义相似度检索。
- hybrid：分别获取两路候选，使用 RRF 融合后返回最终结果。
- ask：基于检索证据调用 DashScope 文本生成 API 输出带 `[n]` 引用的回答。
- `--no-llm`：主动降级为仅检索；模型失败时默认保留证据并返回错误信息。
- `--strict-llm`：禁止失败降级，模型不可用时返回非零退出码。
- 支持 `top_k`、每路 `candidate_k`、双路权重和 `doc_type` 过滤。
- 支持人类可读文本输出与稳定 JSON 输出。

### 2.3 不包含

- 在线增量索引、删除单文档、权限控制。
- Web 服务或 HTTP API。

## 3. 使用约束

- Python 3.10+。
- 使用 Python 自带 `venv`、`pip` 和 `pyproject.toml`。
- 向量建库及检索需要 `DASHSCOPE_API_KEY`。
- BM25 建库及检索必须能够完全离线运行。

## 4. 验收标准

1. `rag build --backend bm25` 在无 API Key 环境中成功生成索引。
2. `rag search "..." --mode bm25` 能返回文件、标题、分数和片段。
3. 配置 Key 后，`rag build --backend vector` 与 `rag search --mode vector` 可工作。
4. 同时建有两路索引后，`rag search --mode hybrid` 能返回融合分数。
5. 非空存储目录未经 `--overwrite` 时拒绝写入。
6. `--json` 的 stdout 是单个 JSON 对象，错误写入 stderr。
7. 自动化测试不依赖外部 API。
8. 配置 Key 后，`rag ask` 可生成带来源引用的回答。
9. `rag ask --no-llm` 可离线返回检索证据，`--strict-llm` 可禁止失败降级。
