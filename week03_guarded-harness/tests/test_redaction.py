"""Redaction boundary tests for WP-01."""

from __future__ import annotations

import json

import pytest

from guarded_harness import ErrorCode, RedactionError, RedactionLimits, Redactor


def test_sensitive_keys_and_values_are_redacted_without_hiding_budget_names() -> None:
    secret_value = "runtime-secret-value"
    redactor = Redactor(sensitive_values=(secret_value,))
    source = {
        "access_token": "token-value",
        "Authorization": "Bearer abcdefghijklmnop",
        "max_token_cost": "0.25",
        "message": f"request failed with {secret_value}",
        "nested": [{"client_secret": "nested-secret"}],
    }

    result = redactor.redact(source)

    assert isinstance(result.value, dict)
    assert result.value["access_token"] == "[REDACTED]"
    assert result.value["Authorization"] == "[REDACTED]"
    assert result.value["max_token_cost"] == "0.25"
    assert result.value["message"] == "request failed with [REDACTED]"
    assert result.value["nested"][0]["client_secret"] == "[REDACTED]"
    assert secret_value not in json.dumps(result.value)
    assert result.redaction_count >= 4


def test_known_credential_shapes_are_redacted() -> None:
    redactor = Redactor()
    source = "\n".join(
        (
            "Authorization: Bearer abcdefghijklmnop",
            "cookie: session=abcdef123456",
            "jwt eyJabcdefgh.abcdefghij.abcdefghij",
            "token=sk-abcdefghijklmnopqrstuvwxyz",
            "github ghp_abcdefghijklmnopqrstuvwxyz",
            "aws AKIAABCDEFGHIJKLMNOP",
            "-----BEGIN PRIVATE KEY-----\nprivate\n-----END PRIVATE KEY-----",
        )
    )

    result = redactor.redact(source)

    assert isinstance(result.value, str)
    for secret_fragment in (
        "abcdefghijklmnop",
        "session=abcdef123456",
        "eyJabcdefgh",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "AKIAABCDEFGHIJKLMNOP",
        "\nprivate\n",
    ):
        assert secret_fragment not in result.value
    assert "[REDACTED]" in result.value


def test_cycles_depth_nodes_and_lines_are_bounded() -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    limits = RedactionLimits(max_output_bytes=4096, max_lines=3, max_depth=3, max_nodes=20)
    redactor = Redactor(limits=limits)

    cycle_result = redactor.redact({"cycle": cycle})
    deep_result = redactor.redact({"a": {"b": {"c": {"d": "too deep"}}}})
    lines_result = redactor.redact("line1\nline2\nline3\nline4")

    assert cycle_result.truncated
    assert "[CYCLE]" in json.dumps(cycle_result.value)
    assert deep_result.truncated
    assert "[TRUNCATED]" in json.dumps(deep_result.value)
    assert lines_result.truncated is True
    assert lines_result.value == "line1\nline2\n[TRUNCATED]"


def test_node_limit_and_output_byte_limit_are_deterministic() -> None:
    limits = RedactionLimits(max_output_bytes=64, max_lines=12, max_depth=8, max_nodes=4)
    redactor = Redactor(limits=limits)

    node_result = redactor.redact([1, 2, 3, 4, 5, 6])
    byte_result = redactor.redact("x" * 1000)

    assert node_result.truncated
    assert node_result.value == [1, 2, 3, "[TRUNCATED]"]
    assert byte_result.truncated
    assert isinstance(byte_result.value, str)
    assert len(json.dumps(byte_result.value, ensure_ascii=False).encode("utf-8")) <= 64
    assert byte_result == redactor.redact("x" * 1000)


def test_unsupported_objects_do_not_call_repr() -> None:
    class Explosive:
        def __repr__(self) -> str:
            raise AssertionError("repr must not be called")

    result = Redactor().redact({"value": Explosive()})

    assert result.value == {"value": "[UNSUPPORTED]"}


def test_invalid_limits_fail_with_stable_error() -> None:
    with pytest.raises(RedactionError) as error:
        RedactionLimits(max_output_bytes=10)
    assert error.value.code == ErrorCode.REDACTION_INVALID_INPUT
