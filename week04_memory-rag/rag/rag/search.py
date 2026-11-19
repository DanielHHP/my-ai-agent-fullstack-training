"""Unified BM25, vector, and hybrid retrieval orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from llama_index.core.schema import NodeWithScore

from .bm25 import BM25Index, load_nodes
from .config import DEFAULT_CANDIDATE_K, DEFAULT_TOP_K
from .embedding import create_embedding_model
from .errors import ConfigurationError
from .hybrid import hybrid_retrieve, metadata_filters
from .storage import BM25_NODES_FILE, load_manifest, validate_backend
from .vector_store import load_vector_index

Mode = Literal["bm25", "vector", "hybrid"]


@dataclass(frozen=True)
class SearchHit:
    rank: int
    node_id: str
    file: str
    doc_type: str
    heading: str
    score: float
    score_type: str
    text: str

    def to_dict(self, snippet_chars: int = 300) -> dict:
        payload = asdict(self)
        payload["score"] = round(self.score, 6)
        payload["snippet"] = self.text[:snippet_chars]
        payload.pop("text")
        return payload


@dataclass(frozen=True)
class SearchResponse:
    question: str
    mode: str
    top_k: int
    candidate_k: int
    result_count: int
    results: list[SearchHit]

    def to_dict(self, snippet_chars: int = 300) -> dict:
        return {
            "question": self.question,
            "mode": self.mode,
            "top_k": self.top_k,
            "candidate_k": self.candidate_k,
            "result_count": self.result_count,
            "results": [
                hit.to_dict(snippet_chars=snippet_chars) for hit in self.results
            ],
        }


def parse_weights(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    try:
        parts = tuple(float(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ConfigurationError("--weights must look like `0.7,0.3`.") from exc
    if len(parts) != 2:
        raise ConfigurationError("--weights must contain exactly two values.")
    if any(weight < 0 for weight in parts) or not any(weight > 0 for weight in parts):
        raise ConfigurationError(
            "--weights must be non-negative and at least one value must be positive."
        )
    return parts[0], parts[1]


def _to_hits(nodes: list[NodeWithScore], score_type: str) -> list[SearchHit]:
    hits = []
    for rank, item in enumerate(nodes, 1):
        node = item.node
        hits.append(
            SearchHit(
                rank=rank,
                node_id=node.node_id,
                file=str(node.metadata.get("file_path", "?")),
                doc_type=str(node.metadata.get("doc_type", "?")),
                heading=str(node.metadata.get("heading", "(正文)")),
                score=float(item.score or 0.0),
                score_type=score_type,
                text=node.text.strip(),
            )
        )
    return hits


def search(
    store_dir: Path,
    question: str,
    mode: Mode = "hybrid",
    top_k: int = DEFAULT_TOP_K,
    candidate_k: int = DEFAULT_CANDIDATE_K,
    weights: tuple[float, float] | None = None,
    doc_type: str | None = None,
) -> SearchResponse:
    """Run one retrieval mode against an existing persisted store."""
    if not question.strip():
        raise ConfigurationError("Query must not be empty.")
    if mode not in {"bm25", "vector", "hybrid"}:
        raise ConfigurationError(f"Unknown search mode: {mode}")
    if top_k <= 0:
        raise ConfigurationError("--top-k must be greater than zero.")
    if candidate_k <= 0:
        raise ConfigurationError("--candidate-k must be greater than zero.")
    if mode == "hybrid" and weights is None:
        weights = (1.0, 1.0)
    if mode != "hybrid" and weights is not None:
        raise ConfigurationError("--weights can only be used with --mode hybrid.")

    store_dir = store_dir.expanduser().resolve()
    manifest = load_manifest(store_dir)

    if mode == "bm25":
        validate_backend(store_dir, manifest, "bm25")
        bm25 = BM25Index(load_nodes(store_dir / BM25_NODES_FILE))
        nodes = bm25.retrieve(question, top_k=top_k, doc_type=doc_type)
        return SearchResponse(
            question=question,
            mode=mode,
            top_k=top_k,
            candidate_k=top_k,
            result_count=len(nodes),
            results=_to_hits(nodes, "bm25"),
        )

    validate_backend(store_dir, manifest, "vector")
    index = load_vector_index(store_dir, create_embedding_model())
    if mode == "vector":
        nodes = index.as_retriever(
            similarity_top_k=top_k,
            filters=metadata_filters(doc_type),
        ).retrieve(question)
        return SearchResponse(
            question=question,
            mode=mode,
            top_k=top_k,
            candidate_k=top_k,
            result_count=len(nodes),
            results=_to_hits(nodes, "vector_similarity"),
        )

    validate_backend(store_dir, manifest, "bm25")
    bm25 = BM25Index(load_nodes(store_dir / BM25_NODES_FILE))
    nodes = hybrid_retrieve(
        index=index,
        bm25=bm25,
        query=question,
        top_k=top_k,
        candidate_k=max(candidate_k, top_k),
        weights=weights,
        doc_type=doc_type,
    )
    return SearchResponse(
        question=question,
        mode=mode,
        top_k=top_k,
        candidate_k=max(candidate_k, top_k),
        result_count=len(nodes),
        results=_to_hits(nodes, "rrf"),
    )
