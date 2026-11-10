"""Safe checkpoints, deterministic recovery planning, and bounded continuation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .approval import ApprovalStoreError
from .audit import AuditDecision, AuditEventDraft, AuditEventType, AuditStatus
from .config import ResolvedHarnessConfig, config_fingerprint
from .domain import (
    AcceptanceStatus,
    ApprovalStatus,
    Checkpoint,
    CheckpointBoundary,
    CheckpointBoundaryKind,
    CheckpointSnapshot,
    CheckpointVerification,
    Clock,
    ContinuationApproval,
    ContinuationContext,
    ContinuationEvidence,
    ContinuationInvocation,
    ContinuationStep,
    ErrorCode,
    Evidence,
    EvidenceKind,
    GatePhase,
    IdFactory,
    InvocationStatus,
    RecoveryAction,
    RecoveryPlan,
    RecoveryRecord,
    RecoveryStatus,
    RemainingBudget,
    RunSnapshot,
    RunStatus,
    StepStatus,
    StopReason,
    ToolInvocation,
    Uuid4IdFactory,
    canonical_json_dump,
)
from .redaction import RedactionError, Redactor
from .sandbox import Sandbox, SandboxViolation, WorkspaceComparison
from .state_store import StateStoreError
from .storage import SQLiteStorage, StorageError, StorageUnitOfWork

MAX_CHECKPOINT_BYTES = 1024 * 1024
MAX_CONTEXT_BYTES = 64 * 1024
MAX_RECENT_STEPS = 10

_DETERMINISTIC_INVOCATION_STATUSES = frozenset(
    {
        InvocationStatus.SUCCEEDED,
        InvocationStatus.FAILED,
        InvocationStatus.TIMED_OUT,
    }
)
_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.STOPPED,
        RunStatus.FAILED,
    }
)
_PROHIBITED_ACTIONS = (
    "Do not replay a STARTED or UNKNOWN_RESULT invocation.",
    "Do not reuse a CONSUMED approval.",
    "Do not execute a tool outside the governed Gate.",
    "Do not treat the final answer as completion evidence.",
)


class CheckpointServiceError(RuntimeError):
    """Stable fail-closed checkpoint or recovery failure."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        operation: str,
        message: str,
    ) -> None:
        self.code = code
        self.operation = operation
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


