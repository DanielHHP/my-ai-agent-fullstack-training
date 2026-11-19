# RAG Search Tool

一个通过 CLI 使用的 RAG 建库、检索与问答工具。建库阶段会解析测试语料并生成
BM25 索引、DashScope 向量索引；检索与问答均支持三种模式：

- `bm25`：基于 jieba 分词的 BM25 关键词检索，不依赖外部 API。
- `vector`：基于 LlamaIndex + DashScope embedding 的语义检索。
- `hybrid`：分别召回 BM25 和向量结果，通过 RRF（倒数排名融合）合并。

`rag search` 只返回带来源的证据片段；`rag ask` 会使用 DashScope 原生文本生成
API 基于这些证据生成带编号引用的回答。

## 环境准备

要求 Python 3.10+。以下命令使用项目内 `.venv` 和 pip：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
```

在 `.env` 中填写 DashScope Key，或直接导出环境变量：

```bash
export DASHSCOPE_API_KEY="sk-..."
```

回答生成默认使用 `qwen-plus`，可按需覆盖：

```bash
export DASHSCOPE_LLM_MODEL="qwen-plus"
export DASHSCOPE_LLM_MAX_TOKENS="800"
export DASHSCOPE_LLM_TIMEOUT_SECONDS="60"
```

## 建库

默认加载 `corpus/`，同时生成向量索引和 BM25 索引，写入 `storage/`：

```bash
rag build
```

常用参数：

```bash
rag build --corpus ./corpus --store ./storage --overwrite
rag build --chunk-size 384 --chunk-overlap 40
rag build --backend bm25                 # 离线建 BM25，不调用 DashScope
rag build --backend vector               # 只建 DashScope 向量索引
rag build --backend all --json           # 机器可读的构建结果
```

`--backend all` 是默认值。若目标存储目录非空，必须显式增加 `--overwrite`。
构建使用临时目录，全部成功后才会替换目标目录，避免留下半成品索引。

## 检索

默认使用 hybrid：

```bash
rag search "登录会话的有效期是多少分钟？"
rag search "SessionExpiryError 在哪" --mode bm25 --top-k 3
rag search "支付会话过期后如何处理" --mode vector
rag search "logout 的实现要求" --mode hybrid --weights 0.7,0.3
rag search "会话规则" --doc-type doc
rag search "会话边界" --json
```

hybrid 中 `--weights` 的顺序是“向量,BM25”，默认两边等权。两种检索的原始
分数量纲不同，因此融合分数使用 RRF，而不是直接相加。

JSON 输出包含模式、最终条数、每路候选数、每条结果的稳定节点 ID、来源文件、
标题、分数、分数类型和片段，适合被其他工具或 Agent 调用。

## 检索增强回答

`rag ask` 默认执行 hybrid 检索，并把前 5 条证据交给 DashScope 生成回答：

```bash
rag ask "登录会话的有效期是多少分钟？"
rag ask "登录会话的有效期是多少分钟？" --json
```

可通过参数显式降级，或在模型失败时自动降级：

```bash
rag ask "会话规则" --no-llm             # 只返回检索证据
rag ask "会话规则" --strict-llm         # 模型失败时返回非零退出码
rag ask "会话规则" --llm-model qwen-turbo
```

默认失败策略是保留检索结果并返回 `answer=null`、`llm_error` 和
`degradation_reason=llm_error`，命令仍以退出码 0 结束。JSON 同时输出
`citations`，其中 `index` 与回答中的 `[n]` 对应。

## 兼容入口

仍可使用参考项目风格的入口：

```bash
python ingest.py --backend all --overwrite
python query.py "会话有效期" --mode hybrid
```

推荐统一使用安装后提供的 `rag` 命令。

## 测试

```bash
python -m pytest
python -m ruff check .
```

测试不调用 DashScope；向量与 live API 校验需要在配置 Key 后手工运行。

## 目录

```text
.
├── pyproject.toml
├── .env.example
├── ingest.py                 # 兼容入口
├── query.py                  # 兼容入口
├── corpus/                   # 测试语料
├── docs/                     # 需求、设计、任务拆解
├── rag/
│   ├── cli.py                # build / search 命令
│   ├── config.py             # 路径、模型、环境变量
│   ├── docload.py            # md/py/html/csv/pdf/txt 解析
│   ├── chunking.py           # 文档分块与标题元数据
│   ├── bm25.py               # BM25 建索引、落盘、检索
│   ├── embedding.py          # DashScope embedding 工厂
│   ├── answer.py             # 证据装配、回答生成与降级
│   ├── llm.py                # DashScope Generation.call 封装
│   ├── vector_store.py       # LlamaIndex SimpleVectorStore
│   ├── hybrid.py             # RRF 融合
│   ├── indexer.py            # 建库编排与安全替换
│   ├── search.py             # 三种检索模式编排
│   └── storage.py            # manifest 与存储校验
├── storage/                  # 运行后生成的索引产物
└── tests/
```
