from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import GatewayConfig, load_config, parse_protocols


def _minimal_raw() -> dict:
    return {
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com",
                "api_key": "secret",
            }
        },
        "models": {
            "smart": {
                "routes": [
                    {
                        "provider": "openai",
                        "model": "gpt-5.2",
                        "api": "chat,responses",
                    }
                ]
            }
        },
    }


def test_parse_protocols_supports_all() -> None:
    assert parse_protocols("chat,responses") == {"chat", "responses"}
    assert parse_protocols(" all ") == {"chat", "responses", "messages"}
    assert parse_protocols("messages") == {"messages"}


def test_parse_protocols_rejects_all_combination() -> None:
    with pytest.raises(ValueError):
        parse_protocols("all,messages")


def test_parse_protocols_rejects_unknown_protocol() -> None:
    with pytest.raises(ValueError, match="both"):
        parse_protocols("chat,both")


def test_config_rejects_unknown_provider() -> None:
    raw = _minimal_raw()
    raw["models"]["smart"]["routes"][0]["provider"] = "missing"
    with pytest.raises(ValidationError):
        GatewayConfig.model_validate(raw)


def test_config_defaults_match_acceptance_expectations() -> None:
    config = GatewayConfig.model_validate(_minimal_raw())

    assert config.retry.max_retries_per_route == 3
    assert config.rate_limit.enabled is True
    assert config.rate_limit.requests_per_minute == 60
    assert config.rate_limit.burst == 10
    assert config.circuit_breaker.failure_threshold == 5
    assert config.circuit_breaker.cooldown_seconds == 30


def test_load_config_expands_env_and_resolves_db_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    config_file = tmp_path / "gateway.yaml"
    config_file.write_text(
        """
providers:
  openai:
    base_url: https://api.openai.com
    api_key: ${OPENAI_API_KEY}
  anthropic:
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY:-fallback-key}
models:
  smart:
    routes:
      - provider: openai
        model: gpt-5.2
        api: "chat,responses"
  claude-fast:
    routes:
      - provider: anthropic
        model: claude-sonnet-4-5
        api: "messages"
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.providers["openai"].api_key.get_secret_value() == "test-secret"
    assert config.providers["anthropic"].api_key.get_secret_value() == "fallback-key"
    assert config.models["smart"].routes[0].protocols == {"chat", "responses"}
    assert config.models["claude-fast"].routes[0].protocols == {"messages"}
    assert config.database_url == str((tmp_path / "data" / "gateway.db").resolve())
