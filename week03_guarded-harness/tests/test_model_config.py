"""Strict version 2 model-profile configuration tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guarded_harness import (
    ConfigurationError,
    ErrorCode,
    ModelRuntimeError,
    config_fingerprint,
    load_config,
)
from guarded_harness.model_runtime import create_model_from_config


def _payload(workspace: Path) -> dict[str, object]:
    return {
        "version": 2,
        "state_dir": "state",
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
            "endpoint": "https://api.example.invalid/v1",
            "credential_ref": "MODEL_API_KEY",
            "tokenizer_id": "tiktoken:o200k_base",
            "request_timeout_seconds": 30,
            "connect_timeout_seconds": 5,
            "max_input_tokens": 1000,
            "max_output_tokens": 100,
            "max_response_bytes": 65536,
            "max_tool_calls_per_response": 4,
            "requests_per_minute": 30,
            "max_retry_attempts": 1,
            "pricing": {
                "version": "price-v1",
                "input_per_million": "1.0",
                "output_per_million": "2.0",
            },
        },
        "task": {
            "id": "model_task",
            "kind": "code_understanding",
            "description": "Explain the code.",
            "allowed_tools": ["read_file", "write_file"],
            "paths": {"allow_write": ["artifacts/report.md"]},
            "acceptance": {
                "strategy": "code_understanding",
                "deliverables": ["artifacts/report.md"],
            },
            "approval": {
                "required_tools": ["write_file"],
                "approver_roles": ["operator"],
            },
        },
    }


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "artifacts").mkdir(exist_ok=True)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_v2_model_profile_loads_and_is_fingerprinted(tmp_path: Path) -> None:
    payload = _payload(tmp_path / "workspace")
    loaded = load_config(_write(tmp_path, payload))

    assert loaded.schema_version == 2
    assert loaded.config.version == 2
    assert loaded.config.model is not None
    assert loaded.config.model.provider == "openai_chat_completions_v1"
    assert loaded.config.model.tokenizer_id == "tiktoken:o200k_base"
    assert loaded.fingerprint == config_fingerprint(loaded)

    changed = _payload(tmp_path / "workspace")
    changed["model"]["model_id"] = "gpt-test-other"  # type: ignore[index]
    assert load_config(_write(tmp_path, changed)).fingerprint != loaded.fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "openai_compatible"),
        ("credential_ref", "ARBITRARY_SECRET"),
        ("tokenizer_id", "remote:unknown"),
        ("endpoint", "https://user:pass@example.invalid/v1"),
        ("endpoint", "https://example.invalid/v1?query=1"),
        ("endpoint", "http://127.0.0.1:8000/v1"),
    ],
)
def test_invalid_model_profile_fails_closed(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    payload = _payload(tmp_path / "workspace")
    payload["model"][field] = value  # type: ignore[index]

    with pytest.raises(ConfigurationError) as exc:
        load_config(_write(tmp_path, payload))
    assert exc.value.code == ErrorCode.CONFIG_SCHEMA_INVALID


def test_v2_requires_model_and_v1_rejects_model(tmp_path: Path) -> None:
    missing = _payload(tmp_path / "workspace")
    missing.pop("model")
    with pytest.raises(ConfigurationError):
        load_config(_write(tmp_path, missing))

    legacy = _payload(tmp_path / "workspace")
    legacy["version"] = 1
    with pytest.raises(ConfigurationError):
        load_config(_write(tmp_path, legacy))


def test_local_http_requires_explicit_exception(tmp_path: Path) -> None:
    payload = _payload(tmp_path / "workspace")
    payload["model"]["endpoint"] = "http://127.0.0.1:8000/v1"
    payload["model"]["allow_insecure_localhost"] = True
    loaded = load_config(_write(tmp_path, payload))
    assert loaded.config.model is not None
    assert loaded.config.model.allow_insecure_localhost is True


def test_default_openai_sdk_builds_public_model_without_network(tmp_path: Path) -> None:
    loaded = load_config(_write(tmp_path, _payload(tmp_path / "workspace")))
    model = create_model_from_config(loaded, secret_resolver=lambda _ref: "test-secret")
    assert model.model_id == "gpt-test-2026-01-01"


def test_default_environment_resolver_rejects_missing_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    loaded = load_config(_write(tmp_path, _payload(tmp_path / "workspace")))
    with pytest.raises(ModelRuntimeError) as exc:
        create_model_from_config(loaded)
    assert exc.value.code == ErrorCode.MODEL_CREDENTIAL_UNAVAILABLE
