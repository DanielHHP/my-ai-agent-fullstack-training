"""Explicit opt-in smoke test for the default OpenAI Chat Completions provider."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from smolagents import Tool
from smolagents.models import ChatMessage, MessageRole

from guarded_harness import load_config
from guarded_harness.model_runtime import create_model_from_config


class _EchoTool(Tool):
    name = "echo"
    description = "Echo the supplied text."
    inputs = {"text": {"type": "string", "description": "Text to echo."}}
    output_type = "string"

    def forward(self, text: str) -> str:
        return text


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
        [
            ChatMessage(
                role=MessageRole.USER,
                content="Use the echo tool to echo the word hello.",
            )
        ],
        tools_to_call_from=[_EchoTool()],
        tool_choice=config.config.model.tool_choice,
        max_tokens=128,
    )
    assert isinstance(response, ChatMessage)
    assert response.tool_calls
    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert call.id
    assert call.function.name == "echo"
    arguments = call.function.arguments
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    assert arguments == {"text": "hello"}
    assert response.token_usage is not None
