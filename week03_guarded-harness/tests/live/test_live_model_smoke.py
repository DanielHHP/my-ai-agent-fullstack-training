"""Explicit opt-in smoke test for the default OpenAI Chat Completions provider."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from smolagents.models import ChatMessage, MessageRole

from guarded_harness import load_config
from guarded_harness.model_runtime import create_model_from_config


@pytest.mark.live_model
def test_live_model_provider_constructs_and_generates() -> None:
    if os.environ.get("GUARDED_HARNESS_LIVE_MODEL") != "1":
        pytest.skip("set GUARDED_HARNESS_LIVE_MODEL=1 to run the live model smoke test")
    config_path = Path(
        os.environ.get(
            "GUARDED_HARNESS_LIVE_CONFIG",
            "examples/config/model-openai-chat-completions.yaml",
        )
    )
    config = load_config(config_path)
    if config.config.model is None:
        pytest.fail("live smoke config must be version 2 with a model profile")
    if not os.environ.get(config.config.model.credential_ref):
        pytest.fail("live model credential is unavailable")
    model = create_model_from_config(config)
    assert model.model_id == config.config.model.model_id
    response = model.generate(
        [ChatMessage(role=MessageRole.USER, content="Reply with the word ok.")],
        max_tokens=16,
    )
    assert isinstance(response, ChatMessage)
    assert response.token_usage is not None
