"""Transactional approval lifecycle for guarded tool execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .audit import AuditDecision, AuditEventDraft, AuditEventType, AuditStatus
from .domain import (
    Approval,
    ApprovalDecision,
    ApprovalId,
    ApprovalRequest,
    ApprovalStatus,
    Approver,
    ChannelDecision,
    ErrorCode,
    GatePhase,
    IdFactory,
    InvocationStatus,
    RunStatus,
    Sha256Digest,
    TaskKind,
    Uuid4IdFactory,
)
from .redaction import Redactor
from .storage import StorageError

if TYPE_CHECKING:
    from .storage import StorageUnitOfWork


_ACTIVE_APPROVAL_STATUSES = frozenset({ApprovalStatus.PENDING, ApprovalStatus.APPROVED})
_TERMINAL_APPROVAL_STATUSES = frozenset(
    {
        ApprovalStatus.CONSUMED,
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.INVALIDATED,
    }
)
_DETERMINISTIC_RESULT_STATUSES = frozenset(
    {
        InvocationStatus.SUCCEEDED,
        InvocationStatus.FAILED,
        InvocationStatus.TIMED_OUT,
    }
)


class ApprovalStoreError(RuntimeError):
    """Stable approval failure without exposing secret values or SQL."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        operation: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.operation = operation
        self.message = message[:1000]
        self.retryable = retryable
        super().__init__(f"{code.value}: {self.message}")


def canonical_approval_args(
    tool_name: str,
    normalized_args: Mapping[str, object],
) -> bytes:
    """Return the canonical domain-separated bytes used for approval binding."""

    _validate_json_value(normalized_args)
    payload = {
        "schema": "guarded-harness/approval-args/v1",
        "tool_name": tool_name,
        "normalized_args": normalized_args,
    }
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_INVALID,
            operation="canonicalize_args",
            message="approval arguments are not canonical JSON",
        ) from exc
    if len(encoded) > 65_536:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_INVALID,
            operation="canonicalize_args",
            message="approval arguments exceed the size limit",
        )
    return encoded


