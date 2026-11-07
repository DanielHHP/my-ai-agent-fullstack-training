"""WP-12 project-level failure, recovery, and formal-sample acceptance tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from shutil import copytree

import pytest
from fake_model import FakeScriptedModel, final_answer, tool_call
from fault_injection import (
    CRASH_CLOCK_START,
    CRASH_EXIT_CODE,
    OffsetClock,
    fault_tool_factory,
    hold_database_write_lock,
    minimal_subprocess_env,
)

import guarded_harness as gh

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = REPO_ROOT / "examples"


@dataclass(frozen=True)
class SampleFixture:
    """Resolved paths and config for one copied formal sample."""

    config_path: Path
    workspace: Path
    state_dir: Path
    database_path: Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _copy_examples(tmp_path: Path) -> Path:
    destination = tmp_path / "examples"
    copytree(EXAMPLES_ROOT, destination)
    subprocess.run(
        ["git", "init", "-q", str(destination)],
        check=True,
        capture_output=True,
    )
    _git(destination, "config", "user.email", "wp12@example.invalid")
    _git(destination, "config", "user.name", "WP12 Tests")
    _git(destination, "add", ".")
    _git(destination, "commit", "-q", "-m", "sample baseline")
    return destination


def _sample_fixture(tmp_path: Path, name: str) -> SampleFixture:
    examples = _copy_examples(tmp_path)
    config_path = examples / "config" / f"{name}.yaml"
    config = gh.load_config(config_path)
    workspace = Path(config.workspace_root)
    state_dir = Path(config.state_dir)
    return SampleFixture(
        config_path=config_path,
        workspace=workspace,
        state_dir=state_dir,
        database_path=state_dir / "harness.sqlite3",
    )


def _snapshot(fixture: SampleFixture, run_id: str) -> gh.RunSnapshot:
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="wp12-reader") as unit:
        return unit.state.get_run_snapshot(run_id)


def _approve(
    fixture: SampleFixture,
    run_id: str,
    approval_id: str,
    *,
    role: str = "local_operator",
) -> None:
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="wp12-operator") as unit:
        lease = unit.state.acquire_lease(run_id, owner_id="wp12-operator")
        approval = unit.approvals.load(approval_id, lease_token=lease.token)
        unit.approvals.approve(
            approval.approval_id,
            expected_revision=approval.revision,
            approver=gh.Approver(
                actor="wp12-operator",
                roles=(role,),
            ),
            lease_token=lease.token,
        )
        unit.state.release_lease(lease)


def _run(
    fixture: SampleFixture,
    model: FakeScriptedModel,
    *,
    run_id: str | None = None,
    clock: gh.Clock | None = None,
    tool_factory=None,
) -> gh.RunResult:
    request = (
        gh.RunRequest(mode=gh.RunMode.START)
        if run_id is None
        else gh.RunRequest(mode=gh.RunMode.RESUME, run_id=run_id)
    )
    return gh.Harness(
        config_path=fixture.config_path,
        model=model,
        clock=clock or gh.SystemClock(),
        tool_factory=tool_factory,
    ).run(request)


def _crash_config(tmp_path: Path) -> SampleFixture:
    root = tmp_path / "crash-repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    (workspace / "src" / "app.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "wp12@example.invalid")
    _git(root, "config", "user.name", "WP12 Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "crash baseline")
    state_dir = tmp_path / "crash-state"
    config_path = tmp_path / "crash.yaml"
    config_path.write_text(
        f"""version: 1
state_dir: {state_dir}
workspace:
  root: {workspace}
  require_clean_workspace: true
  protect_other_git_changes: true
budget:
  max_steps: 8
  max_tool_calls: 8
  max_token_cost: 1
  max_wall_time_seconds: 600
