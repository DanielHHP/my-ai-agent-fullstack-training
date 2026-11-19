"""DashScope embedding construction for LlamaIndex."""

from __future__ import annotations

from http import HTTPStatus

from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.embeddings.dashscope import DashScopeEmbedding

from .config import (
    DASHSCOPE_EMBEDDING_BATCH_SIZE,
    DASHSCOPE_EMBEDDING_MODEL,
    dashscope_api_key,
)


class DashScopeEmbeddingAdapter(DashScopeEmbedding):
    """Compatibility layer around the official LlamaIndex adapter.

    The 0.5.0 integration passes DashScope request attributes under a nested
    ``kwargs`` argument. Current dashscope SDKs expect those attributes directly,
    so this subclass sends correctly typed query/document requests and surfaces
    failed responses instead of returning empty vectors.
    """

    def _request(self, texts: list[str], text_type: str) -> list[list[float]]:
        import dashscope

        response = dashscope.TextEmbedding.call(
            model=self.model_name,
            input=texts,
            api_key=self._api_key,
            text_type=text_type,
        )
        if response.status_code != HTTPStatus.OK:
            message = getattr(response, "message", "unknown error")
            raise RuntimeError(
                f"DashScope embedding request failed "
                f"({response.status_code}): {message}"
            )

        raw_embeddings = response.output.get("embeddings", [])
        embeddings: list[list[float] | None] = [None] * len(texts)
        for item in raw_embeddings:
            embeddings[item["text_index"]] = [
                float(value) for value in item["embedding"]
            ]
        if any(embedding is None for embedding in embeddings):
            raise RuntimeError("DashScope returned an incomplete embedding response.")
        return [embedding for embedding in embeddings if embedding is not None]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._request([query], "query")[0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._request([text], "document")[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._request(texts, "document")

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)


def create_embedding_model() -> BaseEmbedding:
    """Create the official LlamaIndex DashScope embedding adapter."""
    return DashScopeEmbeddingAdapter(
        model_name=DASHSCOPE_EMBEDDING_MODEL,
        text_type="document",
        api_key=dashscope_api_key(),
        embed_batch_size=DASHSCOPE_EMBEDDING_BATCH_SIZE,
    )
