from __future__ import annotations

import json

import httpx
import pytest

from app.config import GatewayConfig
from app.core.errors import GatewayError
from app.schemas import UpstreamRequest
from app.services.adapters import OpenAIResponsesAdapter
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
