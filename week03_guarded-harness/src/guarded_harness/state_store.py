"""Transactional business-state persistence for WP-01."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from .domain import (
    AcceptanceStatus,
    ApprovalId,
    BudgetUsage,
    CompletionCheck,
    CompletionItem,
    ErrorCode,
    Evidence,
    EvidenceKind,
    InvocationStatus,
    LeasePolicy,
    PlanItem,
    Run,
    RunBudget,
    RunLease,
    RunPlan,
    RunSnapshot,
    RunStatus,
    Step,
    StepStatus,
    StopReason,
    TaskKind,
    ToolInvocation,
    ToolPermission,
    ToolRisk,
    WorkspaceIdentity,
    canonical_json_dump,
)
from .storage import StorageError

if TYPE_CHECKING:
    from .storage import StorageUnitOfWork


_RUN_TRANSITIONS = {
    RunStatus.NEW: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_APPROVAL,
            RunStatus.VERIFYING,
            RunStatus.STOPPED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
        }
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.STOPPED, RunStatus.FAILED, RunStatus.BLOCKED}
    ),
    RunStatus.VERIFYING: frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.BLOCKED}),
}
_STEP_TRANSITIONS = {
    StepStatus.PLANNED: frozenset({StepStatus.RUNNING}),
    StepStatus.RUNNING: frozenset({StepStatus.COMPLETED, StepStatus.FAILED}),
}
_TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.STOPPED, RunStatus.FAILED, RunStatus.BLOCKED}
)
_TERMINAL_ACCEPTANCE = frozenset(
    {AcceptanceStatus.ACCEPTED, AcceptanceStatus.REJECTED, AcceptanceStatus.UNVERIFIABLE}
)


class StateStoreError(RuntimeError):
    """Stable state failure that never contains row values."""

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


class StateStore:
    """State persistence operations bound to one active UnitOfWork."""

    def __init__(self, unit_of_work: StorageUnitOfWork) -> None:
        unit_of_work.ensure_active()
        self._unit = unit_of_work
        self._connection = unit_of_work.connection
        self._clock = unit_of_work.storage.clock

    def create_run(
        self,
        run: Run,
        *,
        owner_id: str,
        lease_policy: LeasePolicy | None = None,
    ) -> RunLease:
        """Atomically create a run and its first ownership lease."""

        self._ensure_active()
        if run.status != RunStatus.NEW:
            self._raise(
                ErrorCode.STATE_INVARIANT_VIOLATION,
                "create_run",
                "new runs must start in NEW",
            )
        try:
            self._connection.execute(
                """
                INSERT INTO runs (
                    run_id, trace_id, task_id, task_kind, task_description,
                    config_fingerprint, workspace_json,
                    max_steps, max_tool_calls, max_token_cost, max_wall_time_seconds,
                    used_steps, used_tool_calls, used_token_cost, used_wall_time_seconds,
                    status, stop_reason, acceptance_status,
                    current_step_id, last_checkpoint_id, resumed_from_checkpoint_id,
                    candidate_final_answer, schema_version, revision,
                    created_at, updated_at, started_at, ended_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    run.run_id,
                    run.trace_id,
                    run.task_id,
                    run.task_kind.value,
                    run.task_description,
                    run.config_fingerprint,
                    canonical_json_dump(run.workspace),
                    run.budget.max_steps,
                    run.budget.max_tool_calls,
                    str(run.budget.max_token_cost),
                    run.budget.max_wall_time_seconds,
                    run.usage.steps,
                    run.usage.tool_calls,
                    str(run.usage.token_cost),
                    run.usage.wall_time_seconds,
                    run.status.value,
                    run.stop_reason.value if run.stop_reason else None,
                    run.acceptance_status.value,
                    run.current_step_id,
                    run.last_checkpoint_id,
                    run.resumed_from_checkpoint_id,
                    run.candidate_final_answer,
                    run.schema_version,
                    0,
                    _serialize_datetime(run.created_at),
                    _serialize_datetime(run.updated_at),
                    _serialize_datetime(run.started_at) if run.started_at else None,
                    _serialize_datetime(run.ended_at) if run.ended_at else None,
                ),
            )
        except sqlite3.IntegrityError:
            self._raise(
                ErrorCode.STATE_CONFLICT,
                "create_run",
                "run_id or trace_id already exists",
            )
        return self._acquire_lease(run.run_id, owner_id, lease_policy or LeasePolicy())

    def get_run(self, run_id: str) -> Run:
        """Read and revalidate a run from persistent state."""

        self._ensure_active()
        row = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            self._raise(ErrorCode.STATE_NOT_FOUND, "get_run", "run does not exist")
        return _run_from_row(row)

    def put_plan(
        self,
        run_id: str,
        *,
        expected_revision: int,
        plan: RunPlan,
        lease_token: str,
    ) -> RunPlan:
        """Create or replace the current plan with optimistic concurrency."""

        self._ensure_active()
        now = self._now()
        self._verify_lease(run_id, lease_token, now)
        run = self.get_run(run_id)
        if plan.run_id != run_id:
            self._raise(
                ErrorCode.STATE_INVARIANT_VIOLATION,
                "put_plan",
                "plan run_id does not match target run",
            )
        if run.status in _TERMINAL_RUN_STATUSES:
            self._raise(
                ErrorCode.STATE_INVALID_TRANSITION,
                "put_plan",
                "terminal runs cannot update their plan",
            )
        if self._get_run_revision(run_id) != expected_revision:
            self._raise(ErrorCode.STATE_CONFLICT, "put_plan", "run revision does not match")
        existing = self._get_plan(run_id)
        if existing is not None and existing.items == plan.items:
            return existing
        expected_plan_revision = 0 if existing is None else existing.revision + 1
        if plan.revision != expected_plan_revision:
            self._raise(
                ErrorCode.STATE_CONFLICT,
                "put_plan",
                "plan revision is not the next version",
            )
        stored = RunPlan(
            run_id=run_id,
            revision=plan.revision,
            items=plan.items,
            updated_at=now,
        )
        items_json = _serialize_plan_items(stored.items)
        if existing is None:
            self._connection.execute(
                """
                INSERT INTO run_plans(
                    run_id, plan_schema_version, revision, items_json, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    stored.schema_version,
                    stored.revision,
                    items_json,
                    _serialize_datetime(stored.updated_at),
                ),
            )
        else:
            changed = self._connection.execute(
                """
                UPDATE run_plans
                SET plan_schema_version = ?, revision = ?, items_json = ?, updated_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (
                    stored.schema_version,
                    stored.revision,
                    items_json,
                    _serialize_datetime(stored.updated_at),
                    run_id,
                    existing.revision,
                ),
            ).rowcount
            if changed != 1:
                self._raise(ErrorCode.STATE_CONFLICT, "put_plan", "plan changed concurrently")
        self._touch_run(run_id, expected_revision, now)
        return stored

    def create_step(
        self,
        step: Step,
        *,
        expected_run_revision: int,
        lease_token: str,
    ) -> Step:
        """Persist one planned step and advance the run revision."""

        self._ensure_active()
        now = self._now()
        self._verify_lease(step.run_id, lease_token, now)
        run = self.get_run(step.run_id)
        if self._get_run_revision(step.run_id) != expected_run_revision:
            self._raise(
                ErrorCode.STATE_CONFLICT,
                "create_step",
                "run revision does not match",
            )
        if run.status in _TERMINAL_RUN_STATUSES:
            self._raise(
                ErrorCode.STATE_INVALID_TRANSITION,
                "create_step",
                "terminal runs cannot add steps",
            )
        if step.status != StepStatus.PLANNED or step.ended_at is not None:
            self._raise(
                ErrorCode.STATE_INVARIANT_VIOLATION,
                "create_step",
                "new steps must be PLANNED without an end time",
            )
        try:
            self._connection.execute(
                """
                INSERT INTO steps(
                    step_id, run_id, ordinal, status, summary, schema_version,
                    revision, started_at, ended_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step.step_id,
                    step.run_id,
                    step.ordinal,
                    step.status.value,
                    step.summary,
                    step.schema_version,
                    0,
                    _serialize_datetime(step.started_at),
                    None,
                ),
            )
        except sqlite3.IntegrityError:
            self._raise(
                ErrorCode.STATE_CONFLICT,
                "create_step",
                "step identity or ordinal is duplicated",
            )
        self._touch_run(step.run_id, expected_run_revision, now, current_step_id=step.step_id)
        return step

    def transition_step(
        self,
        step_id: str,
        *,
        expected_status: StepStatus,
        expected_revision: int,
        target: StepStatus,
        ended_at: datetime | None,
        lease_token: str,
    ) -> Step:
        """Apply one validated Step status transition."""

        self._ensure_active()
        now = self._now()
        row = self._connection.execute(
            "SELECT * FROM steps WHERE step_id = ?",
            (step_id,),
        ).fetchone()
        if row is None:
            self._raise(ErrorCode.STATE_NOT_FOUND, "transition_step", "step does not exist")
        run_id = str(row["run_id"])
        self._verify_lease(run_id, lease_token, now)
        current = _step_from_row(row)
        current_revision = int(row["revision"])
        if current_revision != expected_revision or current.status != expected_status:
            self._raise(
                ErrorCode.STATE_CONFLICT,
                "transition_step",
                "step status or revision does not match",
            )
        if target not in _STEP_TRANSITIONS.get(current.status, frozenset()):
            self._raise(
                ErrorCode.STATE_INVALID_TRANSITION,
                "transition_step",
                "step transition is not allowed",
            )
        if target in {StepStatus.COMPLETED, StepStatus.FAILED} and ended_at is None:
            ended_at = now
        updated = Step(
            step_id=current.step_id,
            run_id=current.run_id,
            ordinal=current.ordinal,
            status=target,
            summary=current.summary,
            started_at=current.started_at,
            ended_at=ended_at,
        )
        changed = self._connection.execute(
            """
            UPDATE steps
            SET status = ?, revision = revision + 1, ended_at = ?
            WHERE step_id = ? AND status = ? AND revision = ?
            """,
            (
                updated.status.value,
                _serialize_datetime(updated.ended_at) if updated.ended_at else None,
                step_id,
                current.status.value,
                current_revision,
            ),
        ).rowcount
        if changed != 1:
            self._raise(ErrorCode.STATE_CONFLICT, "transition_step", "step changed concurrently")
        run_revision = self._get_run_revision(run_id)
        current_step_id = step_id if target == StepStatus.RUNNING else None
        self._touch_run(run_id, run_revision, now, current_step_id=current_step_id)
        return updated

    def transition_run(
        self,
        run_id: str,
        *,
        expected_status: RunStatus,
        expected_revision: int,
        target: RunStatus,
        stop_reason: StopReason | None,
        ended_at: datetime | None,
        acceptance_status: AcceptanceStatus | None,
        lease_token: str,
    ) -> Run:
        """Apply one validated Run status transition."""

        self._ensure_active()
        now = self._now()
        self._verify_lease(run_id, lease_token, now)
        current = self.get_run(run_id)
        current_revision = self._get_run_revision(run_id)
        if current_revision != expected_revision or current.status != expected_status:
            self._raise(
                ErrorCode.STATE_CONFLICT,
                "transition_run",
                "run status or revision does not match",
            )
        if target not in _RUN_TRANSITIONS.get(current.status, frozenset()):
            self._raise(
                ErrorCode.STATE_INVALID_TRANSITION,
                "transition_run",
                "run transition is not allowed",
            )
        if target in _TERMINAL_RUN_STATUSES:
            ended_at = ended_at or now
        elif ended_at is not None:
            self._raise(
                ErrorCode.STATE_INVARIANT_VIOLATION,
                "transition_run",
                "non-terminal runs cannot have an end time",
            )
        if target in {RunStatus.STOPPED, RunStatus.FAILED, RunStatus.BLOCKED}:
            if stop_reason is None:
                self._raise(
                    ErrorCode.STATE_INVARIANT_VIOLATION,
                    "transition_run",
                    "stop reason is required for this terminal state",
                )
        elif stop_reason is not None:
            self._raise(
                ErrorCode.STATE_INVARIANT_VIOLATION,
                "transition_run",
                "only stopped, failed, or blocked runs carry a stop reason",
            )
        if stop_reason == StopReason.BUDGET_EXHAUSTED:
            acceptance_status = AcceptanceStatus.NOT_EVALUATED
        if target == RunStatus.COMPLETED:
            acceptance_status = acceptance_status or current.acceptance_status
            if acceptance_status not in _TERMINAL_ACCEPTANCE:
                self._raise(
                    ErrorCode.STATE_INVARIANT_VIOLATION,
                    "transition_run",
                    "completed runs require a terminal acceptance status",
                )
        elif acceptance_status is None:
            acceptance_status = current.acceptance_status
        payload = current.model_dump(mode="python")
        payload.update(
            {
                "status": target,
                "stop_reason": stop_reason,
                "acceptance_status": acceptance_status,
                "ended_at": ended_at,
                "updated_at": now,
                "started_at": (
                    now
                    if target == RunStatus.RUNNING and current.started_at is None
                    else current.started_at
                ),
            }
        )
        updated = Run.model_validate(payload, strict=True)
        changed = self._connection.execute(
            """
            UPDATE runs
            SET status = ?, stop_reason = ?, acceptance_status = ?,
                ended_at = ?, updated_at = ?, revision = revision + 1
            WHERE run_id = ? AND status = ? AND revision = ?
            """,
            (
                updated.status.value,
                updated.stop_reason.value if updated.stop_reason else None,
                updated.acceptance_status.value,
                _serialize_datetime(updated.ended_at) if updated.ended_at else None,
                _serialize_datetime(updated.updated_at),
                run_id,
                current.status.value,
                current_revision,
            ),
        ).rowcount
        if changed != 1:
            self._raise(ErrorCode.STATE_CONFLICT, "transition_run", "run changed concurrently")
        return updated

    def acquire_lease(
        self,
        run_id: str,
        *,
        owner_id: str,
        lease_policy: LeasePolicy | None = None,
    ) -> RunLease:
        """Acquire ownership when the current lease is absent or expired."""

        self._ensure_active()
        self.get_run(run_id)
        return self._acquire_lease(run_id, owner_id, lease_policy or LeasePolicy())

    def renew_lease(self, lease: RunLease, *, ttl_seconds: int = 30) -> RunLease:
        """Renew a lease only for its current owner token."""

        self._ensure_active()
        if not 10 <= ttl_seconds <= 300:
            self._raise(
                ErrorCode.STATE_LEASE_INVALID,
                "renew_lease",
                "lease TTL must be between 10 and 300 seconds",
            )
        now = self._now()
        row = self._connection.execute(
            "SELECT * FROM run_leases WHERE run_id = ?",
            (lease.run_id,),
        ).fetchone()
        if row is None or not hmac.compare_digest(
            str(row["token_digest"]),
            _token_digest(lease.token),
        ):
            self._raise(
                ErrorCode.STATE_LEASE_INVALID,
                "renew_lease",
                "lease token is not current",
            )
        if now >= _parse_datetime(str(row["expires_at"])):
            self._raise(ErrorCode.STATE_LEASE_EXPIRED, "renew_lease", "lease has expired")
        expires_at = now + timedelta(seconds=ttl_seconds)
        self._connection.execute(
            """
            UPDATE run_leases
            SET heartbeat_at = ?, expires_at = ?
            WHERE run_id = ? AND token_digest = ?
            """,
            (
                _serialize_datetime(now),
                _serialize_datetime(expires_at),
                lease.run_id,
                _token_digest(lease.token),
            ),
        )
        return RunLease(
            run_id=lease.run_id,
            owner_id=lease.owner_id,
            token=lease.token,
            acquired_at=lease.acquired_at,
            heartbeat_at=now,
            expires_at=expires_at,
        )

    def release_lease(self, lease: RunLease) -> None:
        """Release a lease only when the caller still holds its token."""

        self._ensure_active()
        changed = self._connection.execute(
            "DELETE FROM run_leases WHERE run_id = ? AND token_digest = ?",
            (lease.run_id, _token_digest(lease.token)),
        ).rowcount
        if changed != 1:
            self._raise(
                ErrorCode.STATE_LEASE_INVALID,
                "release_lease",
                "lease token is not current",
            )

    def get_run_snapshot(self, run_id: str) -> RunSnapshot:
        """Read all WP-01-owned state in the current transaction."""

        self._ensure_active()
        run = self.get_run(run_id)
        plan = self._get_plan(run_id)
        step_rows = self._connection.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY ordinal",
            (run_id,),
        ).fetchall()
        invocation_rows = self._connection.execute(
            "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY created_at, tool_call_id",
            (run_id,),
        ).fetchall()
        evidence_rows = self._connection.execute(
            "SELECT * FROM evidence WHERE run_id = ? ORDER BY created_at, evidence_id",
            (run_id,),
        ).fetchall()
        return RunSnapshot(
            run=run,
            plan=plan,
            steps=tuple(_step_from_row(row) for row in step_rows),
            invocations=tuple(
                _invocation_from_row(self._connection, row) for row in invocation_rows
            ),
            evidence=tuple(_evidence_from_row(row) for row in evidence_rows),
            completion=self._get_completion(run_id),
            read_at=self._now(),
        )

    def _get_plan(self, run_id: str) -> RunPlan | None:
        row = self._connection.execute(
            "SELECT * FROM run_plans WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return _plan_from_row(row) if row is not None else None

    def _get_run_revision(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT revision FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            self._raise(ErrorCode.STATE_NOT_FOUND, "get_run_revision", "run does not exist")
        return int(row["revision"])

    def _get_completion(self, run_id: str) -> CompletionCheck | None:
        row = self._connection.execute(
            "SELECT * FROM completion_results WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        item_rows = self._connection.execute(
            "SELECT * FROM completion_items WHERE run_id = ? ORDER BY ordinal",
            (run_id,),
        ).fetchall()
        return CompletionCheck(
            run_id=run_id,
            acceptance_status=AcceptanceStatus(str(row["acceptance_status"])),
            items=tuple(
                CompletionItem(
                    item_id=str(item["item_id"]),
                    passed=bool(item["passed"]),
                    code=str(item["code"]),
                    message=str(item["message"]),
                    evidence_ids=tuple(json.loads(str(item["evidence_ids_json"]))),
                )
                for item in item_rows
            ),
            failure_summary=str(row["failure_summary"]) if row["failure_summary"] else None,
            checked_at=_parse_datetime(str(row["checked_at"])),
            evidence_ids=tuple(json.loads(str(row["evidence_ids_json"]))),
        )

    def _touch_run(
        self,
        run_id: str,
        expected_revision: int,
        now: datetime,
        *,
        current_step_id: str | None = None,
    ) -> None:
        if current_step_id is None:
            changed = self._connection.execute(
                """
                UPDATE runs
                SET revision = revision + 1, updated_at = ?
                WHERE run_id = ? AND revision = ?
                """,
                (_serialize_datetime(now), run_id, expected_revision),
            ).rowcount
        else:
            changed = self._connection.execute(
                """
                UPDATE runs
                SET revision = revision + 1, updated_at = ?, current_step_id = ?
                WHERE run_id = ? AND revision = ?
                """,
                (_serialize_datetime(now), current_step_id, run_id, expected_revision),
            ).rowcount
        if changed != 1:
            self._raise(ErrorCode.STATE_CONFLICT, "update_run", "run changed concurrently")

    def _acquire_lease(
        self,
        run_id: str,
        owner_id: str,
        policy: LeasePolicy,
    ) -> RunLease:
        normalized_owner = owner_id.strip()
        if not normalized_owner or len(normalized_owner) > 256:
            self._raise(
                ErrorCode.STATE_LEASE_INVALID,
                "acquire_lease",
                "owner_id must be non-empty and bounded",
            )
        now = self._now()
        row = self._connection.execute(
            "SELECT * FROM run_leases WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is not None and now < _parse_datetime(str(row["expires_at"])):
            self._raise(
                ErrorCode.STATE_LEASE_HELD,
                "acquire_lease",
                "run already has an active lease",
                retryable=True,
            )
        token = secrets.token_hex(32)
        expires_at = now + timedelta(seconds=policy.ttl_seconds)
        if row is None:
            self._connection.execute(
                """
                INSERT INTO run_leases(
                    run_id, owner_id, token_digest, acquired_at, heartbeat_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    normalized_owner,
                    _token_digest(token),
                    _serialize_datetime(now),
                    _serialize_datetime(now),
                    _serialize_datetime(expires_at),
                ),
            )
        else:
            self._connection.execute(
                """
                UPDATE run_leases
                SET owner_id = ?, token_digest = ?, acquired_at = ?,
                    heartbeat_at = ?, expires_at = ?
                WHERE run_id = ?
                """,
                (
                    normalized_owner,
                    _token_digest(token),
                    _serialize_datetime(now),
                    _serialize_datetime(now),
                    _serialize_datetime(expires_at),
                    run_id,
                ),
            )
        return RunLease(
            run_id=run_id,
            owner_id=normalized_owner,
            token=token,
            acquired_at=now,
            heartbeat_at=now,
            expires_at=expires_at,
        )

    def _verify_lease(self, run_id: str, token: str, now: datetime) -> None:
        row = self._connection.execute(
            "SELECT * FROM run_leases WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not hmac.compare_digest(str(row["token_digest"]), _token_digest(token)):
            self._raise(
                ErrorCode.STATE_LEASE_INVALID,
                "verify_lease",
                "lease token is not current",
            )
        if now >= _parse_datetime(str(row["expires_at"])):
            self._raise(ErrorCode.STATE_LEASE_EXPIRED, "verify_lease", "lease has expired")

    def _now(self) -> datetime:
        return self._clock.now()

    def _ensure_active(self) -> None:
        try:
            self._unit.ensure_active()
        except StorageError as exc:
            raise StateStoreError(
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
        raise StateStoreError(
            code,
            operation=operation,
            message=message,
            retryable=retryable,
        )


def _run_from_row(row: sqlite3.Row) -> Run:
    workspace_data = json.loads(str(row["workspace_json"]))
    return Run(
        run_id=str(row["run_id"]),
        trace_id=str(row["trace_id"]),
        task_id=str(row["task_id"]),
        task_kind=TaskKind(str(row["task_kind"])),
        task_description=str(row["task_description"]),
        config_fingerprint=str(row["config_fingerprint"]),
        workspace=WorkspaceIdentity.model_validate(workspace_data, strict=True),
        budget=RunBudget(
            max_steps=int(row["max_steps"]),
            max_tool_calls=int(row["max_tool_calls"]),
            max_token_cost=Decimal(str(row["max_token_cost"])),
            max_wall_time_seconds=int(row["max_wall_time_seconds"]),
        ),
        usage=BudgetUsage(
            steps=int(row["used_steps"]),
            tool_calls=int(row["used_tool_calls"]),
            token_cost=Decimal(str(row["used_token_cost"])),
            wall_time_seconds=int(row["used_wall_time_seconds"]),
        ),
        status=RunStatus(str(row["status"])),
        stop_reason=StopReason(str(row["stop_reason"])) if row["stop_reason"] else None,
        acceptance_status=AcceptanceStatus(str(row["acceptance_status"])),
        current_step_id=str(row["current_step_id"]) if row["current_step_id"] else None,
        last_checkpoint_id=str(row["last_checkpoint_id"]) if row["last_checkpoint_id"] else None,
        resumed_from_checkpoint_id=(
            str(row["resumed_from_checkpoint_id"]) if row["resumed_from_checkpoint_id"] else None
        ),
        candidate_final_answer=(
            str(row["candidate_final_answer"]) if row["candidate_final_answer"] else None
        ),
        schema_version=int(row["schema_version"]),
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
        started_at=_parse_datetime(str(row["started_at"])) if row["started_at"] else None,
        ended_at=_parse_datetime(str(row["ended_at"])) if row["ended_at"] else None,
    )


def _plan_from_row(row: sqlite3.Row) -> RunPlan:
    items_data = json.loads(str(row["items_json"]))
    return RunPlan(
        run_id=str(row["run_id"]),
        revision=int(row["revision"]),
        items=tuple(PlanItem.model_validate(item, strict=True) for item in items_data),
        updated_at=_parse_datetime(str(row["updated_at"])),
    )


def _step_from_row(row: sqlite3.Row) -> Step:
    return Step(
        step_id=str(row["step_id"]),
        run_id=str(row["run_id"]),
        ordinal=int(row["ordinal"]),
        status=StepStatus(str(row["status"])),
        summary=str(row["summary"]),
        started_at=_parse_datetime(str(row["started_at"])),
        ended_at=_parse_datetime(str(row["ended_at"])) if row["ended_at"] else None,
    )


def _invocation_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> ToolInvocation:
    evidence_ids = tuple(
        str(item["evidence_id"])
        for item in connection.execute(
            """
            SELECT evidence_id FROM tool_call_evidence
            WHERE run_id = ? AND tool_call_id = ? ORDER BY ordinal
            """,
            (row["run_id"], row["tool_call_id"]),
        ).fetchall()
    )
    return ToolInvocation(
        tool_call_id=str(row["tool_call_id"]),
        run_id=str(row["run_id"]),
        step_id=str(row["step_id"]),
        tool_name=str(row["tool_name"]),
        raw_args=json.loads(str(row["raw_args_json"])),
        normalized_args=json.loads(str(row["normalized_args_json"])),
        args_digest=str(row["args_digest"]),
        risk=ToolRisk(str(row["risk"])),
        required_permissions=frozenset(
            ToolPermission(permission)
            for permission in json.loads(str(row["required_permissions_json"]))
        ),
        idempotent=bool(row["idempotent"]),
        status=InvocationStatus(str(row["status"])),
        approval_id=ApprovalId(str(row["approval_id"])) if row["approval_id"] else None,
        result_summary=str(row["result_summary"]) if row["result_summary"] else None,
        evidence_ids=evidence_ids,
        error_code=str(row["error_code"]) if row["error_code"] else None,
        created_at=_parse_datetime(str(row["created_at"])),
        updated_at=_parse_datetime(str(row["updated_at"])),
    )


def _evidence_from_row(row: sqlite3.Row) -> Evidence:
    return Evidence(
        evidence_id=str(row["evidence_id"]),
        run_id=str(row["run_id"]),
        tool_call_id=str(row["tool_call_id"]) if row["tool_call_id"] else None,
        kind=EvidenceKind(str(row["kind"])),
        path=str(row["path"]) if row["path"] else None,
        command_id=str(row["command_id"]) if row["command_id"] else None,
        line_start=int(row["line_start"]) if row["line_start"] else None,
        line_end=int(row["line_end"]) if row["line_end"] else None,
        content_digest=str(row["content_digest"]),
        created_at=_parse_datetime(str(row["created_at"])),
        is_valid=bool(row["is_valid"]),
    )


def _serialize_plan_items(items: tuple[PlanItem, ...]) -> str:
    return json.dumps(
        [item.model_dump(mode="json") for item in items],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _token_digest(token: str) -> str:
    digest = hashlib.sha256(f"lease_schema:1:{token}".encode()).hexdigest()
    return f"sha256:{digest}"


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
