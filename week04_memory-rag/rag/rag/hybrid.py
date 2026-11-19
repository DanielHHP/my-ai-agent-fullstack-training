"""Hybrid retrieval and reciprocal-rank fusion."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from llama_index.core.schema import NodeWithScore
from llama_index.core.vector_stores import MetadataFilter, MetadataFilters

from .bm25 import BM25Index
from .config import RRF_K


def metadata_filters(doc_type: str | None) -> MetadataFilters | None:
    if not doc_type:
        return None
    return MetadataFilters(
        filters=[MetadataFilter(key="doc_type", value=doc_type)]
    )


def rrf_fuse(
    result_lists: Iterable[Sequence[NodeWithScore]],
    top_k: int = 5,
    k: int = RRF_K,
    weights: Sequence[float] | None = None,
) -> list[NodeWithScore]:
    """Fuse ranked result lists without comparing their raw score scales."""
    lists = [list(results) for results in result_lists]
    if not lists:
        return []
    if weights is None:
        resolved_weights = [1.0] * len(lists)
    else:
        resolved_weights = [float(weight) for weight in weights]
        if len(resolved_weights) != len(lists):
            raise ValueError("There must be exactly one weight per result list.")
        if any(weight < 0 for weight in resolved_weights):
            raise ValueError("RRF weights must be non-negative.")
        if not any(weight > 0 for weight in resolved_weights):
            raise ValueError("At least one RRF weight must be greater than zero.")

    fused_scores: dict[str, float] = {}
    nodes: dict[str, object] = {}
    for weight, results in zip(resolved_weights, lists, strict=True):
        for rank, result in enumerate(results, 1):
            node_id = result.node.node_id
            fused_scores[node_id] = fused_scores.get(node_id, 0.0) + weight / (k + rank)
            nodes.setdefault(node_id, result.node)

    ranked = sorted(fused_scores.items(), key=lambda item: (-item[1], item[0]))
    return [
        NodeWithScore(node=nodes[node_id], score=score)
        for node_id, score in ranked[:top_k]
    ]


def hybrid_retrieve(
    index,
    bm25: BM25Index,
    query: str,
    top_k: int = 5,
    candidate_k: int = 20,
    weights: Sequence[float] | None = None,
    doc_type: str | None = None,
) -> list[NodeWithScore]:
    """Retrieve and fuse one vector list and one BM25 list."""
    filters = metadata_filters(doc_type)
    vector_hits = index.as_retriever(
        similarity_top_k=candidate_k,
        filters=filters,
    ).retrieve(query)
    bm25_hits = bm25.retrieve(query, candidate_k, doc_type=doc_type)
    return rrf_fuse(
        [vector_hits, bm25_hits],
        top_k=top_k,
        weights=weights,
    )
