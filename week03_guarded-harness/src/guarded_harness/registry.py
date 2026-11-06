"""Trusted tool registration and model-visible metadata snapshots."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .domain import (
    ErrorCode,
    HandlerResult,
    JsonValue,
    ToolExecutionContext,
    ToolName,
    ToolSpec,
)

if TYPE_CHECKING:
    from .policy import PolicyEngine


@runtime_checkable
class ToolHandler(Protocol):
    """Execute one already-authorized tool invocation."""

    def __call__(
        self,
        normalized_args: dict[str, JsonValue],
        context: ToolExecutionContext,
    ) -> HandlerResult:
        """Return a structured result without writing State or Audit."""


JsonNormalizer = Callable[[dict[str, JsonValue], ToolExecutionContext], dict[str, JsonValue]]


class ToolRegistryError(RuntimeError):
    """Stable trusted-registry failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


@dataclass(frozen=True)
class ToolDefinition:
    """A trusted tool spec paired with its non-public implementation."""

    spec: ToolSpec
    handler: ToolHandler
    normalize_args: JsonNormalizer | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.spec, ToolSpec):
            raise ToolRegistryError(
                ErrorCode.REGISTRY_INVALID_DEFINITION,
                "tool spec must be a validated ToolSpec",
            )
        if not callable(self.handler):
            raise ToolRegistryError(
                ErrorCode.REGISTRY_INVALID_DEFINITION,
                "tool handler must be callable",
            )
        if self.normalize_args is not None and not callable(self.normalize_args):
            raise ToolRegistryError(
                ErrorCode.REGISTRY_INVALID_DEFINITION,
                "tool normalizer must be callable or None",
            )
        _validate_input_schema(self.spec)


class ToolRegistry:
    """One trusted registry whose snapshots never expose handlers."""

    def __init__(self) -> None:
        self._definitions: dict[ToolName, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """Register one immutable trusted tool definition."""

        if not isinstance(definition, ToolDefinition):
            raise ToolRegistryError(
                ErrorCode.REGISTRY_INVALID_DEFINITION,
                "registry accepts ToolDefinition objects only",
            )
        if definition.spec.name in self._definitions:
            raise ToolRegistryError(
                ErrorCode.REGISTRY_TOOL_CONFLICT,
                "tool name is already registered",
            )
        self._definitions[definition.spec.name] = definition

    def get(self, tool_name: str) -> ToolDefinition:
        """Return one trusted definition or fail with a stable error."""

        definition = self._definitions.get(tool_name)
        if definition is None:
            raise ToolRegistryError(ErrorCode.GATE_TOOL_NOT_FOUND, "tool is not registered")
        return definition

    def snapshot(
        self,
        context: ToolExecutionContext,
        policy: PolicyEngine,
    ) -> tuple[ToolSpec, ...]:
        """Return sorted model-visible specs without implementation details."""

        return tuple(
            definition.spec.model_copy(deep=True)
            for name, definition in sorted(self._definitions.items())
            if policy.is_visible(context, name)
        )

    def names(self) -> tuple[ToolName, ...]:
        """Return registered names in stable order."""

        return tuple(sorted(self._definitions))

    def contains(self, tool_name: str) -> bool:
        """Return whether a tool name is registered."""

        return tool_name in self._definitions


def validate_tool_arguments(spec: ToolSpec, arguments: object) -> None:
    """Validate one argument object against the registration schema."""

    validator = Draft202012Validator(
        spec.input_schema,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )
    validator.validate(arguments)


def _validate_input_schema(spec: ToolSpec) -> None:
    schema = spec.input_schema
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ToolRegistryError(
            ErrorCode.REGISTRY_INVALID_DEFINITION,
            "tool input schema is invalid",
        ) from exc
    if schema.get("type") != "object":
        raise ToolRegistryError(
            ErrorCode.REGISTRY_INVALID_DEFINITION,
            "tool input schema root must be an object",
        )
    if schema.get("additionalProperties") is not False:
        raise ToolRegistryError(
            ErrorCode.REGISTRY_INVALID_DEFINITION,
            "tool input schema must reject additional properties",
        )
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ToolRegistryError(
            ErrorCode.REGISTRY_INVALID_DEFINITION,
            "tool input schema properties must be an object",
        )
    path_arguments = set(spec.path_arguments)
    missing_paths = path_arguments.difference(properties)
    if missing_paths:
        raise ToolRegistryError(
            ErrorCode.REGISTRY_INVALID_DEFINITION,
            "path_arguments must reference declared schema properties",
        )
    if spec.output_limit_bytes > 65_536:
        raise ToolRegistryError(
            ErrorCode.REGISTRY_INVALID_DEFINITION,
            "tool output limit must not exceed the platform limit",
        )
