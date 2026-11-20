# RAG 功能验收操作指引

> 验收依据：飞书文档“第二题 基于 LlamaIndex 实现向量与关键词混合检索 RAG”
>
> 验收对象：当前 `rag-search-tool` 项目
>
> 文档基准：2026-11-19，飞书文档 revision 9
>
> 当前能力：支持 `rag search`、`rag ask`、LLM 自动降级和严格失败模式

## 1. 验收结论口径

本指引覆盖以下四项必验内容：

| 编号 | 验收项 | 通过条件 |
| --- | --- | --- |
| A | 建立索引 | 使用真实 DashScope Embedding；同一批节点同时建立向量和 BM25 索引；输出节点数量及一条带来源的分块 |
| B | 三路查询 | 向量、BM25、RRF 混合检索各输出前 5 条结果，且每条都有文件、标题和片段 |
| C | RAG 回答 | 将混合检索证据交给真实模型，答案为 30 分钟，并带有与证据对应的来源引用 |
| D | 更新验证 | 将登录会话有效期改为 20 分钟并完整重建两路索引；旧日志块退出当前检索，回答和引用切换为新资料 |

作业正文写“各自前三条”，命令说明写“各自前 5 条”。本指引按更严格的前 5 条执行，前 5 条通过即同时覆盖前 3 条要求。

### 当前项目的验收边界

当前项目已经提供：

- `rag search`：只返回带来源的证据。
- `rag ask`：执行检索并调用 DashScope 文本生成 API 输出带引用回答。
- `rag ask --no-llm`：主动降级为仅检索。
- `rag ask --strict-llm`：模型失败时返回非零退出码，不静默降级。

A、B、C、D 均可通过项目自身 CLI 完成验收。

回答生成直接使用 DashScope 原生文本生成 API，不依赖额外的 LlamaIndex
LLM 适配层。默认模型为 `qwen-plus`，默认输出上限为 800 tokens。

## 2. 环境准备

从项目根目录执行：

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

确认 DashScope Key 已配置，但不要把 Key 写入截图或提交材料：

```bash
test -n "$DASHSCOPE_API_KEY" && echo "DASHSCOPE_API_KEY is set"
```

如果使用项目 `.env`，确认其中包含：

```dotenv
DASHSCOPE_API_KEY=sk-...
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v3
DASHSCOPE_EMBEDDING_BATCH_SIZE=25
DASHSCOPE_LLM_MODEL=qwen-plus
DASHSCOPE_LLM_MAX_TOKENS=800
DASHSCOPE_LLM_TIMEOUT_SECONDS=60
```

可选地指定真实回答模型，默认使用 `qwen-plus`：

```bash
export DASHSCOPE_LLM_MODEL="qwen-plus"
```

`DASHSCOPE_LLM_MAX_TOKENS=800` 只限制模型回答长度，不限制检索证据的
输入长度。RAG 问答通常生成数百字，800 足够；如果回答反复被截断，可在
验收环境中临时改为 1200：

```bash
export DASHSCOPE_LLM_MAX_TOKENS="1200"
```

先完成不调用外部 API 的质量检查：

```bash
python -m pytest -q
python -m ruff check .
rag --help
```

当前版本的预期结果：

```text
18 passed
All checks passed!
```

## 3. 建立隔离的验收目录

不要在原始语料上直接修改。创建临时验收目录，并复制一份语料：

```bash
RAG_ACCEPTANCE_DIR="$(mktemp -d /tmp/rag-acceptance.XXXXXX)"
export RAG_ACCEPTANCE_DIR
cp -R corpus "$RAG_ACCEPTANCE_DIR/corpus"
```

后续命令统一使用：

```text
语料：$RAG_ACCEPTANCE_DIR/corpus
索引：$RAG_ACCEPTANCE_DIR/storage
```

确认原始语料中的基线信息：

```bash
rg -n "30 分钟|30 \* 60" "$RAG_ACCEPTANCE_DIR/corpus"
```

基线资料中，以下位置都包含登录会话 30 分钟的信息：

- `docs/session-rules.md`
- `docs/payment-session.md` 的“与登录会话的区别”
- `docs/faq.md`
- `docs/testing-guide.md`
- `tables/session-matrix.csv`
- `app/session.py`
- `history/2026-10-06-logout-request.md`

注意：`tables/session-matrix.csv` 中还有“记住我令牌 30 天”，它不是登录会话的 30 分钟，更新时不要误改。

