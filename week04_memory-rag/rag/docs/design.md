# RAG 工具设计文档

## 1. 总体结构

```text
CLI
 ├── build -> loader -> chunker -> vector index + BM25 index -> store
 └── search -> BM25 and/or vector retrieval -> RRF -> formatted output
```

`rag/` 包按职责拆分，根目录脚本只做兼容入口。安装后的主入口是
`rag = rag.cli:main`。

## 2. 模块职责

| 模块 | 职责 |
| --- | --- |
| `config.py` | 项目路径、环境变量、模型和默认参数 |
| `docload.py` | 各文件格式解析，形成 LlamaIndex `Document` |
| `chunking.py` | Markdown 标题分块、长节二次切分、元数据补全 |
| `bm25.py` | jieba 分词、BM25 检索、节点 JSON 持久化 |
| `embedding.py` | 创建 DashScope embedding，校验 API Key |
| `vector_store.py` | LlamaIndex `SimpleVectorStore` 的建立和加载 |
| `hybrid.py` | 两路召回及加权 RRF |
| `storage.py` | manifest、schema 版本和后端完整性校验 |
| `indexer.py` | 建库流程、临时目录构建、原子替换 |
| `search.py` | 三种检索模式及统一的命中结果模型 |
| `cli.py` | 参数解析、文本/JSON 输出、错误码 |

## 3. 节点模型

同一批 `TextNode` 同时进入向量索引和 BM25 索引，因此 hybrid 可以使用节点 ID
识别两路命中的同一片段。

关键元数据：

- `file_path`：相对语料根目录的路径。
- `doc_type`：`doc`、`code`、`web`、`table`、`pdf` 或 `text`。
- `heading`：最具体的小节标题。
- `header_path`：Markdown 标题层级路径。
- `source`：原始文件后缀。

Markdown 按标题切分，过长的小节再按句子和 token 长度切分；代码文件保留为
一个节点，避免函数实现被切断；HTML 先转成带标题的文本；CSV 转成逐行自然
语言；PDF 使用 pypdf 抽文本。

## 4. 索引存储

```text
storage/
├── manifest.json
├── bm25_nodes.json
├── default__vector_store.json
├── docstore.json
├── index_store.json
└── ... LlamaIndex 持久化文件
```

- BM25 节点使用 JSON 存储，不使用 pickle，降低反序列化风险。
- manifest 记录 schema 版本、语料路径、节点数、分块参数、可用后端和 embedding
  模型。检索前会校验所需后端。
- 建库先在目标同级临时目录完成，成功后替换目标目录。替换失败时保留原索引。

## 5. 检索与融合

BM25 和向量各取 `candidate_k` 条候选。权重为 `(vector_weight, bm25_weight)`
时，每条结果的 RRF 分数是：

```text
score = vector_weight / (60 + vector_rank)
      + bm25_weight   / (60 + bm25_rank)
```

未命中的一路不贡献分数。最终按融合分数排序并截取 `top_k`。RRF 不依赖两路
原始分数的尺度，适合 BM25 与 cosine similarity 混合。

## 6. 错误处理

- API Key 缺失：在建立向量索引前立即失败，并给出配置提示。
- 目标目录非空且没有 `--overwrite`：拒绝覆盖。
- 索引缺失或 manifest 与文件不一致：提示重新建库。
- 无检索结果：正常退出，返回空结果及外层状态。
- JSON 模式：成功结果只写 stdout 的 JSON；可预期错误写 stderr 并以非零码退出。

## 7. 兼容性

依赖明确限定 Python 3.10 可安装范围。`llama-index-embeddings-dashscope` 固定
在 0.5.0，因为 0.7.0 起最低 Python 版本变为 3.11。
