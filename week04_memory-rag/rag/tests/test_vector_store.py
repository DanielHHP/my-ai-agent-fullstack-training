from __future__ import annotations

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import TextNode

from rag.bm25 import BM25Index
from rag.config import DEFAULT_CORPUS
from rag.hybrid import hybrid_retrieve
from rag.search import search
from rag.vector_store import build_vector_index, load_vector_index


class _FakeEmbedding(BaseEmbedding):
    def _vector(self, text: str) -> list[float]:
        vector = [0.0, 0.0, 0.0, 1.0]
        if "支付" in text:
            vector[0] = 1.0
        if "登录" in text:
            vector[1] = 1.0
        if "会话" in text:
            vector[2] = 1.0
        return vector

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._vector(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._vector(text)


def _nodes() -> list[TextNode]:
    return [
        TextNode(
            id_="pay",
            text="支付会话有效期为 5 分钟",
            metadata={
                "file_path": "docs/payment.md",
                "doc_type": "doc",
                "heading": "支付会话",
            },
        ),
        TextNode(
            id_="login",
            text="登录会话有效期为 30 分钟",
            metadata={
                "file_path": "docs/login.md",
                "doc_type": "doc",
                "heading": "登录会话",
            },
        ),
    ]


def test_vector_index_persists_and_loads(tmp_path) -> None:
    build_vector_index(_nodes(), tmp_path, _FakeEmbedding())
    assert (tmp_path / "index_store.json").is_file()
    assert (tmp_path / "default__vector_store.json").is_file()

    index = load_vector_index(tmp_path, _FakeEmbedding())
    hits = index.as_retriever(similarity_top_k=1).retrieve("支付会话有效期")

    assert hits[0].node.node_id == "pay"


def test_hybrid_retrieve_uses_persisted_vector_index(tmp_path) -> None:
    nodes = _nodes()
    build_vector_index(nodes, tmp_path, _FakeEmbedding())
    index = load_vector_index(tmp_path, _FakeEmbedding())

    hits = hybrid_retrieve(
        index=index,
        bm25=BM25Index(nodes),
        query="支付会话有效期",
        top_k=2,
        candidate_k=2,
    )

    assert hits[0].node.node_id == "pay"
    assert hits[0].score > hits[1].score


def test_full_build_and_hybrid_search_paths(tmp_path, monkeypatch) -> None:
    import rag.indexer as indexer
    import rag.search as search_module

    monkeypatch.setattr(indexer, "create_embedding_model", _FakeEmbedding)
    monkeypatch.setattr(search_module, "create_embedding_model", _FakeEmbedding)

    summary = indexer.build_index(
        corpus=DEFAULT_CORPUS,
        store_dir=tmp_path / "store",
        chunk_size=128,
        chunk_overlap=16,
        backend="all",
    )
    response = search(
        store_dir=tmp_path / "store",
        question="支付会话有效期",
        mode="hybrid",
        top_k=3,
        candidate_k=5,
    )

    assert summary.backends == ["bm25", "vector"]
    assert response.result_count > 0
    assert response.results[0].score_type == "rrf"
