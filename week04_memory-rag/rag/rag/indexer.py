"""Build all requested indexes and safely replace the target store."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .bm25 import save_nodes
from .chunking import chunk_documents
from .config import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
)
from .docload import load_all
from .embedding import create_embedding_model
from .errors import ConfigurationError, RagError
from .storage import (
    BM25_NODES_FILE,
    SCHEMA_VERSION,
    write_manifest,
)
from .vector_store import build_vector_index, vector_metadata

Backend = Literal["all", "bm25", "vector"]


@dataclass(frozen=True)
class BuildSummary:
    corpus: str
    store: str
    document_count: int
    node_count: int
    backends: list[str]
    chunk_size: int
    chunk_overlap: int

    def to_dict(self) -> dict:
        return asdict(self)


def _prepare_target(store_dir: Path, overwrite: bool) -> None:
    if store_dir.exists() and not store_dir.is_dir():
        raise ConfigurationError(f"Store path is not a directory: {store_dir}")
    if store_dir.is_dir() and any(store_dir.iterdir()) and not overwrite:
        raise ConfigurationError(
            f"Store directory is not empty: {store_dir}. "
            "Re-run with --overwrite to rebuild it."
        )
    store_dir.parent.mkdir(parents=True, exist_ok=True)


def _replace_store(staging_dir: Path, store_dir: Path) -> None:
    backup_dir: Path | None = None
    if store_dir.exists():
        backup_dir = Path(
            tempfile.mkdtemp(
                prefix=f".{store_dir.name}-backup-",
                dir=store_dir.parent,
            )
        )
        backup_dir.rmdir()
        store_dir.replace(backup_dir)
    try:
        staging_dir.replace(store_dir)
    except BaseException:
        if backup_dir is not None and backup_dir.exists() and not store_dir.exists():
            backup_dir.replace(store_dir)
        raise
    else:
        if backup_dir is not None:
            shutil.rmtree(backup_dir)


def build_index(
    corpus: Path,
    store_dir: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    backend: Backend = "all",
    overwrite: bool = False,
) -> BuildSummary:
    """Build BM25 and/or vector indexes in a transactional staging directory."""
    if backend not in {"all", "bm25", "vector"}:
        raise ConfigurationError(f"Unknown backend: {backend}")

    corpus = corpus.expanduser().resolve()
    store_dir = store_dir.expanduser().resolve()
    _prepare_target(store_dir, overwrite)

    documents = load_all(corpus)
    nodes = chunk_documents(
        documents,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    if not nodes:
        raise RagError("Chunking produced no nodes; the corpus may contain only empty text.")

    enabled_backends = (
        ["bm25", "vector"] if backend == "all" else [backend]
    )
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{store_dir.name}-build-",
            dir=store_dir.parent,
        )
    )

    try:
        if "bm25" in enabled_backends:
            save_nodes(nodes, staging_dir / BM25_NODES_FILE)

        vector_details = None
        if "vector" in enabled_backends:
            embed_model = create_embedding_model()
            build_vector_index(nodes, staging_dir, embed_model)
            vector_details = vector_metadata()

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "corpus": str(corpus),
            "document_count": len(documents),
            "node_count": len(nodes),
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "backends": enabled_backends,
            "bm25": {"tokenizer": "jieba", "algorithm": "BM25Okapi"}
            if "bm25" in enabled_backends
            else None,
            "vector": vector_details,
        }
        write_manifest(staging_dir, manifest)
        _replace_store(staging_dir, store_dir)
    except BaseException:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise

    return BuildSummary(
        corpus=str(corpus),
        store=str(store_dir),
        document_count=len(documents),
        node_count=len(nodes),
        backends=enabled_backends,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
