from __future__ import annotations

from llama_index.core.schema import NodeWithScore, TextNode

from rag.hybrid import rrf_fuse


def _hit(node_id: str, score: float) -> NodeWithScore:
    return NodeWithScore(node=TextNode(id_=node_id, text=node_id), score=score)


def test_rrf_rewards_documents_present_in_both_lists() -> None:
    vector_hits = [_hit("a", 0.9), _hit("b", 0.8)]
    bm25_hits = [_hit("b", 12.0), _hit("c", 8.0)]

    fused = rrf_fuse([vector_hits, bm25_hits], top_k=3)

    assert [hit.node.node_id for hit in fused] == ["b", "a", "c"]
    assert fused[0].score > fused[1].score


def test_rrf_weights_change_ranking() -> None:
    vector_hits = [_hit("a", 0.9)]
    bm25_hits = [_hit("b", 10.0)]

    vector_heavy = rrf_fuse([vector_hits, bm25_hits], top_k=2, weights=[0.9, 0.1])
    bm25_heavy = rrf_fuse([vector_hits, bm25_hits], top_k=2, weights=[0.1, 0.9])

    assert vector_heavy[0].node.node_id == "a"
    assert bm25_heavy[0].node.node_id == "b"
