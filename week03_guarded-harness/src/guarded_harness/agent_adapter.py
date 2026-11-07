"""Thin public smolagents adaptation with original tool-call IDs preserved."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from importlib.metadata import version as distribution_version
from typing import Any

import smolagents
from smolagents import Model, Tool, ToolCallingAgent
from smolagents.agents import ActionOutput, ToolOutput
from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep, ToolCall
from smolagents.models import (
    ChatMessage,
)
from smolagents.monitoring import LogLevel

from .domain import (
    ErrorCode,
    JsonValue,
    ToolCallRequest,
    ToolExecutionContext,
    ToolObservation,
    ToolSpec,
)
from .gate import Gate
from .registry import ToolRegistry

SMOLAGENTS_VERSION = "1.26.0"
MAX_OBSERVATION_CHARS = 64 * 1024


class AgentAdapterError(RuntimeError):
    """Stable fail-closed Agent adapter error."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


class HarnessAgentStopSignal(Exception):
    """Internal control signal used to stop after a persisted safe boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class _CallContext:
    context: ToolExecutionContext
    lease_token: str


class GovernedTool(Tool):
    """A model-visible Tool wrapper that has no trusted handler reference."""

    skip_forward_signature_validation = True
    output_type = "string"

    def __init__(
        self,
        *,
        spec: ToolSpec,
        adapter: HarnessAgentAdapter,
    ) -> None:
        self.name = spec.name
        self.description = spec.description
        self.inputs = _tool_inputs_from_schema(spec.input_schema)
        self._adapter = adapter
        super().__init__()

    def forward(self, **kwargs: Any) -> ToolObservation:
        """Dispatch this wrapper through Gate using the active model call ID."""

        return self._adapter.dispatch_active_tool(self.name, kwargs)


class HarnessAgentAdapter:
    """Own model-visible tools and the ID-aware Gate dispatch boundary."""

    def __init__(
        self,
        *,
        gate: Gate,
        registry: ToolRegistry,
        visible_specs: tuple[ToolSpec, ...],
    ) -> None:
        if not isinstance(gate, Gate):
            raise AgentAdapterError(
                ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
                "adapter requires a Gate",
            )
        if not isinstance(registry, ToolRegistry):
            raise AgentAdapterError(
                ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
                "adapter requires a ToolRegistry",
            )
        self._gate = gate
        self._registry = registry
        self._specs = tuple(sorted(visible_specs, key=lambda spec: spec.name))
        names = tuple(spec.name for spec in self._specs)
        if len(names) != len(set(names)):
            raise AgentAdapterError(
                ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
                "visible tool names must be unique",
            )
        self._tools = tuple(GovernedTool(spec=spec, adapter=self) for spec in self._specs)
        self._tools_by_name = {tool.name: tool for tool in self._tools}
        self._context: _CallContext | None = None
        self._active_call_id: str | None = None
        self._stop_reason: str | None = None

    @property
    def tools(self) -> tuple[GovernedTool, ...]:
        """Return the stable model-visible wrapper tuple."""

        return self._tools

    @property
    def specs(self) -> tuple[ToolSpec, ...]:
        """Return the exact model-visible specs in stable order."""

        return self._specs

    def activate(
        self,
        *,
        context: ToolExecutionContext,
        lease_token: str,
    ) -> None:
        """Bind the trusted execution context for the current action response."""

        if not isinstance(context, ToolExecutionContext):
            raise TypeError("context must be a ToolExecutionContext")
        if not isinstance(lease_token, str) or not lease_token:
            raise AgentAdapterError(
                ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
                "lease token must be non-empty",
            )
        self._context = _CallContext(context=context, lease_token=lease_token)

    def deactivate(self) -> None:
        """Clear all execution authority after one action step."""

        self._context = None
        self._active_call_id = None
        self._stop_reason = None

    def request_stop(self, reason: str) -> None:
        """Prevent any later tool call in the current model response."""

        if not reason:
            raise ValueError("stop reason must be non-empty")
        self._stop_reason = reason

    @property
    def stop_requested(self) -> bool:
        """Return whether the current response must stop before another tool."""

        return self._stop_reason is not None

    def invoke(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: object,
    ) -> ToolObservation:
        """Dispatch one model candidate through Gate, including unknown tools."""

        bound = self._require_context()
        normalized_arguments = _arguments_object(arguments)
        if tool_name != "final_answer" and tool_name in self._tools_by_name:
            previous = self._active_call_id
            self._active_call_id = tool_call_id
            try:
                result = self._tools_by_name[tool_name](
                    **normalized_arguments,
                    sanitize_inputs_outputs=False,
                )
            finally:
                self._active_call_id = previous
            if not isinstance(result, ToolObservation):
                raise AgentAdapterError(
                    ErrorCode.GATE_OUTPUT_INVALID,
                    "governed tool did not return a ToolObservation",
                )
            return _bounded_observation(result)
        return _bounded_observation(
            self._gate.invoke(
                ToolCallRequest(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=normalized_arguments,
                    context=bound.context,
                    lease_token=bound.lease_token,
                )
            )
        )

    def dispatch_active_tool(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ToolObservation:
        """Gate one wrapper call without generating or rewriting its call ID."""

        bound = self._require_context()
        tool_call_id = self._active_call_id
        if tool_call_id is None:
            raise AgentAdapterError(
                ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
                "governed tool was invoked outside the adapter call boundary",
            )
        observation = _bounded_observation(
            self._gate.invoke(
                ToolCallRequest(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=_arguments_object(arguments),
                    context=bound.context,
                    lease_token=bound.lease_token,
                )
            )
        )
        return observation

    def _require_context(self) -> _CallContext:
        if self._context is None:
            raise AgentAdapterError(
                ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
                "no active trusted Agent call context",
            )
        return self._context


class HarnessToolCallingAgent(ToolCallingAgent):
    """ToolCallingAgent with an ID-aware public process_tool_calls boundary."""

    def __init__(
        self,
        *,
        tools: list[Tool],
        model: Model,
        adapter: HarnessAgentAdapter,
        step_callbacks: list[Callable[..., Any]] | None = None,
        final_answer_checks: list[Callable[..., Any]] | None = None,
        max_steps: int = 20,
    ) -> None:
        self._harness_adapter = adapter
        super().__init__(
            tools=tools,
            model=model,
            max_tool_threads=1,
            step_callbacks=step_callbacks,
            final_answer_checks=final_answer_checks,
            max_steps=max_steps,
            verbosity_level=LogLevel.OFF,
        )

    def process_tool_calls(
        self,
        chat_message: ChatMessage,
        memory_step: ActionStep,
    ) -> Generator[ToolCall | ToolOutput, None, None]:
        """Dispatch tool calls in model order while retaining their original IDs."""

        if chat_message.tool_calls is None:
            raise AgentAdapterError(
                ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
                "tool-call message has no tool calls",
            )
        calls: list[ToolCall] = []
        ids: set[str] = set()
        for chat_tool_call in chat_message.tool_calls:
            call = ToolCall(
                name=chat_tool_call.function.name,
                arguments=chat_tool_call.function.arguments,
                id=chat_tool_call.id,
            )
            if call.id in ids:
                raise AgentAdapterError(
                    ErrorCode.SMOLAGENTS_DUPLICATE_TOOL_CALL_ID,
                    "tool-call IDs must be unique within one model response",
                )
            ids.add(call.id)
            calls.append(call)

        outputs: list[ToolOutput] = []
        for call in calls:
            if self._harness_adapter.stop_requested:
                raise HarnessAgentStopSignal(self._harness_adapter._stop_reason or "STOPPED")
            yield call
            if call.name == "final_answer":
                result: object = self.execute_tool_call(call.name, call.arguments or {})
            else:
                result = self._harness_adapter.invoke(
                    call.id,
                    call.name,
                    call.arguments or {},
                )
            observation = _observation_text(result)
            output = ToolOutput(
                id=call.id,
                output=result,
                is_final_answer=call.name == "final_answer",
                observation=observation,
                tool_call=call,
            )
            outputs.append(output)
            yield output

        memory_step.tool_calls = calls
        memory_step.observations = "\n".join(output.observation for output in outputs)


def assert_smolagents_compatibility() -> None:
    """Fail closed when the pinned public adaptation contract changes."""

    try:
        installed = distribution_version("smolagents")
    except Exception as exc:
        raise AgentAdapterError(
            ErrorCode.SMOLAGENTS_VERSION_MISMATCH,
            "smolagents distribution version is unavailable",
        ) from exc
    module_version = getattr(smolagents, "__version__", None)
    if installed != SMOLAGENTS_VERSION or module_version not in {None, SMOLAGENTS_VERSION}:
        raise AgentAdapterError(
            ErrorCode.SMOLAGENTS_VERSION_MISMATCH,
            f"smolagents must be exactly {SMOLAGENTS_VERSION}",
        )
    expected = {
        ToolCallingAgent.run: (
            "self",
            "task",
            "stream",
            "reset",
            "images",
            "additional_args",
            "max_steps",
            "return_full_result",
        ),
        ToolCallingAgent.process_tool_calls: (
            "self",
            "chat_message",
            "memory_step",
        ),
        Model.generate: (
            "self",
            "messages",
            "stop_sequences",
            "response_format",
            "tools_to_call_from",
            "kwargs",
        ),
    }
    for method, parameters in expected.items():
        actual = tuple(inspect.signature(method).parameters)
        if actual != parameters:
            raise AgentAdapterError(
                ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
                f"{method.__qualname__} signature changed",
            )
    required_call_fields = {"id", "name", "arguments"}
    if not required_call_fields.issubset(ToolCall.__dataclass_fields__):
        raise AgentAdapterError(
            ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
            "ToolCall public fields changed",
        )
    required_output_fields = {
        "id",
        "output",
        "is_final_answer",
        "observation",
        "tool_call",
    }
    if not required_output_fields.issubset(ToolOutput.__dataclass_fields__):
        raise AgentAdapterError(
            ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
            "ToolOutput public fields changed",
        )
    if not all(
        isinstance(item, type)
        for item in (ActionStep, PlanningStep, FinalAnswerStep, ActionOutput, ChatMessage)
    ):
        raise AgentAdapterError(
            ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
            "smolagents public step types are unavailable",
        )
    if not {"output", "is_final_answer"}.issubset(ActionOutput.__dataclass_fields__):
        raise AgentAdapterError(
            ErrorCode.SMOLAGENTS_ADAPTER_INCOMPATIBLE,
            "ActionOutput public fields changed",
        )


def build_tool_calling_agent(
    *,
    model: Model,
    adapter: HarnessAgentAdapter,
    step_callbacks: list[Callable[..., Any]] | None = None,
    final_answer_checks: list[Callable[..., Any]] | None = None,
    max_steps: int = 20,
) -> HarnessToolCallingAgent:
    """Construct the native ToolCallingAgent subclass from trusted inputs."""

    if not isinstance(model, Model):
        raise AgentAdapterError(
            ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
            "model must implement smolagents.Model",
        )
    return HarnessToolCallingAgent(
        tools=list(adapter.tools),
        model=model,
        adapter=adapter,
        step_callbacks=step_callbacks,
        final_answer_checks=final_answer_checks,
        max_steps=max_steps,
    )


def _tool_inputs_from_schema(schema: Mapping[str, JsonValue]) -> dict[str, dict[str, Any]]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise AgentAdapterError(
            ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
            "tool schema properties must be an object",
        )
    inputs: dict[str, dict[str, Any]] = {}
    for name, raw_property in properties.items():
        if not isinstance(raw_property, Mapping):
            raise AgentAdapterError(
                ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
                "tool schema property must be an object",
            )
        property_schema = dict(raw_property)
        raw_type = property_schema.get("type")
        if raw_type is None and isinstance(property_schema.get("anyOf"), list):
            types = [
                item.get("type")
                for item in property_schema["anyOf"]
                if isinstance(item, Mapping) and item.get("type") != "null"
            ]
            raw_type = types[0] if len(types) == 1 else types or "any"
        if isinstance(raw_type, list):
            input_type: str | list[str] = [str(item) for item in raw_type]
        else:
            input_type = str(raw_type or "any")
        property_schema["type"] = input_type
        property_schema["description"] = str(
            property_schema.get("description") or f"Argument {name}"
        )
        inputs[str(name)] = property_schema
    return inputs


def _arguments_object(value: object) -> dict[str, JsonValue]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}  # type: ignore[misc]
    invalid = {
        "_invalid_arguments": (value if isinstance(value, str) else json.dumps(value, default=str))
    }
    return invalid


def _bounded_observation(observation: ToolObservation) -> ToolObservation:
    encoded = observation.model_dump_json()
    if len(encoded) <= MAX_OBSERVATION_CHARS:
        return observation
    return observation.model_copy(
        update={
            "message": "tool observation exceeded the model-facing size limit",
            "output": None,
            "truncated": True,
        }
    )


def _observation_text(value: object) -> str:
    if isinstance(value, ToolObservation):
        return _bounded_observation(value).model_dump_json()
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
    except (TypeError, ValueError):
        return str(value)


__all__ = [
    "AgentAdapterError",
    "GovernedTool",
    "HarnessAgentAdapter",
    "HarnessAgentStopSignal",
    "HarnessToolCallingAgent",
    "SMOLAGENTS_VERSION",
    "assert_smolagents_compatibility",
    "build_tool_calling_agent",
]
