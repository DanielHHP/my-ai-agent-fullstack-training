"""State persistence, migration, and run lease contracts for WP-01."""

from __future__ import annotations

import sqlite3
import stat
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from guarded_harness import (
    BudgetUsage,
    ErrorCode,
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
)
from guarded_harness.domain import WorkspaceIdentity

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
LATER = datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
TRACE_ID = "00000000000000000000000000000002"
STEP_ID = "step_00000000000000000000000000000003"
DIGEST = f"sha256:{'a' * 64}"


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


def test_storage_initializes_schema_and_pragmas(tmp_path: Path, fixed_clock) -> None:
    storage, state_dir = _storage(tmp_path, fixed_clock)

    storage.initialize()
    storage.initialize()

    report = storage.verify_integrity()
    assert report.ok
    assert report.schema_version == 1
    assert report.migration_count == 1
    assert report.journal_mode.casefold() == "wal"
    assert report.foreign_keys_enabled
    assert report.busy_timeout_ms == 5000
    assert report.required_tables_present
    assert report.quick_check.casefold() == "ok"
    assert storage.schema_version() == 1
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