## 4. 验收项 A：建立向量与 BM25 索引

### 4.1 构建两路索引

执行并保留输出：

```bash
rag build \
  --backend all \
  --corpus "$RAG_ACCEPTANCE_DIR/corpus" \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --chunk-size 384 \
  --chunk-overlap 10 \
  --overwrite \
  --json
```

当前语料规模的预期关键字段：

```json
{
  "document_count": 12,
  "node_count": 36,
  "backends": ["bm25", "vector"]
}
```

若语料内容发生变化，节点数可以不同，但必须满足：

- `backends` 同时包含 `bm25` 和 `vector`。
- `node_count` 大于 0。
- 构建成功且两个后端均无报错。
- `$RAG_ACCEPTANCE_DIR/storage/manifest.json` 已生成。

### 4.2 证明两路索引使用同一批节点

执行以下检查：

```bash
python - "$RAG_ACCEPTANCE_DIR" <<'PY'
import json
import sys
from pathlib import Path

from llama_index.core import StorageContext

run_dir = Path(sys.argv[1])
store = run_dir / "storage"

manifest = json.loads((store / "manifest.json").read_text(encoding="utf-8"))
bm25_payload = json.loads((store / "bm25_nodes.json").read_text(encoding="utf-8"))
bm25_nodes = bm25_payload["nodes"]
bm25_ids = {node["id_"] for node in bm25_nodes}

storage_context = StorageContext.from_defaults(persist_dir=str(store))
vector_node_ids = set(storage_context.docstore.docs)

print(f"manifest node_count: {manifest['node_count']}")
print(f"bm25 node_count: {len(bm25_ids)}")
print(f"vector docstore node_count: {len(vector_node_ids)}")
print(f"shared node_count: {len(bm25_ids & vector_node_ids)}")

assert len(bm25_ids) == manifest["node_count"]
assert len(vector_node_ids) == manifest["node_count"]
assert bm25_ids == vector_node_ids

sample = next(
    node
    for node in bm25_nodes
    if node["metadata"]["file_path"] == "docs/session-rules.md"
    and "有效期为 30 分钟" in node["text"]
)

print("--- sample chunk ---")
print(f"node_id: {sample['id_']}")
print(f"source: {sample['metadata']['file_path']}")
print(f"heading: {sample['metadata']['heading']}")
print(sample["text"])
PY
```

通过条件：

- `manifest node_count`、`bm25 node_count`、`vector docstore node_count` 三者相等。
- `shared node_count` 与节点数相等，说明向量索引和 BM25 使用同一批节点。
- 分块样例包含节点 ID、来源 `docs/session-rules.md`、标题和原文内容。

截图 1 应包含：

- `rag build --backend all --json` 的成功输出。
- 上述脚本输出的节点数量对比。
- 一条带来源的登录会话分块样例。

## 5. 验收项 B：三种检索方式对比

固定查询：

```text
登录会话的有效期是多少分钟？
```

分别执行三路检索，均取前 5 条：

```bash
rag search "登录会话的有效期是多少分钟？" \
  --mode bm25 \
  --top-k 5 \
  --candidate-k 20 \
  --snippet-chars 500 \
  --store "$RAG_ACCEPTANCE_DIR/storage"
```

```bash
rag search "登录会话的有效期是多少分钟？" \
  --mode vector \
  --top-k 5 \
  --candidate-k 20 \
  --snippet-chars 500 \
  --store "$RAG_ACCEPTANCE_DIR/storage"
```

```bash
rag search "登录会话的有效期是多少分钟？" \
  --mode hybrid \
  --top-k 5 \
  --candidate-k 20 \
  --weights 1,1 \
  --snippet-chars 500 \
  --store "$RAG_ACCEPTANCE_DIR/storage"
```

三条命令都必须满足：

- `results=5`，或至少证据不足时仍完整展示实际命中数。
- 每条结果包含 `[序号] 文件 § 标题`、分数和片段。
- BM25 的 `score_type` 为 `bm25`。
- 向量的 `score_type` 为 `vector_similarity`。
- 混合检索的 `score_type` 为 `rrf`，且展示融合分数。
- 不得把支付会话的 5 分钟误判为登录会话有效期。

建议同时保存机器可读结果，便于核对排名和来源：

