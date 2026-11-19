"""Command-line interface for index building and retrieval."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import (
    DEFAULT_CANDIDATE_K,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CORPUS,
    DEFAULT_STORE,
    DEFAULT_TOP_K,
)
from .docload import DOC_TYPES
from .errors import RagError
from .indexer import build_index
from .search import parse_weights, search


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag",
        description="Build and search BM25, DashScope vector, or hybrid RAG indexes.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build",
        help="Parse corpus files and build indexes.",
    )
    build_parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    build_parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    build_parser.add_argument("--backend", choices=["all", "bm25", "vector"], default="all")
    build_parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    build_parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=DEFAULT_CHUNK_OVERLAP,
    )
    build_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace a non-empty store directory after a successful build.",
    )
    build_parser.add_argument("--json", action="store_true", dest="as_json")

    search_parser = subparsers.add_parser(
        "search",
        aliases=["query"],
        help="Retrieve evidence from an existing index.",
    )
    search_parser.add_argument("question")
    search_parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    search_parser.add_argument(
        "--mode",
        choices=["bm25", "vector", "hybrid"],
        default="hybrid",
    )
    search_parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    search_parser.add_argument(
        "--candidate-k",
        type=int,
        default=DEFAULT_CANDIDATE_K,
        help="Candidates retrieved separately for vector and BM25 in hybrid mode.",
    )
    search_parser.add_argument(
        "--weights",
        help="Hybrid RRF weights: vector,bm25 (for example 0.7,0.3).",
    )
    search_parser.add_argument("--doc-type", choices=DOC_TYPES)
    search_parser.add_argument("--snippet-chars", type=int, default=300)
    search_parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _print_build(summary, as_json: bool) -> None:
    payload = summary.to_dict()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return
    print(
        f"Built {payload['node_count']} nodes from "
        f"{payload['document_count']} documents."
    )
    print(f"Backends: {', '.join(payload['backends'])}")
    print(f"Store: {payload['store']}")


def _print_search(response, snippet_chars: int, as_json: bool) -> None:
    if as_json:
        print(json.dumps(response.to_dict(snippet_chars), ensure_ascii=False))
        return

    print(
        f"Query: {response.question} | mode={response.mode} | "
        f"results={response.result_count}"
    )
    if not response.results:
        print("No matching evidence.")
        return
    for hit in response.results:
        print(
            f"[{hit.rank}] {hit.file} § {hit.heading} "
            f"score={hit.score:.6f} ({hit.score_type})"
        )
        snippet = hit.text[:snippet_chars].replace("\n", " ")
        print(f"    {snippet}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            if args.chunk_size <= 0:
                parser.error("--chunk-size must be greater than zero.")
            if args.chunk_overlap < 0 or args.chunk_overlap >= args.chunk_size:
                parser.error("--chunk-overlap must satisfy 0 <= overlap < chunk-size.")
            summary = build_index(
                corpus=args.corpus,
                store_dir=args.store,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                backend=args.backend,
                overwrite=args.overwrite,
            )
            _print_build(summary, args.as_json)
            return 0

        if args.snippet_chars <= 0:
            parser.error("--snippet-chars must be greater than zero.")
        response = search(
            store_dir=args.store,
            question=args.question,
            mode=args.mode,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            weights=parse_weights(args.weights),
            doc_type=args.doc_type,
        )
        _print_search(response, args.snippet_chars, args.as_json)
        return 0
    except RagError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
