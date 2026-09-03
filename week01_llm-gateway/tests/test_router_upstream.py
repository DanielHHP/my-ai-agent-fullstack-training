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


def _weighted_config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "providers": {
                "openai-a": {
                    "base_url": "https://a.test",
                    "api_key": "key-a",
                },
                "openai-b": {
                    "base_url": "https://b.test",
                    "api_key": "key-b",
                },
            },
            "models": {
                "smart": {
                    "strategy": "weighted_round_robin",
                    "routes": [
                        {
                            "provider": "openai-a",
                            "model": "gpt-a",
                            "weight": 3,
                            "api": "chat",
                        },
                        {
                            "provider": "openai-b",
                            "model": "gpt-b",
                            "weight": 1,
                            "api": "chat",
                        },
                    ],
                }
            },
        }
    )


def test_router_preserves_priority_order() -> None:
    config = GatewayConfig.model_validate(
        {
            "providers": {
                "openai-a": {"base_url": "https://a.test", "api_key": "key-a"},
                "openai-b": {"base_url": "https://b.test", "api_key": "key-b"},
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {"provider": "openai-a", "model": "gpt-a", "api": "chat"},
                        {"provider": "openai-b", "model": "gpt-b", "api": "chat"},
                    ],
                }
            },
        }
    )

    targets = ModelRouter(config).candidates("smart", "chat")

    assert [target.provider for target in targets] == ["openai-a", "openai-b"]


def test_router_weighted_round_robin_uses_model_alias_counter() -> None:
    router = ModelRouter(_weighted_config())

    providers = [
        router.candidates("smart", "chat")[0].provider for _ in range(4)
    ]

    assert providers == ["openai-a", "openai-a", "openai-a", "openai-b"]


def test_router_skips_disabled_provider() -> None:
    config = GatewayConfig.model_validate(
        {
            "providers": {
                "openai-a": {
                    "base_url": "https://a.test",
                    "api_key": "key-a",
                    "enabled": False,
                },
                "openai-b": {"base_url": "https://b.test", "api_key": "key-b"},
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {"provider": "openai-a", "model": "gpt-a", "api": "chat"},
                        {"provider": "openai-b", "model": "gpt-b", "api": "chat"},
                    ],
                }
            },
        }
    )

    targets = ModelRouter(config).candidates("smart", "chat")

    assert [target.provider for target in targets] == ["openai-b"]


def test_router_opens_and_recovers_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    config = GatewayConfig.model_validate(
        {
            "circuit_breaker": {
                "failure_threshold": 2,
                "cooldown_seconds": 10,
            },
            "providers": {
                "openai": {"base_url": "https://a.test", "api_key": "key-a"}
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {"provider": "openai", "model": "gpt-a", "api": "chat"}
                    ],
                }
            },
        }
    )
    router = ModelRouter(config)
    now = 1000.0
    monkeypatch.setattr("app.services.router.time.monotonic", lambda: now)

    assert router.candidates("smart", "chat")[0].provider == "openai"

    router.record_failure("openai")
    router.record_failure("openai")

    with pytest.raises(GatewayError) as exc_info:
        router.candidates("smart", "chat")
    assert exc_info.value.code == "no_healthy_route"

    now = 1011.0
    assert router.candidates("smart", "chat")[0].provider == "openai"


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
