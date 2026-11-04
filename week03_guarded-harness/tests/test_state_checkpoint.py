"""State persistence, migration, and run lease contracts for WP-01."""

from __future__ import annotations

import sqlite3
import stat
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import guarded_harness as gh
import guarded_harness.storage as storage_module
from guarded_harness import (
    AcceptanceStatus,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalStoreError,
    Approver,
    BudgetCategory,
    BudgetDeltaCommand,
    BudgetSourceKind,
    BudgetUsage,
    CompletionCheck,
    CompletionItem,
    DisabledApprovalChannel,
    ErrorCode,
    Evidence,
    EvidenceKind,
    InvocationStatus,
    LeasePolicy,
    PlanItem,
    Run,
    RunBudget,
    RunPlan,
    RunStatus,
    SQLiteStorage,
    StateStoreError,
    Step,
    StepStatus,
    StopReason,
    StorageError,
    TaskKind,
    ToolInvocation,
    ToolPermission,
    ToolRisk,
    approval_args_digest,
)
from guarded_harness.domain import WorkspaceIdentity

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
TRACE_ID = "00000000000000000000000000000002"
STEP_ID = "step_00000000000000000000000000000003"
EVIDENCE_ID = "evidence_00000000000000000000000000000004"
EVIDENCE_ID_2 = "evidence_00000000000000000000000000000005"
EVIDENCE_ID_3 = "evidence_00000000000000000000000000000006"
EVIDENCE_ID_4 = "evidence_00000000000000000000000000000007"
EVIDENCE_ID_5 = "evidence_00000000000000000000000000000008"
TOOL_CALL_ID = "provider-call-1"
TOOL_CALL_ID_2 = "provider-call-2"
DIGEST = f"sha256:{'a' * 64}"
APPROVAL_ARGS = {"path": "artifacts/report.md", "content": "# Report\n"}
APPROVAL_ARGS_DIGEST = approval_args_digest("write_file", APPROVAL_ARGS)
APPROVAL_TOOL_CALL_ID = "provider-approval-call"


