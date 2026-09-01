from __future__ import annotations

import json

import httpx
import pytest

from app.config import GatewayConfig
from app.core.errors import GatewayError
from app.schemas import (
    PromptReference,
    StructuredOutputSpec,
    UnifiedMessage,
    UnifiedRequest,
    UpstreamRequest,
)
from app.services.adapters import (
    AnthropicMessagesAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    get_adapter,
)
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient


def _config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "providers": {
                "openai": {
                    "base_url": "https://openai.test",
                    "api_key": "openai-key",
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
                            "model": "gpt-5.2",
                            "api": "chat,responses",
                        },
                        {
                            "provider": "anthropic",
                            "model": "claude-sonnet-4-5",
                            "api": "messages",
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


def _chat_request(**overrides: object) -> UnifiedRequest:
    data: dict[str, object] = {
        "model": "smart",
        "protocol": "chat_completions",
        "messages": [
            UnifiedMessage(role="system", content="be concise"),
            UnifiedMessage(role="user", content="hello"),
        ],
        "stream": False,
    }
    data.update(overrides)
    return UnifiedRequest.model_validate(data)


def test_adapter_registry_selects_correct_adapter() -> None:
    assert isinstance(get_adapter("chat"), OpenAIChatAdapter)
    assert isinstance(get_adapter("responses"), OpenAIResponsesAdapter)
    assert isinstance(get_adapter("messages"), AnthropicMessagesAdapter)


def test_openai_chat_adapter_builds_expected_upstream_request() -> None:
    config = _config()
    target = config.models["smart"].routes[0]
    request = _chat_request(max_tokens=64)

    upstream = OpenAIChatAdapter().build_request(
        target=target,
        request=request,
        request_id="req_1",
        provider=config.providers["openai"],
    )

    assert upstream.url == "https://openai.test/v1/chat/completions"
    assert upstream.headers["Authorization"] == "Bearer openai-key"
    assert upstream.body["model"] == "gpt-5.2"
    assert upstream.body["max_tokens"] == 64
    assert upstream.body["messages"][1]["content"] == "hello"


def test_openai_responses_adapter_converts_input() -> None:
    config = _config()
    target = config.models["smart"].routes[0]
    request = UnifiedRequest(
        model="smart",
        protocol="openai_responses",
        messages=[UnifiedMessage(role="user", content="hello")],
    )

    upstream = OpenAIResponsesAdapter().build_request(
        target=target,
        request=request,
        request_id="req_2",
        provider=config.providers["openai"],
    )

    assert upstream.url == "https://openai.test/v1/responses"
    assert upstream.body["input"] == "hello"
    assert upstream.body["model"] == "gpt-5.2"


def test_anthropic_adapter_uses_x_api_key_and_requires_max_tokens() -> None:
    config = _config()
    target = config.models["smart"].routes[1]
    request = UnifiedRequest(
        model="smart",
        protocol="anthropic_messages",
        messages=[
            UnifiedMessage(role="system", content="be concise"),
            UnifiedMessage(role="user", content="hello"),
        ],
        max_tokens=32,
    )

    upstream = AnthropicMessagesAdapter().build_request(
        target=target,
        request=request,
        request_id="req_3",
        provider=config.providers["anthropic"],
    )

    assert upstream.url == "https://anthropic.test/v1/messages"
    assert upstream.headers["x-api-key"] == "anthropic-key"
    assert "Authorization" not in upstream.headers
    assert upstream.headers["anthropic-version"] == "2023-06-01"
    assert upstream.body["system"] == [{"type": "text", "text": "be concise"}]

    missing = request.model_copy(update={"max_tokens": None})
    with pytest.raises(GatewayError) as exc_info:
        AnthropicMessagesAdapter().build_request(
            target=target,
            request=missing,
            request_id="req_4",
            provider=config.providers["anthropic"],
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.param == "max_tokens"


def test_anthropic_adapter_injects_structured_schema_into_system() -> None:
    config = _config()
    target = config.models["smart"].routes[1]
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    request = UnifiedRequest(
        model="smart",
        protocol="anthropic_messages",
        messages=[
            UnifiedMessage(role="system", content="be concise"),
            UnifiedMessage(role="user", content="hello"),
        ],
        max_tokens=32,
        response_format=StructuredOutputSpec(schema=schema, name="person"),
    )

    upstream = AnthropicMessagesAdapter().build_request(
        target=target,
        request=request,
        request_id="req_5",
        provider=config.providers["anthropic"],
    )

    system = upstream.body["system"]
    assert isinstance(system, list)
    assert json.dumps(schema) in system[0]["text"]


def test_model_router_filters_routes_by_protocol_and_returns_adapter() -> None:
    config = _config()
    router = ModelRouter(config)

    chat_targets = router.candidates("smart", "chat")
    assert [target.provider for target in chat_targets] == ["openai"]

    target, adapter = router.resolve("smart", "responses")
    assert target.provider == "openai"
    assert isinstance(adapter, OpenAIResponsesAdapter)

    with pytest.raises(GatewayError) as exc_info:
        router.candidates("claude-fast", "chat")
    assert exc_info.value.code == "protocol_not_supported"

    with pytest.raises(GatewayError) as exc_info:
        router.candidates("missing", "chat")
    assert exc_info.value.code == "model_not_found"


@pytest.mark.asyncio
async def test_upstream_client_executes_upstream_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    upstream = UpstreamClient(client)
    result = await upstream.request_json(
        UpstreamRequest(
            provider="openai",
            url="https://openai.test/v1/chat/completions",
            headers={
                "Authorization": "Bearer openai-key",
                "Content-Type": "application/json",
            },
            body={"model": "gpt-5.2", "messages": []},
            stream=False,
            timeout=httpx.Timeout(30),
        )
    )

    assert captured["url"] == "https://openai.test/v1/chat/completions"
    assert captured["authorization"] == "Bearer openai-key"
    assert result["id"] == "chatcmpl_1"
    await upstream.close()


def test_unified_request_allows_extra_fields_and_prompt_reference() -> None:
    request = UnifiedRequest.model_validate(
        {
            "model": "smart",
            "protocol": "chat_completions",
            "messages": [{"role": "user", "content": "hi"}],
            "custom_flag": True,
            "prompt_ref": {
                "id": "reviewer",
                "variables": {"language": "Python"},
            },
        }
    )

    assert request.model_extra == {"custom_flag": True}
    assert isinstance(request.prompt_ref, PromptReference)
    assert request.prompt_ref.variables == {"language": "Python"}
