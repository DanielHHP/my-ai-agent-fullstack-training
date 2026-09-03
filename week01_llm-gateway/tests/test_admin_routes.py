from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app


def _config(database_url: str) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "api_keys": ["test-key"],
            "database_url": database_url,
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
                            "api": "chat,responses",
                        }
                    ],
                },
                "claude-fast": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "claude-sonnet-4-5",
                            "api": "messages",
                        }
                    ],
                },
            },
        }
    )


def test_healthz_and_readyz_do_not_require_auth(tmp_path) -> None:
    app = create_app(_config(str(tmp_path / "gateway.db")))

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/readyz").json() == {"status": "ok"}


def test_protected_routes_require_bearer_key(tmp_path) -> None:
    app = create_app(_config(str(tmp_path / "gateway.db")))

    with TestClient(app) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/admin/usage").status_code == 401
        assert client.get("/admin/routes").status_code == 401


def test_protected_routes_accept_x_api_key(tmp_path) -> None:
    app = create_app(_config(str(tmp_path / "gateway.db")))

    with TestClient(app) as client:
        response = client.get("/v1/models", headers={"x-api-key": "test-key"})

    assert response.status_code == 200


def test_models_admin_usage_and_routes_are_available_with_key(tmp_path) -> None:
    app = create_app(_config(str(tmp_path / "gateway.db")))
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        models_response = client.get("/v1/models", headers=headers)
        assert models_response.status_code == 200
        payload = models_response.json()
        assert payload["object"] == "list"
        model_ids = {item["id"] for item in payload["data"]}
        assert model_ids == {"smart", "claude-fast"}
        smart = next(item for item in payload["data"] if item["id"] == "smart")
        assert smart["supported_protocols"] == ["chat", "responses"]
        claude_fast = next(item for item in payload["data"] if item["id"] == "claude-fast")
        assert claude_fast["supported_protocols"] == ["messages"]

        usage_response = client.get("/admin/usage", headers=headers)
        assert usage_response.status_code == 200
        assert usage_response.json() == {"data": []}

        routes_response = client.get("/admin/routes", headers=headers)
        assert routes_response.status_code == 200
        body = routes_response.json()
        assert "smart" in body["models"]
        assert body["models"]["smart"]["routes"][0]["protocols"] == [
            "chat",
            "responses",
        ]
        assert body["circuits"] == {}


def test_model_endpoint_rate_limits_after_burst(tmp_path) -> None:
    config = GatewayConfig.model_validate(
        {
            "api_keys": ["test-key"],
            "database_url": str(tmp_path / "gateway.db"),
            "rate_limit": {
                "enabled": True,
                "requests_per_minute": 60,
                "burst": 1,
            },
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
                            "api": "chat",
                        }
                    ],
                }
            },
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["model"] == "gpt-primary"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "model": "gpt-primary",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(config, http_client=mock_client)
    headers = {"Authorization": "Bearer test-key"}
    body = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}

    with TestClient(app) as client:
        first = client.post("/v1/chat/completions", headers=headers, json=body)
        assert first.status_code == 200

        second = client.post("/v1/chat/completions", headers=headers, json=body)
        assert second.status_code == 429
        assert second.headers.get("retry-after") == "1"
        assert second.json()["error"]["code"] == "rate_limit_exceeded"


def _rate_limited_chat_config(database_url: str) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "api_keys": ["test-key"],
            "database_url": database_url,
            "rate_limit": {
                "enabled": True,
                "requests_per_minute": 60,
                "burst": 1,
            },
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
                            "api": "chat",
                        }
                    ],
                }
            },
        }
    )


def _chat_mock() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "model": "gpt-primary",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_non_model_endpoints_do_not_rate_limit(tmp_path) -> None:
    app = create_app(
        _rate_limited_chat_config(str(tmp_path / "gateway.db")),
        http_client=_chat_mock(),
    )
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        first = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "smart", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert first.status_code == 200

        for _ in range(3):
            assert client.get("/v1/prompts", headers=headers).status_code == 200
            assert client.get("/admin/routes", headers=headers).status_code == 200


def test_admin_usage_limit_parameter(tmp_path) -> None:
    app = create_app(
        _config(str(tmp_path / "gateway.db")),
        http_client=_chat_mock(),
    )
    headers = {"Authorization": "Bearer test-key"}
    body = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}

    with TestClient(app) as client:
        for _ in range(3):
            assert client.post("/v1/chat/completions", headers=headers, json=body).status_code == 200

        response = client.get("/admin/usage", headers=headers, params={"limit": 2})

    assert response.status_code == 200
    assert len(response.json()["data"]) == 2


def test_model_call_does_not_forward_unknown_fields(tmp_path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_1",
                "object": "chat.completion",
                "model": "gpt-primary",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    app = create_app(
        _config(str(tmp_path / "gateway.db")),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "hello"}],
                "user": "alice",
                "seed": 42,
            },
        )

    assert response.status_code == 200
    assert "user" not in captured
    assert "seed" not in captured
    assert captured["model"] == "gpt-primary"


def test_prompt_creation_rejects_unknown_fields(tmp_path) -> None:
    app = create_app(_config(str(tmp_path / "gateway.db")))
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        response = client.post(
            "/v1/prompts",
            headers=headers,
            json={
                "id": "reviewer",
                "name": "Reviewer",
                "content": "Review {{language}} code.",
                "unexpected": True,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_prompt_render_rejects_unknown_fields(tmp_path) -> None:
    app = create_app(_config(str(tmp_path / "gateway.db")))
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        create_response = client.post(
            "/v1/prompts",
            headers=headers,
            json={
                "id": "reviewer",
                "name": "Reviewer",
                "content": "Review {{language}} code.",
            },
        )
        assert create_response.status_code == 201

        render_response = client.post(
            "/v1/prompts/reviewer/render",
            headers=headers,
            json={"variables": {"language": "Python"}, "unexpected": True},
        )

    assert render_response.status_code == 422
    assert render_response.json()["error"]["code"] == "invalid_request"
