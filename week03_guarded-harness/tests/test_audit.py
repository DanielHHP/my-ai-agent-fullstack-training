"""Append-only audit chain and correlation tests for WP-01."""

from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from guarded_harness import (
    AuditDecision,
    AuditEventDraft,
    AuditEventType,
    AuditStatus,
    ErrorCode,
    GatePhase,
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
    SystemClock,
    TaskKind,
)
from guarded_harness.domain import WorkspaceIdentity

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
TRACE_ID = "00000000000000000000000000000002"
STEP_ID = "step_00000000000000000000000000000003"
DIGEST = f"sha256:{'a' * 64}"


def _storage(
    tmp_path: Path,
    clock,
    *,
    fault_injector=None,
) -> SQLiteStorage:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = SQLiteStorage(
        tmp_path / "state" / "harness.sqlite3",
        workspace,
        clock,
        fault_injector=fault_injector,
    )
    storage.initialize()
    return storage


def _run() -> Run:
    return Run(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        task_id="understand_login",
        task_kind=TaskKind.CODE_UNDERSTANDING,
        task_description="Explain login",
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
        created_at=NOW,
        updated_at=NOW,
    )


def test_audit_events_are_ordered_hashed_and_correlated(
    tmp_path: Path,
    fixed_clock,
) -> None:
    storage = _storage(tmp_path, fixed_clock)

    with storage.transaction(actor="test-harness") as unit:
        state = unit.state
        lease = state.create_run(_run(), owner_id="owner-1", lease_policy=LeasePolicy())
        state.put_plan(
            RUN_ID,
            expected_revision=0,
            plan=RunPlan(
                run_id=RUN_ID,
                revision=0,
                items=(PlanItem(ordinal=0, summary="Read the entry point"),),
                updated_at=NOW,
            ),
            lease_token=lease.token,
        )
        state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                status=StepStatus.PLANNED,
                summary="Inspect entry point",
                started_at=NOW,
            ),
            expected_run_revision=1,
            lease_token=lease.token,
        )
        state.transition_step(
            STEP_ID,
            expected_status=StepStatus.PLANNED,
            expected_revision=0,
            target=StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        state.transition_run(
            RUN_ID,
            expected_status=RunStatus.NEW,
            expected_revision=3,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
        unit.audit.append(
            AuditEventDraft(
                run_id=RUN_ID,
                event_type=AuditEventType.GATE_DECISION,
                phase=GatePhase.SCHEMA,
                status=AuditStatus.DENIED,
                decision=AuditDecision.DENY,
                code="GATE_INVALID_ARGUMENT",
                tool_call_id="call-1",
                tool_name="write_file",
                rule_id="schema.required",
                input_summary={
                    "Authorization": "Bearer should-not-be-persisted",
                    "path": "src/app.py",
                },
            )
        )

    with storage.transaction(actor="test-harness") as unit:
        events = unit.audit.list_run_events(RUN_ID, limit=100)
        chain = unit.audit.verify_chain(RUN_ID)
        denied = unit.audit.list_tool_events(RUN_ID, "call-1")

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert chain.valid
    assert chain.event_count == len(events)
    assert events[0].event_type == AuditEventType.RUN_CREATED
    assert events[1].event_type == AuditEventType.RUN_LEASE_ACQUIRED
    assert events[-1].event_type == AuditEventType.GATE_DECISION
    assert events[-1].event_id.startswith("audit_")
    assert events[-1].prev_hash == events[-2].event_hash
    assert denied[0].rule_id == "schema.required"
    assert denied[0].decision == AuditDecision.DENY
    assert denied[0].actor == "test-harness"
    assert denied[0].tool_call_id == "call-1"
    assert denied[0].input_summary == {
        "Authorization": "[REDACTED]",
        "path": "src/app.py",
    }
    assert "should-not-be-persisted" not in str(denied[0].model_dump())


def test_audit_rows_reject_direct_update_and_delete(tmp_path: Path, fixed_clock) -> None:
    storage = _storage(tmp_path, fixed_clock)
    with storage.transaction(actor="test-harness") as unit:
        unit.state.create_run(_run(), owner_id="owner-1")

    with sqlite3.connect(storage.database_path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="AUDIT_APPEND_ONLY"):
            connection.execute("UPDATE audit_events SET actor = 'tampered'")
        with pytest.raises(sqlite3.DatabaseError, match="AUDIT_APPEND_ONLY"):
            connection.execute("DELETE FROM audit_events")


def test_audit_chain_detects_tampering_after_trigger_bypass(
    tmp_path: Path,
    fixed_clock,
) -> None:
    storage = _storage(tmp_path, fixed_clock)
    with storage.transaction(actor="test-harness") as unit:
        unit.state.create_run(_run(), owner_id="owner-1")

    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("DROP TRIGGER audit_events_no_update")
        connection.execute("UPDATE audit_events SET actor = 'tampered' WHERE sequence = 1")

    with storage.transaction(actor="test-harness") as unit:
        report = unit.audit.verify_chain(RUN_ID)
    assert not report.valid
    assert report.code == ErrorCode.AUDIT_CHAIN_INVALID


def test_audit_failure_rolls_back_state(
    tmp_path: Path,
    fixed_clock,
    fault_injector,
) -> None:
    storage = _storage(tmp_path, fixed_clock, fault_injector=fault_injector)
    fault_injector.arm("audit:before-insert")

    with pytest.raises(RuntimeError, match="injected fault"):
        with storage.transaction(actor="test-harness") as unit:
            unit.state.create_run(_run(), owner_id="owner-1")

    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM run_leases").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 0


def test_state_failure_rolls_back_audit(
    tmp_path: Path,
    fixed_clock,
) -> None:
    storage = _storage(tmp_path, fixed_clock)
    with storage.transaction(actor="test-harness") as unit:
        unit.state.create_run(_run(), owner_id="owner-1")
    with sqlite3.connect(storage.database_path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    with pytest.raises(StateStoreError) as error:
        with storage.transaction(actor="test-harness") as unit:
            unit.audit.append(
                AuditEventDraft(
                    run_id=RUN_ID,
                    event_type=AuditEventType.GATE_DECISION,
                    phase=GatePhase.SCHEMA,
                    status=AuditStatus.ALLOWED,
                    decision=AuditDecision.ALLOW,
                )
            )
            unit.state.create_run(_run(), owner_id="duplicate")
    assert error.value.code == ErrorCode.STATE_CONFLICT

    with sqlite3.connect(storage.database_path) as connection:
        after = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert after == before


def test_commit_fault_rolls_back_state_and_audit(
    tmp_path: Path,
    fixed_clock,
    fault_injector,
) -> None:
    storage = _storage(tmp_path, fixed_clock, fault_injector=fault_injector)
    with storage.transaction(actor="test-harness") as unit:
        lease = unit.state.create_run(_run(), owner_id="owner-1")
    fault_injector.arm("uow:before-commit")

    with pytest.raises(RuntimeError, match="injected fault"):
        with storage.transaction(actor="test-harness") as unit:
            unit.state.put_plan(
                RUN_ID,
                expected_revision=0,
                plan=RunPlan(
                    run_id=RUN_ID,
                    revision=0,
                    items=(PlanItem(ordinal=0, summary="must roll back"),),
                    updated_at=NOW,
                ),
                lease_token=lease.token,
            )

    with sqlite3.connect(storage.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM run_plans").fetchone()[0] == 0
        assert connection.execute("SELECT revision FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0] == 2


def test_concurrent_audit_appends_have_unique_contiguous_sequences(tmp_path: Path) -> None:
    storage = _storage(tmp_path, SystemClock())
    with storage.transaction(actor="setup") as unit:
        unit.state.create_run(_run(), owner_id="owner-1")

    def append_events(worker: int) -> None:
        worker_storage = SQLiteStorage(storage.database_path, storage.workspace_root, SystemClock())
        with worker_storage.transaction(actor=f"worker-{worker}") as unit:
            for index in range(10):
                unit.audit.append(
                    AuditEventDraft(
                        run_id=RUN_ID,
                        event_type=AuditEventType.GATE_DECISION,
                        phase=GatePhase.SCHEMA,
                        status=AuditStatus.ALLOWED,
                        decision=AuditDecision.ALLOW,
                        input_summary={"worker": worker, "index": index},
                    )
                )

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(append_events, worker) for worker in range(3)]
        for future in futures:
            future.result()

    with storage.transaction(actor="verify") as unit:
        events = unit.audit.list_run_events(RUN_ID, limit=1000)
        report = unit.audit.verify_chain(RUN_ID)
    assert [event.sequence for event in events] == list(range(1, 33))
    assert report.valid


def test_parallel_state_writers_have_one_winner(tmp_path: Path) -> None:
    storage = _storage(tmp_path, SystemClock())
    with storage.transaction(actor="setup") as unit:
        lease = unit.state.create_run(_run(), owner_id="owner-1")

    barrier = threading.Barrier(2)

    def update_plan() -> ErrorCode | None:
        worker_storage = SQLiteStorage(storage.database_path, storage.workspace_root, SystemClock())
        barrier.wait()
        try:
            with worker_storage.transaction(actor="writer") as unit:
                unit.state.put_plan(
                    RUN_ID,
                    expected_revision=0,
                    plan=RunPlan(
                        run_id=RUN_ID,
                        revision=0,
                        items=(PlanItem(ordinal=0, summary="competing plan"),),
                        updated_at=NOW,
                    ),
                    lease_token=lease.token,
                )
        except StateStoreError as exc:
            return exc.code
        return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [
            future.result()
            for future in (
                executor.submit(update_plan),
                executor.submit(update_plan),
            )
        ]
    assert outcomes.count(None) == 1
    assert outcomes.count(ErrorCode.STATE_CONFLICT) == 1
