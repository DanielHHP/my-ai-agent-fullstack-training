"""Bounded local control-plane services used by the guarded CLI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .approval import ApprovalStoreError
from .audit import AuditEventRecord, AuditEventType
from .config import ResolvedHarnessConfig, load_config
from .domain import (
    Approval,
    ApprovalStatus,
    Approver,
    Clock,
    ErrorCode,
    Run,
    RunResult,
    SystemClock,
)
from .redaction import Redactor
from .storage import SQLiteStorage

_ACTIVE_APPROVAL_STATUSES = frozenset({ApprovalStatus.PENDING, ApprovalStatus.APPROVED})
_REJECT_REASON_CODE = "CLI_OPERATOR_REJECTED"


class ControlError(RuntimeError):
    """Stable control-plane failure that exposes no SQL or secret values."""

    def __init__(self, code: ErrorCode, operation: str, message: str) -> None:
        self.code = code
        self.operation = operation
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


@dataclass(frozen=True)
class ApprovalView:
    """One bounded approval projection with persisted and effective status."""

    approval: Approval
    effective_status: ApprovalStatus

    def as_dict(self) -> dict[str, Any]:
        approval = self.approval
        return {
            "approval_id": approval.approval_id,
            "run_id": approval.run_id,
            "tool_call_id": approval.tool_call_id,
            "task_id": approval.task_id,
            "task_kind": approval.task_kind.value,
            "tool_name": approval.tool_name,
            "status": approval.status.value,
            "effective_status": self.effective_status.value,
            "revision": approval.revision,
            "requested_by": approval.requested_by,
            "decided_by": approval.decided_by,
            "requested_at": _serialize_datetime(approval.requested_at),
            "expires_at": _serialize_datetime(approval.expires_at),
            "allowed_approver_roles": list(approval.allowed_approver_roles),
        }


@dataclass(frozen=True)
class ApprovalMutationResult:
    """Result of one approve/reject command."""

    approval: Approval
    run: Run
    effective_status: ApprovalStatus

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval": ApprovalView(self.approval, self.effective_status).as_dict(),
            "run": {
                "run_id": self.run.run_id,
                "status": self.run.status.value,
                "stop_reason": self.run.stop_reason.value if self.run.stop_reason else None,
                "acceptance_status": self.run.acceptance_status.value,
                "last_checkpoint_id": self.run.last_checkpoint_id,
            },
        }


def effective_approval_status(
    approval: Approval,
    now: datetime,
) -> ApprovalStatus:
    """Return the status exposed to humans without mutating persisted state."""

    if approval.status in _ACTIVE_APPROVAL_STATUSES and now >= approval.expires_at:
        return ApprovalStatus.EXPIRED
    return approval.status


class ApprovalControlService:
    """Read, decide, and refresh approvals without executing tool handlers."""

    def __init__(self, config_path: Path, *, clock: Clock | None = None) -> None:
        self._config_path = Path(config_path)
        self._clock = clock or SystemClock()

    def list(
        self,
        *,
        run_id: str | None = None,
        statuses: tuple[ApprovalStatus, ...] = (),
        limit: int = 100,
    ) -> tuple[ApprovalView, ...]:
        """List approvals across runs without materializing expiry."""

        config = self._load_config()
        storage = self._storage(config)
        storage.initialize()
        now = self._clock.now()
        with storage.transaction(actor="cli-approvals-read") as unit:
            approvals = unit.approvals.list_all(
                run_id=run_id,
                effective_statuses=statuses,
                limit=limit,
            )
        return tuple(
            ApprovalView(
                approval=approval,
                effective_status=effective_approval_status(approval, now),
            )
            for approval in approvals
        )

    def approve(
        self,
        approval_id: str,
        *,
        actor: str,
        roles: tuple[str, ...],
    ) -> ApprovalMutationResult:
        """Approve one pending request without executing its handler."""

        return self._decide(
            approval_id,
            actor=actor,
            roles=roles,
            approve=True,
        )

    def reject(
        self,
        approval_id: str,
        *,
        actor: str,
        roles: tuple[str, ...],
    ) -> ApprovalMutationResult:
        """Reject one pending request without executing its handler."""

        return self._decide(
            approval_id,
            actor=actor,
            roles=roles,
            approve=False,
        )

    def refresh(self, approval_id: str) -> ApprovalMutationResult:
        """Materialize one due approval through the controlled approval lifecycle."""

        config = self._load_config()
        storage = self._storage(config)
        storage.initialize()
        with storage.transaction(actor="cli-approval-refresh") as unit:
            located = unit.approvals.locate(approval_id)
            now = self._clock.now()
            if located.status in _ACTIVE_APPROVAL_STATUSES and now >= located.expires_at:
                lease = unit.state.acquire_lease(
                    located.run_id,
                    owner_id="cli-approval-refresh",
                )
                approval = unit.approvals.load(approval_id, lease_token=lease.token)
                run = unit.state.get_run(located.run_id)
                unit.state.release_lease(lease)
            else:
                approval = located
                run = unit.state.get_run(located.run_id)
        return ApprovalMutationResult(
            approval=approval,
            run=run,
            effective_status=effective_approval_status(approval, self._clock.now()),
        )

    def _decide(
        self,
        approval_id: str,
        *,
        actor: str,
        roles: tuple[str, ...],
        approve: bool,
    ) -> ApprovalMutationResult:
        config = self._load_config()
        storage = self._storage(config)
        storage.initialize()
        approver = Approver(actor=actor, roles=roles)
        failure: ApprovalStoreError | None = None
        with storage.transaction(actor=actor) as unit:
            located = unit.approvals.locate(approval_id)
            lease = unit.state.acquire_lease(located.run_id, owner_id=f"cli:{actor}")
            try:
                current = unit.approvals.load(approval_id, lease_token=lease.token)
                if current.status != ApprovalStatus.PENDING:
                    run = unit.state.get_run(located.run_id)
                    code = (
                        ErrorCode.APPROVAL_EXPIRED
                        if current.status == ApprovalStatus.EXPIRED
                        else ErrorCode.APPROVAL_NOT_EXECUTABLE
                    )
                    failure = ApprovalStoreError(
                        code,
                        operation="decide",
                        message="approval is not pending",
                    )
                    approval = current
                elif approve:
                    approval = unit.approvals.approve(
                        approval_id,
                        expected_revision=current.revision,
                        approver=approver,
                        lease_token=lease.token,
                    )
                else:
                    approval = unit.approvals.reject(
                        approval_id,
                        expected_revision=current.revision,
                        approver=approver,
                        reason_code=_REJECT_REASON_CODE,
                        lease_token=lease.token,
                    )
                if failure is None:
                    run = unit.state.get_run(located.run_id)
                unit.state.release_lease(lease)
            except Exception:
                raise
        if failure is not None:
            raise failure
        return ApprovalMutationResult(
            approval=approval,
            run=run,
            effective_status=effective_approval_status(approval, self._clock.now()),
        )

    def _load_config(self) -> ResolvedHarnessConfig:
        try:
            return load_config(self._config_path)
        except Exception as exc:
            raise ControlError(
                ErrorCode.CLI_INSPECTION_UNAVAILABLE,
                "load_config",
                _safe_message(exc),
            ) from exc

    def _storage(self, config: ResolvedHarnessConfig) -> SQLiteStorage:
        return SQLiteStorage(
            Path(config.state_dir) / "harness.sqlite3",
            Path(config.workspace_root),
            self._clock,
        )


class InspectionService:
    """Read persisted Run facts without constructing Harness, Gate, or models."""

    def __init__(self, config_path: Path, *, clock: Clock | None = None) -> None:
        self._config_path = Path(config_path)
        self._clock = clock or SystemClock()
        self._redactor = Redactor()

    def inspect_run(
        self,
        run_id: str,
        *,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
        audit_limit: int = 100,
    ) -> dict[str, Any]:
        """Return one bounded inspection projection for a persisted Run."""

        if not 1 <= audit_limit <= 1000:
            raise ControlError(
                ErrorCode.CLI_USAGE_ERROR,
                "inspect",
                "audit limit must be between 1 and 1000",
            )
        config = self._load_config()
        storage = SQLiteStorage(
            Path(config.state_dir) / "harness.sqlite3",
            Path(config.workspace_root),
            self._clock,
        )
        storage.initialize()
        now = self._clock.now()
        with storage.transaction(actor="cli-inspect") as unit:
            snapshot = unit.state.get_run_snapshot(run_id)
            chain = unit.audit.verify_chain(run_id)
            if not chain.valid:
                raise ControlError(
                    chain.code or ErrorCode.AUDIT_CHAIN_INVALID,
                    "inspect",
                    "audit chain verification failed",
                )
            if tool_call_id is not None:
                events = unit.audit.list_tool_events(
                    run_id,
                    tool_call_id,
                    limit=min(audit_limit + 1, 1000),
                )
                if not events:
                    raise ControlError(
                        ErrorCode.CLI_INSPECTION_UNAVAILABLE,
                        "inspect",
                        "tool call does not exist in this run",
                    )
            elif approval_id is not None:
                if not any(item.approval_id == approval_id for item in snapshot.approvals):
                    raise ControlError(
                        ErrorCode.CLI_INSPECTION_UNAVAILABLE,
                        "inspect",
                        "approval does not exist in this run",
                    )
                events = unit.audit.list_approval_events(
                    run_id,
                    approval_id,
                    limit=min(audit_limit + 1, 1000),
                )
            else:
                head = unit.audit.head(run_id)
                head_sequence = head.sequence if head is not None else 0
                after_sequence = max(0, head_sequence - audit_limit)
                events = unit.audit.list_run_events(
                    run_id,
                    after_sequence=after_sequence,
                    limit=min(audit_limit + 1, 1000),
                )
            audit_truncated = len(events) > audit_limit
            visible_events = events[:audit_limit]
        run = snapshot.run
        approval_views = tuple(
            ApprovalView(
                approval=approval,
                effective_status=effective_approval_status(approval, now),
            )
            for approval in snapshot.approvals
        )
        recoveries = [
            self._audit_event_view(event)
            for event in visible_events
            if event.event_type == AuditEventType.RECOVERY_RECORDED
        ]
        projection = {
            "run": {
                "run_id": run.run_id,
                "trace_id": run.trace_id,
                "task_id": run.task_id,
                "task_kind": run.task_kind.value,
                "status": run.status.value,
                "stop_reason": run.stop_reason.value if run.stop_reason else None,
                "acceptance_status": run.acceptance_status.value,
                "config_fingerprint": run.config_fingerprint,
                "workspace": run.workspace.model_dump(mode="json"),
                "candidate_final_answer": run.candidate_final_answer,
                "last_checkpoint_id": run.last_checkpoint_id,
                "created_at": _serialize_datetime(run.created_at),
                "updated_at": _serialize_datetime(run.updated_at),
            },
            "budget": run.budget.model_dump(mode="json"),
            "usage": run.usage.model_dump(mode="json"),
            "steps": [
                {
                    "step_id": step.step_id,
                    "ordinal": step.ordinal,
                    "status": step.status.value,
                    "summary": _bounded_text(step.summary),
                }
                for step in snapshot.steps
            ],
            "approvals": [view.as_dict() for view in approval_views],
            "invocations": [
                {
                    "tool_call_id": item.tool_call_id,
                    "step_id": item.step_id,
                    "tool_name": item.tool_name,
                    "status": item.status.value,
                    "risk": item.risk.value,
                    "approval_id": item.approval_id,
                    "args_digest": item.args_digest,
                    "result_summary": _bounded_text(item.result_summary or ""),
                    "evidence_ids": list(item.evidence_ids),
                    "error_code": item.error_code,
                }
                for item in snapshot.invocations
            ],
            "completion": (
                snapshot.completion.model_dump(mode="json")
                if snapshot.completion is not None
                else None
            ),
            "recoveries": recoveries,
            "audit": {
                "head_sequence": chain.last_sequence,
                "event_count": chain.event_count,
                "valid": chain.valid,
                "truncated": audit_truncated,
                "events": [self._audit_event_view(event) for event in visible_events],
            },
        }
        return self._safe_projection(projection)

    def get_run_result(
        self,
        run_id: str,
        *,
        error_code: str | None = None,
    ) -> RunResult:
        """Read a terminal or waiting Run as the public CLI result type."""

        config = self._load_config()
        storage = SQLiteStorage(
            Path(config.state_dir) / "harness.sqlite3",
            Path(config.workspace_root),
            self._clock,
        )
        storage.initialize()
        with storage.transaction(actor="cli-result-read") as unit:
            snapshot = unit.state.get_run_snapshot(run_id)
            head = unit.audit.head(run_id)
        run = snapshot.run
        return RunResult(
            run_id=run.run_id,
            trace_id=run.trace_id,
            status=run.status,
            stop_reason=run.stop_reason,
            acceptance_status=run.acceptance_status,
            completion=snapshot.completion,
            candidate_final_answer=run.candidate_final_answer,
            approval_id=next(
                (
                    item.approval_id
                    for item in snapshot.approvals
                    if effective_approval_status(item, self._clock.now())
                    in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
                ),
                None,
            ),
            last_checkpoint_id=run.last_checkpoint_id,
            last_audit_sequence=head.sequence if head is not None else 0,
            error_code=error_code,
        )

    def _audit_event_view(self, event: AuditEventRecord) -> dict[str, Any]:
        return {
            "sequence": event.sequence,
            "event_type": event.event_type.value,
            "phase": event.phase.value,
            "status": event.status.value,
            "decision": event.decision.value,
            "code": event.code,
            "step_id": event.step_id,
            "tool_call_id": event.tool_call_id,
            "approval_id": event.approval_id,
            "actor": event.actor,
            "tool_name": event.tool_name,
            "rule_id": event.rule_id,
            "input_summary": event.input_summary,
            "output_summary": event.output_summary,
        }

    def _safe_projection(self, value: object) -> dict[str, Any]:
        result = self._redactor.redact(value)
        if isinstance(result.value, dict):
            return {str(key): item for key, item in result.value.items()}
        return {
            "value": result.value,
            "redaction_truncated": result.truncated,
            "redaction_count": result.redaction_count,
        }

    def _load_config(self) -> ResolvedHarnessConfig:
        try:
            return load_config(self._config_path)
        except Exception as exc:
            raise ControlError(
                ErrorCode.CLI_INSPECTION_UNAVAILABLE,
                "load_config",
                _safe_message(exc),
            ) from exc


def _bounded_text(value: str, limit: int = 2000) -> str:
    redacted = Redactor().redact(value)
    text = redacted.value if isinstance(redacted.value, str) else str(redacted.value)
    if len(text) <= limit:
        return text
    return f"{text[: limit - 16]}\n[TRUNCATED]"


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_message(exc: BaseException) -> str:
    message = getattr(exc, "message", None) or str(exc)
    normalized = " ".join(str(message).split())
    redacted = Redactor().redact(normalized).value
    text = redacted if isinstance(redacted, str) else normalized
    return text[:1000]


def json_dumps(value: object) -> str:
    """Serialize CLI data deterministically with a stable failure code."""

    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ControlError(
            ErrorCode.CLI_OUTPUT_INVALID,
            "serialize",
            "CLI output is not valid JSON",
        ) from exc


__all__ = [
    "ApprovalControlService",
    "ApprovalMutationResult",
    "ApprovalView",
    "ControlError",
    "InspectionService",
    "effective_approval_status",
    "json_dumps",
]