class MutableClock:
    """Advance UTC time without sleeping."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self._now += timedelta(seconds=seconds)


def _storage(
    tmp_path: Path,
    fixed_clock,
    *,
    fault_injector=None,
) -> tuple[SQLiteStorage, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    storage = SQLiteStorage(
        state_dir / "harness.sqlite3",
        workspace,
        fixed_clock,
        fault_injector=fault_injector,
    )
    return storage, state_dir


def _run() -> Run:
    return Run(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        task_id="understand_login",
        task_kind=TaskKind.CODE_UNDERSTANDING,
        task_description="Explain the login flow",
        config_fingerprint=DIGEST,
        workspace=WorkspaceIdentity(
            git_root="/tmp/repo",
            workspace_root="/tmp/repo/task",
            workspace_prefix="task",
            baseline_fingerprint=DIGEST,
            external_dirty_baseline_fingerprint=DIGEST,
        ),
        budget=RunBudget(
            max_steps=10,
            max_tool_calls=20,
            max_token_cost=Decimal("1.25"),
            max_wall_time_seconds=300,
        ),
        usage=BudgetUsage(),
        created_at=NOW,
        updated_at=NOW,
    )


def _plan(revision: int = 0) -> RunPlan:
    return RunPlan(
        run_id=RUN_ID,
        revision=revision,
        items=(
            PlanItem(ordinal=0, summary="Read the login entry point"),
            PlanItem(ordinal=1, summary="Trace authentication flow"),
        ),
        updated_at=NOW,
    )


def _invocation(
    *,
    idempotent: bool = True,
    tool_call_id: str = TOOL_CALL_ID,
) -> ToolInvocation:
    return ToolInvocation(
        tool_call_id=tool_call_id,
        run_id=RUN_ID,
        step_id=STEP_ID,
        tool_name="read_file",
        raw_args={"path": "src/app.py"},
        normalized_args={"path": "src/app.py"},
        args_digest=DIGEST,
        risk=ToolRisk.READ,
        required_permissions=frozenset({ToolPermission.READ}),
        idempotent=idempotent,
        created_at=NOW,
        updated_at=NOW,
    )


def _write_invocation(*, idempotent: bool = False) -> ToolInvocation:
    return ToolInvocation(
        tool_call_id=APPROVAL_TOOL_CALL_ID,
        run_id=RUN_ID,
        step_id=STEP_ID,
        tool_name="write_file",
        raw_args=APPROVAL_ARGS,
        normalized_args=APPROVAL_ARGS,
        args_digest=APPROVAL_ARGS_DIGEST,
        risk=ToolRisk.WRITE,
        required_permissions=frozenset({ToolPermission.WRITE}),
        idempotent=idempotent,
        created_at=NOW,
        updated_at=NOW,
    )


def _approval_request(
    *,
    normalized_args: dict[str, object] | None = None,
    requested_by: str = "agent",
    roles: tuple[str, ...] = ("operator",),
    ttl_seconds: int = 900,
) -> ApprovalRequest:
    return ApprovalRequest(
        run_id=RUN_ID,
        tool_call_id=APPROVAL_TOOL_CALL_ID,
        normalized_args=dict(APPROVAL_ARGS if normalized_args is None else normalized_args),
        requested_by=requested_by,
        allowed_approver_roles=roles,
        ttl_seconds=ttl_seconds,
    )


def _evidence(
    *,
    evidence_id: str = EVIDENCE_ID,
    tool_call_id: str | None = TOOL_CALL_ID,
    path: str = "src/app.py",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        run_id=RUN_ID,
        tool_call_id=tool_call_id,
        kind=EvidenceKind.READ,
        path=path,
        line_start=1,
        line_end=3,
        content_digest=DIGEST,
        created_at=NOW,
    )


def _seed_pending_approval(
    storage: SQLiteStorage,
    *,
    request: ApprovalRequest | None = None,
    idempotent: bool = False,
):
    request = request or _approval_request()
    with storage.transaction(actor=request.requested_by) as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Prepare governed write",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        state.transition_run(
            RUN_ID,
            expected_status=RunStatus.NEW,
            expected_revision=1,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
        state.create_invocation(
            _write_invocation(idempotent=idempotent),
            expected_run_revision=2,
            lease_token=lease.token,
        )
        approval = unit.approvals.create(request, lease_token=lease.token)
        state.transition_run(
            RUN_ID,
            expected_status=RunStatus.RUNNING,
            expected_revision=3,
            target=RunStatus.WAITING_APPROVAL,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
    return lease, approval


def _seed_running_invocation(storage: SQLiteStorage):
    with storage.transaction(actor="agent") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Prepare governed write",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        state.transition_run(
            RUN_ID,
            expected_status=RunStatus.NEW,
            expected_revision=1,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
        state.create_invocation(
            _write_invocation(),
            expected_run_revision=2,
            lease_token=lease.token,
        )
    return lease


def _approve_pending_approval(storage: SQLiteStorage, lease, approval):
    with storage.transaction(actor="operator") as unit:
        approved = unit.approvals.approve(
            approval.approval_id,
            expected_revision=approval.revision,
            approver=Approver(actor="operator", roles=("operator",)),
            lease_token=lease.token,
        )
    return approved


def _claim_approval(storage: SQLiteStorage, lease, approval):
    with storage.transaction(actor="operator") as unit:
        claimed = unit.approvals.claim(
            approval.approval_id,
            expected_revision=approval.revision,
            execution_intent_id=approval.tool_call_id,
            lease_token=lease.token,
        )
        unit.state.transition_run(
            RUN_ID,
            expected_status=RunStatus.WAITING_APPROVAL,
            expected_revision=4,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
    return claimed


def _run_revision(unit, run_id: str = RUN_ID) -> int:
    row = unit.connection.execute(
        "SELECT revision FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row is not None
    return int(row["revision"])


def _tool_revision(unit, tool_call_id: str = APPROVAL_TOOL_CALL_ID) -> int:
    row = unit.connection.execute(
        "SELECT revision FROM tool_calls WHERE run_id = ? AND tool_call_id = ?",
        (RUN_ID, tool_call_id),
    ).fetchone()
    assert row is not None
    return int(row["revision"])


def test_storage_initializes_schema_and_pragmas(tmp_path: Path, fixed_clock) -> None:
    storage, state_dir = _storage(tmp_path, fixed_clock)

    storage.initialize()
    storage.initialize()

    report = storage.verify_integrity()
    assert report.ok
    assert report.schema_version == 4
    assert report.migration_count == 4
    assert report.journal_mode.casefold() == "wal"
    assert report.foreign_keys_enabled
    assert report.busy_timeout_ms == 5000
    assert report.required_tables_present
    assert report.budget_ledger_consistent
    assert report.quick_check.casefold() == "ok"
    assert storage.schema_version() == 4
    if "posix" in __import__("os").name:
        assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(storage.database_path.stat().st_mode) == 0o600


def test_migration_failure_rolls_back_schema(tmp_path: Path, fixed_clock, fault_injector) -> None:
    storage, _ = _storage(tmp_path, fixed_clock, fault_injector=fault_injector)
    fault_injector.arm("storage:migration-before-commit")

    with pytest.raises(RuntimeError, match="injected fault"):
        storage.initialize()

    with sqlite3.connect(storage.database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
    assert table is None


def test_migration_checksum_mismatch_fails_closed(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
            (f"sha256:{'f' * 64}",),
        )

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_SCHEMA_UNSUPPORTED


def test_schema_version_metadata_mismatch_fails_closed(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("PRAGMA user_version = 0")

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_SCHEMA_UNSUPPORTED


def test_storage_rejects_state_dir_inside_workspace(tmp_path: Path, fixed_clock) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = workspace / "state"

    with pytest.raises(StorageError) as error:
        SQLiteStorage(state_dir / "harness.sqlite3", workspace, fixed_clock)
    assert error.value.code == ErrorCode.STATE_DATABASE_UNAVAILABLE
    assert not state_dir.exists()


def test_storage_rejects_unsafe_existing_state_permissions(
    tmp_path: Path,
    fixed_clock,
) -> None:
    import os

    if os.name != "posix":
        pytest.skip("POSIX permissions are not available")
    storage, state_dir = _storage(tmp_path, fixed_clock)
    state_dir.mkdir(mode=0o755)
    state_dir.chmod(0o755)

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_DATABASE_UNAVAILABLE
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o755


def test_newer_schema_version_fails_closed(tmp_path: Path, fixed_clock) -> None:
    storage, state_dir = _storage(tmp_path, fixed_clock)
    state_dir.mkdir(mode=0o700)
    state_dir.chmod(0o700)
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_SCHEMA_UNSUPPORTED


def test_evidence_migration_rejects_conflicting_legacy_links(
    tmp_path: Path,
    fixed_clock,
) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()
    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Read files",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        state.create_invocation(
            _invocation(),
            expected_run_revision=1,
            lease_token=lease.token,
        )
        state.create_invocation(
            _invocation(tool_call_id=TOOL_CALL_ID_2),
            expected_run_revision=2,
            lease_token=lease.token,
        )
        state.add_evidence(_evidence(), lease_token=lease.token)
        state.add_evidence(
            _evidence(
                evidence_id=EVIDENCE_ID_3,
                tool_call_id=TOOL_CALL_ID_2,
                path="src/other.py",
            ),
            lease_token=lease.token,
        )

    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            """
            CREATE TABLE tool_call_evidence (
                run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO tool_call_evidence(run_id, tool_call_id, evidence_id, ordinal)
            VALUES (?, ?, ?, 0)
            """,
            (RUN_ID, TOOL_CALL_ID, EVIDENCE_ID_3),
        )
        connection.execute("DELETE FROM schema_migrations WHERE version >= 3")
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_MIGRATION_FAILED


def test_run_plan_and_step_round_trip_after_reopen(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1", lease_policy=LeasePolicy())
        plan = state.put_plan(
            RUN_ID,
            expected_revision=0,
            plan=_plan(),
            lease_token=lease.token,
        )
        no_op = state.put_plan(
            RUN_ID,
            expected_revision=1,
            plan=_plan(revision=1),
            lease_token=lease.token,
        )
        step = state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Inspect login entry point",
                started_at=NOW,
            ),
            expected_run_revision=1,
            lease_token=lease.token,
        )
        running = state.transition_step(
            step.step_id,
            expected_status=StepStatus.PLANNED,
            expected_revision=0,
            target=StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        running_run = state.transition_run(
            RUN_ID,
            expected_status=RunStatus.NEW,
            expected_revision=3,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )

    assert plan.revision == 0
    assert no_op.revision == 0
    assert running.status == StepStatus.RUNNING
    assert running_run.status == RunStatus.RUNNING
    assert running_run.started_at is not None

    reopened = SQLiteStorage(storage.database_path, storage.workspace_root, fixed_clock)
    reopened.initialize()
    with reopened.transaction(actor="test-harness") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)

    assert snapshot.run.run_id == RUN_ID
    assert snapshot.run.status == RunStatus.RUNNING
    assert snapshot.plan is not None
    assert snapshot.plan.items == _plan().items
    assert len(snapshot.steps) == 1
    assert snapshot.steps[0].status == StepStatus.RUNNING
    assert snapshot.invocations == ()
    assert snapshot.evidence == ()
    assert snapshot.completion is None


def test_run_state_machine_rejects_illegal_transition(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        with pytest.raises(StateStoreError) as error:
            state.transition_run(
                RUN_ID,
                expected_status=RunStatus.NEW,
                expected_revision=0,
                target=RunStatus.STOPPED,
                stop_reason=StopReason.INTERNAL_ERROR,
                ended_at=NOW,
                acceptance_status=None,
                lease_token=lease.token,
            )
        assert error.value.code == ErrorCode.STATE_INVALID_TRANSITION
        assert state.get_run(RUN_ID).status == RunStatus.NEW

        step = state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="planned",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        with pytest.raises(StateStoreError) as step_error:
            state.transition_step(
                step.step_id,
                expected_status=StepStatus.PLANNED,
                expected_revision=0,
                target=StepStatus.COMPLETED,
                ended_at=LATER,
                lease_token=lease.token,
            )
        assert step_error.value.code == ErrorCode.STATE_INVALID_TRANSITION


def test_plan_and_lease_conflicts_fail_closed(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        with pytest.raises(StateStoreError) as revision_error:
            state.put_plan(
                RUN_ID,
                expected_revision=99,
                plan=_plan(),
                lease_token=lease.token,
            )
        assert revision_error.value.code == ErrorCode.STATE_CONFLICT

        with pytest.raises(StateStoreError) as token_error:
            state.put_plan(
                RUN_ID,
                expected_revision=0,
                plan=_plan(),
                lease_token="x" * 64,
            )
        assert token_error.value.code == ErrorCode.STATE_LEASE_INVALID

        with pytest.raises(StateStoreError) as held_error:
            state.acquire_lease(RUN_ID, owner_id="owner-2")
        assert held_error.value.code == ErrorCode.STATE_LEASE_HELD

    with pytest.raises(StateStoreError) as closed_error:
        state.get_run(RUN_ID)
    assert closed_error.value.code == ErrorCode.STATE_TRANSACTION_FAILED


def test_tool_evidence_budget_and_completion_round_trip(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Inspect login flow",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        invocation = state.create_invocation(
            _invocation(),
            expected_run_revision=1,
            lease_token=lease.token,
        )
        started = state.transition_invocation(
            RUN_ID,
            invocation.tool_call_id,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=0,
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )
        evidence = state.add_evidence(_evidence(), lease_token=lease.token)
        succeeded = state.transition_invocation(
            RUN_ID,
            invocation.tool_call_id,
            expected_status=InvocationStatus.STARTED,
            expected_revision=1,
            target=InvocationStatus.SUCCEEDED,
            result_summary="read 3 lines",
            error_code=None,
            lease_token=lease.token,
        )
        usage = state.record_budget_delta(
            BudgetDeltaCommand(
                run_id=RUN_ID,
                source_kind=BudgetSourceKind.STEP,
                source_id=STEP_ID,
                category=BudgetCategory.STEPS,
                expected_run_revision=5,
                delta=Decimal("1"),
            ),
            lease_token=lease.token,
        )
        replayed = state.record_budget_delta(
            BudgetDeltaCommand(
                run_id=RUN_ID,
                source_kind=BudgetSourceKind.STEP,
                source_id=STEP_ID,
                category=BudgetCategory.STEPS,
                expected_run_revision=5,
                delta=Decimal("1"),
            ),
            lease_token=lease.token,
        )
        tool_usage = state.record_budget_delta(
            BudgetDeltaCommand(
                run_id=RUN_ID,
                source_kind=BudgetSourceKind.TOOL_CALL,
                source_id=TOOL_CALL_ID,
                category=BudgetCategory.TOOL_CALLS,
                expected_run_revision=6,
                delta=Decimal("1"),
            ),
            lease_token=lease.token,
        )
        token_usage = state.record_budget_delta(
            BudgetDeltaCommand(
                run_id=RUN_ID,
                source_kind=BudgetSourceKind.STEP,
                source_id=STEP_ID,
                category=BudgetCategory.TOKEN_COST,
                expected_run_revision=7,
                delta=Decimal("0.25"),
            ),
            lease_token=lease.token,
        )
        time_usage = state.record_budget_delta(
            BudgetDeltaCommand(
                run_id=RUN_ID,
                source_kind=BudgetSourceKind.HARNESS,
                source_id="step-1-elapsed",
                category=BudgetCategory.WALL_TIME,
                expected_run_revision=8,
                delta=Decimal("5"),
            ),
            lease_token=lease.token,
        )
        state.transition_run(
            RUN_ID,
            expected_status=RunStatus.NEW,
            expected_revision=9,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
        state.transition_run(
            RUN_ID,
            expected_status=RunStatus.RUNNING,
            expected_revision=10,
            target=RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
        completion = state.record_completion(
            CompletionCheck(
                run_id=RUN_ID,
                acceptance_status=AcceptanceStatus.ACCEPTED,
                items=(
                    CompletionItem(
                        item_id="read_evidence",
                        passed=True,
                        code="OK",
                        message="Evidence exists.",
                        evidence_ids=(evidence.evidence_id,),
                    ),
                ),
                checked_at=LATER,
                evidence_ids=(evidence.evidence_id,),
            ),
            expected_run_revision=11,
            lease_token=lease.token,
        )

    assert started.status == InvocationStatus.STARTED
    assert succeeded.status == InvocationStatus.SUCCEEDED
    assert usage.steps == 1
    assert replayed == usage
    assert tool_usage.tool_calls == 1
    assert token_usage.token_cost == Decimal("0.25")
    assert time_usage.wall_time_seconds == 5
    assert completion.acceptance_status == AcceptanceStatus.ACCEPTED

    reopened = SQLiteStorage(storage.database_path, storage.workspace_root, fixed_clock)
    reopened.initialize()
    with reopened.transaction(actor="test-harness") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)

    assert snapshot.run.usage.steps == 1
    assert snapshot.run.usage.tool_calls == 1
    assert snapshot.run.usage.token_cost == Decimal("0.25")
    assert snapshot.run.usage.wall_time_seconds == 5
    assert snapshot.run.acceptance_status == AcceptanceStatus.ACCEPTED
    assert snapshot.invocations[0].status == InvocationStatus.SUCCEEDED
    assert snapshot.invocations[0].evidence_ids == (EVIDENCE_ID,)
    assert snapshot.evidence[0].evidence_id == EVIDENCE_ID
    assert snapshot.completion is not None
    assert snapshot.completion.items[0].passed


def test_budget_rejects_changed_payload_and_overage(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        first = BudgetDeltaCommand(
            run_id=RUN_ID,
            source_kind=BudgetSourceKind.HARNESS,
            source_id="model-step-1",
            category=BudgetCategory.TOKEN_COST,
            expected_run_revision=0,
            delta=Decimal("0.25"),
        )
        state.record_budget_delta(first, lease_token=lease.token)
        with pytest.raises(StateStoreError) as replay_error:
            state.record_budget_delta(
                first.model_copy(update={"delta": Decimal("0.5")}),
                lease_token=lease.token,
            )
        assert replay_error.value.code == ErrorCode.STATE_CONFLICT

        with pytest.raises(StateStoreError) as overage_error:
            state.record_budget_delta(
                BudgetDeltaCommand(
                    run_id=RUN_ID,
                    source_kind=BudgetSourceKind.HARNESS,
                    source_id="model-step-2",
                    category=BudgetCategory.TOKEN_COST,
                    expected_run_revision=1,
                    delta=Decimal("2"),
                ),
                lease_token=lease.token,
            )
        assert overage_error.value.code == ErrorCode.STATE_BUDGET_EXCEEDED

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="BUDGET_LEDGER_APPEND_ONLY"):
            connection.execute("UPDATE budget_ledger SET delta = '0'")
        connection.execute("UPDATE runs SET used_steps = 2 WHERE run_id = ?", (RUN_ID,))
    assert not storage.verify_integrity().budget_ledger_consistent


def test_invocation_args_and_result_are_redacted(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Read a secret-bearing request",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        invocation = _invocation()
        invocation = invocation.model_copy(
            update={
                "raw_args": {"api_key": "secret-value"},
                "normalized_args": {"api_key": "secret-value"},
            }
        )
        invocation = state.create_invocation(
            invocation,
            expected_run_revision=1,
            lease_token=lease.token,
        )
        state.transition_invocation(
            RUN_ID,
            TOOL_CALL_ID,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=0,
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )

    with storage.transaction(actor="test-harness") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
    assert invocation.raw_args == {"api_key": "[REDACTED]"}
    assert snapshot.invocations[0].normalized_args == {"api_key": "[REDACTED]"}


def test_non_idempotent_timeout_must_be_unknown_result(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Write a file",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        invocation = state.create_invocation(
            _invocation(idempotent=False),
            expected_run_revision=1,
            lease_token=lease.token,
        )
        state.transition_invocation(
            RUN_ID,
            invocation.tool_call_id,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=0,
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )
        with pytest.raises(StateStoreError) as error:
            state.transition_invocation(
                RUN_ID,
                invocation.tool_call_id,
                expected_status=InvocationStatus.STARTED,
                expected_revision=1,
                target=InvocationStatus.TIMED_OUT,
                result_summary="timed out",
                error_code="TOOL_TIMEOUT",
                lease_token=lease.token,
            )
        assert error.value.code == ErrorCode.STATE_INVARIANT_VIOLATION


def test_evidence_has_one_producer_and_is_derived_on_invocation(
    tmp_path: Path,
    fixed_clock,
) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Read files",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        first = state.create_invocation(
            _invocation(),
            expected_run_revision=1,
            lease_token=lease.token,
        )
        second = state.create_invocation(
            _invocation(tool_call_id=TOOL_CALL_ID_2),
            expected_run_revision=2,
            lease_token=lease.token,
        )
        state.transition_invocation(
            RUN_ID,
            first.tool_call_id,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=0,
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )
        state.transition_invocation(
            RUN_ID,
            second.tool_call_id,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=0,
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )
        state.add_evidence(
            _evidence(evidence_id=EVIDENCE_ID_2, path="src/service.py"),
            lease_token=lease.token,
        )
        state.add_evidence(
            _evidence(evidence_id=EVIDENCE_ID, path="src/app.py"),
            lease_token=lease.token,
        )
        state.add_evidence(
            _evidence(
                evidence_id=EVIDENCE_ID_3,
                tool_call_id=TOOL_CALL_ID_2,
                path="src/other.py",
            ),
            lease_token=lease.token,
        )
        state.transition_invocation(
            RUN_ID,
            first.tool_call_id,
            expected_status=InvocationStatus.STARTED,
            expected_revision=1,
            target=InvocationStatus.SUCCEEDED,
            result_summary="read first",
            error_code=None,
            lease_token=lease.token,
        )
        state.transition_invocation(
            RUN_ID,
            second.tool_call_id,
            expected_status=InvocationStatus.STARTED,
            expected_revision=1,
            target=InvocationStatus.SUCCEEDED,
            result_summary="read second",
            error_code=None,
            lease_token=lease.token,
        )
        with pytest.raises(StateStoreError) as terminal_error:
            state.add_evidence(
                _evidence(evidence_id=EVIDENCE_ID_4),
                lease_token=lease.token,
            )
        assert terminal_error.value.code == ErrorCode.STATE_INVALID_TRANSITION
        completion_only = state.add_evidence(
            _evidence(evidence_id=EVIDENCE_ID_5, tool_call_id=None),
            lease_token=lease.token,
        )
        snapshot = state.get_run_snapshot(RUN_ID)

    by_id = {item.tool_call_id: item for item in snapshot.invocations}
    assert by_id[TOOL_CALL_ID].evidence_ids == (EVIDENCE_ID, EVIDENCE_ID_2)
    assert by_id[TOOL_CALL_ID_2].evidence_ids == (EVIDENCE_ID_3,)
    assert completion_only.tool_call_id is None
    with sqlite3.connect(storage.database_path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name = 'tool_call_evidence'"
            ).fetchone()
            is None
        )


def test_lease_renewal_blocks_second_owner(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        assert LeasePolicy(ttl_seconds=10).heartbeat_interval_seconds == 3
        assert LeasePolicy(ttl_seconds=60).heartbeat_interval_seconds == 10
        lease = state.create_run(
            _run(),
            owner_id="owner-1",
            lease_policy=LeasePolicy(ttl_seconds=10),
        )
        row = unit.connection.execute(
            "SELECT token_digest FROM run_leases WHERE run_id = ?",
            (RUN_ID,),
        ).fetchone()
        assert lease.token not in str(row["token_digest"])

    clock.advance(5)
    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        renewed = state.renew_lease(lease, ttl_seconds=10)
        assert renewed.heartbeat_at == clock.now()
        assert renewed.expires_at > lease.expires_at
        with pytest.raises(StateStoreError) as held_error:
            state.acquire_lease(
                RUN_ID,
                owner_id="owner-2",
                lease_policy=LeasePolicy(ttl_seconds=10),
            )
        assert held_error.value.code == ErrorCode.STATE_LEASE_HELD

    clock.advance(6)
    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        state.put_plan(
            RUN_ID,
            expected_revision=0,
            plan=_plan(),
            lease_token=renewed.token,
        )


def test_expired_lease_can_be_taken_over_and_old_token_is_fenced(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        old_lease = unit.state.create_run(
            _run(),
            owner_id="owner-1",
            lease_policy=LeasePolicy(ttl_seconds=10),
        )

    clock.advance(11)
    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        with pytest.raises(StateStoreError) as expired_error:
            state.put_plan(
                RUN_ID,
                expected_revision=0,
                plan=_plan(),
                lease_token=old_lease.token,
            )
        assert expired_error.value.code == ErrorCode.STATE_LEASE_EXPIRED
        with pytest.raises(StateStoreError) as expired_renew_error:
            state.renew_lease(old_lease)
        assert expired_renew_error.value.code == ErrorCode.STATE_LEASE_EXPIRED

        new_lease = state.acquire_lease(
            RUN_ID,
            owner_id="owner-2",
            lease_policy=LeasePolicy(ttl_seconds=20),
        )
        assert new_lease.token != old_lease.token
        assert new_lease.owner_id == "owner-2"

        with pytest.raises(StateStoreError) as renew_error:
            state.renew_lease(old_lease)
        assert renew_error.value.code == ErrorCode.STATE_LEASE_INVALID

        with pytest.raises(StateStoreError) as release_error:
            state.release_lease(old_lease)
        assert release_error.value.code == ErrorCode.STATE_LEASE_INVALID

        with pytest.raises(StateStoreError) as write_error:
            state.put_plan(
                RUN_ID,
                expected_revision=0,
                plan=_plan(),
                lease_token=old_lease.token,
            )
        assert write_error.value.code == ErrorCode.STATE_LEASE_INVALID


def test_released_lease_can_be_reacquired(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1")
        state.release_lease(lease)

    with storage.transaction(actor="test-harness") as unit:
        replacement = unit.state.acquire_lease(RUN_ID, owner_id="owner-2")
    assert replacement.owner_id == "owner-2"
    assert replacement.token != lease.token


def test_approval_round_trip_snapshot_and_reopen(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)

    with storage.transaction(actor="operator") as unit:
        loaded = unit.approvals.load(pending.approval_id, lease_token=lease.token)
        listed = unit.approvals.list_for_run(
            RUN_ID,
            statuses=(ApprovalStatus.PENDING,),
            lease_token=lease.token,
        )
        snapshot = unit.state.get_run_snapshot(RUN_ID)

    assert loaded == pending
    assert loaded.schema_version == 2
    assert loaded.status == ApprovalStatus.PENDING
    assert loaded.tool_call_id == APPROVAL_TOOL_CALL_ID
    assert loaded.revision == 0
    assert listed == (pending,)
    assert snapshot.run.status == RunStatus.WAITING_APPROVAL
    assert snapshot.invocations[0].approval_id == pending.approval_id
    assert snapshot.approvals == (pending,)

    reopened = SQLiteStorage(storage.database_path, storage.workspace_root, clock)
    reopened.initialize()
    with reopened.transaction(actor="operator") as unit:
        restored = unit.approvals.load(pending.approval_id, lease_token=lease.token)
    assert restored == pending


def test_approval_digest_is_canonical_and_bounded() -> None:
    first = approval_args_digest(
        "write_file",
        {"path": "a.txt", "meta": {"z": 1, "a": [True, None]}},
    )
    reordered = approval_args_digest(
        "write_file",
        {"meta": {"a": [True, None], "z": 1}, "path": "a.txt"},
    )
    changed = approval_args_digest(
        "write_file",
        {"path": "a.txt", "meta": {"z": 1, "a": [None, True]}},
    )
    assert first == reordered
    assert first != changed
    with pytest.raises(ApprovalStoreError) as invalid:
        approval_args_digest("write_file", {"bad": object()})
    assert invalid.value.code == ErrorCode.APPROVAL_INVALID
    with pytest.raises(ApprovalStoreError):
        approval_args_digest("write_file", {"bad": float("nan")})


def test_approval_rejects_binding_mismatch_and_self_approval(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    with storage.transaction(actor="agent") as unit:
        with pytest.raises(ApprovalStoreError) as mismatch:
            unit.approvals.create(
                _approval_request(normalized_args={"path": "other.md", "content": "x"}),
                lease_token=lease.token,
            )
        assert mismatch.value.code == ErrorCode.APPROVAL_BINDING_MISMATCH

    with storage.transaction(actor="agent") as unit:
        with pytest.raises(ApprovalStoreError) as self_error:
            unit.approvals.approve(
                pending.approval_id,
                expected_revision=pending.revision,
                approver=Approver(actor="agent", roles=("operator",)),
                lease_token=lease.token,
            )
        assert self_error.value.code == ErrorCode.APPROVAL_SELF_APPROVAL_DENIED
        assert (
            unit.approvals.load(
                pending.approval_id,
                lease_token=lease.token,
            ).status
            == ApprovalStatus.PENDING
        )


def test_approval_roles_and_happy_path(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)

    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as denied:
            unit.approvals.approve(
                pending.approval_id,
                expected_revision=pending.revision,
                approver=Approver(actor="operator", roles=("viewer",)),
                lease_token=lease.token,
            )
        assert denied.value.code == ErrorCode.APPROVAL_APPROVER_DENIED
        with pytest.raises(ApprovalStoreError) as conflict:
            unit.approvals.approve(
                pending.approval_id,
                expected_revision=99,
                approver=Approver(actor="operator", roles=("operator",)),
                lease_token=lease.token,
            )
        assert conflict.value.code == ErrorCode.APPROVAL_CONFLICT

    approved = _approve_pending_approval(storage, lease, pending)
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.revision == 1
    assert approved.decided_by == "operator"


def test_approval_expiry_stops_run_atomically(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(
        storage,
        request=_approval_request(ttl_seconds=10),
    )
    clock.advance(10)

    with storage.transaction(actor="harness") as unit:
        expired = unit.approvals.expire_due(RUN_ID, lease_token=lease.token)
        unit.state.transition_run(
            RUN_ID,
            expected_status=RunStatus.WAITING_APPROVAL,
            expected_revision=_run_revision(unit),
            target=RunStatus.STOPPED,
            stop_reason=StopReason.APPROVAL_EXPIRED,
            ended_at=NOW,
            acceptance_status=AcceptanceStatus.NOT_EVALUATED,
            lease_token=lease.token,
        )

    assert len(expired) == 1
    assert expired[0].approval_id == pending.approval_id
    assert expired[0].status == ApprovalStatus.EXPIRED
    with storage.transaction(actor="operator") as unit:
        run = unit.state.get_run(RUN_ID)
        events = unit.audit.list_approval_events(RUN_ID, pending.approval_id)
    assert run.status == RunStatus.STOPPED
    assert run.stop_reason == StopReason.APPROVAL_EXPIRED
    assert run.acceptance_status == AcceptanceStatus.NOT_EVALUATED
    assert [event.event_type.value for event in events] == [
        "APPROVAL_REQUESTED",
        "APPROVAL_EXPIRED",
    ]


def test_approval_rejection_stops_run_atomically(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)

    with storage.transaction(actor="operator") as unit:
        rejected = unit.approvals.reject(
            pending.approval_id,
            expected_revision=pending.revision,
            approver=Approver(actor="operator", roles=("operator",)),
            reason_code="HUMAN_REJECTED",
            lease_token=lease.token,
        )
        unit.state.transition_run(
            RUN_ID,
            expected_status=RunStatus.WAITING_APPROVAL,
            expected_revision=_run_revision(unit),
            target=RunStatus.STOPPED,
            stop_reason=StopReason.APPROVAL_REJECTED,
            ended_at=NOW,
            acceptance_status=AcceptanceStatus.NOT_EVALUATED,
            lease_token=lease.token,
        )

    assert rejected.status == ApprovalStatus.REJECTED
    with storage.transaction(actor="operator") as unit:
        run = unit.state.get_run(RUN_ID)
    assert run.status == RunStatus.STOPPED
    assert run.stop_reason == StopReason.APPROVAL_REJECTED


def test_duplicate_approval_requests_are_idempotent_or_conflict(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)

    with storage.transaction(actor="agent") as unit:
        replay = unit.approvals.create(_approval_request(), lease_token=lease.token)
        assert replay == pending
        audit_count = len(unit.audit.list_approval_events(RUN_ID, pending.approval_id))
        with pytest.raises(ApprovalStoreError) as conflict:
            unit.approvals.create(
                _approval_request(ttl_seconds=600),
                lease_token=lease.token,
            )
        assert conflict.value.code == ErrorCode.APPROVAL_CONFLICT
    assert audit_count == 1


def test_concurrent_claim_has_one_winner(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)
    second_storage = SQLiteStorage(storage.database_path, storage.workspace_root, clock)
    second_storage.initialize()
    barrier = threading.Barrier(2)
    results: list[ApprovalStatus | ErrorCode] = []
    result_lock = threading.Lock()

    def worker(active_storage: SQLiteStorage) -> None:
        barrier.wait()
        try:
            with active_storage.transaction(actor="operator") as unit:
                claimed = unit.approvals.claim(
                    approved.approval_id,
                    expected_revision=approved.revision,
                    execution_intent_id=approved.tool_call_id,
                    lease_token=lease.token,
                )
                result: ApprovalStatus | ErrorCode = claimed.status
        except ApprovalStoreError as exc:
            result = exc.code
        with result_lock:
            results.append(result)

    threads = [
        threading.Thread(target=worker, args=(storage,)),
        threading.Thread(target=worker, args=(second_storage,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.value for result in results) == [
        "APPROVAL_ALREADY_CLAIMED",
        "CLAIMED",
    ]


def test_resume_claim_and_deterministic_consume(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)
    claimed = _claim_approval(storage, lease, approved)

    with storage.transaction(actor="operator") as unit:
        resumed = unit.approvals.resume_claim(
            claimed.approval_id,
            expected_revision=claimed.revision,
            execution_intent_id=claimed.tool_call_id,
            lease_token=lease.token,
        )
    assert resumed.status == ApprovalStatus.CLAIMED

    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=_tool_revision(unit),
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )

    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.STARTED,
            expected_revision=_tool_revision(unit),
            target=InvocationStatus.SUCCEEDED,
            result_summary="write completed",
            error_code=None,
            lease_token=lease.token,
        )
        consumed = unit.approvals.consume(
            claimed.approval_id,
            expected_revision=claimed.revision,
            execution_intent_id=claimed.tool_call_id,
            result_status=InvocationStatus.SUCCEEDED,
            result_code=None,
            lease_token=lease.token,
        )

    assert consumed.status == ApprovalStatus.CONSUMED
    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as replay:
            unit.approvals.claim(
                claimed.approval_id,
                expected_revision=consumed.revision,
                execution_intent_id=claimed.tool_call_id,
                lease_token=lease.token,
            )
        assert replay.value.code == ErrorCode.APPROVAL_NOT_EXECUTABLE
        events = unit.audit.list_approval_events(RUN_ID, claimed.approval_id)
    assert [event.event_type.value for event in events] == [
        "APPROVAL_REQUESTED",
        "APPROVAL_DECIDED",
        "APPROVAL_CLAIMED",
        "APPROVAL_CLAIM_RECOVERED",
        "APPROVAL_CONSUMED",
    ]
    with storage.transaction(actor="agent") as unit:
        with pytest.raises(ApprovalStoreError) as replayed:
            unit.approvals.create(_approval_request(), lease_token=lease.token)
    assert replayed.value.code == ErrorCode.APPROVAL_REPLAYED


def test_unknown_result_keeps_approval_claimed(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)
    claimed = _claim_approval(storage, lease, approved)

    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=_tool_revision(unit),
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )
    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.STARTED,
            expected_revision=_tool_revision(unit),
            target=InvocationStatus.UNKNOWN_RESULT,
            result_summary=None,
            error_code="TOOL_RESULT_UNKNOWN",
            lease_token=lease.token,
        )
        with pytest.raises(ApprovalStoreError):
            unit.approvals.consume(
                claimed.approval_id,
                expected_revision=claimed.revision,
                execution_intent_id=claimed.tool_call_id,
                result_status=InvocationStatus.UNKNOWN_RESULT,
                result_code="TOOL_RESULT_UNKNOWN",
                lease_token=lease.token,
            )
        with pytest.raises(ApprovalStoreError):
            unit.approvals.resume_claim(
                claimed.approval_id,
                expected_revision=claimed.revision,
                execution_intent_id=claimed.tool_call_id,
                lease_token=lease.token,
            )
        current = unit.approvals.load(claimed.approval_id, lease_token=lease.token)
    assert current.status == ApprovalStatus.CLAIMED


@pytest.mark.parametrize(
    ("result_status", "result_code"),
    [
        (InvocationStatus.FAILED, "TOOL_FAILED"),
        (InvocationStatus.TIMED_OUT, "TOOL_TIMED_OUT"),
    ],
)
def test_deterministic_failures_consume_approval(
    tmp_path: Path,
    result_status: InvocationStatus,
    result_code: str,
) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage, idempotent=True)
    approved = _approve_pending_approval(storage, lease, pending)
    claimed = _claim_approval(storage, lease, approved)

    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=_tool_revision(unit),
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )
    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.STARTED,
            expected_revision=_tool_revision(unit),
            target=result_status,
            result_summary=None,
            error_code=result_code,
            lease_token=lease.token,
        )
        consumed = unit.approvals.consume(
            claimed.approval_id,
            expected_revision=claimed.revision,
            execution_intent_id=claimed.tool_call_id,
            result_status=result_status,
            result_code=result_code,
            lease_token=lease.token,
        )
    assert consumed.status == ApprovalStatus.CONSUMED
    assert consumed.consumption_code == result_code


def test_started_invocation_cannot_be_invalidated(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)
    claimed = _claim_approval(storage, lease, approved)
    with storage.transaction(actor="operator") as unit:
        unit.state.transition_invocation(
            RUN_ID,
            APPROVAL_TOOL_CALL_ID,
            expected_status=InvocationStatus.PREPARED,
            expected_revision=_tool_revision(unit),
            target=InvocationStatus.STARTED,
            result_summary=None,
            error_code=None,
            lease_token=lease.token,
        )

    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as invalid:
            unit.approvals.invalidate(
                claimed.approval_id,
                expected_revision=claimed.revision,
                reason_code="WORKSPACE_CHANGED",
                lease_token=lease.token,
            )
        current = unit.approvals.load(claimed.approval_id, lease_token=lease.token)
    assert invalid.value.code == ErrorCode.APPROVAL_NOT_EXECUTABLE
    assert current.status == ApprovalStatus.CLAIMED


def test_stale_binding_invalidates_and_blocks_run(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            "UPDATE runs SET config_fingerprint = ? WHERE run_id = ?",
            (f"sha256:{'b' * 64}", RUN_ID),
        )

    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as mismatch:
            unit.approvals.claim(
                approved.approval_id,
                expected_revision=approved.revision,
                execution_intent_id=approved.tool_call_id,
                lease_token=lease.token,
            )
        assert mismatch.value.code == ErrorCode.APPROVAL_BINDING_MISMATCH
        invalidated = unit.approvals.invalidate(
            approved.approval_id,
            expected_revision=approved.revision,
            reason_code="WORKSPACE_CHANGED",
            lease_token=lease.token,
        )
        unit.state.transition_run(
            RUN_ID,
            expected_status=RunStatus.WAITING_APPROVAL,
            expected_revision=_run_revision(unit),
            target=RunStatus.BLOCKED,
            stop_reason=StopReason.WORKSPACE_CHANGED,
            ended_at=NOW,
            acceptance_status=AcceptanceStatus.NOT_EVALUATED,
            lease_token=lease.token,
        )

    assert invalidated.status == ApprovalStatus.INVALIDATED
    with storage.transaction(actor="operator") as unit:
        run = unit.state.get_run(RUN_ID)
    assert run.status == RunStatus.BLOCKED
    assert run.stop_reason == StopReason.WORKSPACE_CHANGED


def test_approval_fault_rolls_back_status_and_audit(
    tmp_path: Path,
    fault_injector,
) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock, fault_injector=fault_injector)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    fault_injector.arm("approval:before-audit")

    with pytest.raises(RuntimeError, match="injected fault"):
        with storage.transaction(actor="operator") as unit:
            unit.approvals.approve(
                pending.approval_id,
                expected_revision=pending.revision,
                approver=Approver(actor="operator", roles=("operator",)),
                lease_token=lease.token,
            )

    with storage.transaction(actor="operator") as unit:
        current = unit.approvals.load(pending.approval_id, lease_token=lease.token)
        events = unit.audit.list_approval_events(RUN_ID, pending.approval_id)
    assert current.status == ApprovalStatus.PENDING
    assert current.revision == 0
    assert len(events) == 1


def test_approval_api_does_not_touch_repository_files(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    target = storage.workspace_root / "artifacts" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("unchanged", encoding="utf-8")
    lease, pending = _seed_pending_approval(storage)
    _approve_pending_approval(storage, lease, pending)
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_approval_migration_upgrades_v3_and_preserves_runs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    all_migrations = storage_module.MIGRATIONS
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 3)
    monkeypatch.setattr(storage_module, "MIGRATIONS", all_migrations[:3])
    storage.initialize()
    with storage.transaction(actor="agent") as unit:
        unit.state.create_run(_run(), owner_id="owner-1")
    assert storage.schema_version() == 3

    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 4)
    monkeypatch.setattr(storage_module, "MIGRATIONS", all_migrations)
    storage.initialize()
    report = storage.verify_integrity()
    with storage.transaction(actor="agent") as unit:
        run = unit.state.get_run(RUN_ID)
    assert report.ok
    assert report.schema_version == 4
    assert run.run_id == RUN_ID


def test_approval_rows_are_binding_immutable_and_not_deletable(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    _, pending = _seed_pending_approval(storage)

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="APPROVAL_BINDING_IMMUTABLE"):
            connection.execute(
                "UPDATE approvals SET tool_name = 'other_tool' WHERE approval_id = ?",
                (pending.approval_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="APPROVAL_HISTORY_APPEND_ONLY"):
            connection.execute(
                "DELETE FROM approvals WHERE approval_id = ?",
                (pending.approval_id,),
            )


def test_expired_approval_cannot_be_approved_or_claimed(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(
        storage,
        request=_approval_request(ttl_seconds=10),
    )
    clock.advance(10)

    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as approve_error:
            unit.approvals.approve(
                pending.approval_id,
                expected_revision=pending.revision,
                approver=Approver(actor="operator", roles=("operator",)),
                lease_token=lease.token,
            )
        assert approve_error.value.code == ErrorCode.APPROVAL_EXPIRED
        # The caller deliberately catches the stable denial inside the UnitOfWork so
        # the expiry transition and its audit fact still commit.
        expired = unit.approvals.load(pending.approval_id, lease_token=lease.token)
    assert expired.status == ApprovalStatus.EXPIRED


def test_expired_pending_approval_cannot_be_claimed(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(
        storage,
        request=_approval_request(ttl_seconds=10),
    )
    clock.advance(10)

    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as expired_error:
            unit.approvals.claim(
                pending.approval_id,
                expected_revision=pending.revision,
                execution_intent_id=pending.tool_call_id,
                lease_token=lease.token,
            )
        assert expired_error.value.code == ErrorCode.APPROVAL_EXPIRED
        current = unit.approvals.load(pending.approval_id, lease_token=lease.token)
    assert current.status == ApprovalStatus.EXPIRED


def test_approval_state_version_and_nonempty_roles_are_strict() -> None:
    with pytest.raises(ValueError):
        gh.Approval.model_validate(
            {
                "schema_version": 1,
                "approval_id": "approval_00000000000000000000000000000005",
            }
        )
    with pytest.raises(ValueError):
        gh.ApprovalRequest(
            run_id=RUN_ID,
            tool_call_id="call-1",
            normalized_args={},
            requested_by="agent",
            allowed_approver_roles=(),
            ttl_seconds=900,
        )


def test_approval_creation_rolls_back_with_run_transition(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease = _seed_running_invocation(storage)

    with pytest.raises(StateStoreError):
        with storage.transaction(actor="agent") as unit:
            unit.approvals.create(_approval_request(), lease_token=lease.token)
            unit.state.transition_run(
                RUN_ID,
                expected_status=RunStatus.RUNNING,
                expected_revision=999,
                target=RunStatus.WAITING_APPROVAL,
                stop_reason=None,
                ended_at=None,
                acceptance_status=None,
                lease_token=lease.token,
            )

    with storage.transaction(actor="agent") as unit:
        approval_count = unit.connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        run = unit.state.get_run(RUN_ID)
    assert approval_count == 0
    assert invocation.approval_id is None
    assert run.status == RunStatus.RUNNING


def test_claim_rolls_back_when_run_transition_fails(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)

    with pytest.raises(StateStoreError):
        with storage.transaction(actor="operator") as unit:
            unit.approvals.claim(
                approved.approval_id,
                expected_revision=approved.revision,
                execution_intent_id=approved.tool_call_id,
                lease_token=lease.token,
            )
            unit.state.transition_run(
                RUN_ID,
                expected_status=RunStatus.WAITING_APPROVAL,
                expected_revision=999,
                target=RunStatus.RUNNING,
                stop_reason=None,
                ended_at=None,
                acceptance_status=None,
                lease_token=lease.token,
            )

    with storage.transaction(actor="operator") as unit:
        current = unit.approvals.load(approved.approval_id, lease_token=lease.token)
        run = unit.state.get_run(RUN_ID)
    assert current.status == ApprovalStatus.APPROVED
    assert current.revision == approved.revision
    assert run.status == RunStatus.WAITING_APPROVAL


def test_disabled_approval_channel_fails_closed(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    _, pending = _seed_pending_approval(storage)
    channel = DisabledApprovalChannel()
    assert channel.name == "disabled"
    with pytest.raises(ApprovalStoreError) as submit_error:
        channel.submit(pending)
    assert submit_error.value.code == ErrorCode.APPROVAL_CHANNEL_UNAVAILABLE
    with pytest.raises(ApprovalStoreError):
        channel.poll(pending.approval_id)


def test_approval_audit_has_required_correlation_and_valid_chain(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    approved = _approve_pending_approval(storage, lease, pending)
    _claim_approval(storage, lease, approved)
    with storage.transaction(actor="operator") as unit:
        events = unit.audit.list_approval_events(RUN_ID, pending.approval_id)
        chain = unit.audit.verify_chain(RUN_ID)
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]

    assert chain.valid
    assert invocation.approval_id == pending.approval_id
    assert [event.event_type.value for event in events] == [
        "APPROVAL_REQUESTED",
        "APPROVAL_DECIDED",
        "APPROVAL_CLAIMED",
    ]
    assert all(event.run_id == RUN_ID for event in events)
    assert all(event.tool_call_id == APPROVAL_TOOL_CALL_ID for event in events)
    assert all(event.approval_id == pending.approval_id for event in events)
    assert all(event.actor for event in events)
    assert events[0].rule_id == "APPROVAL_BINDING_V1"


def test_corrupt_approval_row_fails_closed(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease, pending = _seed_pending_approval(storage)
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("DROP TRIGGER approvals_binding_immutable")
        connection.execute(
            "UPDATE approvals SET allowed_approver_roles_json = ? WHERE approval_id = ?",
            ("not-json", pending.approval_id),
        )

    with storage.transaction(actor="operator") as unit:
        with pytest.raises(ApprovalStoreError) as invalid:
            unit.approvals.load(pending.approval_id, lease_token=lease.token)
    assert invalid.value.code == ErrorCode.APPROVAL_INVALID


def test_approval_request_actor_must_match_unit_of_work(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    storage, _ = _storage(tmp_path, clock)
    storage.initialize()
    lease = _seed_running_invocation(storage)

    with storage.transaction(actor="other-agent") as unit:
        with pytest.raises(ApprovalStoreError) as invalid:
            unit.approvals.create(_approval_request(), lease_token=lease.token)
    assert invalid.value.code == ErrorCode.APPROVAL_INVALID
