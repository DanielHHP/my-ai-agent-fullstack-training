"""LlamaIndex SimpleVectorStore persistence helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from llama_index.core import Settings, StorageContext, VectorStoreIndex, load_index_from_storage
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.vector_stores import SimpleVectorStore

from .errors import IndexError


def build_vector_index(
    nodes: list,
    store_dir: Path,
    embed_model: BaseEmbedding,
) -> VectorStoreIndex:
    """Embed nodes and persist a SimpleVectorStore index to store_dir."""
    Settings.embed_model = embed_model
    vector_store = SimpleVectorStore()
    index = VectorStoreIndex(
        nodes,
        storage_context=StorageContext.from_defaults(vector_store=vector_store),
        embed_model=embed_model,
        show_progress=True,
    )
    store_dir.mkdir(parents=True, exist_ok=True)
    index.storage_context.persist(persist_dir=str(store_dir))
    return index


def load_vector_index(
    store_dir: Path,
    embed_model: BaseEmbedding | None = None,
) -> VectorStoreIndex:
    """Load a persisted vector index without re-embedding source nodes."""
    if not (store_dir / "index_store.json").is_file():
        raise IndexError(
            f"Vector index files are missing under {store_dir}. Rebuild the index."
        )
    if embed_model is not None:
        Settings.embed_model = embed_model
    try:
        context = StorageContext.from_defaults(persist_dir=str(store_dir))
        return load_index_from_storage(context, embed_model=embed_model)
    except Exception as exc:
        raise IndexError(f"Vector index cannot be loaded from {store_dir}") from exc


def vector_metadata() -> dict[str, Any]:
    from .config import DASHSCOPE_EMBEDDING_BATCH_SIZE, DASHSCOPE_EMBEDDING_MODEL

    return {
        "provider": "dashscope",
        "model": DASHSCOPE_EMBEDDING_MODEL,
        "batch_size": DASHSCOPE_EMBEDDING_BATCH_SIZE,
    }
