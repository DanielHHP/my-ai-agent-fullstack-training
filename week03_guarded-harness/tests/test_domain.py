"""Contract tests for strict public domain models and injectable helpers."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import guarded_harness as gh
from guarded_harness.domain import (
    BudgetUsage,
    DomainContractError,
    RunBudget,
    ToolCallId,
    WorkspaceIdentity,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc)
EXPIRES = datetime(2026, 1, 2, 4, 4, 5, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
TRACE_ID = "00000000000000000000000000000002"
STEP_ID = "step_00000000000000000000000000000003"
EVIDENCE_ID = "evidence_00000000000000000000000000000004"
APPROVAL_ID = "approval_00000000000000000000000000000005"
CHECKPOINT_ID = "checkpoint_00000000000000000000000000000006"
DIGEST = f"sha256:{hashlib.sha256(b'evidence').hexdigest()}"


def _workspace() -> WorkspaceIdentity:
    return WorkspaceIdentity(
        git_root="/tmp/repo",
        workspace_root="/tmp/repo/task",
        workspace_prefix="task",
        baseline_fingerprint=DIGEST,
        external_dirty_baseline_fingerprint=DIGEST,
    )


def _budget() -> RunBudget:
    return RunBudget(
        max_steps=10,
        max_tool_calls=20,
        max_token_cost=Decimal("1.25"),
        max_wall_time_seconds=300,
    )


def _run_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": RUN_ID,
        "trace_id": TRACE_ID,
        "task_id": "understand_login",
        "task_kind": gh.TaskKind.CODE_UNDERSTANDING,
        "task_description": "Explain the login flow",
        "config_fingerprint": DIGEST,
        "workspace": _workspace(),
        "budget": _budget(),
        "usage": BudgetUsage(),
        "created_at": NOW,
        "updated_at": NOW,
    }


def test_public_api_importable() -> None:
    expected = {
        "AcceptanceStatus",
        "Approval",
        "ApprovalChannel",
        "ApprovalDecision",
        "ApprovalStore",
        "ApprovalRequest",
        "Approver",
        "Clock",
        "CompletionCheck",
        "CompletionItem",
        "Checkpoint",
        "CheckpointBoundary",
        "CheckpointService",
        "ContinuationContext",
        "ConfigurationError",
        "ErrorCode",
        "Evidence",
        "FaultInjector",
        "Gate",
        "HandlerResult",
        "IdFactory",
        "PolicyEngine",
        "RecoveryAction",
        "RecoveryPlan",
        "RecoveryStatus",
        "Run",
        "RunStatus",
        "Step",
        "StopReason",
        "TaskKind",
        "ToolInvocation",
        "ToolExecutionContext",
        "ToolObservation",
        "ToolRegistry",
        "ToolSpec",
        "load_config",
    }
    assert expected.issubset(set(gh.__all__))
    assert len(gh.__all__) == len(set(gh.__all__))
    assert gh.__version__ == "0.1.0"


def test_pyproject_is_the_single_dependency_source() -> None:
    project_root = Path(__file__).resolve().parents[1]
    requires = metadata.requires("guarded-harness") or []
    assert not (project_root / "requirements.txt").exists()
    assert any(requirement.startswith("smolagents==1.26.0") for requirement in requires)
    assert any(requirement.startswith("jsonschema") for requirement in requires)
    assert any(
        requirement.startswith("pytest") and 'extra == "dev"' in requirement
        for requirement in requires
    )
    assert any(
        requirement.startswith("ruff") and 'extra == "dev"' in requirement
        for requirement in requires
    )


def test_models_forbid_unknown_and_missing() -> None:
    with pytest.raises(DomainContractError) as unknown:
        gh.validate_contract(
            gh.ToolObservation,
            {
                "status": gh.ObservationStatus.DENIED,
                "code": "GATE_INVALID_ARGUMENT",
                "tool_call_id": "call-1",
                "phase": gh.GatePhase.SCHEMA,
                "message": "invalid arguments",
                "injected": True,
            },
        )
    assert unknown.value.code == gh.ErrorCode.DOMAIN_INVALID_MODEL
    assert any(issue.path == "injected" for issue in unknown.value.issues)

    with pytest.raises(DomainContractError) as missing:
        gh.validate_contract(gh.ToolObservation, {"status": "DENIED"})
    assert missing.value.code == gh.ErrorCode.DOMAIN_INVALID_MODEL
    assert missing.value.issues


def test_domain_models_round_trip() -> None:
    run = gh.Run.model_validate(_run_payload())
    serialized = gh.canonical_json_dump(run)
    restored = gh.Run.model_validate_json(serialized, strict=True)
    assert gh.canonical_json_dump(restored) == serialized
    assert restored.created_at == NOW
    assert '"schema_version":1' in serialized
    assert '"created_at":"2026-01-02T03:04:05.000000Z"' in serialized


def test_public_domain_models_contain_required_fields() -> None:
    tool = gh.ToolSpec(
        name="read_file",
        description="Read a bounded file range.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        output_kind=gh.ToolOutputKind.JSON,
        risk=gh.ToolRisk.READ,
        required_permissions=frozenset({gh.ToolPermission.READ}),
        timeout_seconds=10,
        retry_policy=gh.RetryPolicy.SAFE_READ_ONLY,
        idempotent=True,
        path_arguments=("path",),
        output_limit_bytes=8192,
        approval_requirement=gh.ApprovalRequirement.NEVER,
        concurrency_class=gh.ConcurrencyClass.READ_ONLY_PARALLEL,
    )
    assert tool.required_permissions == frozenset({gh.ToolPermission.READ})

    invocation = gh.ToolInvocation(
        tool_call_id="provider-call-1",
        run_id=RUN_ID,
        step_id=STEP_ID,
        tool_name="read_file",
        raw_args={"path": "src/app.py"},
        normalized_args={"path": "src/app.py"},
        args_digest=DIGEST,
        risk=gh.ToolRisk.READ,
        required_permissions=frozenset({gh.ToolPermission.READ}),
        idempotent=True,
        created_at=NOW,
        updated_at=LATER,
    )
    assert invocation.tool_call_id == "provider-call-1"

    observation = gh.ToolObservation(
        status=gh.ObservationStatus.DENIED,
        code="GATE_TOOL_NOT_VISIBLE",
        tool_call_id="provider-call-1",
        phase=gh.GatePhase.VISIBILITY,
        message="Tool is not visible to this task.",
    )
    assert observation.retryable is False
    assert observation.schema_version == 2
    assert observation.evidence_ids == ()

    approval = gh.Approval(
        approval_id=APPROVAL_ID,
        run_id=RUN_ID,
        tool_call_id="provider-call-1",
        task_id="understand_login",
        task_kind=gh.TaskKind.CODE_UNDERSTANDING,
        git_root="/tmp/repo",
        workspace_root="/tmp/repo/task",
        workspace_prefix="task",
        tool_name="write_file",
        canonical_args_digest=DIGEST,
        binding_digest=DIGEST,
        config_fingerprint=DIGEST,
        requested_by="agent",
        allowed_approver_roles=("operator",),
        requested_at=NOW,
        expires_at=EXPIRES,
    )
    assert approval.schema_version == 2
    assert approval.status == gh.ApprovalStatus.PENDING
    serialized_approval = gh.canonical_json_dump(approval)
    restored_approval = gh.Approval.model_validate_json(serialized_approval, strict=True)
    assert gh.canonical_json_dump(restored_approval) == serialized_approval

    handler_result = gh.HandlerResult(
        status=gh.HandlerResultStatus.SUCCEEDED,
        output={"ok": True},
        summary="ok",
    )
    assert handler_result.status == gh.HandlerResultStatus.SUCCEEDED

    evidence = gh.Evidence(
        evidence_id=EVIDENCE_ID,
        run_id=RUN_ID,
        kind=gh.EvidenceKind.READ,
        path="src/app.py",
        line_start=1,
        line_end=10,
        content_digest=DIGEST,
        created_at=NOW,
    )
    item = gh.CompletionItem(
        item_id="deliverable_exists",
        passed=True,
        code="OK",
        message="Deliverable exists.",
        evidence_ids=(evidence.evidence_id,),
    )
    completion = gh.CompletionCheck(
        run_id=RUN_ID,
        acceptance_status=gh.AcceptanceStatus.ACCEPTED,
        items=(item,),
        checked_at=LATER,
    )
    assert completion.acceptance_status == gh.AcceptanceStatus.ACCEPTED

    serialized_tool = gh.canonical_json_dump(tool)
    restored_tool = gh.ToolSpec.model_validate_json(serialized_tool, strict=True)
    assert gh.canonical_json_dump(restored_tool) == serialized_tool
    assert '"required_permissions":["READ"]' in serialized_tool


def test_run_plan_rejects_non_contiguous_items() -> None:
    with pytest.raises(ValidationError):
        gh.RunPlan(
            run_id=RUN_ID,
            revision=0,
            items=(
                gh.PlanItem(ordinal=0, summary="first"),
                gh.PlanItem(ordinal=2, summary="third"),
            ),
            updated_at=NOW,
        )


def test_enum_id_and_time_boundaries() -> None:
    invalid_enum = _run_payload()
    invalid_enum["task_kind"] = "unknown_task"
    with pytest.raises(DomainContractError) as enum_error:
        gh.validate_contract(gh.Run, invalid_enum)
    assert enum_error.value.code == gh.ErrorCode.DOMAIN_ENUM_INVALID

    invalid_time = _run_payload()
    invalid_time["created_at"] = NOW.replace(tzinfo=None)
    with pytest.raises(ValidationError):
        gh.Run.model_validate(invalid_time)

    invalid_offset = _run_payload()
    invalid_offset["created_at"] = NOW.astimezone(timezone(timedelta(hours=8)))
    with pytest.raises(ValidationError):
        gh.Run.model_validate(invalid_offset)

    invalid_id = _run_payload()
    invalid_id["run_id"] = "not-a-run-id"
    with pytest.raises(DomainContractError) as id_error:
        gh.validate_contract(gh.Run, invalid_id)
    assert id_error.value.code == gh.ErrorCode.DOMAIN_ID_INVALID

    with pytest.raises(ValidationError):
        gh.ToolObservation.model_validate(
            {
                "status": gh.ObservationStatus.DENIED,
                "code": "GATE_INVALID_ARGUMENT",
                "tool_call_id": "bad\x00id",
                "phase": gh.GatePhase.SCHEMA,
                "message": "invalid",
            }
        )

    unsupported_schema = _run_payload()
    unsupported_schema["schema_version"] = 2
    with pytest.raises(DomainContractError) as schema_error:
        gh.validate_contract(gh.Run, unsupported_schema)
    assert schema_error.value.code == gh.ErrorCode.DOMAIN_SCHEMA_VERSION_UNSUPPORTED

    over_budget = _run_payload()
    over_budget["usage"] = BudgetUsage(steps=11)
    with pytest.raises(DomainContractError) as budget_error:
        gh.validate_contract(gh.Run, over_budget)
    assert budget_error.value.code == gh.ErrorCode.DOMAIN_INVALID_MODEL


def test_clock_ids_and_fault_injector_contract(
    fixed_clock: Any,
    fixed_ids: Any,
    fault_injector: Any,
) -> None:
    assert isinstance(fixed_clock, gh.Clock)
    assert isinstance(fixed_ids, gh.IdFactory)
    assert isinstance(fault_injector, gh.FaultInjector)

    first_time = fixed_clock.now()
    second_time = fixed_clock.now()
    assert first_time < second_time
    assert first_time.utcoffset() == timedelta(0)

    run_id = fixed_ids.new_run_id()
    trace_id = fixed_ids.new_trace_id()
    gh.validate_contract(gh.Run, {**_run_payload(), "run_id": run_id, "trace_id": trace_id})
    assert run_id != trace_id

    assert fault_injector.call_count("handler.before") == 0
    assert fault_injector.record("handler.before") == 1
    fault_injector.arm("handler.after")
    with pytest.raises(RuntimeError, match="handler.after"):
        fault_injector.trigger("handler.after")
    fault_injector.disable("handler.after")
    fault_injector.trigger("handler.after")


def test_canonical_json_sorts_maps_but_preserves_lists() -> None:
    first = gh.ToolObservation(
        status=gh.ObservationStatus.ALLOWED,
        code="OK",
        tool_call_id="call-1",
        phase=gh.GatePhase.EXECUTION,
        message="allowed",
        output={"z": 1, "a": [2, 1]},
    )
    second = gh.ToolObservation(
        output={"a": [2, 1], "z": 1},
        message="allowed",
        phase=gh.GatePhase.EXECUTION,
        tool_call_id="call-1",
        code="OK",
        status=gh.ObservationStatus.ALLOWED,
    )
    assert gh.canonical_json_dump(first) == gh.canonical_json_dump(second)
    assert '"a":[2,1]' in gh.canonical_json_dump(first)
    assert ToolCallId("unchanged-call-id") == "unchanged-call-id"
