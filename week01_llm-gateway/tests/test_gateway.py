from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import GatewayConfig
from app.core.errors import GatewayError
from app.schemas import UnifiedMessage, UnifiedRequest
from app.services.gateway import GatewayService
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient


def _config(*, retries: int = 2) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "retry": {
                "max_retries_per_route": retries,
                "base_delay_seconds": 0,
                "max_delay_seconds": 0,
                "retry_statuses": [500],
            },
            "providers": {
                "openai": {
                    "base_url": "https://primary.test",
                    "api_key": "primary-key",
                },
                "openai-backup": {
                    "base_url": "https://backup.test",
                    "api_key": "backup-key",
                },
                "anthropic": {
                    "base_url": "https://anthropic.test",
                    "api_key": "anthropic-key",
                },
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "gpt-primary",
                            "api": "chat,responses",
                        },
                        {
                            "provider": "openai-backup",
                            "model": "gpt-backup",
                            "api": "chat,responses",
                        },
                    ],
                },
                "claude-fast": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "anthropic",
                            "model": "claude-sonnet-4-5",
                            "api": "messages",
                        }
                    ],
                },
            },
        }
    )


def _chat_request(*, stream: bool = False) -> UnifiedRequest:
    return UnifiedRequest(
        model="smart",
        protocol="chat_completions",
        messages=[UnifiedMessage(role="user", content="hello")],
        stream=stream,
    )


def _anthropic_request(*, stream: bool = False) -> UnifiedRequest:
    return UnifiedRequest(
        model="claude-fast",
        protocol="anthropic_messages",
        messages=[UnifiedMessage(role="user", content="hello")],
        max_tokens=32,
        stream=stream,
    )


def _chat_response(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "model": "gpt-primary",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }


@pytest.mark.asyncio
async def test_complete_routes_to_adapter_and_returns_raw_response() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response("ok"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        result = await service.complete(_chat_request())

        assert captured["url"] == "https://primary.test/v1/chat/completions"
        assert captured["body"]["model"] == "gpt-primary"
        assert result.raw["choices"][0]["message"]["content"] == "ok"
        assert result.metrics.retries == 0
        assert result.metrics.fallbacks == 0
        assert result.metrics.status == "success"
        assert result.metrics.provider == "openai"
        assert result.metrics.upstream_model == "gpt-primary"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_retries_then_falls_back_to_next_route() -> None:
    counts: dict[str, int] = {"primary": 0, "backup": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "primary.test" in str(request.url):
            counts["primary"] += 1
            return httpx.Response(
                500,
                json={"error": {"message": "primary unavailable"}},
            )
        counts["backup"] += 1
        return httpx.Response(200, json=_chat_response("recovered"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config(retries=2)
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        result = await service.complete(_chat_request())

        assert counts == {"primary": 3, "backup": 1}
        assert result.raw["choices"][0]["message"]["content"] == "recovered"
        assert result.metrics.retries == 2
        assert result.metrics.fallbacks == 1
        assert result.metrics.provider == "openai-backup"
        assert result.metrics.upstream_model == "gpt-backup"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_non_retryable_4xx_still_falls_back() -> None:
    counts: dict[str, int] = {"primary": 0, "backup": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "primary.test" in str(request.url):
            counts["primary"] += 1
            return httpx.Response(400, json={"error": {"message": "bad request"}})
        counts["backup"] += 1
        return httpx.Response(200, json=_chat_response("fallback ok"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        result = await service.complete(_chat_request())

        assert counts == {"primary": 1, "backup": 1}
        assert result.metrics.retries == 0
        assert result.metrics.fallbacks == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_raises_unified_error_when_all_routes_fail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "down"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config(retries=0)
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        with pytest.raises(GatewayError) as exc_info:
            await service.complete(_chat_request())

        assert exc_info.value.status_code == 502
        assert exc_info.value.error_type == "upstream_error"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_forwards_sse_and_records_first_token() -> None:
    body = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":" world"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        stream_result = await service.stream(_chat_request(stream=True))
        chunks = [chunk async for chunk in stream_result.stream]
        joined = b"".join(chunks)

        assert b"Hello" in joined
        assert b" world" in joined
        assert stream_result.metrics.first_token_ms is not None
        assert stream_result.metrics.status == "success"
        assert stream_result.metrics.status_code == 200
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_retries_before_first_byte_and_falls_back() -> None:
    counts: dict[str, int] = {"primary": 0, "backup": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "primary.test" in str(request.url):
            counts["primary"] += 1
            return httpx.Response(500, json={"error": {"message": "primary down"}})
        counts["backup"] += 1
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"content":"recovered"}}]}\n\n'
            b"data: [DONE]\n\n",
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config(retries=1)
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        stream_result = await service.stream(_chat_request(stream=True))
        chunks = [chunk async for chunk in stream_result.stream]

        assert counts == {"primary": 2, "backup": 1}
        assert b"recovered" in b"".join(chunks)
        assert stream_result.metrics.retries == 1
        assert stream_result.metrics.fallbacks == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_cancels_upstream_on_client_disconnect() -> None:
    body = (
        b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "text/event-stream"},
        )

    async def is_disconnected() -> bool:
        return True

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        stream_result = await service.stream(
            _chat_request(stream=True),
            is_disconnected=is_disconnected,
        )
        chunks = [chunk async for chunk in stream_result.stream]

        assert chunks == []
        assert stream_result.metrics.status == "cancelled"
        assert stream_result.metrics.status_code == 499
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_supports_anthropic_native_events() -> None:
    body = (
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n\n'
        b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}\n\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":3}}\n\n'
        b'data: {"type":"message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "text/event-stream"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )

        stream_result = await service.stream(_anthropic_request(stream=True))
        chunks = [chunk async for chunk in stream_result.stream]

        assert b"hi" in b"".join(chunks)
        assert stream_result.metrics.first_token_ms is not None
        assert stream_result.metrics.input_tokens == 5
        assert stream_result.metrics.output_tokens == 3
    finally:
        await client.aclose()