```bash
rag search "登录会话的有效期是多少分钟？" \
  --mode hybrid \
  --top-k 5 \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json > "$RAG_ACCEPTANCE_DIR/hybrid-before.json"
```

## 6. 验收项 C：真实模型生成带来源回答

`rag ask` 默认执行混合检索，并把前 5 条带编号证据交给真实 DashScope 模型。该步骤会实际调用模型，请确认 Key 和模型可用。

当前回答链路：

1. `rag ask` 执行 BM25、向量或 RRF 混合检索。
2. `answer.py` 将证据组织为带 `[1]`、`[2]` 编号的上下文。
3. `llm.py` 调用 DashScope `Generation.call`。
4. JSON 同时返回回答、引用、检索结果和降级状态。

执行：

```bash
rag ask "登录会话的有效期是多少分钟？" \
  --mode hybrid \
  --top-k 5 \
  --candidate-k 20 \
  --weights 1,1 \
  --snippet-chars 500 \
  --store "$RAG_ACCEPTANCE_DIR/storage"
```

同时保存机器可读结果：

```bash
rag ask "登录会话的有效期是多少分钟？" \
  --mode hybrid \
  --top-k 5 \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json > "$RAG_ACCEPTANCE_DIR/answer-before.json"
```

通过条件：

- 模型明确回答“登录会话有效期为 30 分钟”。
- 回答包含 `[1]`、`[2]` 等证据编号，且编号能在前面的证据列表中找到对应文件。
- 回答长度、措辞可以不同，但数字、对象和来源必须一致。
- 不能因为支付会话的 5 分钟证据而回答 5 分钟。
- JSON 中 `degraded=false`、`degradation_reason=null`，`answer` 非空。
- JSON 的 `citations` 与检索证据编号一一对应。
- JSON 的 `llm_model` 为实际调用模型，预期为 `qwen-plus`。

### 6.1 验证主动降级

```bash
rag ask "登录会话的有效期是多少分钟？" \
  --mode bm25 \
  --no-llm \
  --top-k 5 \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json
```

该命令应返回 `answer=null`、`degraded=true` 和
`degradation_reason=llm_disabled`，同时保留全部检索证据。

### 6.2 验证自动降级与严格模式

正常验收要求模型调用成功，因此截图不要求故意制造失败。若需要额外验证
降级控制，可使用一个不存在的模型名：

```bash
rag ask "登录会话的有效期是多少分钟？" \
  --mode bm25 \
  --top-k 5 \
  --llm-model "__invalid_model_for_acceptance__" \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json

rag ask "登录会话的有效期是多少分钟？" \
  --mode bm25 \
  --top-k 5 \
  --llm-model "__invalid_model_for_acceptance__" \
  --strict-llm \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json
```

第一条命令预期：

- 退出码为 0。
- `answer=null`、`degraded=true`。
- `degradation_reason=llm_error`，并提供 `llm_error`。
- `results` 和 `citations` 仍然保留。

第二条命令应返回相同的降级 JSON，但退出码必须为 1。

截图 2 应同时包含：

- BM25、向量、混合检索的关键输出，至少能看出三种模式及各自来源。
- `rag ask` 的混合检索证据列表。
- 真实模型回答及来源编号。
- `answer`、`degraded=false` 和 `llm_model=qwen-plus` 等关键 JSON 字段。

如果终端一屏放不下，建议缩小字体后截一张完整终端截图，同时保留完整文本日志作为附件。

## 7. 验收项 D：修改为 20 分钟并更新两路索引

### 7.1 修改验收副本

只修改 `$RAG_ACCEPTANCE_DIR/corpus`，不要修改原始 `corpus/`。

将下列资料中表示“当前登录会话有效期”的 `30 分钟` 改为 `20 分钟`：

- `docs/session-rules.md`：有效期长度、到期说明、续期说明全部改为 20 分钟。
- `docs/faq.md`：默认有效期改为 20 分钟。
- `docs/testing-guide.md`：续期用例说明改为 20 分钟。
- `docs/payment-session.md`：只把“登录会话 30 分钟”改为“登录会话 20 分钟”；支付会话仍为 5 分钟。
- `tables/session-matrix.csv`：只改 `login_demo,登录会话` 这一行；支付会话 5 分钟、匿名浏览 120 分钟、记住我令牌 30 天保持不变。
- `app/session.py`：将 `SESSION_TTL_SECONDS = 30 * 60` 改为 `20 * 60`。
- `history/2026-10-06-logout-request.md`：验收副本中将自然到期时间同步改为 20 分钟。

