from __future__ import annotations

from rag.chunking import chunk_documents
from rag.config import DEFAULT_CORPUS
from rag.docload import load_all


def test_test_corpus_loads_all_supported_formats() -> None:
    documents = load_all(DEFAULT_CORPUS)
    doc_types = {document.metadata["doc_type"] for document in documents}

    assert len(documents) >= 10
    assert doc_types == {"doc", "code", "web", "table", "pdf"}


def test_chunking_preserves_source_metadata() -> None:
    documents = load_all(DEFAULT_CORPUS)
    nodes = chunk_documents(documents, chunk_size=128, chunk_overlap=16)

    assert nodes
    assert all(node.metadata.get("file_path") for node in nodes)
    assert all(node.metadata.get("heading") for node in nodes)
