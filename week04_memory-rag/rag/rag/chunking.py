"""Split loaded documents into shared nodes for vector and BM25 indexes."""

from __future__ import annotations

import re
from collections.abc import Sequence

from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter
from llama_index.core.schema import TextNode


def _heading_for(text: str, header_path: str) -> str:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    heading_match = re.match(r"^#{1,6}\s+(.*)$", first_line)
    own_heading = heading_match.group(1).strip() if heading_match else ""
    parents = header_path.strip("/").replace("/", " > ")
    if parents and own_heading:
        return f"{parents} > {own_heading}"
    return own_heading or parents or "(正文)"


def _decorate_node(node: TextNode, document) -> TextNode:
    header_path = str(node.metadata.get("header_path", "") or "")
    node.metadata = {
        **document.metadata,
        "heading": _heading_for(node.text, header_path),
        "header_path": header_path,
    }
    return node


def chunk_documents(
    documents: Sequence,
    chunk_size: int = 384,
    chunk_overlap: int = 0,
) -> list[TextNode]:
    """Create stable, metadata-rich nodes; code files remain whole."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")

    markdown_parser = MarkdownNodeParser()
    sentence_splitter = SentenceSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    nodes: list[TextNode] = []

    for document in documents:
        if document.metadata.get("doc_type") == "code":
            nodes.append(
                TextNode(
                    text=document.text,
                    metadata={
                        **document.metadata,
                        "heading": "(整文件)",
                        "header_path": "",
                    },
                )
            )
            continue

        parsed_nodes = markdown_parser.get_nodes_from_documents([document])
        for parsed_node in parsed_nodes or [document]:
            split_nodes = sentence_splitter.get_nodes_from_documents([parsed_node])
            for node in split_nodes or [parsed_node]:
                if node.text.strip():
                    nodes.append(_decorate_node(node, document))

    return nodes