def approval_args_digest(
    tool_name: str,
    normalized_args: Mapping[str, object],
) -> Sha256Digest:
    """Return the stable digest binding one tool name and its normalized arguments."""

    payload = canonical_approval_args(tool_name, normalized_args)
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def approval_binding_digest(
    *,
    run_id: str,
    tool_call_id: str,
    task_id: str,
    task_kind: TaskKind,
    git_root: str,
    workspace_root: str,
    workspace_prefix: str,
    tool_name: str,
    canonical_args_digest: str,
    config_fingerprint: str,
    requested_by: str,
    allowed_approver_roles: tuple[str, ...],
    requested_at: datetime,
    expires_at: datetime,
) -> Sha256Digest:
    """Return the stable digest binding the complete approval context."""

    payload = {
        "schema": "guarded-harness/approval-binding/v1",
        "run_id": run_id,
        "tool_call_id": tool_call_id,
        "task_id": task_id,
        "task_kind": task_kind.value,
        "git_root": git_root,
        "workspace_root": workspace_root,
        "workspace_prefix": workspace_prefix,
        "tool_name": tool_name,
        "canonical_args_digest": canonical_args_digest,
        "config_fingerprint": config_fingerprint,
        "requested_by": requested_by,
        "allowed_approver_roles": sorted(role.casefold() for role in allowed_approver_roles),
        "requested_at": _serialize_datetime(requested_at),
        "expires_at": _serialize_datetime(expires_at),
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class ApprovalStore:
    """Approval operations bound to one active SQLite UnitOfWork."""

    def __init__(
        self,
        unit_of_work: StorageUnitOfWork,
        *,
        id_factory: IdFactory | None = None,
    ) -> None:
        unit_of_work.ensure_active()
        self._unit = unit_of_work
        self._connection = unit_of_work.connection
        self._clock = unit_of_work.storage.clock
        self._id_factory = id_factory or Uuid4IdFactory()
        self._redactor = Redactor()

    def create(
        self,
        request: ApprovalRequest,
        *,
        lease_token: str,
    ) -> Approval:
        """Create one approval and bind it to an existing prepared invocation."""

        self._ensure_active()
        if request.requested_by != self._unit.actor:
            self._raise(
                ErrorCode.APPROVAL_INVALID,
                "create",
                "requested_by must match the trusted transaction actor",
            )
        now = self._now()
        self._verify_lease(request.run_id, lease_token, now)
        run = self._unit.state.get_run(request.run_id)
        invocation = self._invocation_row(request.run_id, request.tool_call_id)
        if invocation is None:
            self._raise(
                ErrorCode.APPROVAL_NOT_FOUND,
                "create",
                "tool invocation does not exist in this run",
            )
        args_digest = approval_args_digest(str(invocation["tool_name"]), request.normalized_args)
        if args_digest != str(invocation["args_digest"]):
            self._raise(
                ErrorCode.APPROVAL_BINDING_MISMATCH,
                "create",
                "normalized arguments do not match the prepared invocation",
            )
        existing = self._connection.execute(
            "SELECT * FROM approvals WHERE run_id = ? AND tool_call_id = ?",
            (request.run_id, request.tool_call_id),
        ).fetchone()
        if existing is not None:
            return self._handle_duplicate(
                existing,
                request=request,
                args_digest=args_digest,
                now=now,
            )
        if str(invocation["status"]) != InvocationStatus.PREPARED.value:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "create",
                "approval can only be requested for a prepared invocation",
            )
        if run.status != RunStatus.RUNNING:
            self._raise(
                ErrorCode.APPROVAL_INVALID,
                "create",
                "approval requests require a running run",
            )
        requested_at = now
        expires_at = now + timedelta(seconds=request.ttl_seconds)
        roles = _normalized_roles(request.allowed_approver_roles)
        binding_digest = approval_binding_digest(
            run_id=run.run_id,
            tool_call_id=request.tool_call_id,
            task_id=run.task_id,
            task_kind=run.task_kind,
            git_root=run.workspace.git_root,
            workspace_root=run.workspace.workspace_root,
            workspace_prefix=run.workspace.workspace_prefix,
            tool_name=str(invocation["tool_name"]),
            canonical_args_digest=args_digest,
            config_fingerprint=run.config_fingerprint,
            requested_by=request.requested_by,
            allowed_approver_roles=roles,
            requested_at=requested_at,
            expires_at=expires_at,
        )
        approval_id = self._id_factory.new_approval_id()
        roles_json = _json_dumps(list(roles))
        self._trigger("approval:before-insert")
        try:
            self._connection.execute(
                """
                INSERT INTO approvals(
                    approval_id, schema_version, run_id, tool_call_id, task_id,
                    task_kind, git_root, workspace_root, workspace_prefix, tool_name,
                    canonical_args_digest, binding_digest, config_fingerprint,
                    requested_by, allowed_approver_roles_json, status, revision,
                    requested_at, expires_at, decided_at, decided_by, claimed_at,
                    execution_intent_id, consumed_at, consumption_code,
                    created_at, updated_at
                ) VALUES (
                    ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0,
                    ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?
                )
                """,
                (
                    approval_id,
                    run.run_id,
                    request.tool_call_id,
                    run.task_id,
                    run.task_kind.value,
                    run.workspace.git_root,
                    run.workspace.workspace_root,
                    run.workspace.workspace_prefix,
                    str(invocation["tool_name"]),
                    args_digest,
                    binding_digest,
                    run.config_fingerprint,
                    request.requested_by,
                    roles_json,
                    _serialize_datetime(requested_at),
                    _serialize_datetime(expires_at),
                    _serialize_datetime(now),
                    _serialize_datetime(now),
                ),
            )
        except sqlite3.IntegrityError:
            self._raise(
                ErrorCode.APPROVAL_CONFLICT,
                "create",
                "approval identity or binding already exists",
            )
        changed = self._connection.execute(
            """
            UPDATE tool_calls
            SET approval_id = ?, revision = revision + 1, updated_at = ?
            WHERE run_id = ? AND tool_call_id = ? AND status = 'PREPARED'
              AND approval_id IS NULL AND revision = ?
            """,
            (
                approval_id,
                _serialize_datetime(now),
                run.run_id,
                request.tool_call_id,
                int(invocation["revision"]),
            ),
        ).rowcount
        if changed != 1:
            self._raise(
                ErrorCode.APPROVAL_CONFLICT,
                "create",
                "tool invocation approval association changed concurrently",
            )
        self._append_event(
            run_id=run.run_id,
            event_type=AuditEventType.APPROVAL_REQUESTED,
            status=AuditStatus.PENDING,
            decision=AuditDecision.NOT_APPLICABLE,
            code=ErrorCode.APPROVAL_REQUIRED.value,
            tool_call_id=request.tool_call_id,
            approval_id=approval_id,
            tool_name=str(invocation["tool_name"]),
            rule_id="APPROVAL_BINDING_V1",
            input_summary={
                "allowed_roles": list(roles),
                "ttl_seconds": request.ttl_seconds,
                "canonical_args_digest": args_digest,
            },
            output_summary={"binding_digest": binding_digest, "status": "PENDING"},
        )
        return self.load(approval_id, lease_token=lease_token)

    def load(self, approval_id: str, *, lease_token: str) -> Approval:
        """Load and revalidate one approval, materializing expiry when needed."""

        self._ensure_active()
        row = self._required_row(approval_id)
        self._verify_lease(str(row["run_id"]), lease_token, self._now())
        return self._materialize_expiry(approval_from_row(row))

    def list_for_run(
        self,
        run_id: str,
        *,
        statuses: tuple[ApprovalStatus, ...] = (),
        after_created_at: datetime | None = None,
        limit: int = 100,
        lease_token: str,
    ) -> tuple[Approval, ...]:
        """List approvals for one run in stable request order."""

        self._ensure_active()
        if not 1 <= limit <= 1000:
            self._raise(
                ErrorCode.APPROVAL_INVALID,
                "list_for_run",
                "approval list limit must be between 1 and 1000",
            )
        now = self._now()
        self._verify_lease(run_id, lease_token, now)
        clauses = ["run_id = ?"]
        parameters: list[object] = [run_id]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(status.value for status in statuses)
        if after_created_at is not None:
            clauses.append("requested_at > ?")
            parameters.append(_serialize_datetime(after_created_at))
        parameters.append(limit)
        rows = self._connection.execute(
            f"""
            SELECT * FROM approvals
            WHERE {" AND ".join(clauses)}
            ORDER BY requested_at, approval_id LIMIT ?
            """,
            parameters,
        ).fetchall()
        return tuple(self._materialize_expiry(approval_from_row(row)) for row in rows)

    def approve(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        approver: Approver,
        lease_token: str,
    ) -> Approval:
        """Approve one pending request without executing its handler."""

        return self._decide(
            approval_id,
            expected_revision=expected_revision,
            approver=approver,
            lease_token=lease_token,
            decision=ApprovalDecision.APPROVE,
            reason_code=None,
        )

    def reject(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        approver: Approver,
        reason_code: str,
        lease_token: str,
    ) -> Approval:
        """Reject one pending request without executing its handler."""

        return self._decide(
            approval_id,
            expected_revision=expected_revision,
            approver=approver,
            lease_token=lease_token,
            decision=ApprovalDecision.REJECT,
            reason_code=reason_code,
        )

    def expire_due(self, run_id: str, *, lease_token: str) -> tuple[Approval, ...]:
        """Materialize due PENDING/APPROVED rows and append one event per transition."""

        self._ensure_active()
        now = self._now()
        self._verify_lease(run_id, lease_token, now)
        rows = self._connection.execute(
            """
            SELECT * FROM approvals
            WHERE run_id = ? AND status IN ('PENDING', 'APPROVED') AND expires_at <= ?
            ORDER BY requested_at, approval_id
            """,
            (run_id, _serialize_datetime(now)),
        ).fetchall()
        return tuple(self._expire(approval_from_row(row), now=now) for row in rows)

    def invalidate(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        reason_code: str,
        lease_token: str,
    ) -> Approval:
        """Invalidate an approval that can no longer authorize execution."""

        self._ensure_active()
        approval = self._required_approval(approval_id)
        now = self._now()
        self._verify_lease(approval.run_id, lease_token, now)
        if approval.revision != expected_revision:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "invalidate", "approval revision changed")
        if approval.status == ApprovalStatus.CLAIMED:
            invocation = self._invocation_row(approval.run_id, approval.tool_call_id)
            if invocation is None or str(invocation["status"]) != InvocationStatus.PREPARED.value:
                self._raise(
                    ErrorCode.APPROVAL_NOT_EXECUTABLE,
                    "invalidate",
                    "claimed approval cannot be invalidated after execution begins",
                )
        elif approval.status not in _ACTIVE_APPROVAL_STATUSES:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "invalidate",
                "approval state cannot be invalidated",
            )
        stable_reason = _stable_code(reason_code, "invalidate")
        self._trigger("approval:before-update")
        changed = self._connection.execute(
            """
            UPDATE approvals
            SET status = 'INVALIDATED', claimed_at = NULL,
                execution_intent_id = NULL, consumed_at = NULL,
                consumption_code = NULL, revision = revision + 1, updated_at = ?
            WHERE approval_id = ? AND status = ? AND revision = ?
            """,
            (
                _serialize_datetime(now),
                approval_id,
                approval.status.value,
                expected_revision,
            ),
        ).rowcount
        if changed != 1:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "invalidate", "approval changed concurrently")
        self._append_event(
            run_id=approval.run_id,
            event_type=AuditEventType.APPROVAL_INVALIDATED,
            status=AuditStatus.DENIED,
            decision=AuditDecision.DENY,
            code=stable_reason,
            tool_call_id=approval.tool_call_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            rule_id="APPROVAL_BINDING_V1",
            output_summary={"from": approval.status.value, "reason": stable_reason},
        )
        return self._required_approval(approval_id, expire=False)

    def claim(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        execution_intent_id: str,
        lease_token: str,
    ) -> Approval:
        """Claim an approved request exactly once for one execution intent."""

        self._ensure_active()
        approval = self._required_approval(approval_id)
        now = self._now()
        self._verify_lease(approval.run_id, lease_token, now)
        approval = self._materialize_expiry(approval)
        if approval.status == ApprovalStatus.EXPIRED:
            self._raise(ErrorCode.APPROVAL_EXPIRED, "claim", "approval has expired")
        if approval.status == ApprovalStatus.CLAIMED:
            self._raise(
                ErrorCode.APPROVAL_ALREADY_CLAIMED,
                "claim",
                "approval is already claimed",
            )
        if approval.revision != expected_revision:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "claim", "approval revision changed")
        if approval.status != ApprovalStatus.APPROVED:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "claim",
                "approval is not approved",
            )
        if now >= approval.expires_at:
            self._raise(ErrorCode.APPROVAL_EXPIRED, "claim", "approval has expired")
        if execution_intent_id != approval.tool_call_id:
            self._raise(
                ErrorCode.APPROVAL_BINDING_MISMATCH,
                "claim",
                "execution intent must equal the tool call identity",
            )
        if self._unit.state.get_run(approval.run_id).status != RunStatus.WAITING_APPROVAL:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "claim",
                "run is not waiting for approval",
            )
        self._validate_current_binding(approval)
        self._trigger("approval:before-update")
        changed = self._connection.execute(
            """
            UPDATE approvals
            SET status = 'CLAIMED', claimed_at = ?, execution_intent_id = ?,
                revision = revision + 1, updated_at = ?
            WHERE approval_id = ? AND status = 'APPROVED' AND revision = ?
              AND expires_at > ?
            """,
            (
                _serialize_datetime(now),
                execution_intent_id,
                _serialize_datetime(now),
                approval_id,
                expected_revision,
                _serialize_datetime(now),
            ),
        ).rowcount
        if changed != 1:
            self._raise(
                ErrorCode.APPROVAL_ALREADY_CLAIMED,
                "claim",
                "approval was claimed or changed concurrently",
            )
        self._append_event(
            run_id=approval.run_id,
            event_type=AuditEventType.APPROVAL_CLAIMED,
            status=AuditStatus.STARTED,
            decision=AuditDecision.NOT_APPLICABLE,
            tool_call_id=approval.tool_call_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            rule_id="APPROVAL_BINDING_V1",
            output_summary={"execution_intent_id": execution_intent_id},
        )
        return self._required_approval(approval_id, expire=False)

    def resume_claim(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        execution_intent_id: str,
        lease_token: str,
    ) -> Approval:
        """Recover an existing claim only while the invocation is still PREPARED."""

        self._ensure_active()
        approval = self._required_approval(approval_id)
        now = self._now()
        self._verify_lease(approval.run_id, lease_token, now)
        self._validate_current_binding(approval)
        if approval.revision != expected_revision:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "resume_claim", "approval revision changed")
        if (
            approval.status != ApprovalStatus.CLAIMED
            or approval.execution_intent_id != execution_intent_id
            or execution_intent_id != approval.tool_call_id
        ):
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "resume_claim",
                "approval claim is not resumable for this execution intent",
            )
        if self._unit.state.get_run(approval.run_id).status != RunStatus.RUNNING:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "resume_claim",
                "run is not running",
            )
        invocation = self._invocation_row(approval.run_id, approval.tool_call_id)
        if invocation is None or str(invocation["status"]) != InvocationStatus.PREPARED.value:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "resume_claim",
                "execution has already started or reached a terminal state",
            )
        self._append_event(
            run_id=approval.run_id,
            event_type=AuditEventType.APPROVAL_CLAIM_RECOVERED,
            status=AuditStatus.STARTED,
            decision=AuditDecision.NOT_APPLICABLE,
            tool_call_id=approval.tool_call_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            rule_id="APPROVAL_BINDING_V1",
            output_summary={"execution_intent_id": execution_intent_id},
        )
        return approval

    def consume(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        execution_intent_id: str,
        result_status: InvocationStatus,
        result_code: str | None,
        lease_token: str,
    ) -> Approval:
        """Consume a claimed approval only after a deterministic invocation result."""

        self._ensure_active()
        approval = self._required_approval(approval_id)
        now = self._now()
        self._verify_lease(approval.run_id, lease_token, now)
        if approval.revision != expected_revision:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "consume", "approval revision changed")
        if (
            approval.status != ApprovalStatus.CLAIMED
            or approval.execution_intent_id != execution_intent_id
            or execution_intent_id != approval.tool_call_id
        ):
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "consume",
                "approval claim does not match this execution intent",
            )
        if self._unit.state.get_run(approval.run_id).status != RunStatus.RUNNING:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "consume",
                "run is not running",
            )
        if result_status not in _DETERMINISTIC_RESULT_STATUSES:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "consume",
                "unknown results cannot consume approval",
            )
        invocation = self._invocation_row(approval.run_id, approval.tool_call_id)
        if invocation is None or str(invocation["status"]) != result_status.value:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "consume",
                "invocation does not have the supplied deterministic result",
            )
        stable_code = _optional_stable_code(result_code, "consume")
        self._trigger("approval:before-update")
        changed = self._connection.execute(
            """
            UPDATE approvals
            SET status = 'CONSUMED', consumed_at = ?, consumption_code = ?,
                revision = revision + 1, updated_at = ?
            WHERE approval_id = ? AND status = 'CLAIMED' AND revision = ?
              AND execution_intent_id = ?
            """,
            (
                _serialize_datetime(now),
                stable_code,
                _serialize_datetime(now),
                approval_id,
                expected_revision,
                execution_intent_id,
            ),
        ).rowcount
        if changed != 1:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "consume", "approval changed concurrently")
        self._append_event(
            run_id=approval.run_id,
            event_type=AuditEventType.APPROVAL_CONSUMED,
            status=AuditStatus.SUCCEEDED,
            decision=AuditDecision.NOT_APPLICABLE,
            code=stable_code,
            tool_call_id=approval.tool_call_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            rule_id="APPROVAL_BINDING_V1",
            input_summary={"execution_intent_id": execution_intent_id},
            output_summary={"result_status": result_status.value, "result_code": stable_code},
        )
        return self._required_approval(approval_id, expire=False)

    def _decide(
        self,
        approval_id: str,
        *,
        expected_revision: int,
        approver: Approver,
        lease_token: str,
        decision: ApprovalDecision,
        reason_code: str | None,
    ) -> Approval:
        self._ensure_active()
        approval = self._required_approval(approval_id)
        now = self._now()
        self._verify_lease(approval.run_id, lease_token, now)
        approval = self._materialize_expiry(approval)
        if approval.status == ApprovalStatus.EXPIRED:
            self._raise(ErrorCode.APPROVAL_EXPIRED, "decide", "approval has expired")
        if approval.revision != expected_revision:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "decide", "approval revision changed")
        if approval.status != ApprovalStatus.PENDING:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "decide",
                "approval is no longer pending",
            )
        if approver.actor != self._unit.actor:
            self._raise(
                ErrorCode.APPROVAL_APPROVER_DENIED,
                "decide",
                "approver must match the trusted transaction actor",
            )
        if self._unit.state.get_run(approval.run_id).status != RunStatus.WAITING_APPROVAL:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "decide",
                "run is not waiting for approval",
            )
        if approver.actor == approval.requested_by:
            self._raise(
                ErrorCode.APPROVAL_SELF_APPROVAL_DENIED,
                "decide",
                "requester cannot approve their own call",
            )
        allowed = {role.casefold() for role in approval.allowed_approver_roles}
        if not allowed.intersection(role.casefold() for role in approver.roles):
            self._raise(
                ErrorCode.APPROVAL_APPROVER_DENIED,
                "decide",
                "approver does not hold an allowed role",
            )
        stable_reason = (
            _stable_code(reason_code, "reject") if decision == ApprovalDecision.REJECT else None
        )
        target = (
            ApprovalStatus.APPROVED
            if decision == ApprovalDecision.APPROVE
            else ApprovalStatus.REJECTED
        )
        self._trigger("approval:before-update")
        changed = self._connection.execute(
            """
            UPDATE approvals
            SET status = ?, decided_at = ?, decided_by = ?,
                revision = revision + 1, updated_at = ?
            WHERE approval_id = ? AND status = 'PENDING' AND revision = ?
            """,
            (
                target.value,
                _serialize_datetime(now),
                approver.actor,
                _serialize_datetime(now),
                approval_id,
                expected_revision,
            ),
        ).rowcount
        if changed != 1:
            self._raise(ErrorCode.APPROVAL_CONFLICT, "decide", "approval changed concurrently")
        self._append_event(
            run_id=approval.run_id,
            event_type=AuditEventType.APPROVAL_DECIDED,
            status=(
                AuditStatus.SUCCEEDED if target == ApprovalStatus.APPROVED else AuditStatus.DENIED
            ),
            decision=(
                AuditDecision.ALLOW if target == ApprovalStatus.APPROVED else AuditDecision.DENY
            ),
            code=stable_reason,
            tool_call_id=approval.tool_call_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            rule_id="APPROVAL_ROLE_POLICY",
            input_summary={"approver": approver.actor, "roles": list(approver.roles)},
            output_summary={"status": target.value, "reason": stable_reason},
        )
        return self._required_approval(approval_id, expire=False)

    def _handle_duplicate(
        self,
        row: sqlite3.Row,
        *,
        request: ApprovalRequest,
        args_digest: str,
        now: datetime,
    ) -> Approval:
        approval = approval_from_row(row)
        if (
            approval.status in _TERMINAL_APPROVAL_STATUSES
            or approval.status == ApprovalStatus.CLAIMED
        ):
            self._raise(
                ErrorCode.APPROVAL_REPLAYED,
                "create",
                "approval for this tool call is already claimed or terminal",
            )
        existing_ttl = int((approval.expires_at - approval.requested_at).total_seconds())
        same_request = (
            approval.requested_by == request.requested_by
            and approval.tool_name == str(row["tool_name"])
            and approval.canonical_args_digest == args_digest
            and _normalized_roles(approval.allowed_approver_roles)
            == _normalized_roles(request.allowed_approver_roles)
            and existing_ttl == request.ttl_seconds
        )
        if not same_request:
            self._raise(
                ErrorCode.APPROVAL_CONFLICT,
                "create",
                "tool call already has a different approval binding",
            )
        if approval.status in _ACTIVE_APPROVAL_STATUSES and now >= approval.expires_at:
            self._raise(ErrorCode.APPROVAL_REPLAYED, "create", "approval has expired")
        if self._unit.state.get_run(approval.run_id).status != RunStatus.WAITING_APPROVAL:
            self._raise(
                ErrorCode.APPROVAL_NOT_EXECUTABLE,
                "create",
                "run is not waiting for this approval",
            )
        self._validate_current_binding(approval)
        return approval

    def _validate_current_binding(self, approval: Approval) -> None:
        run = self._unit.state.get_run(approval.run_id)
        invocation = self._invocation_row(approval.run_id, approval.tool_call_id)
        if invocation is None:
            self._raise(
                ErrorCode.APPROVAL_BINDING_MISMATCH,
                "validate_binding",
                "bound tool invocation is missing",
            )
        expected_binding = approval_binding_digest(
            run_id=run.run_id,
            tool_call_id=approval.tool_call_id,
            task_id=run.task_id,
            task_kind=run.task_kind,
            git_root=run.workspace.git_root,
            workspace_root=run.workspace.workspace_root,
            workspace_prefix=run.workspace.workspace_prefix,
            tool_name=str(invocation["tool_name"]),
            canonical_args_digest=str(invocation["args_digest"]),
            config_fingerprint=run.config_fingerprint,
            requested_by=approval.requested_by,
            allowed_approver_roles=approval.allowed_approver_roles,
            requested_at=approval.requested_at,
            expires_at=approval.expires_at,
        )
        if (
            expected_binding != approval.binding_digest
            or str(invocation["tool_name"]) != approval.tool_name
            or str(invocation["args_digest"]) != approval.canonical_args_digest
            or str(invocation["approval_id"] or "") != approval.approval_id
        ):
            self._raise(
                ErrorCode.APPROVAL_BINDING_MISMATCH,
                "validate_binding",
                "approval binding no longer matches current execution context",
            )

    def _materialize_expiry(self, approval: Approval) -> Approval:
        if approval.status not in _ACTIVE_APPROVAL_STATUSES:
            return approval
        now = self._now()
        if now < approval.expires_at:
            return approval
        return self._expire(approval, now=now)

    def _expire(self, approval: Approval, *, now: datetime) -> Approval:
        if approval.status not in _ACTIVE_APPROVAL_STATUSES:
            return approval
        self._trigger("approval:before-update")
        changed = self._connection.execute(
            """
            UPDATE approvals
            SET status = 'EXPIRED', revision = revision + 1, updated_at = ?
            WHERE approval_id = ? AND status = ? AND revision = ?
              AND expires_at <= ?
            """,
            (
                _serialize_datetime(now),
                approval.approval_id,
                approval.status.value,
                approval.revision,
                _serialize_datetime(now),
            ),
        ).rowcount
        if changed != 1:
            current = self._required_approval(approval.approval_id, expire=False)
            if current.status != ApprovalStatus.EXPIRED:
                self._raise(ErrorCode.APPROVAL_CONFLICT, "expire", "approval changed concurrently")
            return current
        self._append_event(
            run_id=approval.run_id,
            event_type=AuditEventType.APPROVAL_EXPIRED,
            status=AuditStatus.DENIED,
            decision=AuditDecision.DENY,
            code=ErrorCode.APPROVAL_EXPIRED.value,
            tool_call_id=approval.tool_call_id,
            approval_id=approval.approval_id,
            tool_name=approval.tool_name,
            rule_id="APPROVAL_TTL",
            output_summary={"from": approval.status.value, "status": "EXPIRED"},
        )
        return self._required_approval(approval.approval_id, expire=False)

    def _required_approval(self, approval_id: str, *, expire: bool = True) -> Approval:
        approval = approval_from_row(self._required_row(approval_id))
        return self._materialize_expiry(approval) if expire else approval

    def _required_row(self, approval_id: str) -> sqlite3.Row:
        row = self._get_row(approval_id)
        if row is None:
            self._raise(ErrorCode.APPROVAL_NOT_FOUND, "load", "approval does not exist")
        return row

    def _get_row(self, approval_id: str) -> sqlite3.Row | None:
        if not approval_id:
            return None
        return self._connection.execute(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()

    def _invocation_row(self, run_id: str, tool_call_id: str) -> sqlite3.Row | None:
        return self._connection.execute(
            "SELECT * FROM tool_calls WHERE run_id = ? AND tool_call_id = ?",
            (run_id, tool_call_id),
        ).fetchone()

    def _verify_lease(self, run_id: str, token: str, now: datetime) -> None:
        row = self._connection.execute(
            "SELECT token_digest, expires_at FROM run_leases WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not hmac.compare_digest(
            str(row["token_digest"]),
            _token_digest(token),
        ):
            self._raise(
                ErrorCode.STATE_LEASE_INVALID,
                "verify_lease",
                "run lease token is not current",
            )
        if now >= _parse_datetime(str(row["expires_at"])):
            self._raise(ErrorCode.STATE_LEASE_EXPIRED, "verify_lease", "run lease has expired")

    def _append_event(
        self,
        *,
        run_id: str,
        event_type: AuditEventType,
        status: AuditStatus,
        decision: AuditDecision,
        code: str | None = None,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
        tool_name: str | None = None,
        rule_id: str | None = None,
        input_summary: object = None,
        output_summary: object = None,
    ) -> None:
        self._trigger("approval:before-audit")
        self._unit.audit.append(
            AuditEventDraft(
                run_id=run_id,
                event_type=event_type,
                phase=GatePhase.APPROVAL,
                status=status,
                decision=decision,
                code=code,
                tool_call_id=tool_call_id,
                approval_id=approval_id,
                tool_name=tool_name,
                rule_id=rule_id,
                input_summary=self._redact(input_summary),
                output_summary=self._redact(output_summary),
            )
        )

    def _redact(self, value: object) -> object:
        if value is None:
            return None
        return self._redactor.redact(value).value

    def _trigger(self, point: str) -> None:
        if self._unit.storage.fault_injector is not None:
            self._unit.storage.fault_injector.trigger(point)

    def _now(self) -> datetime:
        return self._clock.now()

    def _ensure_active(self) -> None:
        try:
            self._unit.ensure_active()
        except StorageError as exc:
            raise ApprovalStoreError(
                exc.code,
                operation=exc.operation,
                message=exc.message,
                retryable=exc.retryable,
            ) from exc

    @staticmethod
    def _raise(
        code: ErrorCode,
        operation: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        raise ApprovalStoreError(
            code,
            operation=operation,
            message=message,
            retryable=retryable,
        )


class DisabledApprovalChannel:
    """Fail-closed channel used when no human approval transport is configured."""

    @property
    def name(self) -> str:
        return "disabled"

    def submit(self, approval: Approval) -> None:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_CHANNEL_UNAVAILABLE,
            operation="channel_submit",
            message="approval channel is disabled",
        )

    def poll(self, approval_id: ApprovalId) -> ChannelDecision | None:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_CHANNEL_UNAVAILABLE,
            operation="channel_poll",
            message="approval channel is disabled",
        )


