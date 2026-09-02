from __future__ import annotations

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
        assert body["circuits"] == {}