最后一项的目的，是确保检索前 5 条中不再残留与 20 分钟冲突的旧登录有效期片段。验收副本只用于证明更新链路，不代表要修改生产历史记录。

修改后执行：

```bash
rg -n "30 分钟|30 \* 60" "$RAG_ACCEPTANCE_DIR/corpus"
rg -n "20 分钟|20 \* 60" "$RAG_ACCEPTANCE_DIR/corpus"
```

第一条命令应当没有输出。若仍有输出，逐条判断：

- 如果属于登录会话旧口径，继续修改。
- 如果只是“30 天”，不属于本次替换目标，可以保留。

### 7.2 完整重建两路索引

重新构建到同一个存储目录，使用 `--overwrite` 触发安全替换：

```bash
rag build \
  --backend all \
  --corpus "$RAG_ACCEPTANCE_DIR/corpus" \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --chunk-size 384 \
  --chunk-overlap 10 \
  --overwrite \
  --json
```

项目会先在临时目录完成 BM25 和向量两路建库，全部成功后再替换旧索引。如果其中一路失败，旧索引不会被半成品覆盖。

### 7.3 重新查询三种模式

重新执行与基线完全相同的三条查询命令，重点检查：

- 结果片段使用“登录会话有效期为 20 分钟”。
- 不再出现“登录会话有效期为 30 分钟”的活动资料片段。
- 来源优先指向更新后的 `docs/session-rules.md`、`tables/session-matrix.csv` 或 `app/session.py`。
- 支付会话仍然只能回答 5 分钟，不被本次登录会话更新污染。

可再次保存混合检索 JSON：

```bash
rag search "登录会话的有效期是多少分钟？" \
  --mode hybrid \
  --top-k 5 \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json > "$RAG_ACCEPTANCE_DIR/hybrid-after.json"
```

检查更新前后的来源：

```bash
python - "$RAG_ACCEPTANCE_DIR" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
before = json.loads((run_dir / "hybrid-before.json").read_text(encoding="utf-8"))
after = json.loads((run_dir / "hybrid-after.json").read_text(encoding="utf-8"))

print("=== before ===")
for hit in before["results"]:
    print(hit["rank"], hit["file"], hit["heading"])

print("=== after ===")
for hit in after["results"]:
    print(hit["rank"], hit["file"], hit["heading"])

old_login_text = [
    hit
    for hit in after["results"]
    if "登录" in hit["snippet"] and "30 分钟" in hit["snippet"]
]
assert not old_login_text, "更新后前 5 条中仍存在登录会话 30 分钟片段"

new_login_text = [
    hit
    for hit in after["results"]
    if "登录" in hit["snippet"] and "20 分钟" in hit["snippet"]
]
assert new_login_text, "更新后前 5 条中没有登录会话 20 分钟片段"
PY
```

### 7.4 重新生成回答

保存更新后的机器可读回答：

```bash
rag ask "登录会话的有效期是多少分钟？" \
  --mode hybrid \
  --top-k 5 \
  --candidate-k 20 \
  --weights 1,1 \
  --store "$RAG_ACCEPTANCE_DIR/storage" \
  --json > "$RAG_ACCEPTANCE_DIR/answer-after.json"
```

对比更新前后的回答和引用：

```bash
python - "$RAG_ACCEPTANCE_DIR" <<'PY'
import json
import sys
from pathlib import Path

run_dir = Path(sys.argv[1])
before = json.loads((run_dir / "answer-before.json").read_text(encoding="utf-8"))
after = json.loads((run_dir / "answer-after.json").read_text(encoding="utf-8"))
normalized_answer = after["answer"].replace(" ", "")

print("before answer:", before["answer"])
print("after answer:", after["answer"])
print("after citation files:", [item["file"] for item in after["citations"]])

assert before["answer"], "更新前没有生成回答"
assert after["answer"], "更新后没有生成回答"
assert "20分钟" in normalized_answer, "更新后回答没有使用 20 分钟"
assert "30分钟" not in normalized_answer, "更新后回答仍包含 30 分钟"
assert after["llm_error"] is None and after["degraded"] is False
assert after["citations"], "更新后回答没有引用"
PY
```

同时执行一次人类可读的 `rag ask` 用于截图。通过条件：

