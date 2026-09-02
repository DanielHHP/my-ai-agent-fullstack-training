from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import GatewayConfig
from app.core.errors import GatewayError
from app.schemas import (
    PromptCreate,
    PromptReference,
    StructuredOutputSpec,
    UnifiedMessage,
    UnifiedRequest,
)
from app.services.gateway import GatewayService
from app.services.prompts import PromptRepository
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient
from app.services.usage import UsageEvent, UsageRepository


def _config(database_url: str, *, structured_output_retries: int = 1) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "database_url": database_url,
            "structured_output_retries": structured_output_retries,
            "retry": {
                "max_retries_per_route": 0,
                "base_delay_seconds": 0,
                "max_delay_seconds": 0,
                "retry_statuses": [500],
            },
            "pricing": {
                "gpt-primary": {
                    "input_per_million": 1.0,
                    "output_per_million": 2.0,
                    "cached_input_per_million": 0.5,
                }
            },
            "providers": {
                "openai": {
                    "base_url": "https://openai.test",
                    "api_key": "openai-key",
                }
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "gpt-primary",
                            "api": "chat",
                        }
                    ],
                }
            },
        }
    )


def _chat_request(**overrides: object) -> UnifiedRequest:
    data: dict[str, object] = {
        "model": "smart",
        "protocol": "chat_completions",
        "messages": [UnifiedMessage(role="user", content="hello")],
        "stream": False,
    }
    data.update(overrides)
    return UnifiedRequest.model_validate(data)


def _chat_response(
    content: str,
    *,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cached_tokens: int = 20,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl_usage",
        "object": "chat.completion",
        "model": "gpt-primary",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        },
    }


@pytest.mark.asyncio
async def test_usage_repository_round_trip_and_cost(tmp_path) -> None:
    config = _config(str(tmp_path / "usage.db"))
    repo = UsageRepository(str(tmp_path / "usage.db"), config)
    await repo.initialize()

    assert repo.calculate_cost("gpt-primary", 1000, 500, 200) == 0.0019

    await repo.record(
        UsageEvent(
            request_id="req_1",
            api_key_hash="abc123",
            protocol="chat_completions",
            endpoint="/v1/chat/completions",
            requested_model="smart",
            provider="openai",
            upstream_model="gpt-primary",
            status="success",
            status_code=200,
            input_tokens=100,
            output_tokens=50,
            cached_tokens=20,
            cost_usd=0.00017,
            latency_ms=12.5,
            first_token_ms=3.2,
            retries=1,
            fallbacks=0,
            repair_retries=0,
            metadata={"usage": {"prompt_tokens": 100}},
        )
    )

    rows = await repo.recent()
    assert len(rows) == 1
    assert rows[0]["request_id"] == "req_1"
    assert rows[0]["api_key_hash"] == "abc123"
    assert rows[0]["status"] == "success"
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["metadata"] == {"usage": {"prompt_tokens": 100}}


@pytest.mark.asyncio
async def test_gateway_records_success_usage_and_prompt_metadata(tmp_path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = _config(database_path)
    repo = UsageRepository(database_path, config)
    prompts = PromptRepository(database_path)
    await repo.initialize()
    await prompts.initialize()
    await prompts.create_version(
        PromptCreate(
            id="reviewer",
            name="Reviewer",
            content="Review {{language}} code.",
            role="system",
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            json.loads(request.content)["messages"][0]["content"]
            == "Review Python code."
        )
        return httpx.Response(200, json=_chat_response("ok"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
            prompts,
            repo,
        )
        result = await service.complete(
            _chat_request(
                prompt_ref=PromptReference(
                    id="reviewer",
                    variables={"language": "Python"},
                )
            ),
            api_key_hash="caller-hash",
        )

        assert result.metrics.input_tokens == 100
        assert result.metrics.output_tokens == 50
        assert result.metrics.cached_tokens == 20
        assert result.metrics.cost_usd > 0

        rows = await repo.recent()
        assert len(rows) == 1
        row = rows[0]
        assert row["api_key_hash"] == "caller-hash"
        assert row["requested_model"] == "smart"
        assert row["upstream_model"] == "gpt-primary"
        assert row["prompt_id"] == "reviewer"
        assert row["prompt_version"] == 1
        assert row["status"] == "success"
        assert row["input_tokens"] == 100
        assert row["output_tokens"] == 50
        assert row["cached_tokens"] == 20
        assert row["metadata"]["usage"]["completion_tokens"] == 50
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_records_structured_repair_usage(tmp_path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = _config(database_path)
    repo = UsageRepository(database_path, config)
    await repo.initialize()
    responses: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if len(responses) == 0:
            responses.append({})
            return httpx.Response(
                200,
                json=_chat_response(
                    "not-json",
                    prompt_tokens=10,
                    completion_tokens=5,
                    cached_tokens=1,
                ),
            )
        responses.append({})
        return httpx.Response(
            200,
            json=_chat_response(
                '{"name":"Alice"}',
                prompt_tokens=20,
                completion_tokens=7,
                cached_tokens=2,
            ),
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
            usage=repo,
        )
        request = _chat_request(
            response_format=StructuredOutputSpec(
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                name="person",
            )
        )

        result = await service.complete(request)

        assert result.metrics.repair_retries == 1
        assert result.metrics.retry_input_tokens == 10
        assert result.metrics.retry_output_tokens == 5
        assert result.metrics.retry_cached_tokens == 1
        assert result.metrics.retry_cost_usd > 0
        assert result.metrics.input_tokens == 20
        assert result.metrics.output_tokens == 7

        rows = await repo.recent()
        assert len(rows) == 1
        assert rows[0]["repair_retries"] == 1
        assert rows[0]["retry_input_tokens"] == 10
        assert rows[0]["retry_output_tokens"] == 5
        assert rows[0]["input_tokens"] == 20
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_records_error_usage_when_all_routes_fail(tmp_path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = _config(database_path)
    repo = UsageRepository(database_path, config)
    await repo.initialize()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "down"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
            usage=repo,
        )

        with pytest.raises(GatewayError):
            await service.complete(_chat_request())

        rows = await repo.recent()
        assert len(rows) == 1
        assert rows[0]["status"] == "error"
        assert rows[0]["status_code"] == 502
        assert rows[0]["error_type"] == "upstream_error"
        assert "down" in rows[0]["error_message"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_records_stream_usage_and_ttft(tmp_path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = _config(database_path)
    repo = UsageRepository(database_path, config)
    await repo.initialize()
    body = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,'
        b'"prompt_tokens_details":{"cached_tokens":2}}}\n\n'
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
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
            usage=repo,
        )

        result = await service.stream(
            _chat_request(stream=True),
            api_key_hash="stream-caller",
        )
        chunks = [chunk async for chunk in result.stream]

        assert b"Hello" in b"".join(chunks)
        assert result.metrics.first_token_ms is not None
        assert result.metrics.stream is True
        assert result.metrics.status == "success"

        rows = await repo.recent()
        assert len(rows) == 1
        assert rows[0]["stream"] == 1
        assert rows[0]["api_key_hash"] == "stream-caller"
        assert rows[0]["input_tokens"] == 8
        assert rows[0]["output_tokens"] == 3
        assert rows[0]["cached_tokens"] == 2
        assert rows[0]["first_token_ms"] is not None
    finally:
        await client.aclose()