def approval_from_row(row: sqlite3.Row) -> Approval:
    """Revalidate one persisted approval row into the public schema-v2 model."""

    try:
        roles_payload = json.loads(str(row["allowed_approver_roles_json"]))
        if not isinstance(roles_payload, list) or not all(
            isinstance(role, str) for role in roles_payload
        ):
            raise ValueError("approval roles are invalid")
        return Approval(
            schema_version=int(row["schema_version"]),
            approval_id=str(row["approval_id"]),
            run_id=str(row["run_id"]),
            tool_call_id=str(row["tool_call_id"]),
            task_id=str(row["task_id"]),
            task_kind=TaskKind(str(row["task_kind"])),
            git_root=str(row["git_root"]),
            workspace_root=str(row["workspace_root"]),
            workspace_prefix=str(row["workspace_prefix"]),
            tool_name=str(row["tool_name"]),
            canonical_args_digest=str(row["canonical_args_digest"]),
            binding_digest=str(row["binding_digest"]),
            config_fingerprint=str(row["config_fingerprint"]),
            requested_by=str(row["requested_by"]),
            allowed_approver_roles=tuple(roles_payload),
            status=ApprovalStatus(str(row["status"])),
            revision=int(row["revision"]),
            requested_at=_parse_datetime(str(row["requested_at"])),
            expires_at=_parse_datetime(str(row["expires_at"])),
            decided_at=(_parse_datetime(str(row["decided_at"])) if row["decided_at"] else None),
            decided_by=str(row["decided_by"]) if row["decided_by"] else None,
            claimed_at=_parse_datetime(str(row["claimed_at"])) if row["claimed_at"] else None,
            execution_intent_id=(
                str(row["execution_intent_id"]) if row["execution_intent_id"] else None
            ),
            consumed_at=(_parse_datetime(str(row["consumed_at"])) if row["consumed_at"] else None),
            consumption_code=(str(row["consumption_code"]) if row["consumption_code"] else None),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_INVALID,
            operation="load",
            message="persisted approval is invalid",
        ) from exc


