"""Deterministic scripted smolagents model used only by offline tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from smolagents import Model
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import TokenUsage


class FakeModelExhaustedError(RuntimeError):
    """Raised when the script has no response left."""


@dataclass(frozen=True)
class ScriptedToolCall:
    """One deterministic model-proposed tool call."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptedResponse:
    """One model response or injected generation failure."""

    content: str | None = None
    tool_calls: tuple[ScriptedToolCall, ...] = ()
    input_tokens: int = 1
    output_tokens: int = 1
    error: BaseException | None = None

    def __post_init__(self) -> None:
        if self.error is not None and (self.content is not None or self.tool_calls):
            raise ValueError("an error response cannot also contain content or tool calls")
        if self.error is None and not self.tool_calls:
            raise ValueError("scripted responses must contain at least one tool call")
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("scripted token usage must be non-negative")


@dataclass(frozen=True)
class ModelCallRecord:
    """Observed inputs from one Fake Model generation."""

    messages: tuple[ChatMessage, ...]
    tools: tuple[Any, ...]


class FakeScriptedModel(Model):
    """A strict finite script implementing the public Model.generate contract."""

    def __init__(
        self,
        responses: tuple[ScriptedResponse, ...],
        *,
        before_generate: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(model_id="fake-scripted-model")
        if not responses:
            raise ValueError("responses must not be empty")
        self._responses = tuple(responses)
        self._position = 0
        self._before_generate = before_generate
        self.calls: list[ModelCallRecord] = []

    def generate(
        self,
        messages: list[ChatMessage],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Any] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        del stop_sequences, response_format, kwargs
        if self._before_generate is not None:
            self._before_generate()
        self.calls.append(
            ModelCallRecord(
                messages=tuple(messages),
                tools=tuple(tools_to_call_from or ()),
            )
        )
        if self._position >= len(self._responses):
            raise FakeModelExhaustedError("scripted model response is exhausted")
        response = self._responses[self._position]
        self._position += 1
        if response.error is not None:
            raise response.error
        tool_calls = [
            ChatMessageToolCall(
                id=call.id,
                type="function",
                function=ChatMessageToolCallFunction(
                    name=call.name,
                    arguments=call.arguments,
                ),
            )
            for call in response.tool_calls
        ]
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=response.content,
            tool_calls=tool_calls,
            token_usage=TokenUsage(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            ),
        )

    def assert_consumed(self) -> None:
        """Assert that every scripted response was requested exactly once."""

        if self._position != len(self._responses):
            raise AssertionError(
                f"script consumed {self._position} of {len(self._responses)} responses"
            )


def tool_call(
    call_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> ScriptedResponse:
    """Build one scripted tool-call response."""

    return ScriptedResponse(
        tool_calls=(ScriptedToolCall(call_id, name, arguments),),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def multi_tool_call(
    calls: tuple[ScriptedToolCall, ...],
    *,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> ScriptedResponse:
    """Build one scripted response containing multiple ordered tool calls."""

    return ScriptedResponse(
        tool_calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def final_answer(
    text: str,
    *,
    call_id: str = "call-final",
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> ScriptedResponse:
    """Build one final_answer tool call."""

    return tool_call(
        call_id,
        "final_answer",
        {"answer": text},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


__all__ = [
    "FakeModelExhaustedError",
    "FakeScriptedModel",
    "ModelCallRecord",
    "ScriptedResponse",
    "ScriptedToolCall",
    "final_answer",
    "multi_tool_call",
    "tool_call",
]
