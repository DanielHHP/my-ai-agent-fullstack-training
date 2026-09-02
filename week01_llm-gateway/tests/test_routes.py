from __future__ import annotations

import json

import pytest

from app.api.routes import (
    _chat_request,
    _json_payload,
    _messages_request,
    _prompt_ref_from_payload,
    _responses_request,
    _structured_spec_from_payload,
)
from app.core.errors import GatewayError


def test_structured_spec_parses_openai_chat_json_schema() -> None:
    spec = _structured_spec_from_payload(
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "person",
                    "strict": True,
                    "schema": {"type": "object"},
                },
            }
        }
    )

    assert spec is not None
    assert spec.name == "person"
    assert spec.strict is True
    assert spec.schema == {"type": "object"}


def test_structured_spec_parses_responses_text_format() -> None:
    spec = _structured_spec_from_payload(
        {
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "person",
                    "schema": {"type": "object"},
                }
            }
        }
    )

    assert spec is not None
    assert spec.schema == {"type": "object"}


def test_structured_spec_ignores_non_object_text() -> None:
    assert _structured_spec_from_payload({"text": "hello"}) is None


def test_structured_spec_rejects_missing_schema() -> None:
    with pytest.raises(GatewayError) as exc_info:
        _structured_spec_from_payload(
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "person"},
                }
            }
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.param == "response_format"


def test_structured_spec_rejects_unsupported_type() -> None:
    with pytest.raises(GatewayError) as exc_info:
        _structured_spec_from_payload(
            {"response_format": {"type": "json_object"}}
        )

    assert exc_info.value.status_code == 422


def test_chat_request_reads_prompt_ref() -> None:
    request = _chat_request(
        {
            "model": "smart",
            "messages": [{"role": "user", "content": "hello"}],
            "prompt_ref": {
                "id": "reviewer",
                "version": 2,
                "variables": {"language": "Python"},
                "position": "append",
            },
        }
    )

    assert request.prompt_ref is not None
    assert request.prompt_ref.id == "reviewer"
    assert request.prompt_ref.version == 2
    assert request.prompt_ref.variables == {"language": "Python"}
    assert request.prompt_ref.position == "append"


def test_responses_request_reads_prompt_ref() -> None:
    request = _responses_request(
        {
            "model": "smart",
            "input": "hello",
            "prompt_ref": {"id": "reviewer", "variables": {"language": "Python"}},
        }
    )

    assert request.prompt_ref is not None
    assert request.prompt_ref.id == "reviewer"


def test_messages_request_reads_prompt_ref() -> None:
    request = _messages_request(
        {
            "model": "claude-fast",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "hello"}],
            "prompt_ref": {"id": "reviewer"},
        }
    )

    assert request.prompt_ref is not None
    assert request.prompt_ref.id == "reviewer"


def test_prompt_ref_rejects_non_object() -> None:
    with pytest.raises(GatewayError) as exc_info:
        _prompt_ref_from_payload({"prompt_ref": "reviewer"})

    assert exc_info.value.status_code == 422
    assert exc_info.value.param == "prompt_ref"


@pytest.mark.asyncio
async def test_json_payload_rejects_invalid_json() -> None:
    class FakeRequest:
        async def json(self) -> dict:
            raise json.JSONDecodeError("bad json", "", 0)

    with pytest.raises(GatewayError) as exc_info:
        await _json_payload(FakeRequest())

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "invalid_json"