def _validate_json_value(value: object, *, depth: int = 0, budget: list[int] | None = None) -> None:
    if budget is None:
        budget = [10_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_INVALID,
            operation="canonicalize_args",
            message="approval arguments exceed structural limits",
        )
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ApprovalStoreError(
                ErrorCode.APPROVAL_INVALID,
                operation="canonicalize_args",
                message="approval arguments contain a non-finite number",
            )
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ApprovalStoreError(
                    ErrorCode.APPROVAL_INVALID,
                    operation="canonicalize_args",
                    message="approval argument keys must be strings",
                )
            _validate_json_value(item, depth=depth + 1, budget=budget)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item, depth=depth + 1, budget=budget)
        return
    raise ApprovalStoreError(
        ErrorCode.APPROVAL_INVALID,
        operation="canonicalize_args",
        message="approval arguments must contain JSON values only",
    )


def _normalized_roles(roles: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted((role.casefold() for role in roles), key=str.casefold))


def _stable_code(value: str, operation: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_INVALID,
            operation=operation,
            message="approval reason code is invalid",
        )
    if not all(
        character.isupper() or character.isdigit() or character == "_" for character in value
    ):
        raise ApprovalStoreError(
            ErrorCode.APPROVAL_INVALID,
            operation=operation,
            message="approval reason code must be a stable uppercase identifier",
        )
    return value


def _optional_stable_code(value: str | None, operation: str) -> str | None:
    return _stable_code(value, operation) if value is not None else None


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _token_digest(token: str) -> str:
    return f"sha256:{hashlib.sha256(f'lease_schema:1:{token}'.encode()).hexdigest()}"


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


__all__ = [
    "ApprovalStore",
    "ApprovalStoreError",
    "DisabledApprovalChannel",
    "approval_args_digest",
    "approval_binding_digest",
    "approval_from_row",
    "canonical_approval_args",
]