task:
  id: wp12_crash_window
  kind: code_understanding
  description: Exercise the post-handler pre-result crash window.
  allowed_tools:
    - fault_write
  paths:
    deny_write:
      - src/**
    allow_write:
      - artifacts/crash-result.txt
  acceptance:
    strategy: code_understanding
    deliverables:
      - artifacts/crash-result.txt
  approval:
    required_tools: []
    approver_roles:
      - local_operator
    ttl_seconds: 600
    channel: local_cli
""",
        encoding="utf-8",
    )
    return SampleFixture(
        config_path=config_path,
        workspace=workspace,
        state_dir=state_dir,
        database_path=state_dir / "harness.sqlite3",
    )


def _checkpoint_crash_config(tmp_path: Path) -> SampleFixture:
    root = tmp_path / "checkpoint-repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    (workspace / "src" / "app.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "wp12@example.invalid")
    _git(root, "config", "user.name", "WP12 Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "checkpoint baseline")
    state_dir = tmp_path / "checkpoint-state"
    config_path = tmp_path / "checkpoint-crash.yaml"
    config_path.write_text(
        f"""version: 1
state_dir: {state_dir}
workspace:
  root: {workspace}
  require_clean_workspace: true
  protect_other_git_changes: true
budget:
  max_steps: 8
  max_tool_calls: 8
  max_token_cost: 1
  max_wall_time_seconds: 600
task:
  id: wp10_checkpoint_window
  kind: code_understanding
  description: Exercise result-committed and checkpoint-missing recovery.
  allowed_tools:
    - read_file
  paths:
    deny_write:
      - src/**
    allow_write:
      - artifacts/report.md
  acceptance:
    strategy: code_understanding
    deliverables:
      - artifacts/report.md
  approval:
    required_tools: []
    approver_roles:
      - local_operator
    ttl_seconds: 600
    channel: local_cli
""",
        encoding="utf-8",
    )
    return SampleFixture(
        config_path=config_path,
        workspace=workspace,
        state_dir=state_dir,
        database_path=state_dir / "harness.sqlite3",
    )


def test_formal_examples_load_with_strict_production_loader() -> None:
    code_config = gh.load_config(EXAMPLES_ROOT / "config" / "code-understanding.yaml")
    repair_config = gh.load_config(EXAMPLES_ROOT / "config" / "test-repair.yaml")

    assert code_config.config.task.kind == gh.TaskKind.CODE_UNDERSTANDING
    assert code_config.config.task.acceptance.deliverables == ("artifacts/code-understanding.md",)
    assert repair_config.config.task.kind == gh.TaskKind.TEST_REPAIR
    assert repair_config.config.task.acceptance.test_command[-1] == "-q"
    assert Path(code_config.state_dir) != Path(code_config.workspace_root)
    assert Path(repair_config.state_dir) != Path(repair_config.workspace_root)


def test_code_understanding_formal_sample_waits_then_accepts(
    tmp_path: Path,
) -> None:
    fixture = _sample_fixture(tmp_path, "code-understanding")
    document = """## 事实

- `login_status(True)` 返回 `ok`，未认证时返回 `denied`。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
"""
    waiting = _run(
        fixture,
        FakeScriptedModel(
            (
                tool_call(
                    "wp12-read-1",
                    "read_file",
                    {"path": "src/app.py", "offset": 1, "limit": 20},
                ),
                tool_call(
                    "wp12-write-1",
                    "write_file",
                    {
                        "path": "artifacts/code-understanding.md",
                        "content": document,
                    },
                ),
            )
        ),
    )

    report = fixture.workspace / "artifacts" / "code-understanding.md"
    assert waiting.status == gh.RunStatus.WAITING_APPROVAL
    assert waiting.approval_id is not None
    assert not report.exists()
    approval_id = waiting.approval_id
    assert approval_id is not None
    _approve(fixture, waiting.run_id, approval_id)

    resumed = _run(
        fixture,
        FakeScriptedModel((final_answer("The report is complete."),)),
        run_id=waiting.run_id,
    )

    assert resumed.status == gh.RunStatus.COMPLETED
    assert resumed.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    assert report.read_text(encoding="utf-8") == document
    expected_source = (
        "def login_status(is_authenticated: bool) -> str:\n"
        '    return "ok" if is_authenticated else "denied"\n'
    )
    assert (fixture.workspace / "src" / "app.py").read_text(encoding="utf-8") == expected_source


def test_test_repair_formal_sample_baseline_patch_and_accepts(
    tmp_path: Path,
) -> None:
    fixture = _sample_fixture(tmp_path, "test-repair")
    source = fixture.workspace / "src" / "calculator.py"
    original = source.read_text(encoding="utf-8")
    waiting = _run(
        fixture,
        FakeScriptedModel(
            (
                tool_call(
                    "wp12-patch-1",
                    "apply_patch",
                    {
                        "path": "src/calculator.py",
                        "edits": [
                            {
                                "oldText": "return left - right",
                                "newText": "return left + right",
                            }
                        ],
                    },
                ),
            )
        ),
    )

    assert waiting.status == gh.RunStatus.WAITING_APPROVAL
    assert waiting.approval_id is not None
    assert source.read_text(encoding="utf-8") == original
    approval_id = waiting.approval_id
    assert approval_id is not None
    _approve(fixture, waiting.run_id, approval_id)

    resumed = _run(
        fixture,
        FakeScriptedModel((final_answer("The calculator test is repaired."),)),
        run_id=waiting.run_id,
    )

    assert resumed.status == gh.RunStatus.COMPLETED
    assert resumed.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    assert source.read_text(encoding="utf-8") == original.replace(
        "return left - right",
        "return left + right",
    )
    snapshot = _snapshot(fixture, waiting.run_id)
    test_calls = [call for call in snapshot.invocations if call.tool_name == "test_verification"]
    assert len(test_calls) == 2


def test_hard_exit_after_side_effect_recovers_unknown_without_replay(
    tmp_path: Path,
) -> None:
    fixture = _crash_config(tmp_path)
    marker = tmp_path / "crash-marker.json"
    runner = Path(__file__).with_name("fault_injection.py")
    child = subprocess.run(
        [
            sys.executable,
            str(runner),
            "crash-after-side-effect",
            str(fixture.config_path),
            str(marker),
        ],
        cwd=tmp_path,
        env=minimal_subprocess_env(
            test_root=tmp_path,
            tests_dir=runner.parent,
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert child.returncode == CRASH_EXIT_CODE, child.stderr
    marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_payload["point"] == "wp12:handler-after-side-effect"
    report = fixture.workspace / "artifacts" / "crash-result.txt"
    assert report.read_text(encoding="utf-8") == "written-before-crash\n"
    with sqlite3.connect(fixture.database_path) as connection:
        run_id = str(connection.execute("SELECT run_id FROM runs").fetchone()[0])
    before = _snapshot(fixture, run_id)
    assert before.invocations[0].status == gh.InvocationStatus.STARTED

    recovery_clock = OffsetClock(CRASH_CLOCK_START)
    recovery_clock.advance(60)
    resume_model = FakeScriptedModel((final_answer("must not be replayed"),))
    resumed = _run(
        fixture,
        resume_model,
        run_id=run_id,
        clock=recovery_clock,
        tool_factory=fault_tool_factory(None),
    )

    assert resumed.status == gh.RunStatus.BLOCKED
    assert resumed.error_code == gh.ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED.value
    assert resume_model.calls == []
    assert report.read_text(encoding="utf-8") == "written-before-crash\n"
    after = _snapshot(fixture, run_id)
    assert after.run.stop_reason == gh.StopReason.UNKNOWN_RESULT
    assert after.invocations[0].status == gh.InvocationStatus.UNKNOWN_RESULT


def test_result_committed_before_checkpoint_resumes_without_replay(
    tmp_path: Path,
) -> None:
    fixture = _checkpoint_crash_config(tmp_path)
    marker = tmp_path / "checkpoint-crash-marker.json"
    runner = Path(__file__).with_name("fault_injection.py")
    child = subprocess.run(
        [
            sys.executable,
            str(runner),
            "exit-after-result-before-checkpoint",
            str(fixture.config_path),
            str(marker),
        ],
        cwd=tmp_path,
        env=minimal_subprocess_env(
            test_root=tmp_path,
            tests_dir=runner.parent,
        ),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert child.returncode == CRASH_EXIT_CODE, child.stderr
    assert json.loads(marker.read_text(encoding="utf-8"))["count"] == 2
    with sqlite3.connect(fixture.database_path) as connection:
        run_id = str(connection.execute("SELECT run_id FROM runs").fetchone()[0])
        kinds = {
            str(row[0])
            for row in connection.execute(
                "SELECT boundary_kind FROM checkpoints WHERE run_id = ?",
                (run_id,),
            )
        }
    before = _snapshot(fixture, run_id)
    assert before.invocations[0].status == gh.InvocationStatus.SUCCEEDED
    assert "TOOL_SUCCEEDED" not in kinds

    recovery_clock = OffsetClock(CRASH_CLOCK_START)
    recovery_clock.advance(60)
    resume_model = FakeScriptedModel((final_answer("no report was written"),))
    resumed = _run(
        fixture,
        resume_model,
        run_id=run_id,
        clock=recovery_clock,
    )

    assert resumed.status == gh.RunStatus.COMPLETED
    assert resumed.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert len(resume_model.calls) == 1
    after = _snapshot(fixture, run_id)
    reads = [call for call in after.invocations if call.tool_name == "read_file"]
    assert len(reads) == 1
    assert reads[0].status == gh.InvocationStatus.SUCCEEDED
    with sqlite3.connect(fixture.database_path) as connection:
        recovered_kinds = {
            str(row[0])
            for row in connection.execute(
                "SELECT boundary_kind FROM checkpoints WHERE run_id = ?",
                (run_id,),
            )
        }
    assert "TOOL_SUCCEEDED" in recovered_kinds


def test_database_write_lock_fails_closed_without_partial_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    storage = gh.SQLiteStorage(
        tmp_path / "state" / "harness.sqlite3",
        workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    before = storage.schema_version()

    with hold_database_write_lock(storage.database_path):
        blocked = gh.SQLiteStorage(
            storage.database_path,
            workspace,
            gh.SystemClock(),
            busy_timeout_ms=20,
        )
        with pytest.raises(gh.StorageError) as failure:
            with blocked.transaction(actor="wp12-lock-test"):
                raise AssertionError("locked transaction must not yield")

    assert failure.value.code == gh.ErrorCode.STATE_TRANSACTION_FAILED
    assert failure.value.retryable
    assert storage.schema_version() == before


def test_checkpoint_tampering_is_detected_after_successful_run(
    tmp_path: Path,
) -> None:
    fixture = _sample_fixture(tmp_path, "code-understanding")
    document = """## 事实

- `login_status(True)` 返回 `ok`，未认证时返回 `denied`。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
"""
    waiting = _run(
        fixture,
        FakeScriptedModel(
            (
                tool_call(
                    "tamper-read",
                    "read_file",
                    {"path": "src/app.py", "offset": 1, "limit": 20},
                ),
                tool_call(
                    "tamper-write",
                    "write_file",
                    {
                        "path": "artifacts/code-understanding.md",
                        "content": document,
                    },
                ),
            )
        ),
    )
    assert waiting.approval_id is not None
    _approve(fixture, waiting.run_id, waiting.approval_id)
    completed = _run(
        fixture,
        FakeScriptedModel((final_answer("complete"),)),
        run_id=waiting.run_id,
    )
    assert completed.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    assert completed.last_checkpoint_id is not None

    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("DROP TRIGGER checkpoints_no_update")
        connection.execute(
            "UPDATE checkpoints SET snapshot_json = '{}' WHERE checkpoint_id = ?",
            (completed.last_checkpoint_id,),
        )
        connection.commit()

    config = gh.load_config(fixture.config_path)
    service = gh.CheckpointService(
        storage=gh.SQLiteStorage(
            fixture.database_path,
            fixture.workspace,
            gh.SystemClock(),
        ),
        config=config,
        sandbox=gh.Sandbox.from_resolved_config(config),
    )
    with pytest.raises(gh.CheckpointServiceError):
        service.get_checkpoint(completed.last_checkpoint_id)


def test_lease_conflict_fences_old_token(tmp_path: Path) -> None:
    fixture = _sample_fixture(tmp_path, "code-understanding")
    waiting = _run(
        fixture,
        FakeScriptedModel(
            (
                tool_call(
                    "lease-read",
                    "read_file",
                    {"path": "src/app.py", "offset": 1, "limit": 20},
                ),
                tool_call(
                    "lease-write",
                    "write_file",
                    {
                        "path": "artifacts/code-understanding.md",
                        "content": "## 事实\n\n- 无\n\n## 推断\n\n- 无\n",
                    },
                ),
            )
        ),
    )
    clock = OffsetClock()
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        clock,
    )
    storage.initialize()
    with storage.transaction(actor="first-owner") as unit:
        first = unit.state.acquire_lease(waiting.run_id, owner_id="first-owner")
    with storage.transaction(actor="second-owner") as unit:
        with pytest.raises(gh.StateStoreError) as held:
            unit.state.acquire_lease(waiting.run_id, owner_id="second-owner")
    assert held.value.code == gh.ErrorCode.STATE_LEASE_HELD

    clock.advance(31)
    with storage.transaction(actor="second-owner") as unit:
        second = unit.state.acquire_lease(waiting.run_id, owner_id="second-owner")
    with storage.transaction(actor="second-owner") as unit:
        with pytest.raises(gh.StateStoreError) as stale:
            unit.state.verify_lease(waiting.run_id, first.token)
        unit.state.verify_lease(waiting.run_id, second.token)
    assert stale.value.code == gh.ErrorCode.STATE_LEASE_INVALID


def test_production_cli_rejects_unknown_fault_flags(
    tmp_path: Path,
) -> None:
    fixture = _sample_fixture(tmp_path, "code-understanding")
    from guarded_harness.cli import main

    code = main(
        [
            "--config",
            str(fixture.config_path),
            "run",
            "--fault-point",
            "audit:before-insert",
        ]
    )

    assert code == 2
