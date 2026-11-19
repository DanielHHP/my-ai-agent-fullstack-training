from __future__ import annotations

from llama_index.core.schema import TextNode

from rag.bm25 import BM25Index, load_nodes, save_nodes, tokenize


def _node(text: str, file_path: str, doc_type: str = "doc") -> TextNode:
    return TextNode(
        text=text,
        metadata={
            "file_path": file_path,
            "doc_type": doc_type,
            "heading": "test",
        },
    )


def test_tokenize_keeps_chinese_and_code_terms() -> None:
    tokens = tokenize("SessionExpiryError 的 now < expires_at")
    assert "sessionexpiryerror" in tokens
    assert "expires_at" in tokens
    assert "<" in tokens


def test_bm25_prefers_exact_keyword_and_filters_doc_type() -> None:
    index = BM25Index(
        [
            _node("登录会话有效期为 30 分钟。", "docs/session.md"),
            _node("支付会话 validuntil 有效期为 5 分钟。", "docs/payment.md"),
            _node("def logout(token): return None", "app/auth.py", "code"),
        ]
    )

    hits = index.retrieve("支付会话有效期", top_k=2)
    assert hits[0].node.metadata["file_path"] == "docs/payment.md"

    code_hits = index.retrieve("logout", top_k=3, doc_type="code")
    assert len(code_hits) == 1
    assert code_hits[0].node.metadata["file_path"] == "app/auth.py"


def test_bm25_nodes_json_round_trip(tmp_path) -> None:
    nodes = [_node("会话到期边界 now == expires_at", "docs/session.md")]
    path = tmp_path / "bm25_nodes.json"

    save_nodes(nodes, path)
    loaded = load_nodes(path)

    assert len(loaded) == 1
    assert loaded[0].text == nodes[0].text
    assert loaded[0].metadata["file_path"] == "docs/session.md"
