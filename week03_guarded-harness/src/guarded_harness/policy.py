"""Deterministic visibility, role, mode, risk, and approval policy."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .domain import (
    ApprovalRequirement,
    ErrorCode,
    ExecutionMode,
    ToolExecutionContext,
    ToolPermission,
    ToolRisk,
    ToolSpec,
)


@dataclass(frozen=True)
class PolicyDecision:
    """One bounded policy result with a stable rule code."""

    allowed: bool
    code: str
    message: str


class PolicyError(RuntimeError):
    """Stable trusted-policy configuration failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


class PolicyEngine:
    """Conservative policy consumed by Gate and model-tool snapshots."""

    def __init__(
        self,
        *,
        allowed_tool_names: Iterable[str],
        role_permissions: Mapping[str, Iterable[ToolPermission]],
        task_approval_tools: Iterable[str] = (),
        approval_roles: Iterable[str] = (),
        approval_ttl_seconds: int = 900,
        read_execution_limit: int = 2,
        write_execution_limit: int = 1,
    ) -> None:
        self._allowed_tools = frozenset(_validated_names(allowed_tool_names))
        if not self._allowed_tools:
            raise PolicyError(
                ErrorCode.POLICY_CONFIG_INVALID,
                "allowed_tool_names must not be empty",
            )
        normalized_permissions: dict[str, frozenset[ToolPermission]] = {}
        for role, permissions in role_permissions.items():
            normalized_role = _validated_role(role)
            try:
                values = frozenset(permissions)
            except TypeError as exc:
                raise PolicyError(
                    ErrorCode.POLICY_CONFIG_INVALID,
                    "role permissions must be iterable",
                ) from exc
            if any(not isinstance(permission, ToolPermission) for permission in values):
                raise PolicyError(
                    ErrorCode.POLICY_CONFIG_INVALID,
                    "role permissions must use ToolPermission values",
                )
            if normalized_role in normalized_permissions:
                raise PolicyError(
                    ErrorCode.POLICY_CONFIG_INVALID,
                    "role names must be unique ignoring case",
                )
            normalized_permissions[normalized_role] = values
        self._role_permissions = MappingProxyType(normalized_permissions)
        self._approval_tools = frozenset(_validated_names(task_approval_tools))
        self._approval_roles = tuple(_validated_roles(approval_roles))
        if type(approval_ttl_seconds) is not int or not 1 <= approval_ttl_seconds <= 86_400:
            raise PolicyError(
                ErrorCode.POLICY_CONFIG_INVALID,
                "approval_ttl_seconds must be between 1 and 86400",
            )
        self._approval_ttl_seconds = approval_ttl_seconds
        if type(read_execution_limit) is not int or read_execution_limit < 0:
            raise PolicyError(
                ErrorCode.POLICY_CONFIG_INVALID,
                "read_execution_limit must be a non-negative integer",
            )
        if type(write_execution_limit) is not int or write_execution_limit < 0:
            raise PolicyError(
                ErrorCode.POLICY_CONFIG_INVALID,
                "write_execution_limit must be a non-negative integer",
            )
        self._read_execution_limit = read_execution_limit
        self._write_execution_limit = write_execution_limit

    def is_visible(self, context: ToolExecutionContext, tool_name: str) -> bool:
        """Return whether a tool is visible for this trusted task context."""

        return tool_name in self._allowed_tools

    def permissions_for(self, context: ToolExecutionContext) -> frozenset[ToolPermission]:
        """Return the deterministic union of trusted role permissions."""

        permissions: set[ToolPermission] = set()
        for role in context.roles:
            permissions.update(self._role_permissions.get(role.casefold(), ()))
        return frozenset(permissions)

    def check_permission(
        self,
        context: ToolExecutionContext,
        spec: ToolSpec,
    ) -> PolicyDecision:
        """Require every declared tool permission from the role union."""

        missing = spec.required_permissions.difference(self.permissions_for(context))
        if missing:
            names = ",".join(sorted(permission.value for permission in missing))
            return PolicyDecision(
                allowed=False,
                code=ErrorCode.GATE_PERMISSION_DENIED.value,
                message=f"missing required permissions: {names}",
            )
        return PolicyDecision(True, ErrorCode.GATE_ALLOWED.value, "permissions satisfied")

    def check_risk(
        self,
        context: ToolExecutionContext,
        spec: ToolSpec,
    ) -> PolicyDecision:
        """Reject non-read tools in plan mode."""

        if context.mode == ExecutionMode.PLAN and spec.risk != ToolRisk.READ:
            return PolicyDecision(
                allowed=False,
                code=ErrorCode.GATE_MODE_DENIED.value,
                message="plan mode permits read-only tools only",
            )
        if spec.risk != ToolRisk.READ and not spec.required_permissions:
            return PolicyDecision(
                allowed=False,
                code=ErrorCode.GATE_RISK_DENIED.value,
                message="write or execute tools require an explicit permission",
            )
        return PolicyDecision(True, ErrorCode.GATE_ALLOWED.value, "risk policy satisfied")

    def requires_approval(self, context: ToolExecutionContext, spec: ToolSpec) -> bool:
        """Return whether either trusted tool or task policy requires approval."""

        del context
        return (
            spec.approval_requirement == ApprovalRequirement.REQUIRED
            or spec.name in self._approval_tools
        )

    def execution_limit(self, spec: ToolSpec) -> int:
        """Return the trusted in-run execution limit for this tool class."""

        return (
            self._read_execution_limit
            if spec.risk == ToolRisk.READ
            else self._write_execution_limit
        )

    @property
    def approval_roles(self) -> tuple[str, ...]:
        """Return the trusted approver roles for this task."""

        return self._approval_roles

    @property
    def approval_ttl_seconds(self) -> int:
        """Return the trusted approval TTL for this task."""

        return self._approval_ttl_seconds


def _validated_names(values: Iterable[str]) -> tuple[str, ...]:
    names = tuple(values)
    if any(not isinstance(name, str) or not name or name != name.strip() for name in names):
        raise PolicyError(
            ErrorCode.POLICY_CONFIG_INVALID,
            "tool names must be non-empty trimmed strings",
        )
    if len(set(names)) != len(names):
        raise PolicyError(ErrorCode.POLICY_CONFIG_INVALID, "tool names must be unique")
    for name in names:
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) is None:
            raise PolicyError(
                ErrorCode.POLICY_CONFIG_INVALID,
                "tool names must use the registered tool-name format",
            )
    return names


def _validated_role(role: str) -> str:
    if not isinstance(role, str) or not role or role != role.strip():
        raise PolicyError(
            ErrorCode.POLICY_CONFIG_INVALID,
            "role names must be non-empty trimmed strings",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in role):
        raise PolicyError(
            ErrorCode.POLICY_CONFIG_INVALID,
            "role names must not contain control characters",
        )
    return role.casefold()


def _validated_roles(roles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(_validated_role(role) for role in roles)
    if len(set(normalized)) != len(normalized):
        raise PolicyError(
            ErrorCode.POLICY_CONFIG_INVALID,
            "approval roles must be unique ignoring case",
        )
    return normalized
