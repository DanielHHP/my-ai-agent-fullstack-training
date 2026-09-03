from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.config import GatewayConfig
from app.core.errors import GatewayError, StructuredOutputError
from app.schemas import StructuredOutputSpec, UnifiedMessage, UnifiedRequest
from app.services.gateway import GatewayService
from app.services.router import ModelRouter
from app.services.structured import repair_instruction, validate_structured_content
from app.services.upstream import UpstreamClient


def test_validate_structured_content_accepts_markdown_fenced_json() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    parsed = validate_structured_content("```json\n{\"name\":\"Alice\"}\n```", schema)

    assert parsed == {"name": "Alice"}


def test_validate_structured_content_extracts_fenced_json_with_surrounding_text() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    parsed = validate_structured_content(
        "Here is the answer:\n```json\n{\"name\":\"Alice\"}\n```\nHope this helps.",
        schema,
    )

    assert parsed == {"name": "Alice"}


def test_validate_structured_content_rejects_invalid_json() -> None:
    schema = {"type": "object"}

    with pytest.raises(StructuredOutputError) as exc_info:
        validate_structured_content("not-json", schema)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "structured_output_error"


def test_validate_structured_content_rejects_schema_mismatch() -> None:
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    with pytest.raises(StructuredOutputError) as exc_info:
        validate_structured_content('{"age": 12}', schema)

    assert "does not match" in exc_info.value.message


def test_repair_instruction_contains_schema_and_error() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    error = StructuredOutputError("bad json", details={"message": "boom"})

    instruction = repair_instruction(error, schema)

    assert "Return only corrected JSON" in instruction
    assert "boom" in instruction
    assert "name" in instruction


def _config(*, structured_output_retries: int = 1) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "structured_output_retries": structured_output_retries,
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
                            "model": "gpt-structured",
                            "api": "chat",
                        }
                    ],
                }
            },
        }
    )


def _chat_response(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl_structured",
        "object": "chat.completion",
        "model": "gpt-structured",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 2},
    }


@pytest.mark.asyncio
async def test_complete_accepts_valid_structured_output_without_repair() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response('{"name":"Alice"}'))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )
        request = UnifiedRequest(
            model="smart",
            protocol="chat_completions",
            messages=[UnifiedMessage(role="user", content="return a person")],
            response_format=StructuredOutputSpec(
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                name="person",
            ),
        )

        result = await service.complete(request)

        assert result.metrics.repair_retries == 0
        assert result.raw["choices"][0]["message"]["content"] == '{"name":"Alice"}'
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_validates_anthropic_structured_output_end_to_end() -> None:
    config = GatewayConfig.model_validate(
        {
            "structured_output_retries": 0,
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
                            "model": "claude-acceptance",
                            "api": "messages",
                        }
                    ],
                }
            },
        }
    )
    captured_body: dict[str, Any] = {}
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-acceptance",
                "content": [{"type": "text", "text": '{"name":"Alice"}'}],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
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
            messages=[UnifiedMessage(role="user", content="return a person")],
            max_tokens=32,
            response_format=StructuredOutputSpec(schema=schema, name="person"),
        )

        result = await service.complete(request)

        system = captured_body["system"]
        assert isinstance(system, str)
        assert "Return only valid JSON" in system
        assert json.dumps(schema) in system
        assert result.metrics.repair_retries == 0
        assert result.raw["content"][0]["text"] == '{"name":"Alice"}'
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_repairs_invalid_structured_output_once() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(200, json=_chat_response("not-json"))
        return httpx.Response(200, json=_chat_response('{"name":"Alice"}'))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config()
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )
        request = UnifiedRequest(
            model="smart",
            protocol="chat_completions",
            messages=[UnifiedMessage(role="user", content="return a person")],
            response_format=StructuredOutputSpec(
                schema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                name="person",
            ),
        )

        result = await service.complete(request)

        assert len(bodies) == 2
        assert result.raw["choices"][0]["message"]["content"] == '{"name":"Alice"}'
        assert result.metrics.repair_retries == 1
        assert result.metrics.retries == 0
        assert result.metrics.fallbacks == 0
        assert len(bodies[1]["messages"]) == 3
        assert bodies[1]["messages"][-1]["role"] == "user"
        assert "failed JSON Schema validation" in bodies[1]["messages"][-1]["content"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_complete_raises_structured_error_when_repair_budget_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response("still-not-json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        config = _config(structured_output_retries=0)
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
        )
        request = UnifiedRequest(
            model="smart",
            protocol="chat_completions",
            messages=[UnifiedMessage(role="user", content="return a person")],
            response_format=StructuredOutputSpec(schema={"type": "object"}),
        )

        with pytest.raises(GatewayError) as exc_info:
            await service.complete(request)

        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "structured_output_error"
    finally:
        await client.aclose()
