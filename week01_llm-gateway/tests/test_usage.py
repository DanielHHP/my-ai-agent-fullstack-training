from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
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


@pytest.mark.asyncio
async def test_gateway_records_anthropic_stream_raw_usage_metadata() -> None:
    config = GatewayConfig.model_validate(
        {
            "retry": {
                "max_retries_per_route": 0,
                "base_delay_seconds": 0,
                "max_delay_seconds": 0,
            },
            "providers": {
                "anthropic": {
                    "base_url": "https://anthropic.test",
                    "api_key": "anthropic-key",
                }
            },
            "models": {
                "claude-fast": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "anthropic",
                            "model": "claude-sonnet-4-5",
                            "api": "messages",
                        }
                    ],
                }
            },
        }
    )
    body = (
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":5,'
        b'"cache_read_input_tokens":2}}}\n\n'
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
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )
        request = UnifiedRequest(
            model="claude-fast",
            protocol="anthropic_messages",
            messages=[UnifiedMessage(role="user", content="hi")],
            max_tokens=32,
            stream=True,
        )

        result = await service.stream(request)
        chunks = [chunk async for chunk in result.stream]

        assert chunks
        assert result.metrics.metadata["usage"] == {
            "input_tokens": 5,
            "cache_read_input_tokens": 2,
            "output_tokens": 3,
        }
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_records_responses_stream_raw_usage_metadata() -> None:
    config = GatewayConfig.model_validate(
        {
            "retry": {
                "max_retries_per_route": 0,
                "base_delay_seconds": 0,
                "max_delay_seconds": 0,
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
                            "api": "responses",
                        }
                    ],
                }
            },
        }
    )
    body = (
        b'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        b'data: {"type":"response.completed","response":{"id":"resp_1",'
        b'"usage":{"input_tokens":4,"output_tokens":2,'
        b'"input_tokens_details":{"cached_tokens":1}}}}\n\n'
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
        )
        request = UnifiedRequest(
            model="smart",
            protocol="openai_responses",
            messages=[UnifiedMessage(role="user", content="hi")],
            stream=True,
        )

        result = await service.stream(request)
        chunks = [chunk async for chunk in result.stream]

        assert chunks
        assert result.metrics.metadata["usage"] == {
            "input_tokens": 4,
            "output_tokens": 2,
            "input_tokens_details": {"cached_tokens": 1},
        }
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_stream_latency_includes_prompt_preparation() -> None:
    config = _config("/tmp/unused-stream-latency.db")

    async def fake_render(prompt_id: str, variables: dict, version: int | None = None):
        del variables, version
        await asyncio.sleep(0.03)
        return (
            SimpleNamespace(id=prompt_id, version=1, role="system"),
            "be concise",
        )

    body = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
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
            prompts=SimpleNamespace(render=fake_render),
        )
        request = _chat_request(
            stream=True,
            prompt_ref=PromptReference(id="p", variables={}),
        )

        result = await service.stream(request)
        chunks = [chunk async for chunk in result.stream]

        assert chunks
        assert result.metrics.latency_ms is not None
        assert result.metrics.latency_ms >= 20
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_structured_repair_cost_uses_each_upstream_model(tmp_path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = GatewayConfig.model_validate(
        {
            "database_url": database_path,
            "structured_output_retries": 2,
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
                    "cached_input_per_million": 1.0,
                },
                "gpt-backup": {
                    "input_per_million": 10.0,
                    "output_per_million": 20.0,
                    "cached_input_per_million": 10.0,
                },
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
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "gpt-primary",
                            "api": "chat",
                        },
                        {
                            "provider": "openai-backup",
                            "model": "gpt-backup",
                            "api": "chat",
                        },
                    ],
                }
            },
        }
    )
    repo = UsageRepository(database_path, config)
    await repo.initialize()

    primary_calls = 0
    backup_calls = 0

    def response(content: str, model: str, prompt: int, completion: int, cached: int) -> dict:
        return {
            "id": "chatcmpl_usage",
            "object": "chat.completion",
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "prompt_tokens_details": {"cached_tokens": cached},
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal primary_calls, backup_calls
        if "primary.test" in str(request.url):
            primary_calls += 1
            if primary_calls == 1:
                return httpx.Response(
                    200,
                    json=response(
                        "not-json",
                        "gpt-primary",
                        prompt=10,
                        completion=5,
                        cached=1,
                    ),
                )
            if primary_calls == 2:
                return httpx.Response(
                    500,
                    json={"error": {"message": "primary down"}},
                )
            return httpx.Response(
                200,
                json=response(
                    '{"name":"Alice"}',
                    "gpt-primary",
                    prompt=30,
                    completion=8,
                    cached=3,
                ),
            )

        backup_calls += 1
        return httpx.Response(
            200,
            json=response(
                "not-json",
                "gpt-backup",
                prompt=20,
                completion=7,
                cached=2,
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

        assert primary_calls == 3
        assert backup_calls == 1
        assert result.metrics.repair_retries == 2
        assert result.metrics.retry_input_tokens == 30
        assert result.metrics.retry_output_tokens == 12
        assert result.metrics.retry_cached_tokens == 3
        assert result.metrics.retry_cost_usd == pytest.approx(0.00036)
        assert result.metrics.fallbacks == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_gateway_records_attempted_routes_when_all_routes_fail(tmp_path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = GatewayConfig.model_validate(
        {
            "database_url": database_path,
            "retry": {
                "max_retries_per_route": 0,
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
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "gpt-primary",
                            "api": "chat",
                        },
                        {
                            "provider": "openai-backup",
                            "model": "gpt-backup",
                            "api": "chat",
                        },
                    ],
                }
            },
        }
    )
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
        assert rows[0]["fallbacks"] == 1
        assert rows[0]["metadata"]["attempted_routes"] == [
            {"provider": "openai", "upstream_model": "gpt-primary"},
            {"provider": "openai-backup", "upstream_model": "gpt-backup"},
        ]
    finally:
        await client.aclose()
