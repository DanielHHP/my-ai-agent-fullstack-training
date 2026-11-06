"""Registry and policy contract tests for WP-04."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from guarded_harness import (
    ApprovalRequirement,
    ConcurrencyClass,
    ErrorCode,
    ExecutionMode,
    HandlerResult,
    HandlerResultStatus,
    PolicyEngine,
    PolicyError,
    RetryPolicy,
    TaskKind,
    ToolDefinition,
    ToolExecutionContext,
    ToolOutputKind,
    ToolPermission,
    ToolRegistry,
    ToolRegistryError,
    ToolRisk,
    ToolSpec,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
STEP_ID = "step_00000000000000000000000000000002"


def _context(
    *,
    roles: tuple[str, ...] = ("developer",),
    mode: ExecutionMode = ExecutionMode.EXECUTE,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=RUN_ID,
        step_id=STEP_ID,
        task_id="understand_login",
        task_kind=TaskKind.CODE_UNDERSTANDING,
        mode=mode,
        actor="agent",
        roles=roles,
    )


def _spec(
    *,
    name: str = "read_file",
    risk: ToolRisk = ToolRisk.READ,
    permissions: frozenset[ToolPermission] = frozenset({ToolPermission.READ}),
    approval: ApprovalRequirement = ApprovalRequirement.NEVER,
    idempotent: bool = True,
    path_arguments: tuple[str, ...] = ("path",),
    schema: dict | None = None,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description="Test tool.",
        input_schema=schema
        or {
            "type": "object",
            "properties": {"path": {"type": "string", "minLength": 1}},
            "required": ["path"],
            "additionalProperties": False,
        },
        output_kind=ToolOutputKind.JSON,
        risk=risk,
        required_permissions=permissions,
        timeout_seconds=10,
        retry_policy=RetryPolicy.SAFE_READ_ONLY if idempotent else RetryPolicy.NEVER,
        idempotent=idempotent,
        path_arguments=path_arguments,
        output_limit_bytes=4096,
        approval_requirement=approval,
        concurrency_class=(
            ConcurrencyClass.READ_ONLY_PARALLEL
            if risk == ToolRisk.READ
            else ConcurrencyClass.SERIAL
        ),
    )


def _handler(_args, _context) -> HandlerResult:
    return HandlerResult(
        status=HandlerResultStatus.SUCCEEDED,
        output={"ok": True},
        summary="ok",
    )


def _policy(
    *,
    allowed: tuple[str, ...] = ("read_file",),
    approval_tools: tuple[str, ...] = (),
) -> PolicyEngine:
    return PolicyEngine(
        allowed_tool_names=allowed,
        role_permissions={
            "developer": {
                ToolPermission.READ,
                ToolPermission.WRITE,
                ToolPermission.EXECUTE,
            }
        },
        task_approval_tools=approval_tools,
        approval_roles=("operator",),
    )


def test_registry_snapshot_contains_only_model_safe_specs() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(spec=_spec(), handler=_handler))

    specs = registry.snapshot(_context(), _policy())

    assert tuple(spec.name for spec in specs) == ("read_file",)
    dumped = specs[0].model_dump(mode="json")
    assert "handler" not in dumped
    assert "normalize_args" not in dumped
    assert registry.get("read_file").handler is _handler


def test_registry_rejects_duplicate_and_invalid_schemas() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(spec=_spec(), handler=_handler))
    with pytest.raises(ToolRegistryError) as duplicate:
        registry.register(ToolDefinition(spec=_spec(), handler=_handler))
    assert duplicate.value.code == ErrorCode.REGISTRY_TOOL_CONFLICT

    permissive = _spec(
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
        }
    )
    with pytest.raises(ToolRegistryError) as invalid:
        ToolDefinition(spec=permissive, handler=_handler)
    assert invalid.value.code == ErrorCode.REGISTRY_INVALID_DEFINITION


def test_policy_visibility_permissions_mode_risk_and_approval() -> None:
    policy = _policy(allowed=("read_file", "write_file"), approval_tools=("write_file",))
    read = _spec()
    write = _spec(
        name="write_file",
        risk=ToolRisk.WRITE,
        permissions=frozenset({ToolPermission.WRITE}),
    )
    context = _context()

    assert policy.is_visible(context, "read_file")
    assert not policy.is_visible(context, "git_status")
    assert policy.check_permission(context, read).allowed
    denied = policy.check_permission(_context(roles=("observer",)), read)
    assert not denied.allowed
    assert denied.code == ErrorCode.GATE_PERMISSION_DENIED.value
    plan_write = policy.check_risk(
        _context(mode=ExecutionMode.PLAN),
        write,
    )
    assert not plan_write.allowed
    assert plan_write.code == ErrorCode.GATE_MODE_DENIED.value
    assert policy.requires_approval(context, write)
    assert not policy.requires_approval(context, read)


def test_policy_rejects_unbounded_repeat_limits() -> None:
    with pytest.raises(PolicyError) as invalid:
        PolicyEngine(
            allowed_tool_names=("read_file",),
            role_permissions={"developer": {ToolPermission.READ}},
            read_execution_limit=-1,
        )
    assert invalid.value.code == ErrorCode.POLICY_CONFIG_INVALID
