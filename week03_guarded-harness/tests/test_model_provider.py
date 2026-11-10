"""Offline OpenAI Chat Completions protocol test through the real SDK path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import yaml

from guarded_harness import (
    AcceptanceStatus,
    Harness,
    ModelCallStatus,
    RunMode,
    RunRequest,
    RunStatus,
    SQLiteStorage,
    SystemClock,
    load_config,
)
from guarded_harness.model_runtime import create_model_from_config


class _Counter:
    def count_messages(self, messages, tools) -> int:
        del messages, tools
        return 20


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Harness Tests"],
        check=True,
    )
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "initial"], check=True)
    return workspace


def test_real_openai_sdk_chat_completions_path_is_supported(tmp_path: Path) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert body["model"] == "gpt-test-2026-01-01"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test-1",
                "object": "chat.completion",
                "created": 1,
                "model": "gpt-test-2026-01-01",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-final",
                                    "type": "function",
                                    "function": {
                                        "name": "final_answer",
                                        "arguments": '{"answer":"candidate"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    workspace = _workspace(tmp_path)
    endpoint = "http://127.0.0.1:8000/v1"
    payload = {
        "version": 2,
        "state_dir": str(tmp_path / "state"),
        "workspace": {"root": str(workspace)},
        "budget": {
            "max_steps": 4,
            "max_tool_calls": 4,
            "max_token_cost": 1,
            "max_wall_time_seconds": 120,
        },
        "model": {
            "provider": "openai_chat_completions_v1",
            "model_id": "gpt-test-2026-01-01",
            "endpoint": endpoint,
            "credential_ref": "MODEL_API_KEY",
            "tokenizer_id": "tiktoken:o200k_base",
            "allow_insecure_localhost": True,
            "max_input_tokens": 1000,
            "max_output_tokens": 100,
            "pricing": {
                "version": "test-price",
                "input_per_million": "1",
                "output_per_million": "2",
            },
        },
        "task": {
            "id": "sdk_protocol",
            "kind": "code_understanding",
            "description": "Explain the code.",
            "allowed_tools": ["read_file"],
            "paths": {"allow_write": ["artifacts/report.md"]},
            "acceptance": {
                "strategy": "code_understanding",
                "deliverables": ["artifacts/report.md"],
            },
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_config(config_path)
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        trust_env=False,
        follow_redirects=False,
    )
    model = create_model_from_config(
        config,
        secret_resolver=lambda _ref: "test-secret",
        http_client=client,
    )
    result = Harness(
        config_path=config_path,
        model=model,
        token_counter=_Counter(),
    ).run(RunRequest(mode=RunMode.START))

    assert requests
    assert result.status == RunStatus.COMPLETED
    assert result.acceptance_status == AcceptanceStatus.REJECTED
    storage = SQLiteStorage(
        tmp_path / "state" / "harness.sqlite3",
        workspace,
        SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="test-reader") as unit:
        calls = unit.state.get_model_calls(result.run_id)
        assert calls[0].status == ModelCallStatus.SUCCEEDED
        assert calls[0].input_tokens == 10
        assert calls[0].output_tokens == 5
        assert calls[0].provider_request_id == "chatcmpl-test-1"