class CheckpointService:
    """Create checkpoints and calculate safe actions after process interruption."""

    def __init__(
        self,
        *,
        storage: SQLiteStorage,
        config: ResolvedHarnessConfig,
        sandbox: Sandbox,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._storage = storage
        self._config = config
        self._sandbox = sandbox
        self._clock = clock or storage.clock
        self._ids = id_factory or Uuid4IdFactory()
        self._redactor = Redactor()
        self._validate_service_binding()

    def create_checkpoint(
        self,
        run_id: str,
        lease_token: str,
        boundary: CheckpointBoundary,
    ) -> Checkpoint:
        """Atomically persist one append-only checkpoint."""

        try:
            with self._storage.transaction(actor="checkpoint-service") as unit:
                return self._create_checkpoint_in_unit(
                    unit,
                    run_id,
                    lease_token,
                    boundary,
                )
        except CheckpointServiceError:
            raise
        except (
            StorageError,
            StateStoreError,
            SandboxViolation,
            sqlite3.Error,
            ValueError,
        ) as exc:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_WRITE_FAILED,
                operation="create_checkpoint",
                message=_safe_error_message(exc),
            ) from exc
        except Exception as exc:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_WRITE_FAILED,
                operation="create_checkpoint",
                message=_safe_error_message(exc),
            ) from exc

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        """Read and revalidate one checkpoint by ID."""

        try:
            with self._storage.transaction(actor="checkpoint-reader") as unit:
                return self._load_checkpoint_in_unit(unit, checkpoint_id)
        except CheckpointServiceError:
            raise
        except (StorageError, SandboxViolation, sqlite3.Error, ValueError) as exc:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="get_checkpoint",
                message=_safe_error_message(exc),
            ) from exc

    def latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        """Return the latest checkpoint for one run."""

        try:
            with self._storage.transaction(actor="checkpoint-reader") as unit:
                return self._latest_checkpoint_in_unit(unit, run_id)
        except CheckpointServiceError:
            raise
        except (StorageError, SandboxViolation, sqlite3.Error, ValueError) as exc:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="latest_checkpoint",
                message=_safe_error_message(exc),
            ) from exc

    def verify_checkpoint(
        self,
        checkpoint_id: str,
        lease_token: str,
    ) -> CheckpointVerification:
        """Validate checkpoint integrity and its append-only audit chain."""

        try:
            checkpoint = self.get_checkpoint(checkpoint_id)
            with self._storage.transaction(actor="checkpoint-verifier") as unit:
                unit.state.verify_lease(checkpoint.run_id, lease_token)
                chain = unit.audit.verify_chain(checkpoint.run_id)
                if not chain.valid:
                    return CheckpointVerification(
                        checkpoint_id=checkpoint.checkpoint_id,
                        run_id=checkpoint.run_id,
                        valid=False,
                        checked_audit_sequence=chain.last_sequence,
                        code=ErrorCode.AUDIT_CHAIN_INVALID.value,
                        reason=chain.detail or "audit chain is invalid",
                        checked_at=self._clock.now(),
                    )
                self._validate_checkpoint_references(unit, checkpoint)
                return CheckpointVerification(
                    checkpoint_id=checkpoint.checkpoint_id,
                    run_id=checkpoint.run_id,
                    valid=True,
                    checked_audit_sequence=chain.last_sequence,
                    checked_at=self._clock.now(),
                )
        except (StorageError, StateStoreError, CheckpointServiceError, sqlite3.Error) as exc:
            return CheckpointVerification(
                checkpoint_id=checkpoint_id,
                run_id="run_00000000000000000000000000000000",
                valid=False,
                checked_audit_sequence=0,
                code=getattr(exc, "code", ErrorCode.CHECKPOINT_INVALID).value,
                reason=_safe_error_message(exc),
                checked_at=self._clock.now(),
            )

    def prepare_recovery(self, run_id: str, lease_token: str) -> RecoveryPlan:
        """Validate persisted state and persist one deterministic recovery decision."""

        try:
            integrity = self._storage.verify_integrity()
        except StorageError as exc:
            raise CheckpointServiceError(
                ErrorCode.STATE_INTEGRITY_ERROR,
                operation="prepare_recovery",
                message=_safe_error_message(exc),
            ) from exc
        if not integrity.ok:
            raise CheckpointServiceError(
                ErrorCode.STATE_INTEGRITY_ERROR,
                operation="prepare_recovery",
                message="storage integrity validation failed",
            )
        try:
            with self._storage.transaction(actor="recovery-service") as unit:
                unit.state.verify_lease(run_id, lease_token)
                chain = unit.audit.verify_chain(run_id)
                if not chain.valid:
                    raise CheckpointServiceError(
                        ErrorCode.RECOVERY_AUDIT_INVALID,
                        operation="prepare_recovery",
                        message=chain.detail or "audit chain is invalid",
                    )
                run = unit.state.get_run(run_id)
                if run.status in _TERMINAL_RUN_STATUSES:
                    snapshot = unit.state.get_run_snapshot(run_id)
                    return self._record_plan(
                        unit,
                        run_id,
                        latest_checkpoint_id=run.last_checkpoint_id,
                        snapshot=snapshot,
                        action=RecoveryAction.ALREADY_TERMINAL,
                        status=RecoveryStatus.ALREADY_TERMINAL,
                        reason_code=None,
                        invocation_id=None,
                        approval_id=None,
                        valid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if evidence.is_valid
                        ),
                        invalid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if not evidence.is_valid
                        ),
                    )
                if run.status == RunStatus.BLOCKED:
                    snapshot = unit.state.get_run_snapshot(run_id)
                    return self._record_plan(
                        unit,
                        run_id,
                        latest_checkpoint_id=run.last_checkpoint_id,
                        snapshot=snapshot,
                        action=RecoveryAction.MANUAL_REVIEW,
                        status=RecoveryStatus.MANUAL_REVIEW,
                        reason_code=ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED.value,
                        invocation_id=_first_unknown_invocation_id(snapshot),
                        approval_id=None,
                        valid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if evidence.is_valid
                        ),
                        invalid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if not evidence.is_valid
                        ),
                    )
                if run.status not in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
                    snapshot = unit.state.get_run_snapshot(run_id)
                    return self._record_plan(
                        unit,
                        run_id,
                        latest_checkpoint_id=run.last_checkpoint_id,
                        snapshot=snapshot,
                        action=RecoveryAction.MANUAL_REVIEW,
                        status=RecoveryStatus.MANUAL_REVIEW,
                        reason_code=ErrorCode.RECOVERY_NOT_ALLOWED.value,
                        invocation_id=None,
                        approval_id=None,
                        valid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if evidence.is_valid
                        ),
                        invalid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if not evidence.is_valid
                        ),
                    )

                if run.config_fingerprint != self._config.fingerprint:
                    return self._block_recovery(
                        unit,
                        run_id,
                        lease_token,
                        reason_code=ErrorCode.RECOVERY_CONFIG_MISMATCH,
                        stop_reason=StopReason.WORKSPACE_CHANGED,
                    )

                latest = self._latest_checkpoint_in_unit(unit, run_id)
                if latest is None:
                    snapshot = unit.state.get_run_snapshot(run_id)
                    if snapshot.invocations or snapshot.approvals or snapshot.evidence:
                        return self._block_recovery(
                            unit,
                            run_id,
                            lease_token,
                            reason_code=ErrorCode.RECOVERY_CHECKPOINT_REQUIRED,
                            stop_reason=StopReason.GATE_BLOCKED,
                        )
                else:
                    comparison = self._sandbox.compare_checkpoint_workspace(
                        latest.snapshot.workspace
                    )
                    if comparison.identity_changed or comparison.external_changed:
                        return self._block_recovery(
                            unit,
                            run_id,
                            lease_token,
                            reason_code=ErrorCode.RECOVERY_WORKSPACE_CHANGED,
                            stop_reason=StopReason.WORKSPACE_CHANGED,
                        )

                unit.approvals.expire_due(run_id, lease_token=lease_token)
                run = unit.state.get_run(run_id)
                if run.status in _TERMINAL_RUN_STATUSES:
                    snapshot = unit.state.get_run_snapshot(run_id)
                    return self._record_plan(
                        unit,
                        run_id,
                        latest_checkpoint_id=run.last_checkpoint_id,
                        snapshot=snapshot,
                        action=RecoveryAction.ALREADY_TERMINAL,
                        status=RecoveryStatus.ALREADY_TERMINAL,
                        reason_code=None,
                        invocation_id=None,
                        approval_id=None,
                        valid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if evidence.is_valid
                        ),
                        invalid_evidence_ids=tuple(
                            evidence.evidence_id
                            for evidence in snapshot.evidence
                            if not evidence.is_valid
                        ),
                    )

                snapshot = unit.state.get_run_snapshot(run_id)
                valid_ids, invalid_ids = self._validate_evidence_versions(
                    unit,
                    run_id,
                    lease_token,
                    snapshot.evidence,
                )
                snapshot = unit.state.get_run_snapshot(run_id)
                active = tuple(
                    invocation
                    for invocation in snapshot.invocations
                    if invocation.status in {InvocationStatus.PREPARED, InvocationStatus.STARTED}
                )
                started = tuple(
                    invocation
                    for invocation in active
                    if invocation.status == InvocationStatus.STARTED
                )
                prepared = tuple(
                    invocation
                    for invocation in active
                    if invocation.status == InvocationStatus.PREPARED
                )
                unknown = tuple(
                    invocation
                    for invocation in snapshot.invocations
                    if invocation.status == InvocationStatus.UNKNOWN_RESULT
                )

                if unknown:
                    return self._block_recovery(
                        unit,
                        run_id,
                        lease_token,
                        reason_code=ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED,
                        stop_reason=StopReason.UNKNOWN_RESULT,
                        invocation_id=unknown[0].tool_call_id,
                        valid_evidence_ids=valid_ids,
                        invalid_evidence_ids=invalid_ids,
                    )
                if len(active) > 1 or (started and prepared):
                    return self._block_recovery(
                        unit,
                        run_id,
                        lease_token,
                        reason_code=ErrorCode.RECOVERY_STATE_AMBIGUOUS,
                        stop_reason=StopReason.GATE_BLOCKED,
                        valid_evidence_ids=valid_ids,
                        invalid_evidence_ids=invalid_ids,
                    )
                if started:
                    invocation = started[0]
                    self._trigger("recovery:before-unknown")
                    unit.state.transition_invocation(
                        run_id,
                        invocation.tool_call_id,
                        expected_status=InvocationStatus.STARTED,
                        expected_revision=unit.state.get_invocation_revision(
                            run_id,
                            invocation.tool_call_id,
                        ),
                        target=InvocationStatus.UNKNOWN_RESULT,
                        result_summary=(
                            "recovery found STARTED invocation without a confirmed result"
                        ),
                        error_code=ErrorCode.GATE_RESULT_UNKNOWN.value,
                        lease_token=lease_token,
                    )
                    return self._block_recovery(
                        unit,
                        run_id,
                        lease_token,
                        reason_code=ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED,
                        stop_reason=StopReason.UNKNOWN_RESULT,
                        invocation_id=invocation.tool_call_id,
                        action=RecoveryAction.MARK_UNKNOWN_RESULT,
                        status=RecoveryStatus.MANUAL_REVIEW,
                        valid_evidence_ids=valid_ids,
                        invalid_evidence_ids=invalid_ids,
                        run_already_updated=False,
                    )
                active_approvals = tuple(
                    approval
                    for approval in snapshot.approvals
                    if approval.status
                    in {
                        ApprovalStatus.PENDING,
                        ApprovalStatus.APPROVED,
                        ApprovalStatus.CLAIMED,
                    }
                )
                if active_approvals and not prepared:
                    return self._block_recovery(
                        unit,
                        run_id,
                        lease_token,
                        reason_code=ErrorCode.RECOVERY_STATE_AMBIGUOUS,
                        stop_reason=StopReason.GATE_BLOCKED,
                        valid_evidence_ids=valid_ids,
                        invalid_evidence_ids=invalid_ids,
                    )
                if prepared:
                    invocation = prepared[0]
                    approval = _approval_for_invocation(snapshot, invocation)
                    if latest is not None and comparison.workspace_changed:
                        return self._block_recovery(
                            unit,
                            run_id,
                            lease_token,
                            reason_code=ErrorCode.RECOVERY_WORKSPACE_CHANGED,
                            stop_reason=StopReason.WORKSPACE_CHANGED,
                            invocation_id=invocation.tool_call_id,
                            approval_id=(approval.approval_id if approval is not None else None),
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    if approval is not None and approval.status == ApprovalStatus.PENDING:
                        if run.status != RunStatus.WAITING_APPROVAL:
                            return self._block_recovery(
                                unit,
                                run_id,
                                lease_token,
                                reason_code=ErrorCode.RECOVERY_STATE_AMBIGUOUS,
                                stop_reason=StopReason.GATE_BLOCKED,
                                valid_evidence_ids=valid_ids,
                                invalid_evidence_ids=invalid_ids,
                            )
                        return self._record_plan(
                            unit,
                            run_id,
                            latest_checkpoint_id=run.last_checkpoint_id,
                            snapshot=snapshot,
                            action=RecoveryAction.WAIT_FOR_APPROVAL,
                            status=RecoveryStatus.WAITING_APPROVAL,
                            reason_code=ErrorCode.APPROVAL_REQUIRED.value,
                            invocation_id=invocation.tool_call_id,
                            approval_id=approval.approval_id,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    if approval is None:
                        if run.status != RunStatus.RUNNING:
                            return self._block_recovery(
                                unit,
                                run_id,
                                lease_token,
                                reason_code=ErrorCode.RECOVERY_STATE_AMBIGUOUS,
                                stop_reason=StopReason.GATE_BLOCKED,
                                valid_evidence_ids=valid_ids,
                                invalid_evidence_ids=invalid_ids,
                            )
                        return self._record_plan(
                            unit,
                            run_id,
                            latest_checkpoint_id=run.last_checkpoint_id,
                            snapshot=snapshot,
                            action=RecoveryAction.REENTER_GATE,
                            status=RecoveryStatus.RESUMABLE,
                            reason_code=None,
                            invocation_id=invocation.tool_call_id,
                            approval_id=None,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    if approval.status == ApprovalStatus.APPROVED:
                        return self._record_plan(
                            unit,
                            run_id,
                            latest_checkpoint_id=run.last_checkpoint_id,
                            snapshot=snapshot,
                            action=RecoveryAction.REENTER_GATE,
                            status=RecoveryStatus.RESUMABLE,
                            reason_code=None,
                            invocation_id=invocation.tool_call_id,
                            approval_id=approval.approval_id,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    if approval.status == ApprovalStatus.CLAIMED:
                        if run.status != RunStatus.RUNNING:
                            return self._block_recovery(
                                unit,
                                run_id,
                                lease_token,
                                reason_code=ErrorCode.RECOVERY_STATE_AMBIGUOUS,
                                stop_reason=StopReason.GATE_BLOCKED,
                                valid_evidence_ids=valid_ids,
                                invalid_evidence_ids=invalid_ids,
                            )
                        return self._record_plan(
                            unit,
                            run_id,
                            latest_checkpoint_id=run.last_checkpoint_id,
                            snapshot=snapshot,
                            action=RecoveryAction.RESUME_CLAIM_THEN_REENTER_GATE,
                            status=RecoveryStatus.RESUMABLE,
                            reason_code=None,
                            invocation_id=invocation.tool_call_id,
                            approval_id=approval.approval_id,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    return self._block_recovery(
                        unit,
                        run_id,
                        lease_token,
                        reason_code=ErrorCode.RECOVERY_APPROVAL_INVALID,
                        stop_reason=StopReason.WORKSPACE_CHANGED,
                        approval_id=approval.approval_id,
                        valid_evidence_ids=valid_ids,
                        invalid_evidence_ids=invalid_ids,
                    )

                missing_checkpoint = self._missing_checkpoint_invocation(unit, run_id, snapshot)
                if missing_checkpoint is not None:
                    if latest is None:
                        return self._block_recovery(
                            unit,
                            run_id,
                            lease_token,
                            reason_code=ErrorCode.RECOVERY_CHECKPOINT_REQUIRED,
                            stop_reason=StopReason.GATE_BLOCKED,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    comparison = self._sandbox.compare_checkpoint_workspace(
                        latest.snapshot.workspace
                    )
                    if comparison.workspace_changed and not self._workspace_delta_attributable(
                        latest,
                        snapshot,
                        comparison,
                    ):
                        return self._block_recovery(
                            unit,
                            run_id,
                            lease_token,
                            reason_code=ErrorCode.RECOVERY_WORKSPACE_CHANGED,
                            stop_reason=StopReason.WORKSPACE_CHANGED,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )
                    rebuilt = self._create_checkpoint_in_unit(
                        unit,
                        run_id,
                        lease_token,
                        CheckpointBoundary(
                            kind=_boundary_kind_for_invocation(missing_checkpoint.status),
                            boundary_ref=missing_checkpoint.tool_call_id,
                            step_id=missing_checkpoint.step_id,
                            tool_call_id=missing_checkpoint.tool_call_id,
                            public_summary=missing_checkpoint.result_summary
                            or f"rebuilt {missing_checkpoint.tool_name} result boundary",
                        ),
                    )
                    refreshed = unit.state.get_run_snapshot(run_id)
                    return self._record_plan(
                        unit,
                        run_id,
                        latest_checkpoint_id=rebuilt.checkpoint_id,
                        snapshot=refreshed,
                        action=RecoveryAction.REBUILD_CHECKPOINT_THEN_CONTINUE,
                        status=RecoveryStatus.RESUMABLE,
                        reason_code=None,
                        invocation_id=missing_checkpoint.tool_call_id,
                        approval_id=missing_checkpoint.approval_id,
                        valid_evidence_ids=valid_ids,
                        invalid_evidence_ids=invalid_ids,
                    )

                if latest is not None:
                    comparison = self._sandbox.compare_checkpoint_workspace(
                        latest.snapshot.workspace
                    )
                    if comparison.workspace_changed:
                        return self._block_recovery(
                            unit,
                            run_id,
                            lease_token,
                            reason_code=ErrorCode.RECOVERY_WORKSPACE_CHANGED,
                            stop_reason=StopReason.WORKSPACE_CHANGED,
                            valid_evidence_ids=valid_ids,
                            invalid_evidence_ids=invalid_ids,
                        )

                return self._record_plan(
                    unit,
                    run_id,
                    latest_checkpoint_id=run.last_checkpoint_id,
                    snapshot=snapshot,
                    action=RecoveryAction.CONTINUE_AGENT,
                    status=RecoveryStatus.RESUMABLE,
                    reason_code=None,
                    invocation_id=None,
                    approval_id=None,
                    valid_evidence_ids=valid_ids,
                    invalid_evidence_ids=invalid_ids,
                )
        except CheckpointServiceError:
            raise
        except (
            StorageError,
            StateStoreError,
            ApprovalStoreError,
            SandboxViolation,
            sqlite3.Error,
        ) as exc:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_WRITE_FAILED,
                operation="prepare_recovery",
                message=_safe_error_message(exc),
            ) from exc
        except Exception as exc:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_WRITE_FAILED,
                operation="prepare_recovery",
                message=_safe_error_message(exc),
            ) from exc

    def build_continuation_context(
        self,
        recovery_plan: RecoveryPlan,
    ) -> ContinuationContext:
        """Build bounded context from a previously persisted recovery decision."""

        with self._storage.transaction(actor="recovery-context") as unit:
            row = unit.connection.execute(
                "SELECT * FROM recovery_records WHERE recovery_id = ?",
                (recovery_plan.recovery_id,),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.RECOVERY_NOT_ALLOWED,
                    operation="build_continuation_context",
                    message="recovery plan is not persisted",
                )
            persisted = _recovery_record_from_row(row)
            if persisted.plan != recovery_plan:
                raise CheckpointServiceError(
                    ErrorCode.RECOVERY_NOT_ALLOWED,
                    operation="build_continuation_context",
                    message="recovery plan does not match persisted facts",
                )
            if (
                unit.state.get_run_revision(recovery_plan.run_id)
                != recovery_plan.source_run_revision
            ):
                raise CheckpointServiceError(
                    ErrorCode.RECOVERY_NOT_ALLOWED,
                    operation="build_continuation_context",
                    message="recovery plan is stale for the current run revision",
                )
            snapshot = unit.state.get_run_snapshot(recovery_plan.run_id)
            context = self._build_context(
                snapshot,
                checkpoint_id=recovery_plan.checkpoint_id,
                valid_evidence_ids=recovery_plan.valid_evidence_ids,
                invalid_evidence_ids=recovery_plan.invalid_evidence_ids,
                invocation_id=recovery_plan.invocation_id,
                approval_id=recovery_plan.approval_id,
                generated_at=recovery_plan.created_at,
            )
            if (
                recovery_plan.continuation_context_digest is not None
                and context.context_digest != recovery_plan.continuation_context_digest
            ):
                raise CheckpointServiceError(
                    ErrorCode.RECOVERY_NOT_ALLOWED,
                    operation="build_continuation_context",
                    message="continuation context no longer matches the recovery plan",
                )
        return context

    def render_continuation(self, context: ContinuationContext) -> str:
        """Render a deterministic bounded continuation prompt."""

        if _continuation_digest(context) != context.context_digest:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="render_continuation",
                message="continuation context digest does not match",
            )
        safe = _safe_line
        lines = [
            "GUARDED HARNESS RESUME CONTEXT",
            f"run_id: {context.run_id}",
            f"checkpoint_id: {context.checkpoint_id or '<none>'}",
            f"task_id: {safe(context.task_id)}",
            f"task_kind: {context.task_kind.value}",
            f"task: {safe(context.task_description)}",
            f"config_fingerprint: {context.config_fingerprint}",
            f"workspace_root: {safe(context.workspace_root)}",
            f"git_root: {safe(context.git_root)}",
            f"status: {context.current_status.value}",
            f"stop_reason: {context.stop_reason.value if context.stop_reason else '<none>'}",
            (
                "remaining_budget: "
                f"steps={context.remaining_budget.steps}, "
                f"tool_calls={context.remaining_budget.tool_calls}, "
                f"token_cost={context.remaining_budget.token_cost}, "
                f"wall_time_seconds={context.remaining_budget.wall_time_seconds}"
            ),
            "plan:",
            *(
                [f"- [{item.ordinal}] {safe(item.summary)}" for item in context.plan.items]
                if context.plan is not None
                else ["- <none>"]
            ),
            "completed_steps:",
            *(
                [
                    f"- [{step.ordinal}] {step.status.value}: {safe(step.summary)}"
                    for step in context.completed_steps
                ]
                if context.completed_steps
                else ["- <none>"]
            ),
            "valid_evidence:",
            *(
                [
                    f"- {evidence.evidence_id} {evidence.kind.value} "
                    f"{evidence.path or '<none>'} {evidence.content_digest}"
                    for evidence in context.valid_evidence
                ]
                if context.valid_evidence
                else ["- <none>"]
            ),
            "invalid_evidence_ids:",
            *(
                [f"- {evidence_id}" for evidence_id in context.invalid_evidence_ids]
                if context.invalid_evidence_ids
                else ["- <none>"]
            ),
            "pending_invocation:",
            (
                f"- {context.pending_invocation.tool_name} "
                f"{context.pending_invocation.tool_call_id} "
                f"{context.pending_invocation.status.value} "
                f"{context.pending_invocation.args_digest}"
                if context.pending_invocation
                else "- <none>"
            ),
            "pending_approval:",
            (
                f"- {context.pending_approval.approval_id} {context.pending_approval.status.value}"
                if context.pending_approval
                else "- <none>"
            ),
            "prohibited_actions:",
            *[f"- {action}" for action in context.prohibited_actions],
            f"context_digest: {context.context_digest}",
        ]
        rendered = "\n".join(lines) + "\n"
        if len(rendered.encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_CONTEXT_TOO_LARGE,
                operation="render_continuation",
                message="continuation prompt exceeds the 64 KiB limit",
            )
        return rendered

    def get_recovery_record(self, recovery_id: str) -> RecoveryRecord:
        """Load one append-only recovery record."""

        with self._storage.transaction(actor="recovery-reader") as unit:
            row = unit.connection.execute(
                "SELECT * FROM recovery_records WHERE recovery_id = ?",
                (recovery_id,),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.RECOVERY_NOT_ALLOWED,
                    operation="get_recovery_record",
                    message="recovery record does not exist",
                )
            return _recovery_record_from_row(row)

    def list_recovery_records(
        self,
        run_id: str,
        *,
        limit: int = 100,
    ) -> tuple[RecoveryRecord, ...]:
        """List recovery decisions for one run in stable append order."""

        if not 1 <= limit <= 1000:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_NOT_ALLOWED,
                operation="list_recovery_records",
                message="limit must be between 1 and 1000",
            )
        with self._storage.transaction(actor="recovery-reader") as unit:
            rows = unit.connection.execute(
                """
                SELECT * FROM recovery_records WHERE run_id = ?
                ORDER BY created_at, recovery_id LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
            return tuple(_recovery_record_from_row(row) for row in rows)

    def _create_checkpoint_in_unit(
        self,
        unit: StorageUnitOfWork,
        run_id: str,
        lease_token: str,
        boundary: CheckpointBoundary,
    ) -> Checkpoint:
        unit.state.verify_lease(run_id, lease_token)
        snapshot_state = unit.state.get_run_snapshot(run_id)
        snapshot_state = self._sanitize_snapshot(snapshot_state)
        if snapshot_state.run.config_fingerprint != self._config.fingerprint:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_CONFIG_MISMATCH,
                operation="create_checkpoint",
                message="run configuration no longer matches the loaded config",
            )
        self._validate_boundary(snapshot_state, boundary)
        audit_head = unit.audit.head(run_id)
        if audit_head is None:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="create_checkpoint",
                message="run has no audit head",
            )
        workspace = self._sandbox.capture_checkpoint_workspace()
        now = self._clock.now()
        snapshot = CheckpointSnapshot(
            run=snapshot_state.run,
            plan=snapshot_state.plan,
            steps=snapshot_state.steps,
            invocations=snapshot_state.invocations,
            model_calls=snapshot_state.model_calls,
            evidence=snapshot_state.evidence,
            approvals=snapshot_state.approvals,
            completion=snapshot_state.completion,
            last_audit_sequence=audit_head.sequence,
            last_audit_hash=audit_head.event_hash,
            workspace=workspace,
            recent_observations=_recent_observations(snapshot_state),
            created_at=now,
        )
        snapshot_json = canonical_json_dump(snapshot)
        if len(snapshot_json.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_SNAPSHOT_TOO_LARGE,
                operation="create_checkpoint",
                message="checkpoint snapshot exceeds 1 MiB",
            )
        snapshot_digest = _snapshot_digest(snapshot_json)
        existing_row = unit.connection.execute(
            """
            SELECT * FROM checkpoints
            WHERE run_id = ? AND boundary_kind = ? AND boundary_ref = ?
            """,
            (run_id, boundary.kind.value, boundary.boundary_ref),
        ).fetchone()
        if existing_row is not None:
            existing = self._checkpoint_from_row(existing_row)
            if existing.snapshot_digest != snapshot_digest:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_SEQUENCE_CONFLICT,
                    operation="create_checkpoint",
                    message="boundary already has a different checkpoint",
                )
            return existing
        row = unit.connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM checkpoints WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sequence = int(row["max_sequence"]) + 1
        checkpoint_id = self._ids.new_checkpoint_id()
        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            sequence=sequence,
            boundary=boundary,
            run_revision=unit.state.get_run_revision(run_id),
            last_audit_sequence=audit_head.sequence,
            last_audit_hash=audit_head.event_hash,
            snapshot_digest=snapshot_digest,
            snapshot=snapshot,
            created_at=now,
        )
        self._trigger("checkpoint:before-insert")
        unit.connection.execute(
            """
            INSERT INTO checkpoints(
                checkpoint_id, schema_version, run_id, sequence,
                boundary_kind, boundary_ref, boundary_json, step_id, tool_call_id, approval_id,
                run_revision, last_audit_sequence, last_audit_hash,
                snapshot_json, snapshot_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                checkpoint.checkpoint_id,
                checkpoint.schema_version,
                checkpoint.run_id,
                checkpoint.sequence,
                boundary.kind.value,
                boundary.boundary_ref,
                canonical_json_dump(boundary),
                boundary.step_id,
                boundary.tool_call_id,
                boundary.approval_id,
                checkpoint.run_revision,
                checkpoint.last_audit_sequence,
                checkpoint.last_audit_hash,
                snapshot_json,
                checkpoint.snapshot_digest,
                _serialize_datetime(checkpoint.created_at),
            ),
        )
        self._trigger("checkpoint:before-run-update")
        unit.state.mark_checkpoint_written(
            run_id,
            checkpoint.checkpoint_id,
            lease_token=lease_token,
        )
        self._trigger("checkpoint:before-audit")
        return checkpoint

    def _load_checkpoint_in_unit(
        self,
        unit: StorageUnitOfWork,
        checkpoint_id: str,
    ) -> Checkpoint:
        row = unit.connection.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        ).fetchone()
        if row is None:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_NOT_FOUND,
                operation="load_checkpoint",
                message="checkpoint does not exist",
            )
        checkpoint = self._checkpoint_from_row(row)
        if _snapshot_digest(canonical_json_dump(checkpoint.snapshot)) != checkpoint.snapshot_digest:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_HASH_MISMATCH,
                operation="load_checkpoint",
                message="checkpoint snapshot hash does not match",
            )
        return checkpoint

    def _latest_checkpoint_in_unit(
        self,
        unit: StorageUnitOfWork,
        run_id: str,
    ) -> Checkpoint | None:
        row = unit.connection.execute(
            """
            SELECT * FROM checkpoints WHERE run_id = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return self._checkpoint_from_row(row) if row is not None else None

    def _checkpoint_from_row(self, row: sqlite3.Row) -> Checkpoint:
        try:
            if int(row["schema_version"]) != 1:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED,
                    operation="load_checkpoint",
                    message="unsupported checkpoint schema version",
                )
            raw_snapshot = json.loads(str(row["snapshot_json"]))
            if not isinstance(raw_snapshot, dict) or raw_snapshot.get("schema_version") != 1:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_SCHEMA_UNSUPPORTED,
                    operation="load_checkpoint",
                    message="unsupported checkpoint snapshot schema version",
                )
            snapshot = CheckpointSnapshot.model_validate_json(
                str(row["snapshot_json"]),
                strict=True,
            )
            boundary = CheckpointBoundary.model_validate_json(
                str(row["boundary_json"]),
                strict=True,
            )
            if (
                boundary.kind.value != str(row["boundary_kind"])
                or boundary.boundary_ref != str(row["boundary_ref"])
                or boundary.step_id != (str(row["step_id"]) if row["step_id"] else None)
                or boundary.tool_call_id
                != (str(row["tool_call_id"]) if row["tool_call_id"] else None)
                or boundary.approval_id != (str(row["approval_id"]) if row["approval_id"] else None)
            ):
                raise ValueError("checkpoint boundary index does not match payload")
            return Checkpoint(
                checkpoint_id=str(row["checkpoint_id"]),
                run_id=str(row["run_id"]),
                sequence=int(row["sequence"]),
                boundary=boundary,
                run_revision=int(row["run_revision"]),
                last_audit_sequence=int(row["last_audit_sequence"]),
                last_audit_hash=str(row["last_audit_hash"]),
                snapshot_digest=str(row["snapshot_digest"]),
                snapshot=snapshot,
                created_at=_parse_datetime(str(row["created_at"])),
            )
        except CheckpointServiceError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="load_checkpoint",
                message="checkpoint row is invalid",
            ) from exc

    def _validate_boundary(
        self,
        snapshot_state: RunSnapshot,
        boundary: CheckpointBoundary,
    ) -> None:
        run = snapshot_state.run
        allowed_states: set[RunStatus]
        if boundary.kind == CheckpointBoundaryKind.COMPLETION_RECORDED:
            allowed_states = {
                RunStatus.VERIFYING,
                RunStatus.COMPLETED,
                RunStatus.FAILED,
            }
            if snapshot_state.completion is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="completion boundary requires a completion result",
                )
        elif boundary.kind in {
            CheckpointBoundaryKind.APPROVAL_REQUESTED,
            CheckpointBoundaryKind.APPROVAL_SETTLED,
        }:
            allowed_states = {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}
        else:
            allowed_states = {RunStatus.RUNNING}
        if run.status not in allowed_states:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="create_checkpoint",
                message="run state does not permit this checkpoint boundary",
            )
        if boundary.step_id is not None:
            step = next(
                (item for item in snapshot_state.steps if item.step_id == boundary.step_id),
                None,
            )
            if step is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="boundary step is not part of this run",
                )
            if boundary.kind == CheckpointBoundaryKind.STEP_COMPLETED and step.status not in {
                StepStatus.COMPLETED,
                StepStatus.FAILED,
            }:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="step boundary requires a terminal step",
                )
        if boundary.tool_call_id is not None:
            invocation = next(
                (
                    item
                    for item in snapshot_state.invocations
                    if item.tool_call_id == boundary.tool_call_id
                ),
                None,
            )
            if invocation is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="boundary invocation is not part of this run",
                )
            expected = {
                CheckpointBoundaryKind.TOOL_SUCCEEDED: InvocationStatus.SUCCEEDED,
                CheckpointBoundaryKind.TOOL_FAILED: InvocationStatus.FAILED,
                CheckpointBoundaryKind.TOOL_TIMED_OUT: InvocationStatus.TIMED_OUT,
                CheckpointBoundaryKind.TOOL_UNKNOWN_RESULT: InvocationStatus.UNKNOWN_RESULT,
            }.get(boundary.kind)
            if expected is not None and invocation.status != expected:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="boundary invocation status does not match",
                )
        if boundary.approval_id is not None:
            approval = next(
                (
                    item
                    for item in snapshot_state.approvals
                    if item.approval_id == boundary.approval_id
                ),
                None,
            )
            if approval is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="boundary approval is not part of this run",
                )
            if (
                boundary.kind == CheckpointBoundaryKind.APPROVAL_SETTLED
                and approval.status == ApprovalStatus.PENDING
            ):
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="pending approvals are not settled boundaries",
                )
            if (
                boundary.kind == CheckpointBoundaryKind.APPROVAL_REQUESTED
                and approval.status != ApprovalStatus.PENDING
            ):
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="create_checkpoint",
                    message="approval request boundaries require a pending approval",
                )

    def _validate_checkpoint_references(
        self,
        unit: StorageUnitOfWork,
        checkpoint: Checkpoint,
    ) -> None:
        run_id = checkpoint.run_id
        current_revision = unit.state.get_run_revision(run_id)
        if checkpoint.run_revision > current_revision:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="verify_checkpoint",
                message="checkpoint run revision is newer than persistent state",
            )
        audit_head = unit.audit.get(run_id, checkpoint.last_audit_sequence)
        if (
            audit_head is None
            or audit_head.event_hash != checkpoint.last_audit_hash
            or checkpoint.snapshot.last_audit_sequence != checkpoint.last_audit_sequence
            or checkpoint.snapshot.last_audit_hash != checkpoint.last_audit_hash
        ):
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="verify_checkpoint",
                message="checkpoint audit head does not match",
            )
        if checkpoint.snapshot.run.run_id != run_id:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="verify_checkpoint",
                message="checkpoint snapshot run does not match",
            )
        if checkpoint.boundary.step_id is not None:
            row = unit.connection.execute(
                "SELECT 1 FROM steps WHERE run_id = ? AND step_id = ?",
                (run_id, checkpoint.boundary.step_id),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="verify_checkpoint",
                    message="checkpoint boundary references missing step",
                )
        if checkpoint.boundary.tool_call_id is not None:
            row = unit.connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id = ? AND tool_call_id = ?",
                (run_id, checkpoint.boundary.tool_call_id),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="verify_checkpoint",
                    message="checkpoint boundary references missing invocation",
                )
        if checkpoint.boundary.approval_id is not None:
            row = unit.connection.execute(
                "SELECT 1 FROM approvals WHERE run_id = ? AND approval_id = ?",
                (run_id, checkpoint.boundary.approval_id),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="verify_checkpoint",
                    message="checkpoint boundary references missing approval",
                )
        if checkpoint.sequence > 1:
            previous = unit.connection.execute(
                """
                SELECT 1 FROM checkpoints
                WHERE run_id = ? AND sequence = ?
                """,
                (run_id, checkpoint.sequence - 1),
            ).fetchone()
            if previous is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_SEQUENCE_CONFLICT,
                    operation="verify_checkpoint",
                    message="checkpoint sequence is not contiguous",
                )
        for evidence in checkpoint.snapshot.evidence:
            row = unit.connection.execute(
                "SELECT 1 FROM evidence WHERE run_id = ? AND evidence_id = ?",
                (run_id, evidence.evidence_id),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="verify_checkpoint",
                    message="checkpoint references missing evidence",
                )
        for invocation in checkpoint.snapshot.invocations:
            row = unit.connection.execute(
                "SELECT 1 FROM tool_calls WHERE run_id = ? AND tool_call_id = ?",
                (run_id, invocation.tool_call_id),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="verify_checkpoint",
                    message="checkpoint references missing invocation",
                )
        for approval in checkpoint.snapshot.approvals:
            row = unit.connection.execute(
                "SELECT 1 FROM approvals WHERE run_id = ? AND approval_id = ?",
                (run_id, approval.approval_id),
            ).fetchone()
            if row is None:
                raise CheckpointServiceError(
                    ErrorCode.CHECKPOINT_INVALID,
                    operation="verify_checkpoint",
                    message="checkpoint references missing approval",
                )

    def _validate_evidence_versions(
        self,
        unit: StorageUnitOfWork,
        run_id: str,
        lease_token: str,
        evidence_items: tuple[Evidence, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        valid: list[str] = []
        invalid: list[str] = []
        for evidence in evidence_items:
            if not evidence.is_valid:
                invalid.append(evidence.evidence_id)
                continue
            if (
                evidence.kind
                not in {
                    EvidenceKind.READ,
                    EvidenceKind.DOCUMENT,
                    EvidenceKind.DIFF,
                }
                or evidence.path is None
            ):
                valid.append(evidence.evidence_id)
                continue
            try:
                current = self._sandbox.read_file(evidence.path)
                current_digest = current.content_digest
            except SandboxViolation:
                current_digest = None
            if current_digest == evidence.content_digest:
                valid.append(evidence.evidence_id)
                continue
            unit.state.invalidate_evidence(
                run_id,
                evidence.evidence_id,
                reason_code=ErrorCode.RECOVERY_EVIDENCE_INVALID,
                lease_token=lease_token,
            )
            invalid.append(evidence.evidence_id)
        return tuple(valid), tuple(invalid)

    def _missing_checkpoint_invocation(
        self,
        unit: StorageUnitOfWork,
        run_id: str,
        snapshot: RunSnapshot,
    ) -> ToolInvocation | None:
        terminal = tuple(
            invocation
            for invocation in snapshot.invocations
            if invocation.status in _DETERMINISTIC_INVOCATION_STATUSES
        )
        for invocation in sorted(terminal, key=lambda item: item.updated_at):
            row = unit.connection.execute(
                """
                SELECT 1 FROM checkpoints
                WHERE run_id = ? AND tool_call_id = ?
                  AND boundary_kind IN (
                    'TOOL_SUCCEEDED', 'TOOL_FAILED', 'TOOL_TIMED_OUT',
                    'TOOL_UNKNOWN_RESULT'
                  )
                """,
                (run_id, invocation.tool_call_id),
            ).fetchone()
            if row is None:
                return invocation
        return None

    def _workspace_delta_attributable(
        self,
        checkpoint: Checkpoint,
        snapshot: RunSnapshot,
        comparison: WorkspaceComparison,
    ) -> bool:
        if checkpoint.snapshot.workspace.entries_truncated:
            return False
        changed_paths = set(comparison.changed_paths)
        if not changed_paths:
            return True
        later_terminal = tuple(
            invocation
            for invocation in snapshot.invocations
            if invocation.status in _DETERMINISTIC_INVOCATION_STATUSES
            and invocation.updated_at >= checkpoint.created_at
        )
        if not later_terminal:
            return False
        terminal_ids = {invocation.tool_call_id for invocation in later_terminal}
        related_evidence = tuple(
            evidence for evidence in snapshot.evidence if evidence.tool_call_id in terminal_ids
        )
        prefix = snapshot.run.workspace.workspace_prefix
        covered_paths = {
            evidence.path if prefix == "." else f"{prefix}/{evidence.path}"
            for evidence in related_evidence
            if evidence.path is not None
        }
        if changed_paths.issubset(covered_paths):
            return True
        return any(
            evidence.kind in {EvidenceKind.COMMAND, EvidenceKind.TEST_REPORT}
            for evidence in related_evidence
        )

    def _block_recovery(
        self,
        unit: StorageUnitOfWork,
        run_id: str,
        lease_token: str,
        *,
        reason_code: ErrorCode,
        stop_reason: StopReason,
        invocation_id: str | None = None,
        approval_id: str | None = None,
        action: RecoveryAction = RecoveryAction.MANUAL_REVIEW,
        status: RecoveryStatus = RecoveryStatus.MANUAL_REVIEW,
        valid_evidence_ids: tuple[str, ...] = (),
        invalid_evidence_ids: tuple[str, ...] = (),
        run_already_updated: bool = False,
    ) -> RecoveryPlan:
        snapshot = unit.state.get_run_snapshot(run_id)
        for approval in snapshot.approvals:
            if approval.status in {
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.INVALIDATED,
                ApprovalStatus.CONSUMED,
            }:
                continue
            related = next(
                (
                    invocation
                    for invocation in snapshot.invocations
                    if invocation.tool_call_id == approval.tool_call_id
                ),
                None,
            )
            if approval.status == ApprovalStatus.CLAIMED and (
                related is None or related.status != InvocationStatus.PREPARED
            ):
                continue
            unit.approvals.invalidate(
                approval.approval_id,
                expected_revision=approval.revision,
                reason_code=reason_code.value,
                lease_token=lease_token,
            )
        current = unit.state.get_run(run_id)
        if not run_already_updated and current.status in {
            RunStatus.RUNNING,
            RunStatus.WAITING_APPROVAL,
        }:
            unit.state.transition_run(
                run_id,
                expected_status=current.status,
                expected_revision=unit.state.get_run_revision(run_id),
                target=RunStatus.BLOCKED,
                stop_reason=stop_reason,
                ended_at=self._clock.now(),
                acceptance_status=AcceptanceStatus.NOT_EVALUATED,
                lease_token=lease_token,
            )
        current_snapshot = unit.state.get_run_snapshot(run_id)
        if not valid_evidence_ids and not invalid_evidence_ids and current_snapshot.evidence:
            valid_evidence_ids = tuple(
                evidence.evidence_id for evidence in current_snapshot.evidence if evidence.is_valid
            )
            invalid_evidence_ids = tuple(
                evidence.evidence_id
                for evidence in current_snapshot.evidence
                if not evidence.is_valid
            )
        return self._record_plan(
            unit,
            run_id,
            latest_checkpoint_id=current.last_checkpoint_id,
            snapshot=current_snapshot,
            action=action,
            status=status,
            reason_code=reason_code.value,
            invocation_id=invocation_id,
            approval_id=approval_id,
            valid_evidence_ids=valid_evidence_ids,
            invalid_evidence_ids=invalid_evidence_ids,
        )

    def _record_plan(
        self,
        unit: StorageUnitOfWork,
        run_id: str,
        *,
        latest_checkpoint_id: str | None,
        snapshot: RunSnapshot,
        action: RecoveryAction,
        status: RecoveryStatus,
        reason_code: str | None,
        invocation_id: str | None,
        approval_id: str | None,
        valid_evidence_ids: tuple[str, ...],
        invalid_evidence_ids: tuple[str, ...],
    ) -> RecoveryPlan:
        idempotency_key = _recovery_idempotency_key(
            run_id=run_id,
            checkpoint_id=latest_checkpoint_id,
            run_revision=unit.state.get_run_revision(run_id),
            action=action,
            status=status,
            reason_code=reason_code,
            invocation_id=invocation_id,
            approval_id=approval_id,
            valid_evidence_ids=valid_evidence_ids,
            invalid_evidence_ids=invalid_evidence_ids,
        )
        existing = unit.connection.execute(
            "SELECT * FROM recovery_records WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            return _recovery_record_from_row(existing).plan
        now = self._clock.now()
        context = self._build_context(
            snapshot,
            checkpoint_id=latest_checkpoint_id,
            valid_evidence_ids=valid_evidence_ids,
            invalid_evidence_ids=invalid_evidence_ids,
            invocation_id=invocation_id,
            approval_id=approval_id,
            generated_at=now,
        )
        plan = RecoveryPlan(
            recovery_id=self._ids.new_recovery_id(),
            run_id=run_id,
            checkpoint_id=latest_checkpoint_id,
            source_run_revision=unit.state.get_run_revision(run_id),
            status=status,
            action=action,
            reason_code=reason_code,
            invocation_id=invocation_id,
            approval_id=approval_id,
            valid_evidence_ids=valid_evidence_ids,
            invalid_evidence_ids=invalid_evidence_ids,
            continuation_context_digest=context.context_digest,
            created_at=now,
        )
        record = RecoveryRecord(
            recovery_id=plan.recovery_id,
            run_id=run_id,
            checkpoint_id=latest_checkpoint_id,
            plan=plan,
            idempotency_key=idempotency_key,
            created_at=now,
        )
        record_json = canonical_json_dump(record)
        unit.connection.execute(
            """
            INSERT INTO recovery_records(
                recovery_id, schema_version, run_id, checkpoint_id, action,
                status, reason_code, invocation_id, approval_id, input_digest,
                idempotency_key, continuation_context_digest, record_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.recovery_id,
                record.schema_version,
                record.run_id,
                record.checkpoint_id,
                plan.action.value,
                plan.status.value,
                plan.reason_code,
                plan.invocation_id,
                plan.approval_id,
                idempotency_key,
                record.idempotency_key,
                plan.continuation_context_digest,
                record_json,
                _serialize_datetime(record.created_at),
            ),
        )
        audit_status = {
            RecoveryStatus.RESUMABLE: AuditStatus.SUCCEEDED,
            RecoveryStatus.WAITING_APPROVAL: AuditStatus.PENDING,
            RecoveryStatus.MANUAL_REVIEW: AuditStatus.BLOCKED,
            RecoveryStatus.ALREADY_TERMINAL: AuditStatus.STOPPED,
        }[status]
        unit.audit.append(
            AuditEventDraft(
                run_id=run_id,
                event_type=AuditEventType.RECOVERY_RECORDED,
                phase=_gate_phase_for_recovery(action),
                status=audit_status,
                decision=(
                    AuditDecision.DENY
                    if status == RecoveryStatus.MANUAL_REVIEW
                    else AuditDecision.NOT_APPLICABLE
                ),
                code=(reason_code if status == RecoveryStatus.MANUAL_REVIEW else None),
                tool_call_id=invocation_id,
                approval_id=approval_id,
                rule_id="RECOVERY_PLAN_V1",
                input_summary={
                    "recovery_id": plan.recovery_id,
                    "checkpoint_id": latest_checkpoint_id,
                    "action": action.value,
                },
                output_summary={
                    "status": status.value,
                    "valid_evidence": len(valid_evidence_ids),
                    "invalid_evidence": len(invalid_evidence_ids),
                },
            )
        )
        return plan

    def _build_context(
        self,
        snapshot: RunSnapshot,
        *,
        checkpoint_id: str | None,
        valid_evidence_ids: tuple[str, ...],
        invalid_evidence_ids: tuple[str, ...],
        invocation_id: str | None,
        approval_id: str | None,
        generated_at: datetime | None = None,
    ) -> ContinuationContext:
        snapshot = self._sanitize_snapshot(snapshot)
        run = snapshot.run
        valid_set = set(valid_evidence_ids)
        valid_evidence = tuple(
            ContinuationEvidence(
                evidence_id=evidence.evidence_id,
                kind=evidence.kind,
                path=evidence.path,
                line_start=evidence.line_start,
                line_end=evidence.line_end,
                content_digest=evidence.content_digest,
            )
            for evidence in snapshot.evidence
            if evidence.is_valid and evidence.evidence_id in valid_set
        )
        pending_invocation = next(
            (
                ContinuationInvocation(
                    tool_call_id=invocation.tool_call_id,
                    tool_name=invocation.tool_name,
                    status=invocation.status,
                    args_digest=invocation.args_digest,
                    approval_id=invocation.approval_id,
                )
                for invocation in snapshot.invocations
                if invocation.tool_call_id == invocation_id
                and invocation.status == InvocationStatus.PREPARED
            ),
            None,
        )
        selected_approval = next(
            (approval for approval in snapshot.approvals if approval.approval_id == approval_id),
            None,
        )
        pending_approval = (
            ContinuationApproval(
                approval_id=selected_approval.approval_id,
                tool_call_id=selected_approval.tool_call_id,
                tool_name=selected_approval.tool_name,
                status=selected_approval.status,
                binding_digest=selected_approval.binding_digest,
                expires_at=selected_approval.expires_at,
            )
            if selected_approval is not None
            else None
        )
        completed_steps = tuple(
            ContinuationStep(
                step_id=step.step_id,
                ordinal=step.ordinal,
                status=step.status,
                summary=step.summary,
            )
            for step in sorted(snapshot.steps, key=lambda item: item.ordinal)[-MAX_RECENT_STEPS:]
        )
        remaining = RemainingBudget(
            steps=max(0, run.budget.max_steps - run.usage.steps),
            tool_calls=max(0, run.budget.max_tool_calls - run.usage.tool_calls),
            token_cost=max(0, run.budget.max_token_cost - run.usage.token_cost),
            wall_time_seconds=max(
                0,
                run.budget.max_wall_time_seconds - run.usage.wall_time_seconds,
            ),
        )
        generated_at = generated_at or self._clock.now()
        payload: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run.run_id,
            "checkpoint_id": checkpoint_id,
            "task_id": run.task_id,
            "task_kind": run.task_kind,
            "task_description": run.task_description,
            "config_fingerprint": run.config_fingerprint,
            "workspace_root": run.workspace.workspace_root,
            "git_root": run.workspace.git_root,
            "current_status": run.status,
            "stop_reason": run.stop_reason,
            "budget": run.budget,
            "usage": run.usage,
            "remaining_budget": remaining,
            "plan": snapshot.plan,
            "completed_steps": completed_steps,
            "valid_evidence": valid_evidence,
            "invalid_evidence_ids": invalid_evidence_ids,
            "pending_invocation": pending_invocation,
            "pending_approval": pending_approval,
            "prohibited_actions": _PROHIBITED_ACTIONS,
            "generated_at": generated_at,
        }
        context_digest = _digest_json(
            {
                "schema": "guarded-harness/continuation/v1",
                "context": _jsonable(payload),
            }
        )
        context = ContinuationContext(
            **payload,
            context_digest=context_digest,
        )
        encoded = canonical_json_dump(context)
        if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_CONTEXT_TOO_LARGE,
                operation="build_continuation_context",
                message="continuation context exceeds 64 KiB",
            )
        return context

    def _validate_service_binding(self) -> None:
        if config_fingerprint(self._config) != self._config.fingerprint:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_CONFIG_MISMATCH,
                operation="initialize",
                message="resolved configuration fingerprint is invalid",
            )
        if Path(self._config.workspace_root) != self._sandbox.workspace_root:
            raise CheckpointServiceError(
                ErrorCode.RECOVERY_CONFIG_MISMATCH,
                operation="initialize",
                message="config and sandbox workspace roots differ",
            )

    def _sanitize_snapshot(self, snapshot: RunSnapshot) -> RunSnapshot:
        run = snapshot.run.model_copy(
            update={
                "task_description": self._redacted_text(snapshot.run.task_description),
                "candidate_final_answer": (
                    self._redacted_text(snapshot.run.candidate_final_answer)
                    if snapshot.run.candidate_final_answer is not None
                    else None
                ),
            }
        )
        plan = (
            snapshot.plan.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"summary": self._redacted_text(item.summary)})
                        for item in snapshot.plan.items
                    )
                }
            )
            if snapshot.plan is not None
            else None
        )
        steps = tuple(
            step.model_copy(update={"summary": self._redacted_text(step.summary)})
            for step in snapshot.steps
        )
        invocations = tuple(
            invocation.model_copy(
                update={
                    "result_summary": (
                        self._redacted_text(invocation.result_summary)
                        if invocation.result_summary is not None
                        else None
                    )
                }
            )
            for invocation in snapshot.invocations
        )
        completion = (
            snapshot.completion.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"message": self._redacted_text(item.message)})
                        for item in snapshot.completion.items
                    ),
                    "failure_summary": (
                        self._redacted_text(snapshot.completion.failure_summary)
                        if snapshot.completion.failure_summary is not None
                        else None
                    ),
                }
            )
            if snapshot.completion is not None
            else None
        )
        return snapshot.model_copy(
            update={
                "run": run,
                "plan": plan,
                "steps": steps,
                "invocations": invocations,
                "model_calls": snapshot.model_calls,
                "completion": completion,
            }
        )

    def _redacted_text(self, value: str) -> str:
        try:
            redacted = self._redactor.redact(value).value
        except RedactionError as exc:
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="redact_checkpoint",
                message=exc.message,
            ) from exc
        if not isinstance(redacted, str):
            raise CheckpointServiceError(
                ErrorCode.CHECKPOINT_INVALID,
                operation="redact_checkpoint",
                message="redaction did not produce text",
            )
        return redacted

    def _trigger(self, point: str) -> None:
        injector = self._storage.fault_injector
        if injector is not None:
            injector.trigger(point)


