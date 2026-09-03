from __future__ import annotations

import json
from pathlib import Path

import httpx
import yaml
from fastapi.testclient import TestClient

from app.config import GatewayConfig
from app.main import create_app


def _chat_response(content: str = "ok") -> dict[str, object]:
    return {
        "id": "chatcmpl_acceptance",
        "object": "chat.completion",
        "model": "gpt-acceptance",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }


def _responses_response() -> dict[str, object]:
    return {
        "id": "resp_acceptance",
        "object": "response",
        "status": "completed",
        "model": "gpt-acceptance",
        "output_text": "responses-ok",
        "usage": {"input_tokens": 4, "output_tokens": 2},
    }


def _messages_response() -> dict[str, object]:
    return {
        "id": "msg_acceptance",
        "type": "message",
        "role": "assistant",
        "model": "claude-acceptance",
        "content": [{"type": "text", "text": "messages-ok"}],
        "usage": {"input_tokens": 3, "output_tokens": 1},
    }


def test_acceptance_config_file_is_tracked_and_mock_only(
    acceptance_config_path: Path,
    acceptance_config: GatewayConfig,
) -> None:
    raw = yaml.safe_load(acceptance_config_path.read_text(encoding="utf-8"))

    assert raw["api_keys"] == ["test-key", "rate-limit-key"]
    assert all(
        provider["base_url"].endswith(".invalid")
        for provider in raw["providers"].values()
    )

    assert acceptance_config.models["smart"].routes[0].provider == "openai-primary"
    assert acceptance_config.models["smart"].routes[0].protocols == {
        "chat",
        "responses",
    }
    assert acceptance_config.models["claude-fast"].routes[0].protocols == {"messages"}
    assert acceptance_config.rate_limit.burst == 2


def test_chat_completions_end_to_end_uses_shared_acceptance_config(
    acceptance_config: GatewayConfig,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response())

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(acceptance_config, http_client=mock_client)
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "ok"

        usage = client.get("/admin/usage", headers=headers)
        assert usage.status_code == 200
        assert len(usage.json()["data"]) == 1

    assert captured["url"] == "https://openai-primary.invalid/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-openai-primary-key"
    assert captured["body"]["model"] == "gpt-acceptance"


def test_responses_and_messages_endpoints_use_shared_acceptance_config(
    acceptance_config: GatewayConfig,
) -> None:
    captured_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_paths.append(request.url.path)
        if request.url.path == "/v1/responses":
            return httpx.Response(200, json=_responses_response())
        if request.url.path == "/v1/messages":
            return httpx.Response(200, json=_messages_response())
        raise AssertionError(f"unexpected upstream path: {request.url.path}")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(acceptance_config, http_client=mock_client)
    headers = {"Authorization": "Bearer test-key"}

    with TestClient(app) as client:
        responses = client.post(
            "/v1/responses",
            headers=headers,
            json={"model": "smart", "input": "hello"},
        )
        assert responses.status_code == 200
        assert responses.json()["output_text"] == "responses-ok"

        messages = client.post(
            "/v1/messages",
            headers=headers,
            json={
                "model": "claude-fast",
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert messages.status_code == 200
        assert messages.json()["content"][0]["text"] == "messages-ok"

    assert captured_paths == ["/v1/responses", "/v1/messages"]


def test_rate_limit_uses_burst_from_acceptance_config(
    acceptance_config: GatewayConfig,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json=_chat_response())

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(acceptance_config, http_client=mock_client)
    headers = {"Authorization": "Bearer test-key"}
    body = {"model": "smart", "messages": [{"role": "user", "content": "hi"}]}

    with TestClient(app) as client:
        first = client.post("/v1/chat/completions", headers=headers, json=body)
        second = client.post("/v1/chat/completions", headers=headers, json=body)
        third = client.post("/v1/chat/completions", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.headers.get("retry-after") == "1"
    assert third.json()["error"]["code"] == "rate_limit_exceeded"
