from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import GatewayConfig
from app.main import create_app

ROOT_DIR = Path(__file__).resolve().parent.parent
ACCEPTANCE_CONFIG_PATH = ROOT_DIR / "tests" / "configs" / "acceptance.yaml"


def _load_blackbox_config() -> GatewayConfig:
    raw: dict[str, Any] = yaml.safe_load(
        ACCEPTANCE_CONFIG_PATH.read_text(encoding="utf-8")
    )
    raw["database_url"] = os.environ.get(
        "BLACKBOX_DB_URL",
        str(ROOT_DIR / "reports" / "blackbox" / "gateway.db"),
    )
    return GatewayConfig.model_validate(raw)


def _chat_response() -> dict[str, Any]:
    return {
        "id": "chatcmpl_blackbox",
        "object": "chat.completion",
        "model": "gpt-acceptance",
        "choices": [{"message": {"role": "assistant", "content": "blackbox-ok"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }


def _chat_stream_response() -> httpx.Response:
    body = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n'
        b"data: [DONE]\n\n"
    )
    return httpx.Response(
        200,
        content=body,
        headers={"Content-Type": "text/event-stream"},
    )


def _mock_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path != "/v1/chat/completions":
        return httpx.Response(
            404,
            json={"error": {"message": "mock upstream route not found"}},
        )

    payload = json.loads(request.content)
    if payload.get("stream"):
        return _chat_stream_response()
    return httpx.Response(200, json=_chat_response())


def create_blackbox_app() -> Any:
    config = _load_blackbox_config()
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_mock_handler)
    )
    return create_app(config, http_client=mock_client)