def _snapshot_digest(snapshot_json: str) -> str:
    payload = json.loads(snapshot_json)
    encoded = json.dumps(
        {
            "schema": "guarded-harness/checkpoint/v1",
            "snapshot": payload,
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _recovery_idempotency_key(
    *,
    run_id: str,
    checkpoint_id: str | None,
    run_revision: int,
    action: RecoveryAction,
    status: RecoveryStatus,
    reason_code: str | None,
    invocation_id: str | None,
    approval_id: str | None,
    valid_evidence_ids: tuple[str, ...],
    invalid_evidence_ids: tuple[str, ...],
) -> str:
    return _digest_json(
        {
            "schema": "guarded-harness/recovery-key/v1",
            "run_id": run_id,
            "checkpoint_id": checkpoint_id,
            "run_revision": run_revision,
            "action": action.value,
            "status": status.value,
            "reason_code": reason_code,
            "invocation_id": invocation_id,
            "approval_id": approval_id,
            "valid_evidence_ids": sorted(valid_evidence_ids),
            "invalid_evidence_ids": sorted(invalid_evidence_ids),
        }
    )


def _recovery_record_from_row(row: sqlite3.Row) -> RecoveryRecord:
    try:
        record = RecoveryRecord.model_validate_json(
            str(row["record_json"]),
            strict=True,
        )
    except (TypeError, ValueError) as exc:
        raise CheckpointServiceError(
            ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED,
            operation="load_recovery_record",
            message="recovery record is invalid",
        ) from exc
    if record.recovery_id != str(row["recovery_id"]):
        raise CheckpointServiceError(
            ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED,
            operation="load_recovery_record",
            message="recovery record index does not match payload",
        )
    return record


def _approval_for_invocation(snapshot: RunSnapshot, invocation: ToolInvocation):
    return next(
        (
            approval
            for approval in snapshot.approvals
            if approval.tool_call_id == invocation.tool_call_id
        ),
        None,
    )


def _first_unknown_invocation_id(snapshot: RunSnapshot) -> str | None:
    return next(
        (
            invocation.tool_call_id
            for invocation in snapshot.invocations
            if invocation.status == InvocationStatus.UNKNOWN_RESULT
        ),
        None,
    )


def _boundary_kind_for_invocation(status: InvocationStatus) -> CheckpointBoundaryKind:
    return {
        InvocationStatus.SUCCEEDED: CheckpointBoundaryKind.TOOL_SUCCEEDED,
        InvocationStatus.FAILED: CheckpointBoundaryKind.TOOL_FAILED,
        InvocationStatus.TIMED_OUT: CheckpointBoundaryKind.TOOL_TIMED_OUT,
        InvocationStatus.UNKNOWN_RESULT: CheckpointBoundaryKind.TOOL_UNKNOWN_RESULT,
    }[status]


def _recent_observations(snapshot: RunSnapshot) -> tuple[str, ...]:
    summaries = tuple(
        invocation.result_summary
        for invocation in sorted(
            snapshot.invocations,
            key=lambda item: (item.updated_at, item.tool_call_id),
        )
        if invocation.result_summary
    )
    return summaries[-MAX_RECENT_STEPS:]


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return _serialize_datetime(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _continuation_digest(context: ContinuationContext) -> str:
    payload = context.model_dump(mode="json")
    payload.pop("context_digest", None)
    return _digest_json(
        {
            "schema": "guarded-harness/continuation/v1",
            "context": payload,
        }
    )


def _gate_phase_for_recovery(action: RecoveryAction) -> GatePhase:
    if action in {
        RecoveryAction.REENTER_GATE,
        RecoveryAction.RESUME_CLAIM_THEN_REENTER_GATE,
    }:
        return GatePhase.EXECUTION
    if action == RecoveryAction.WAIT_FOR_APPROVAL:
        return GatePhase.APPROVAL
    return GatePhase.EXECUTION


def _safe_error_message(exc: BaseException) -> str:
    raw = (getattr(exc, "message", None) or str(exc) or type(exc).__name__)[:1000]
    try:
        redacted = Redactor().redact(raw).value
    except RedactionError:
        return "checkpoint or recovery operation failed"
    if not isinstance(redacted, str):
        return "checkpoint or recovery operation failed"
    return redacted


def _safe_line(value: str) -> str:
    return " ".join(value.replace("\x00", "").splitlines()).strip()[:4000]


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
