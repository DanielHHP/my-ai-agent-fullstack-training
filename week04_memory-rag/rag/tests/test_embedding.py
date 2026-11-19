from __future__ import annotations

from http import HTTPStatus

import dashscope

from rag.embedding import create_embedding_model


class _Response:
    status_code = HTTPStatus.OK

    def __init__(self, texts: list[str]) -> None:
        self.output = {
            "embeddings": [
                {"text_index": index, "embedding": [float(index), 1.0]}
                for index, _ in enumerate(texts)
            ]
        }


def test_dashscope_adapter_uses_query_and_document_types(monkeypatch) -> None:
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        return _Response(kwargs["input"])

    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(dashscope.TextEmbedding, "call", fake_call)
    embedding = create_embedding_model()

    assert embedding.get_query_embedding("query") == [0.0, 1.0]
    assert embedding.get_text_embedding("document") == [0.0, 1.0]

    assert calls[0]["text_type"] == "query"
    assert calls[1]["text_type"] == "document"
    assert calls[0]["api_key"] == "test-key"
