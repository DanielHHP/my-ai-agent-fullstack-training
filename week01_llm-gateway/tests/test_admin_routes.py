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