- 回答明确为“登录会话有效期为 20 分钟”。
- 来源引用指向更新后的资料。
- 回答不再把 30 分钟作为当前登录会话有效期。
- 如果引用多个来源，所有来源片段都必须支持 20 分钟。
- `llm_error=null`、`degraded=false`，回答不是自动降级结果。

截图 3 应包含：

- 修改确认：`30 分钟` 检查无输出，`20 分钟` 检查有输出。
- 完整重建两路索引的成功结果。
- 更新后的混合检索结果。
- 更新后的真实模型回答及来源引用。

## 8. 截图与提交清单

作业要求提交代码和三张截图，建议文件命名如下：

```text
01-build-index.png
02-three-modes-and-answer.png
03-after-update.png
```

每张截图都应能看到当前命令和关键输出；不要只截结果数字。提交前逐项检查：

- [ ] A：向量与 BM25 两路索引创建成功。
- [ ] A：manifest、BM25 节点、向量 docstore 节点 ID 完全一致。
- [ ] A：展示了一条带文件来源和标题的分块。
- [ ] B：BM25、向量、混合检索均输出前 5 条。
- [ ] B：每条结果均有来源和片段，混合检索分数类型为 `rrf`。
- [ ] C：真实模型回答 30 分钟，并带来源编号。
- [ ] C：`rag ask --json` 中 `degraded=false`，`llm_error=null`。
- [ ] C：`rag ask --no-llm` 保留证据，并返回 `llm_disabled`。
- [ ] D：资料副本已改为 20 分钟，两路索引已完整重建。
- [ ] D：更新后的前 5 条不再包含登录会话 30 分钟旧片段。
- [ ] D：真实模型回答 20 分钟，且引用更新后的资料。
- [ ] `python -m pytest -q` 和 `python -m ruff check .` 均通过。
- [ ] 截图中没有暴露完整 API Key。

## 9. 严格判定标准

以下任一情况判定不通过：

- 只建立了 BM25 索引，或只建立了向量索引。
- 向量维度由 mock/fake embedding 生成，而非真实 DashScope Embedding。
- 两路索引节点数量不同，或混合检索不能通过稳定节点 ID 去重融合。
- 三种检索未分别展示结果，或没有文件来源和片段。
- 回答没有来源编号，或引用的来源与回答不一致。
- 把支付会话的 5 分钟回答成登录会话有效期。
- 更新 20 分钟后未重建索引，只修改原始文档。
- 更新后仍检索到登录会话 30 分钟旧片段。
- 更新后回答仍为 30 分钟，或引用旧资料。
- `rag ask` 没有生成回答，或回答中没有可追溯的来源编号。
- 正常模型调用被误判为降级成功，或 JSON 中 `degraded=true`。
- `--no-llm` 仍发起模型请求，或丢失检索证据。
- `--strict-llm` 在模型失败时仍返回退出码 0。

## 10. 常见失败排查

### API Key 缺失

报错包含 `DASHSCOPE_API_KEY is not set` 时，重新执行：

```bash
export DASHSCOPE_API_KEY="sk-..."
```

### 存储目录非空

完整重建必须增加 `--overwrite`。首次建库可省略，但指引统一使用它便于重复验收。

### 更新后仍有 30 分钟

先检查原始语料的所有出现位置：

```bash
rg -n "30 分钟|30 \* 60" "$RAG_ACCEPTANCE_DIR/corpus"
```

再确认是否没有使用 `--overwrite` 重建，或查询是否误用了旧 `--store`。

### 回答引用不准确

检查传给模型的证据列表是否包含文件、标题和 `[编号]`。如果模型没有按编号引用，可在系统提示中进一步强调“每个结论必须带证据编号”。

### 模型接口失败

先单独确认 Key 和模型可用，可通过环境变量切换模型：

```bash
export DASHSCOPE_LLM_MODEL="qwen-plus"
```

如果正常模型调用返回 `degradation_reason=llm_error`，先检查：

- `DASHSCOPE_API_KEY` 是否有效。
- 当前账号是否已开通 `qwen-plus`。
- `DASHSCOPE_LLM_TIMEOUT_SECONDS=60` 是否需要根据网络调整。

### 回答被截断

如果回答在句子中途结束，可临时提高输出上限：

```bash
export DASHSCOPE_LLM_MAX_TOKENS="1200"
```

该值只影响模型回答长度。更新环境变量后重新执行 `rag ask` 即可，无需重建索引。
