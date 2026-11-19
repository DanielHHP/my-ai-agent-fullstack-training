"""BM25 keyword indexing and JSON-backed node persistence."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Sequence
from pathlib import Path

import jieba
from llama_index.core.schema import NodeWithScore, TextNode
from rank_bm25 import BM25Okapi

from .errors import IndexError

jieba.setLogLevel(60)


def tokenize(text: str) -> list[str]:
    """Tokenize Chinese while also preserving complete code identifiers/operators."""
    folded = text.casefold()
    tokens = [
        token.casefold()
        for token in jieba.lcut(folded)
        if token.strip() and not token.isspace()
    ]
    code_tokens = re.findall(r"[a-z_][a-z0-9_./:-]*", folded)
    code_tokens.extend(re.findall(r"==|!=|<=|>=|<|>", folded))
    for token in code_tokens:
        if token not in tokens:
            tokens.append(token)
    return tokens


class BM25Index:
    """In-memory BM25 index over already-chunked nodes."""

    def __init__(self, nodes: Sequence[TextNode]) -> None:
        self.nodes = list(nodes)
        tokenized_corpus = [tokenize(node.text) for node in self.nodes]
        if not self.nodes or not any(tokenized_corpus):
            raise IndexError("BM25 index requires at least one non-empty node")
        self._bm25 = BM25Okapi(tokenized_corpus)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        doc_type: str | None = None,
    ) -> list[NodeWithScore]:
        tokens = tokenize(query)
        if not tokens or top_k <= 0:
            return []

        scores = self._bm25.get_scores(tokens)
        candidates = []
        for index, raw_score in enumerate(scores):
            node = self.nodes[index]
            if doc_type and node.metadata.get("doc_type") != doc_type:
                continue
            score = float(raw_score)
            if score > 0:
                candidates.append((score, index))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [
            NodeWithScore(node=self.nodes[index], score=score)
            for score, index in candidates[:top_k]
        ]


def save_nodes(nodes: Sequence[TextNode], path: Path) -> None:
    """Atomically persist BM25 source nodes as UTF-8 JSON."""
    payload = {
        "schema_version": 1,
        "node_count": len(nodes),
        "nodes": [node.to_dict() for node in nodes],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def load_nodes(path: Path) -> list[TextNode]:
    """Load persisted BM25 nodes and validate their basic schema."""
    if not path.is_file():
        raise IndexError(f"BM25 index file is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_nodes = payload["nodes"]
        nodes = [TextNode.from_dict(item) for item in raw_nodes]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IndexError(f"BM25 index is unreadable: {path}") from exc
    if not nodes:
        raise IndexError(f"BM25 index contains no nodes: {path}")
    return nodes
