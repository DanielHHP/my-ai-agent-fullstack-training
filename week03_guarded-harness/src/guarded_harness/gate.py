"""Single governed execution entry for all tool calls."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from jsonschema.exceptions import ValidationError as JSONSchemaValidationError
from pydantic import JsonValue

from .approval import ApprovalStore, ApprovalStoreError, approval_args_digest
from .audit import (
    AuditDecision,
    AuditEventDraft,
    AuditEventType,
    AuditStatus,
)
from .domain import (
    Approval,
    ApprovalRequest,
    ApprovalStatus,
    BudgetCategory,
    BudgetDeltaCommand,
    BudgetSourceKind,
    Clock,
    ErrorCode,
    Evidence,
    GatePhase,
    HandlerResult,
    HandlerResultStatus,
    IdFactory,
    InvocationStatus,
    ObservationStatus,
    Run,
    RunSnapshot,
    RunStatus,
    ToolCallRequest,
    ToolInvocation,
    ToolObservation,
    ToolOutputKind,
    ToolRisk,
    Uuid4IdFactory,
)
from .policy import PolicyEngine
from .redaction import RedactionError, Redactor
from .registry import ToolDefinition, ToolRegistry, ToolRegistryError, validate_tool_arguments
from .sandbox import Sandbox, SandboxViolation
from .state_store import StateStoreError
from .storage import SQLiteStorage, StorageError

_DETERMINISTIC_STATUSES = frozenset(
    {
        InvocationStatus.SUCCEEDED,
        InvocationStatus.FAILED,
        InvocationStatus.TIMED_OUT,
    }
)
_TERMINAL_STATUSES = _DETERMINISTIC_STATUSES | {InvocationStatus.UNKNOWN_RESULT}


@dataclass(frozen=True)
class _GateDenial(Exception):
    code: ErrorCode
    phase: GatePhase
    message: str
    rule_id: str
    approval_id: str | None = None
    evidence_ids: tuple[str, ...] = ()
    retryable: bool = False


@dataclass(frozen=True)
class _PreparedCall:
    definition: ToolDefinition
    normalized_args: dict[str, JsonValue]
    args_digest: str


class Gate:
    """Validate, authorize, persist, and execute one tool call."""

    def __init__(
        self,
        *,
        storage: SQLiteStorage,
        registry: ToolRegistry,
        policy: PolicyEngine,
        sandbox: Sandbox,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._storage = storage
        self._registry = registry
        self._policy = policy
        self._sandbox = sandbox
        self._clock = clock or storage.clock
        self._ids = id_factory or Uuid4IdFactory()
        self._redactor = Redactor()
        self._lock_guard = threading.Lock()
        self._run_locks: dict[str, threading.Lock] = {}

    def invoke(self, request: ToolCallRequest) -> ToolObservation:
        """Run one candidate tool call through the fixed Gate chain."""

        if not isinstance(request, ToolCallRequest):
            raise TypeError("request must be a ToolCallRequest")
        with self._run_lock(request.context.run_id):
            try:
                return self._invoke_locked(request)
            except _GateDenial as denial:
                return self._deny_observation(request, denial)
            except Exception as exc:
                denial = _GateDenial(
                    ErrorCode.GATE_INTERNAL_ERROR,
                    GatePhase.EXECUTION,
                    _safe_exception_message(exc),
                    "GATE_INTERNAL_V1",
                )
                return self._deny_observation(request, denial)

    def _invoke_locked(self, request: ToolCallRequest) -> ToolObservation:
        definition = self._lookup_definition(request)
        snapshot = self._read_snapshot(request)
        run = snapshot.run
        self._validate_context(request, run)
        approval = _approval_for_call(snapshot, request.tool_call_id)

        if run.status in {RunStatus.COMPLETED, RunStatus.STOPPED, RunStatus.FAILED}:
            if approval is not None and approval.status in {
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
            }:
                raise _GateDenial(
                    _approval_error_code(approval.status),
                    GatePhase.APPROVAL,
                    "approval has a terminal denial",
                    "GATE_APPROVAL_V1",
                    approval_id=approval.approval_id,
                )
            raise _GateDenial(
                ErrorCode.GATE_INTERNAL_ERROR,
                GatePhase.VISIBILITY,
                "terminal runs cannot execute tools",
                "GATE_RUN_STATE_V1",
            )
        if run.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
            raise _GateDenial(
                ErrorCode.GATE_INTERNAL_ERROR,
                GatePhase.VISIBILITY,
                "run state does not permit tool execution",
                "GATE_RUN_STATE_V1",
            )

        self._check_visibility(request, snapshot, definition)
        normalized = self._validate_and_normalize(request, definition)
        self._check_permission_and_risk(request, definition)

        existing = _invocation_by_call_id(snapshot, request.tool_call_id)
        if existing is not None and (
            existing.tool_name != request.tool_name
            or existing.args_digest != normalized.args_digest
        ):
            raise _GateDenial(
                ErrorCode.GATE_TOOL_CALL_ID_CONFLICT,
                GatePhase.VISIBILITY,
                "tool_call_id was already used with different tool or arguments",
                "GATE_TOOL_CALL_ID_V1",
            )

        approval_required = self._policy.requires_approval(request.context, definition.spec)

        if existing is not None and existing.status == InvocationStatus.STARTED:
            raise _GateDenial(
                ErrorCode.GATE_RESULT_UNKNOWN,
                GatePhase.EXECUTION,
                "tool invocation has started and has no confirmed result",
                "GATE_STARTED_UNKNOWN_V1",
            )

        if existing is not None and existing.status in _TERMINAL_STATUSES:
            return self._duplicate_result_observation(request, existing)

        if existing is None:
            self._preempt_completed_write_replay(snapshot, normalized)

        if approval is not None and approval.status == ApprovalStatus.PENDING:
            return self._wait_for_existing_approval(request, approval)
        if approval is not None and approval.status in {
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.INVALIDATED,
            ApprovalStatus.CONSUMED,
            ApprovalStatus.CLAIMED,
        }:
            raise _GateDenial(
                _approval_error_code(approval.status),
                GatePhase.APPROVAL,
                "approval is not executable",
                "GATE_APPROVAL_V1",
                approval_id=approval.approval_id,
            )

        if approval_required and approval is None:
            return self._open_approval(request, normalized, existing)
        if (
            approval_required
            and approval is not None
            and approval.status != ApprovalStatus.APPROVED
        ):
            raise _GateDenial(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                GatePhase.APPROVAL,
                "approval is not approved",
                "GATE_APPROVAL_V1",
                approval_id=approval.approval_id,
            )

        if (
            existing is not None
            and existing.status == InvocationStatus.PREPARED
            and approval is None
        ):
            raise _GateDenial(
                ErrorCode.GATE_TOOL_CALL_ID_CONFLICT,
                GatePhase.EXECUTION,
                "unapproved prepared invocation requires explicit recovery",
                "GATE_PREPARED_RECOVERY_V1",
            )

        self._check_repeat_and_replay(request, snapshot, normalized)
        self._check_budget(request, snapshot)

        if approval is not None:
            self._claim_and_allow(request, normalized, approval)
        else:
            self._prepare_and_allow(request, normalized)

        self._mark_started(request)
        return self._execute(request, normalized, approval)

    def _lookup_definition(self, request: ToolCallRequest) -> ToolDefinition:
        try:
            return self._registry.get(request.tool_name)
        except ToolRegistryError as exc:
            raise _GateDenial(
                ErrorCode.GATE_TOOL_NOT_FOUND,
                GatePhase.VISIBILITY,
                exc.message,
                "GATE_VISIBILITY_V1",
            ) from exc

    def _read_snapshot(self, request: ToolCallRequest) -> RunSnapshot:
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                unit.state.verify_lease(request.context.run_id, request.lease_token)
                return unit.state.get_run_snapshot(request.context.run_id)
        except (StateStoreError, StorageError) as exc:
            raise _GateDenial(
                ErrorCode.GATE_INTERNAL_ERROR,
                GatePhase.VISIBILITY,
                exc.message,
                "GATE_STATE_READ_V1",
            ) from exc

    def _validate_context(self, request: ToolCallRequest, run: Run) -> None:
        if request.context.task_id != run.task_id or request.context.task_kind != run.task_kind:
            raise _GateDenial(
                ErrorCode.GATE_PERMISSION_DENIED,
                GatePhase.PERMISSION,
                "trusted task context does not match the run",
                "GATE_CONTEXT_V1",
            )

    def _check_visibility(
        self,
        request: ToolCallRequest,
        snapshot: RunSnapshot,
        definition: ToolDefinition,
    ) -> None:
        del snapshot
        if not self._policy.is_visible(request.context, definition.spec.name):
            raise _GateDenial(
                ErrorCode.GATE_TOOL_NOT_VISIBLE,
                GatePhase.VISIBILITY,
                "tool is not visible to this task",
                "GATE_VISIBILITY_V1",
            )

    def _validate_and_normalize(
        self,
        request: ToolCallRequest,
        definition: ToolDefinition,
    ) -> _PreparedCall:
        arguments = _normalize_json_object(request.args)
        try:
            validate_tool_arguments(definition.spec, arguments)
        except JSONSchemaValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "arguments"
            raise _GateDenial(
                ErrorCode.GATE_INVALID_ARGUMENT,
                GatePhase.SCHEMA,
                f"invalid tool argument at {location}",
                "GATE_SCHEMA_V1",
            ) from exc

        if definition.normalize_args is not None:
            try:
                normalized = definition.normalize_args(
                    _deep_copy_json(arguments),
                    request.context,
                )
                arguments = _normalize_json_object(normalized)
                validate_tool_arguments(definition.spec, arguments)
            except JSONSchemaValidationError as exc:
                raise _GateDenial(
                    ErrorCode.GATE_SEMANTIC_INVALID,
                    GatePhase.SEMANTIC,
                    "trusted normalizer produced schema-invalid arguments",
                    "GATE_SEMANTIC_V1",
                ) from exc
            except SandboxViolation as exc:
                raise _GateDenial(
                    exc.code,
                    GatePhase.SEMANTIC,
                    exc.message,
                    "GATE_SEMANTIC_V1",
                ) from exc
            except Exception as exc:
                raise _GateDenial(
                    ErrorCode.GATE_SEMANTIC_INVALID,
                    GatePhase.SEMANTIC,
                    "trusted semantic normalization failed",
                    "GATE_SEMANTIC_V1",
                ) from exc

        self._normalize_paths(definition, arguments)
        if definition.spec.risk == ToolRisk.EXECUTE and set(arguments) != {"command_id"}:
            raise _GateDenial(
                ErrorCode.GATE_SEMANTIC_INVALID,
                GatePhase.SEMANTIC,
                "execute tools may receive only a trusted command_id",
                "GATE_COMMAND_V1",
            )
        try:
            digest = approval_args_digest(definition.spec.name, arguments)
        except ApprovalStoreError as exc:
            raise _GateDenial(
                ErrorCode.GATE_SEMANTIC_INVALID,
                GatePhase.SEMANTIC,
                exc.message,
                "GATE_SEMANTIC_V1",
            ) from exc
        return _PreparedCall(definition, arguments, digest)

    def _normalize_paths(
        self,
        definition: ToolDefinition,
        arguments: dict[str, JsonValue],
    ) -> None:
        for argument_name in definition.spec.path_arguments:
            value = arguments.get(argument_name)
            if not isinstance(value, str):
                raise _GateDenial(
                    ErrorCode.GATE_SEMANTIC_INVALID,
                    GatePhase.SEMANTIC,
                    "declared path argument must be a string",
                    "GATE_PATH_V1",
                )
            try:
                checked = (
                    self._sandbox.check_read(value)
                    if definition.spec.risk == ToolRisk.READ
                    else self._sandbox.check_write(value)
                )
            except SandboxViolation as exc:
                raise _GateDenial(
                    exc.code,
                    GatePhase.SEMANTIC,
                    exc.message,
                    "GATE_PATH_V1",
                ) from exc
            arguments[argument_name] = checked.workspace_relative

    def _check_permission_and_risk(
        self,
        request: ToolCallRequest,
        definition: ToolDefinition,
    ) -> None:
        permission = self._policy.check_permission(request.context, definition.spec)
        if not permission.allowed:
            raise _GateDenial(
                ErrorCode.GATE_PERMISSION_DENIED,
                GatePhase.PERMISSION,
                permission.message,
                "GATE_PERMISSION_V1",
            )
        risk = self._policy.check_risk(request.context, definition.spec)
        if not risk.allowed:
            phase = GatePhase.RISK
            raise _GateDenial(
                ErrorCode(risk.code),
                phase,
                risk.message,
                "GATE_MODE_V1" if risk.code == ErrorCode.GATE_MODE_DENIED.value else "GATE_RISK_V1",
            )

    def _open_approval(
        self,
        request: ToolCallRequest,
        prepared: _PreparedCall,
        existing_invocation: Any,
    ) -> ToolObservation:
        if not self._policy.approval_roles:
            raise _GateDenial(
                ErrorCode.APPROVAL_CHANNEL_UNAVAILABLE,
                GatePhase.APPROVAL,
                "task has no trusted approver roles",
                "GATE_APPROVAL_V1",
            )
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                unit.state.verify_lease(request.context.run_id, request.lease_token)
                run = unit.state.get_run(request.context.run_id)
                invocation = existing_invocation
                if invocation is None:
                    invocation_model = _new_invocation(
                        request,
                        prepared,
                        self._clock.now(),
                    )
                    invocation = unit.state.create_invocation(
                        invocation_model,
                        expected_run_revision=unit.state.get_run_revision(run.run_id),
                        lease_token=request.lease_token,
                    )
                else:
                    if invocation.status != InvocationStatus.PREPARED:
                        raise _GateDenial(
                            ErrorCode.APPROVAL_NOT_EXECUTABLE,
                            GatePhase.APPROVAL,
                            "approval requires a prepared invocation",
                            "GATE_APPROVAL_V1",
                        )
                approval = ApprovalStore(unit, id_factory=self._ids).create(
                    ApprovalRequest(
                        run_id=run.run_id,
                        tool_call_id=request.tool_call_id,
                        normalized_args=prepared.normalized_args,
                        requested_by=request.context.actor,
                        allowed_approver_roles=self._policy.approval_roles,
                        ttl_seconds=self._policy.approval_ttl_seconds,
                    ),
                    lease_token=request.lease_token,
                )
                if run.status == RunStatus.RUNNING:
                    unit.state.transition_run(
                        run.run_id,
                        expected_status=RunStatus.RUNNING,
                        expected_revision=unit.state.get_run_revision(run.run_id),
                        target=RunStatus.WAITING_APPROVAL,
                        stop_reason=None,
                        ended_at=None,
                        acceptance_status=None,
                        lease_token=request.lease_token,
                    )
                self._append_decision(
                    unit,
                    request,
                    phase=GatePhase.APPROVAL,
                    status=AuditStatus.PENDING,
                    decision=AuditDecision.NOT_APPLICABLE,
                    code=ErrorCode.APPROVAL_REQUIRED,
                    rule_id="GATE_APPROVAL_V1",
                    approval_id=approval.approval_id,
                    args_digest=prepared.args_digest,
                )
        except _GateDenial:
            raise
        except Exception as exc:
            raise _GateDenial(
                ErrorCode.GATE_INTENT_WRITE_FAILED,
                GatePhase.APPROVAL,
                _safe_exception_message(exc),
                "GATE_APPROVAL_V1",
            ) from exc
        return _observation(
            request,
            ObservationStatus.WAITING_APPROVAL,
            ErrorCode.APPROVAL_REQUIRED,
            GatePhase.APPROVAL,
            "approval is required before this tool can execute",
            approval_id=approval.approval_id,
        )

    def _wait_for_existing_approval(
        self,
        request: ToolCallRequest,
        approval: Approval,
    ) -> ToolObservation:
        current_status: ApprovalStatus
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                unit.state.verify_lease(request.context.run_id, request.lease_token)
                if unit.state.get_run(request.context.run_id).status != RunStatus.WAITING_APPROVAL:
                    raise _GateDenial(
                        ErrorCode.APPROVAL_STATE_INVALID,
                        GatePhase.APPROVAL,
                        "run is not waiting for approval",
                        "GATE_APPROVAL_V1",
                        approval_id=approval.approval_id,
                    )
                current = ApprovalStore(unit, id_factory=self._ids).load(
                    approval.approval_id,
                    lease_token=request.lease_token,
                )
                current_status = current.status
                if current_status == ApprovalStatus.PENDING:
                    self._append_decision(
                        unit,
                        request,
                        phase=GatePhase.APPROVAL,
                        status=AuditStatus.PENDING,
                        decision=AuditDecision.NOT_APPLICABLE,
                        code=ErrorCode.APPROVAL_REQUIRED,
                        rule_id="GATE_APPROVAL_V1",
                        approval_id=current.approval_id,
                    )
        except Exception as exc:
            raise _GateDenial(
                ErrorCode.GATE_INTERNAL_ERROR,
                GatePhase.APPROVAL,
                _safe_exception_message(exc),
                "GATE_APPROVAL_V1",
                approval_id=approval.approval_id,
            ) from exc
        if current_status == ApprovalStatus.APPROVED:
            return self._invoke_locked(request)
        if current_status != ApprovalStatus.PENDING:
            raise _GateDenial(
                _approval_error_code(current_status),
                GatePhase.APPROVAL,
                "approval changed while the tool call was waiting",
                "GATE_APPROVAL_V1",
                approval_id=approval.approval_id,
            )
        return _observation(
            request,
            ObservationStatus.WAITING_APPROVAL,
            ErrorCode.APPROVAL_REQUIRED,
            GatePhase.APPROVAL,
            "approval is still pending",
            approval_id=approval.approval_id,
        )

    def _claim_and_allow(
        self,
        request: ToolCallRequest,
        prepared: _PreparedCall,
        approval: Approval,
    ) -> None:
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                unit.state.verify_lease(request.context.run_id, request.lease_token)
                current = ApprovalStore(unit, id_factory=self._ids).load(
                    approval.approval_id,
                    lease_token=request.lease_token,
                )
                ApprovalStore(unit, id_factory=self._ids).claim(
                    current.approval_id,
                    expected_revision=current.revision,
                    execution_intent_id=request.tool_call_id,
                    lease_token=request.lease_token,
                )
                unit.state.transition_run(
                    request.context.run_id,
                    expected_status=RunStatus.WAITING_APPROVAL,
                    expected_revision=unit.state.get_run_revision(request.context.run_id),
                    target=RunStatus.RUNNING,
                    stop_reason=None,
                    ended_at=None,
                    acceptance_status=None,
                    lease_token=request.lease_token,
                )
                self._record_tool_budget(unit.state, request)
                self._append_decision(
                    unit,
                    request,
                    phase=GatePhase.EXECUTION,
                    status=AuditStatus.ALLOWED,
                    decision=AuditDecision.ALLOW,
                    code=ErrorCode.GATE_ALLOWED,
                    rule_id="GATE_ALLOW_V1",
                    approval_id=approval.approval_id,
                    args_digest=prepared.args_digest,
                )
        except _GateDenial:
            raise
        except Exception as exc:
            raise _GateDenial(
                ErrorCode.GATE_INTENT_WRITE_FAILED,
                GatePhase.APPROVAL,
                _safe_exception_message(exc),
                "GATE_APPROVAL_V1",
                approval_id=approval.approval_id,
            ) from exc

    def _prepare_and_allow(
        self,
        request: ToolCallRequest,
        prepared: _PreparedCall,
    ) -> None:
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                unit.state.verify_lease(request.context.run_id, request.lease_token)
                unit.state.create_invocation(
                    _new_invocation(request, prepared, self._clock.now()),
                    expected_run_revision=unit.state.get_run_revision(request.context.run_id),
                    lease_token=request.lease_token,
                )
                self._record_tool_budget(unit.state, request)
                self._append_decision(
                    unit,
                    request,
                    phase=GatePhase.EXECUTION,
                    status=AuditStatus.ALLOWED,
                    decision=AuditDecision.ALLOW,
                    code=ErrorCode.GATE_ALLOWED,
                    rule_id="GATE_ALLOW_V1",
                    args_digest=prepared.args_digest,
                )
        except _GateDenial:
            raise
        except Exception as exc:
            raise _GateDenial(
                ErrorCode.GATE_INTENT_WRITE_FAILED,
                GatePhase.EXECUTION,
                _safe_exception_message(exc),
                "GATE_INTENT_V1",
            ) from exc

    def _record_tool_budget(self, state: Any, request: ToolCallRequest) -> None:
        state.record_budget_delta(
            BudgetDeltaCommand(
                run_id=request.context.run_id,
                source_kind=BudgetSourceKind.TOOL_CALL,
                source_id=request.tool_call_id,
                category=BudgetCategory.TOOL_CALLS,
                expected_run_revision=state.get_run_revision(request.context.run_id),
                delta=Decimal(1),
            ),
            lease_token=request.lease_token,
        )

    def _mark_started(self, request: ToolCallRequest) -> None:
        try:
            if self._storage.fault_injector is not None:
                self._storage.fault_injector.trigger("gate:before-start")
            with self._storage.transaction(actor=request.context.actor) as unit:
                revision = unit.state.get_invocation_revision(
                    request.context.run_id,
                    request.tool_call_id,
                )
                unit.state.transition_invocation(
                    request.context.run_id,
                    request.tool_call_id,
                    expected_status=InvocationStatus.PREPARED,
                    expected_revision=revision,
                    target=InvocationStatus.STARTED,
                    result_summary=None,
                    error_code=None,
                    lease_token=request.lease_token,
                )
        except Exception as exc:
            raise _GateDenial(
                ErrorCode.GATE_START_WRITE_FAILED,
                GatePhase.EXECUTION,
                _safe_exception_message(exc),
                "GATE_START_V1",
            ) from exc

    def _execute(
        self,
        request: ToolCallRequest,
        prepared: _PreparedCall,
        approval: Approval | None,
    ) -> ToolObservation:
        started_at = self._clock.now()
        try:
            result = prepared.definition.handler(prepared.normalized_args, request.context)
            if not isinstance(result, HandlerResult):
                raise TypeError("handler did not return HandlerResult")
        except Exception:
            result = HandlerResult(
                status=HandlerResultStatus.UNKNOWN_RESULT,
                summary="tool handler raised without a confirmed side-effect state",
                error_code=ErrorCode.GATE_RESULT_UNKNOWN.value,
            )
        finished_at = self._clock.now()
        elapsed = (finished_at - started_at).total_seconds()
        if elapsed > prepared.definition.spec.timeout_seconds:
            result = HandlerResult(
                status=HandlerResultStatus.UNKNOWN_RESULT,
                summary="tool handler exceeded its declared timeout",
                error_code=ErrorCode.GATE_HANDLER_TIMEOUT.value,
            )

        mapped_status, observation_status = _map_handler_result(
            result,
            prepared.definition.spec.idempotent,
        )
        try:
            output, output_truncated = self._bounded_output(prepared.definition, result)
        except _GateDenial as invalid_output:
            result = HandlerResult(
                status=HandlerResultStatus.UNKNOWN_RESULT,
                summary=invalid_output.message,
                error_code=ErrorCode.GATE_RESULT_INVALID.value,
            )
            mapped_status = InvocationStatus.UNKNOWN_RESULT
            observation_status = ObservationStatus.UNKNOWN_RESULT
            output, output_truncated = None, False
        evidence_ids = self._persist_result(
            request,
            mapped_status,
            result,
            approval,
        )
        redacted_summary = self._redactor.redact(result.summary).value
        message = (
            redacted_summary
            if isinstance(redacted_summary, str) and redacted_summary
            else (result.error_code or ErrorCode.GATE_ALLOWED.value)
        )
        return _observation(
            request,
            observation_status,
            result.error_code or ErrorCode.GATE_ALLOWED.value,
            GatePhase.EXECUTION,
            message,
            approval_id=approval.approval_id if approval else None,
            evidence_ids=evidence_ids,
            retryable=result.retryable,
            truncated=output_truncated,
            output=output,
        )

    def _bounded_output(
        self,
        definition: ToolDefinition,
        result: HandlerResult,
    ) -> tuple[JsonValue | None, bool]:
        output = result.output
        if definition.spec.output_kind == ToolOutputKind.NONE:
            if output is not None:
                raise _GateDenial(
                    ErrorCode.GATE_OUTPUT_INVALID,
                    GatePhase.EXECUTION,
                    "NONE output tools must return null",
                    "GATE_OUTPUT_V1",
                )
            return None, False
        if definition.spec.output_kind == ToolOutputKind.TEXT and not isinstance(output, str):
            raise _GateDenial(
                ErrorCode.GATE_OUTPUT_INVALID,
                GatePhase.EXECUTION,
                "TEXT output tools must return a string",
                "GATE_OUTPUT_V1",
            )
        try:
            redacted = self._redactor.redact(output)
        except RedactionError as exc:
            raise _GateDenial(
                ErrorCode.GATE_OUTPUT_INVALID,
                GatePhase.EXECUTION,
                exc.message,
                "GATE_OUTPUT_V1",
            ) from exc
        value = redacted.value
        truncated = redacted.truncated
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) <= definition.spec.output_limit_bytes:
            return value, truncated
        preview_budget = max(1, definition.spec.output_limit_bytes - 128)
        preview = encoded[:preview_budget].decode("utf-8", errors="ignore")
        return (
            {
                "output_kind": definition.spec.output_kind.value,
                "preview": preview,
                "omitted_bytes": len(encoded) - len(preview.encode("utf-8")),
            },
            True,
        )

    def _persist_result(
        self,
        request: ToolCallRequest,
        target: InvocationStatus,
        result: HandlerResult,
        approval: Approval | None,
    ) -> tuple[str, ...]:
        evidence_ids: list[str] = []
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                now = self._clock.now()
                for draft in result.evidence:
                    evidence = Evidence(
                        evidence_id=self._ids.new_evidence_id(),
                        run_id=request.context.run_id,
                        tool_call_id=request.tool_call_id,
                        kind=draft.kind,
                        path=draft.path,
                        command_id=draft.command_id,
                        line_start=draft.line_start,
                        line_end=draft.line_end,
                        content_digest=draft.content_digest,
                        created_at=now,
                    )
                    unit.state.add_evidence(evidence, lease_token=request.lease_token)
                    evidence_ids.append(evidence.evidence_id)
                revision = unit.state.get_invocation_revision(
                    request.context.run_id,
                    request.tool_call_id,
                )
                unit.state.transition_invocation(
                    request.context.run_id,
                    request.tool_call_id,
                    expected_status=InvocationStatus.STARTED,
                    expected_revision=revision,
                    target=target,
                    result_summary=result.summary or None,
                    error_code=result.error_code,
                    lease_token=request.lease_token,
                )
                if approval is not None and target in _DETERMINISTIC_STATUSES:
                    current = ApprovalStore(unit, id_factory=self._ids).load(
                        approval.approval_id,
                        lease_token=request.lease_token,
                    )
                    ApprovalStore(unit, id_factory=self._ids).consume(
                        current.approval_id,
                        expected_revision=current.revision,
                        execution_intent_id=request.tool_call_id,
                        result_status=target,
                        result_code=result.error_code,
                        lease_token=request.lease_token,
                    )
        except Exception as exc:
            raise _GateDenial(
                ErrorCode.GATE_RESULT_WRITE_FAILED,
                GatePhase.EXECUTION,
                _safe_exception_message(exc),
                "GATE_RESULT_V1",
                approval_id=approval.approval_id if approval else None,
            ) from exc
        return tuple(evidence_ids)

    def _check_repeat_and_replay(
        self,
        request: ToolCallRequest,
        snapshot: RunSnapshot,
        prepared: _PreparedCall,
    ) -> None:
        matching = tuple(
            invocation
            for invocation in snapshot.invocations
            if invocation.tool_name == request.tool_name
            and invocation.args_digest == prepared.args_digest
            and invocation.status != InvocationStatus.PREPARED
        )
        if matching and prepared.definition.spec.risk != ToolRisk.READ:
            prior = _latest_success(matching)
            if prior is not None:
                raise _GateDenial(
                    ErrorCode.GATE_REPLAY_SUPPRESSED,
                    GatePhase.REPEAT,
                    "equivalent write call already completed",
                    "GATE_REPLAY_V1",
                    evidence_ids=prior.evidence_ids,
                )
            raise _GateDenial(
                ErrorCode.GATE_REPEATED_CALL,
                GatePhase.REPEAT,
                "equivalent write call has already been attempted",
                "GATE_REPEAT_V1",
            )
        limit = self._policy.execution_limit(prepared.definition.spec)
        if len(matching) >= limit:
            raise _GateDenial(
                ErrorCode.GATE_REPEATED_CALL,
                GatePhase.REPEAT,
                "equivalent read call reached its execution limit",
                "GATE_REPEAT_V1",
            )

    def _preempt_completed_write_replay(
        self,
        snapshot: RunSnapshot,
        prepared: _PreparedCall,
    ) -> None:
        if prepared.definition.spec.risk == ToolRisk.READ:
            return
        matching = tuple(
            invocation
            for invocation in snapshot.invocations
            if invocation.tool_name == prepared.definition.spec.name
            and invocation.args_digest == prepared.args_digest
            and invocation.status != InvocationStatus.PREPARED
        )
        if not matching:
            return
        prior = _latest_success(matching)
        if prior is not None:
            raise _GateDenial(
                ErrorCode.GATE_REPLAY_SUPPRESSED,
                GatePhase.REPEAT,
                "equivalent write call already completed",
                "GATE_REPLAY_V1",
                evidence_ids=prior.evidence_ids,
            )
        raise _GateDenial(
            ErrorCode.GATE_REPEATED_CALL,
            GatePhase.REPEAT,
            "equivalent write call has already been attempted",
            "GATE_REPEAT_V1",
        )

    def _check_budget(self, request: ToolCallRequest, snapshot: RunSnapshot) -> None:
        run = snapshot.run
        if run.usage.tool_calls >= run.budget.max_tool_calls:
            raise _GateDenial(
                ErrorCode.GATE_BUDGET_EXHAUSTED,
                GatePhase.BUDGET,
                "tool-call budget is exhausted",
                "GATE_BUDGET_V1",
            )
        if run.usage.steps >= run.budget.max_steps:
            raise _GateDenial(
                ErrorCode.GATE_BUDGET_EXHAUSTED,
                GatePhase.BUDGET,
                "step budget is exhausted",
                "GATE_BUDGET_V1",
            )
        if run.usage.token_cost >= run.budget.max_token_cost:
            raise _GateDenial(
                ErrorCode.GATE_BUDGET_EXHAUSTED,
                GatePhase.BUDGET,
                "token/cost budget is exhausted",
                "GATE_BUDGET_V1",
            )
        if run.started_at is not None:
            elapsed = int((self._clock.now() - run.started_at).total_seconds())
            if elapsed >= run.budget.max_wall_time_seconds:
                raise _GateDenial(
                    ErrorCode.GATE_BUDGET_EXHAUSTED,
                    GatePhase.BUDGET,
                    "wall-time budget is exhausted",
                    "GATE_BUDGET_V1",
                )

    def _duplicate_result_observation(
        self,
        request: ToolCallRequest,
        existing: ToolInvocation,
    ) -> ToolObservation:
        if existing.status == InvocationStatus.SUCCEEDED:
            raise _GateDenial(
                ErrorCode.GATE_REPLAY_SUPPRESSED,
                GatePhase.REPEAT,
                "tool call already completed; original evidence is returned",
                "GATE_REPLAY_V1",
                evidence_ids=existing.evidence_ids,
            )
        raise _GateDenial(
            ErrorCode.GATE_REPEATED_CALL,
            GatePhase.REPEAT,
            "tool call has already reached a terminal state",
            "GATE_REPEAT_V1",
            evidence_ids=existing.evidence_ids,
        )

    def _append_decision(
        self,
        unit: Any,
        request: ToolCallRequest,
        *,
        phase: GatePhase,
        status: AuditStatus,
        decision: AuditDecision,
        code: ErrorCode | str,
        rule_id: str,
        args_digest: str | None = None,
        approval_id: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> None:
        unit.audit.append(
            AuditEventDraft(
                run_id=request.context.run_id,
                event_type=AuditEventType.GATE_DECISION,
                phase=phase,
                status=status,
                decision=decision,
                code=_code_value(code),
                step_id=request.context.step_id,
                tool_call_id=request.tool_call_id,
                approval_id=approval_id,
                tool_name=request.tool_name,
                rule_id=rule_id,
                input_summary={
                    "args_digest": args_digest,
                    "argument_keys": sorted(request.args),
                },
                output_summary={
                    "status": status.value,
                    "code": _code_value(code),
                    "evidence_ids": list(evidence_ids),
                },
                evidence_ref=evidence_ids[0] if evidence_ids else None,
            )
        )

    def _deny_observation(
        self,
        request: ToolCallRequest,
        denial: _GateDenial,
    ) -> ToolObservation:
        audit_code = denial.code
        message = denial.message
        try:
            with self._storage.transaction(actor=request.context.actor) as unit:
                unit.state.verify_lease(request.context.run_id, request.lease_token)
                self._append_decision(
                    unit,
                    request,
                    phase=denial.phase,
                    status=AuditStatus.DENIED,
                    decision=AuditDecision.DENY,
                    code=denial.code,
                    rule_id=denial.rule_id,
                    approval_id=denial.approval_id,
                    evidence_ids=denial.evidence_ids,
                )
        except Exception:
            audit_code = ErrorCode.AUDIT_WRITE_FAILED
            message = "tool call denied; denial audit could not be persisted"
        return _observation(
            request,
            ObservationStatus.DENIED,
            audit_code,
            denial.phase,
            message,
            approval_id=denial.approval_id,
            evidence_ids=denial.evidence_ids,
            retryable=False,
        )

    def _run_lock(self, run_id: str) -> threading.Lock:
        with self._lock_guard:
            return self._run_locks.setdefault(run_id, threading.Lock())


def _new_invocation(
    request: ToolCallRequest,
    prepared: _PreparedCall,
    now: Any,
) -> ToolInvocation:
    return ToolInvocation(
        tool_call_id=request.tool_call_id,
        run_id=request.context.run_id,
        step_id=request.context.step_id,
        tool_name=request.tool_name,
        raw_args=request.args,
        normalized_args=prepared.normalized_args,
        args_digest=prepared.args_digest,
        risk=prepared.definition.spec.risk,
        required_permissions=prepared.definition.spec.required_permissions,
        idempotent=prepared.definition.spec.idempotent,
        created_at=now,
        updated_at=now,
    )


def _map_handler_result(
    result: HandlerResult,
    idempotent: bool,
) -> tuple[InvocationStatus, ObservationStatus]:
    if result.status == HandlerResultStatus.SUCCEEDED:
        return InvocationStatus.SUCCEEDED, ObservationStatus.ALLOWED
    if result.status == HandlerResultStatus.FAILED:
        return InvocationStatus.FAILED, ObservationStatus.FAILED
    if result.status == HandlerResultStatus.TIMED_OUT and idempotent:
        return InvocationStatus.TIMED_OUT, ObservationStatus.FAILED
    return InvocationStatus.UNKNOWN_RESULT, ObservationStatus.UNKNOWN_RESULT


def _observation(
    request: ToolCallRequest,
    status: ObservationStatus,
    code: ErrorCode | str,
    phase: GatePhase,
    message: str,
    *,
    approval_id: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    retryable: bool = False,
    truncated: bool = False,
    output: JsonValue | None = None,
) -> ToolObservation:
    return ToolObservation(
        status=status,
        code=_code_value(code),
        tool_call_id=request.tool_call_id,
        phase=phase,
        message=message[:4000] or _code_value(code),
        retryable=retryable,
        approval_id=approval_id,
        evidence_ids=evidence_ids,
        truncated=truncated,
        output=output,
    )


def _invocation_by_call_id(snapshot: RunSnapshot, tool_call_id: str) -> Any:
    return next(
        (
            invocation
            for invocation in snapshot.invocations
            if invocation.tool_call_id == tool_call_id
        ),
        None,
    )


def _approval_for_call(snapshot: RunSnapshot, tool_call_id: str) -> Approval | None:
    return next(
        (approval for approval in snapshot.approvals if approval.tool_call_id == tool_call_id),
        None,
    )


def _latest_success(invocations: tuple[Any, ...]) -> Any:
    return next(
        (
            invocation
            for invocation in reversed(invocations)
            if invocation.status == InvocationStatus.SUCCEEDED
        ),
        None,
    )


def _approval_error_code(status: ApprovalStatus) -> ErrorCode:
    return {
        ApprovalStatus.REJECTED: ErrorCode.APPROVAL_REJECTED,
        ApprovalStatus.EXPIRED: ErrorCode.APPROVAL_EXPIRED,
        ApprovalStatus.INVALIDATED: ErrorCode.APPROVAL_INVALID,
        ApprovalStatus.CONSUMED: ErrorCode.APPROVAL_REPLAYED,
        ApprovalStatus.CLAIMED: ErrorCode.APPROVAL_ALREADY_CLAIMED,
    }[status]


def _code_value(code: ErrorCode | str) -> str:
    """Return the stable string value of an error code."""

    return code.value if isinstance(code, ErrorCode) else code


def _safe_exception_message(exc: Exception) -> str:
    """Return a bounded public message without SQL or secret-bearing details."""

    candidate = getattr(exc, "message", None)
    if not isinstance(candidate, str) or not candidate:
        candidate = "governed operation failed closed"
    return candidate[:1000]


def _normalize_json_object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise _GateDenial(
            ErrorCode.GATE_INVALID_ARGUMENT,
            GatePhase.SCHEMA,
            "tool arguments must be a JSON object",
            "GATE_SCHEMA_V1",
        )
    _validate_json_tree(value)
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise _GateDenial(
            ErrorCode.GATE_INVALID_ARGUMENT,
            GatePhase.SCHEMA,
            "tool arguments must contain JSON values only",
            "GATE_SCHEMA_V1",
        ) from exc
    if len(encoded) > 65_536:
        raise _GateDenial(
            ErrorCode.GATE_INVALID_ARGUMENT,
            GatePhase.SCHEMA,
            "tool arguments exceed the size limit",
            "GATE_SCHEMA_V1",
        )
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise _GateDenial(
            ErrorCode.GATE_INVALID_ARGUMENT,
            GatePhase.SCHEMA,
            "tool arguments must be a JSON object",
            "GATE_SCHEMA_V1",
        )
    return decoded


def _validate_json_tree(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > 16 or nodes[0] > 10_000:
        raise _GateDenial(
            ErrorCode.GATE_INVALID_ARGUMENT,
            GatePhase.SCHEMA,
            "tool arguments exceed structural limits",
            "GATE_SCHEMA_V1",
        )
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise _GateDenial(
                ErrorCode.GATE_INVALID_ARGUMENT,
                GatePhase.SCHEMA,
                "tool arguments contain a non-finite number",
                "GATE_SCHEMA_V1",
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _GateDenial(
                    ErrorCode.GATE_INVALID_ARGUMENT,
                    GatePhase.SCHEMA,
                    "tool argument keys must be strings",
                    "GATE_SCHEMA_V1",
                )
            _validate_json_tree(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_tree(item, depth=depth + 1, nodes=nodes)
        return
    raise _GateDenial(
        ErrorCode.GATE_INVALID_ARGUMENT,
        GatePhase.SCHEMA,
        "tool arguments contain non-JSON values",
        "GATE_SCHEMA_V1",
    )


def _deep_copy_json(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return json.loads(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
