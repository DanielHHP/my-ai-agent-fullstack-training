"""Offline Agent loop, approval, budget, and completion integration tests."""

from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fake_model import (
    FakeScriptedModel,
    ScriptedResponse,
    ScriptedToolCall,
    final_answer,
    multi_tool_call,
    tool_call,
)

import guarded_harness as gh
import guarded_harness.agent_adapter as adapter_module
import guarded_harness.storage as storage_module


@dataclass(frozen=True)
class HarnessFixture:
    config_path: Path
    workspace: Path
    state_dir: Path
    source: str

    @property
    def database_path(self) -> Path:
        return self.state_dir / "harness.sqlite3"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 11, 7, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _fixture(
    tmp_path: Path,
    *,
    max_steps: int = 12,
    max_tool_calls: int = 20,
    max_token_cost: str = "1",
    max_wall_time_seconds: int = 600,
    allowed_tools: tuple[str, ...] = ("read_file", "write_file"),
    required_tools: tuple[str, ...] = ("write_file",),
) -> HarnessFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    source = "def value():\n    return 1\n"
    (workspace / "src" / "app.py").write_text(source, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Harness Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    state_dir = tmp_path / "state"
    allowed_yaml = "\n".join(f"    - {name}" for name in allowed_tools)
    required_yaml = "\n".join(f"      - {name}" for name in required_tools)
    config_path = tmp_path / "harness.yaml"
    config_path.write_text(
        f"""version: 1
state_dir: {state_dir}
workspace:
  root: {workspace}
  require_clean_workspace: true
  protect_other_git_changes: true
budget:
  max_steps: {max_steps}
  max_tool_calls: {max_tool_calls}
  max_token_cost: {max_token_cost}
  max_wall_time_seconds: {max_wall_time_seconds}
task:
  id: understand_app
  kind: code_understanding
  description: Explain value without changing source.
  allowed_tools:
{allowed_yaml}
  paths:
    deny_read:
      - "**/.env"
    deny_write:
      - src/**
    allow_write:
      - artifacts/report.md
  acceptance:
    strategy: code_understanding
    deliverables:
      - artifacts/report.md
  approval:
    required_tools:
{required_yaml or "      []"}
    approver_roles:
      - operator
    ttl_seconds: 600
    channel: local_cli
""",
        encoding="utf-8",
    )
    return HarnessFixture(
        config_path=config_path,
        workspace=workspace,
        state_dir=state_dir,
        source=source,
    )


def _test_repair_fixture(tmp_path: Path) -> HarnessFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    source = "def value():\n    return 1\n"
    (workspace / "src" / "app.py").write_text(source, encoding="utf-8")
    (workspace / "tests" / "test_app.py").write_text(
        "from src.app import value\n\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Harness Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    state_dir = tmp_path / "state"
    config_path = tmp_path / "test-repair.yaml"
    config_path.write_text(
        f"""version: 1
state_dir: {state_dir}
workspace:
  root: {workspace}
  require_clean_workspace: true
  protect_other_git_changes: true
budget:
  max_steps: 12
  max_tool_calls: 20
  max_token_cost: 1
  max_wall_time_seconds: 600
task:
  id: repair_app
  kind: test_repair
  description: Repair the failing test by changing source only.
  allowed_tools:
    - read_file
    - apply_patch
  paths:
    deny_write:
      - tests/**
    allow_write:
      - src/**
  acceptance:
    strategy: test_repair
    test_command:
      - python
      - -m
      - pytest
      - -q
    baseline_required: true
    report_format: pytest_junit_xml
  approval:
    required_tools:
      - apply_patch
    approver_roles:
      - operator
    ttl_seconds: 600
    channel: local_cli
""",
        encoding="utf-8",
    )
    return HarnessFixture(
        config_path=config_path,
        workspace=workspace,
        state_dir=state_dir,
        source=source,
    )


def _valid_document() -> str:
    return """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
"""


def _snapshot(fixture: HarnessFixture, run_id: str) -> gh.RunSnapshot:
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="test-reader") as unit:
        return unit.state.get_run_snapshot(run_id)


def _approve(fixture: HarnessFixture, run_id: str, approval_id: str) -> None:
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="local-operator") as unit:
        lease = unit.state.acquire_lease(run_id, owner_id="local-operator")
        approval = unit.approvals.load(approval_id, lease_token=lease.token)
        unit.approvals.approve(
            approval.approval_id,
            expected_revision=approval.revision,
            approver=gh.Approver(actor="local-operator", roles=("operator",)),
            lease_token=lease.token,
        )
        unit.state.release_lease(lease)


def _run_start(fixture: HarnessFixture, model: FakeScriptedModel) -> gh.RunResult:
    return gh.Harness(
        config_path=fixture.config_path,
        model=model,
        clock=gh.SystemClock(),
    ).run(gh.RunRequest(mode=gh.RunMode.START))


def _run_resume(
    fixture: HarnessFixture,
    run_id: str,
    model: FakeScriptedModel,
) -> gh.RunResult:
    return gh.Harness(
        config_path=fixture.config_path,
        model=model,
        clock=gh.SystemClock(),
    ).run(gh.RunRequest(mode=gh.RunMode.RESUME, run_id=run_id))


def test_code_understanding_waits_then_resumes_and_accepts(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    document = _valid_document()
    start_model = FakeScriptedModel(
        (
            tool_call(
                "read-1",
                "read_file",
                {"path": "src/app.py", "offset": 1, "limit": 20},
            ),
            tool_call(
                "write-1",
                "write_file",
                {"path": "artifacts/report.md", "content": document},
            ),
        )
    )

    waiting = _run_start(fixture, start_model)

    assert waiting.status == gh.RunStatus.WAITING_APPROVAL
    assert waiting.stop_reason is None
    assert waiting.approval_id is not None
    assert waiting.error_code == gh.ErrorCode.APPROVAL_REQUIRED.value
    assert not (fixture.workspace / "artifacts" / "report.md").exists()
    start_model.assert_consumed()
    tools = start_model.calls[0].tools
    assert {tool.name for tool in tools} == {"read_file", "write_file", "final_answer"}
    assert all(not hasattr(tool, "handler") for tool in tools)

    pending = _snapshot(fixture, waiting.run_id)
    assert [call.tool_call_id for call in pending.invocations] == ["read-1", "write-1"]
    assert pending.invocations[0].status == gh.InvocationStatus.SUCCEEDED
    assert pending.invocations[1].status == gh.InvocationStatus.PREPARED

    _approve(fixture, waiting.run_id, waiting.approval_id)
    resume_model = FakeScriptedModel((final_answer("The explanation is complete."),))
    completed = _run_resume(fixture, waiting.run_id, resume_model)

    assert completed.status == gh.RunStatus.COMPLETED
    assert completed.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    assert completed.completion is not None
    assert all(item.passed for item in completed.completion.items)
    assert (fixture.workspace / "artifacts" / "report.md").read_text(encoding="utf-8") == document
    resume_model.assert_consumed()

    final = _snapshot(fixture, waiting.run_id)
    agent_calls = [
        call for call in final.invocations if call.tool_name in {"read_file", "write_file"}
    ]
    assert [call.tool_call_id for call in agent_calls] == ["read-1", "write-1"]
    assert agent_calls[1].status == gh.InvocationStatus.SUCCEEDED
    assert final.approvals[0].status == gh.ApprovalStatus.CONSUMED
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="auditor") as unit:
        baseline = unit.state.get_workspace_baseline(waiting.run_id)
        chain = unit.audit.verify_chain(waiting.run_id)
    assert baseline is not None
    assert baseline.identity == final.run.workspace
    assert chain.valid
    with sqlite3.connect(fixture.database_path) as connection:
        kinds = {
            row[0]
            for row in connection.execute(
                "SELECT boundary_kind FROM checkpoints WHERE run_id = ?",
                (waiting.run_id,),
            )
        }
        with pytest.raises(sqlite3.IntegrityError, match="WORKSPACE_BASELINE_APPEND_ONLY"):
            connection.execute(
                "UPDATE run_workspace_baselines SET created_at = 'x' WHERE run_id = ?",
                (waiting.run_id,),
            )
    assert {
        "RUN_STARTED",
        "TOOL_SUCCEEDED",
        "APPROVAL_REQUESTED",
        "STEP_COMPLETED",
        "APPROVAL_SETTLED",
        "COMPLETION_RECORDED",
    }.issubset(kinds)


def test_final_answer_without_deliverable_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel((final_answer("已完成，测试全部通过。", call_id="claim-1"),)),
    )

    assert result.status == gh.RunStatus.COMPLETED
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert result.candidate_final_answer == "已完成，测试全部通过。"
    assert result.completion is not None
    items = {item.item_id: item for item in result.completion.items}
    assert not items["deliverable_present"].passed
    assert not items["claim_without_evidence"].passed


def test_candidate_final_answer_is_redacted_before_persistence(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel((final_answer("token=super-secret-token"),)),
    )

    assert result.candidate_final_answer is not None
    assert "super-secret-token" not in result.candidate_final_answer


def test_model_exception_fails_without_fake_completion(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel((ScriptedResponse(error=RuntimeError("synthetic failure")),)),
    )

    assert result.status == gh.RunStatus.FAILED
    assert result.stop_reason == gh.StopReason.INTERNAL_ERROR
    assert result.acceptance_status == gh.AcceptanceStatus.NOT_EVALUATED
    assert result.completion is None
    assert result.error_code == gh.ErrorCode.HARNESS_INTERNAL_ERROR.value


def test_step_budget_stops_before_completion(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, max_steps=1, max_tool_calls=5)
    result = _run_start(
        fixture,
        FakeScriptedModel((final_answer("candidate", input_tokens=1, output_tokens=1),)),
    )

    assert result.status == gh.RunStatus.STOPPED
    assert result.stop_reason == gh.StopReason.BUDGET_EXHAUSTED
    assert result.acceptance_status == gh.AcceptanceStatus.NOT_EVALUATED
    assert result.completion is None
    assert result.candidate_final_answer == "candidate"
    assert result.error_code == gh.ErrorCode.GATE_BUDGET_EXHAUSTED.value


def test_token_cost_budget_uses_trusted_decimal_policy(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        max_steps=5,
        max_tool_calls=5,
        max_token_cost="0.000001",
    )
    result = _run_start(
        fixture,
        FakeScriptedModel((final_answer("candidate", input_tokens=1, output_tokens=1),)),
    )

    assert result.status == gh.RunStatus.STOPPED
    assert result.stop_reason == gh.StopReason.BUDGET_EXHAUSTED
    snapshot = _snapshot(fixture, result.run_id)
    assert snapshot.run.usage.token_cost == Decimal("0.000001")


def test_wall_time_budget_stops_before_completion(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        max_steps=5,
        max_tool_calls=5,
        max_wall_time_seconds=1,
    )
    clock = MutableClock()
    model = FakeScriptedModel(
        (final_answer("candidate"),),
        before_generate=lambda: clock.advance(2),
    )
    result = gh.Harness(
        config_path=fixture.config_path,
        model=model,
        clock=clock,
    ).run(gh.RunRequest(mode=gh.RunMode.START))

    assert result.status == gh.RunStatus.STOPPED
    assert result.stop_reason == gh.StopReason.BUDGET_EXHAUSTED
    assert result.acceptance_status == gh.AcceptanceStatus.NOT_EVALUATED
    snapshot = _snapshot(fixture, result.run_id)
    assert snapshot.run.usage.wall_time_seconds == 1


def test_unknown_tool_is_denied_and_audited_before_final_answer(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    model = FakeScriptedModel(
        (
            tool_call("unknown-1", "delete_repository", {"path": "src/app.py"}),
            final_answer("No deliverable was created."),
        )
    )
    result = _run_start(
        fixture,
        model,
    )

    assert result.status == gh.RunStatus.COMPLETED
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    snapshot = _snapshot(fixture, result.run_id)
    assert snapshot.invocations == ()
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="auditor") as unit:
        events = unit.audit.list_tool_events(result.run_id, "unknown-1")
    assert any(event.code == gh.ErrorCode.GATE_TOOL_NOT_FOUND.value for event in events)
    second_call_text = str(model.calls[1].messages)
    assert "GATE_TOOL_NOT_FOUND" in second_call_text
    assert "unknown-1" in second_call_text


def test_schema_injection_is_denied_before_invocation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel(
            (
                tool_call(
                    "bad-1",
                    "read_file",
                    {"path": "src/app.py", "unexpected": "field"},
                ),
                final_answer("No deliverable was created."),
            )
        ),
    )

    assert result.status == gh.RunStatus.COMPLETED
    snapshot = _snapshot(fixture, result.run_id)
    assert snapshot.invocations == ()
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="auditor") as unit:
        events = unit.audit.list_tool_events(result.run_id, "bad-1")
    assert any(event.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value for event in events)


def test_third_equivalent_read_is_denied_after_two_handler_calls(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    read = {"path": "src/app.py"}
    result = _run_start(
        fixture,
        FakeScriptedModel(
            (
                tool_call("read-1", "read_file", read),
                tool_call("read-2", "read_file", read),
                tool_call("read-3", "read_file", read),
                final_answer("No deliverable was created."),
            )
        ),
    )

    assert result.status == gh.RunStatus.COMPLETED
    snapshot = _snapshot(fixture, result.run_id)
    reads = [call for call in snapshot.invocations if call.tool_name == "read_file"]
    assert [call.tool_call_id for call in reads] == ["read-1", "read-2"]
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="auditor") as unit:
        events = unit.audit.list_tool_events(result.run_id, "read-3")
    assert any(event.code == gh.ErrorCode.GATE_REPEATED_CALL.value for event in events)


def test_agent_path_escape_is_denied_before_invocation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel(
            (
                tool_call(
                    "escape-1",
                    "read_file",
                    {"path": "artifacts/../../.env"},
                ),
                final_answer("No deliverable was created."),
            )
        ),
    )

    assert result.status == gh.RunStatus.COMPLETED
    snapshot = _snapshot(fixture, result.run_id)
    assert not any(call.tool_call_id == "escape-1" for call in snapshot.invocations)
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="auditor") as unit:
        events = unit.audit.list_tool_events(result.run_id, "escape-1")
    assert any(event.code.startswith("SANDBOX_PATH_") for event in events)


def test_duplicate_call_ids_fail_before_side_effects(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel(
            (
                multi_tool_call(
                    (
                        ScriptedToolCall("duplicate", "read_file", {"path": "src/app.py"}),
                        ScriptedToolCall("duplicate", "write_file", {"path": "x", "content": "x"}),
                    )
                ),
            )
        ),
    )

    assert result.status == gh.RunStatus.FAILED
    assert result.error_code == gh.ErrorCode.SMOLAGENTS_DUPLICATE_TOOL_CALL_ID.value
    snapshot = _snapshot(fixture, result.run_id)
    assert snapshot.invocations == ()
    assert not (fixture.workspace / "x").exists()


def test_multi_call_batch_stops_at_approval_in_declared_order(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        allowed_tools=("read_file", "write_file", "git_status"),
    )
    result = _run_start(
        fixture,
        FakeScriptedModel(
            (
                multi_tool_call(
                    (
                        ScriptedToolCall("read-1", "read_file", {"path": "src/app.py"}),
                        ScriptedToolCall(
                            "write-1",
                            "write_file",
                            {"path": "artifacts/report.md", "content": _valid_document()},
                        ),
                        ScriptedToolCall("status-after-write", "git_status", {}),
                    )
                ),
            )
        ),
    )

    assert result.status == gh.RunStatus.WAITING_APPROVAL
    snapshot = _snapshot(fixture, result.run_id)
    assert [call.tool_call_id for call in snapshot.invocations] == ["read-1", "write-1"]
    assert not (fixture.workspace / "artifacts" / "report.md").exists()


def test_oversized_final_answer_fails_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _run_start(
        fixture,
        FakeScriptedModel((final_answer("x" * 16_001),)),
    )

    assert result.status == gh.RunStatus.FAILED
    assert result.error_code == gh.ErrorCode.HARNESS_FINAL_ANSWER_INVALID.value
    assert result.candidate_final_answer is None


def test_run_request_requires_mode_specific_run_id() -> None:
    with pytest.raises(ValueError):
        gh.RunRequest(mode=gh.RunMode.START, run_id=f"run_{'0' * 32}")
    with pytest.raises(ValueError):
        gh.RunRequest(mode=gh.RunMode.RESUME)


def test_test_repair_baseline_patch_and_final_acceptance(tmp_path: Path) -> None:
    fixture = _test_repair_fixture(tmp_path)
    start_model = FakeScriptedModel(
        (
            tool_call(
                "patch-1",
                "apply_patch",
                {
                    "path": "src/app.py",
                    "edits": [{"oldText": "return 1", "newText": "return 2"}],
                },
            ),
        )
    )

    waiting = _run_start(fixture, start_model)

    assert waiting.status == gh.RunStatus.WAITING_APPROVAL
    assert {tool.name for tool in start_model.calls[0].tools} == {
        "apply_patch",
        "read_file",
        "final_answer",
    }
    pending = _snapshot(fixture, waiting.run_id)
    assert pending.steps
    storage = gh.SQLiteStorage(
        fixture.database_path,
        fixture.workspace,
        gh.SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="test-reader") as unit:
        baseline = unit.state.get_test_execution(
            waiting.run_id,
            gh.TestExecutionPhase.BASELINE,
        )
    assert baseline is not None
    assert baseline.failed_count >= 1
    assert (fixture.workspace / "src" / "app.py").read_text(encoding="utf-8") == fixture.source
    _approve(fixture, waiting.run_id, waiting.approval_id)

    completed = _run_resume(
        fixture,
        waiting.run_id,
        FakeScriptedModel((final_answer("The failing test is repaired."),)),
    )

    assert completed.status == gh.RunStatus.COMPLETED
    assert completed.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    assert (fixture.workspace / "src" / "app.py").read_text(
        encoding="utf-8"
    ) == "def value():\n    return 2\n"
    final = _snapshot(fixture, waiting.run_id)
    baseline_calls = [call for call in final.invocations if call.tool_name == "test_verification"]
    assert len(baseline_calls) == 2


def test_completed_write_replay_is_suppressed_after_resume(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    document = _valid_document()
    write_arguments = {"path": "artifacts/report.md", "content": document}
    waiting = _run_start(
        fixture,
        FakeScriptedModel(
            (
                tool_call("read-1", "read_file", {"path": "src/app.py"}),
                tool_call("write-1", "write_file", write_arguments),
            )
        ),
    )
    assert waiting.status == gh.RunStatus.WAITING_APPROVAL
    before = _snapshot(fixture, waiting.run_id)
    _approve(fixture, waiting.run_id, waiting.approval_id)

    completed = _run_resume(
        fixture,
        waiting.run_id,
        FakeScriptedModel(
            (
                tool_call("write-replay", "write_file", write_arguments),
                final_answer("The report is complete."),
            )
        ),
    )

    assert completed.status == gh.RunStatus.COMPLETED
    assert completed.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    after = _snapshot(fixture, waiting.run_id)
    writes = [call for call in after.invocations if call.tool_name == "write_file"]
    assert len(writes) == 1
    assert after.run.usage.tool_calls >= before.run.usage.tool_calls
    assert after.run.usage.steps >= before.run.usage.steps


def test_resume_without_approval_returns_waiting_without_model_call(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    waiting = _run_start(
        fixture,
        FakeScriptedModel(
            (
                tool_call("read-1", "read_file", {"path": "src/app.py"}),
                tool_call(
                    "write-1",
                    "write_file",
                    {"path": "artifacts/report.md", "content": _valid_document()},
                ),
            )
        ),
    )
    model = FakeScriptedModel((final_answer("must not be called"),))
    resumed = _run_resume(fixture, waiting.run_id, model)

    assert resumed.status == gh.RunStatus.WAITING_APPROVAL
    assert resumed.approval_id == waiting.approval_id
    assert model.calls == []
    assert not (fixture.workspace / "artifacts" / "report.md").exists()


def test_resume_blocks_when_workspace_changes_after_approval(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    waiting = _run_start(
        fixture,
        FakeScriptedModel(
            (
                tool_call("read-1", "read_file", {"path": "src/app.py"}),
                tool_call(
                    "write-1",
                    "write_file",
                    {"path": "artifacts/report.md", "content": _valid_document()},
                ),
            )
        ),
    )
    _approve(fixture, waiting.run_id, waiting.approval_id)
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 9\n",
        encoding="utf-8",
    )
    model = FakeScriptedModel((final_answer("must not be called"),))
    result = _run_resume(fixture, waiting.run_id, model)

    assert result.status == gh.RunStatus.BLOCKED
    assert result.error_code == gh.ErrorCode.RECOVERY_WORKSPACE_CHANGED.value
    assert model.calls == []
    assert not (fixture.workspace / "artifacts" / "report.md").exists()


def test_unknown_result_blocks_resume_without_model_call(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        allowed_tools=("read_file",),
        required_tools=(),
    )

    def unknown_factory(
        config: gh.ResolvedHarnessConfig,
        sandbox: gh.Sandbox,
    ) -> gh.ToolRegistry:
        del config, sandbox
        registry = gh.ToolRegistry()
        registry.register(
            gh.ToolDefinition(
                spec=gh.ToolSpec(
                    name="read_file",
                    description="Return an indeterminate result.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "minLength": 1},
                        },
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    output_kind=gh.ToolOutputKind.JSON,
                    risk=gh.ToolRisk.READ,
                    required_permissions=frozenset({gh.ToolPermission.READ}),
                    timeout_seconds=5,
                    retry_policy=gh.RetryPolicy.NEVER,
                    idempotent=False,
                    path_arguments=("path",),
                    output_limit_bytes=4096,
                    approval_requirement=gh.ApprovalRequirement.NEVER,
                    concurrency_class=gh.ConcurrencyClass.SERIAL,
                ),
                handler=lambda _args, _context: gh.HandlerResult(
                    status=gh.HandlerResultStatus.UNKNOWN_RESULT,
                    summary="synthetic unknown result",
                    error_code=gh.ErrorCode.GATE_RESULT_UNKNOWN.value,
                ),
            )
        )
        return registry

    first_model = FakeScriptedModel(
        (tool_call("unknown-result-1", "read_file", {"path": "src/app.py"}),)
    )
    first = gh.Harness(
        config_path=fixture.config_path,
        model=first_model,
        tool_factory=unknown_factory,
        clock=gh.SystemClock(),
    ).run(gh.RunRequest(mode=gh.RunMode.START))
    assert first.status == gh.RunStatus.BLOCKED
    assert first.stop_reason == gh.StopReason.UNKNOWN_RESULT

    resume_model = FakeScriptedModel((final_answer("must not be called"),))
    resumed = _run_resume(fixture, first.run_id, resume_model)
    assert resumed.status == gh.RunStatus.BLOCKED
    assert resumed.error_code == gh.ErrorCode.RECOVERY_MANUAL_REVIEW_REQUIRED.value
    assert resume_model.calls == []


def test_smolagents_version_mismatch_fails_before_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(adapter_module, "distribution_version", lambda _name: "1.27.0")
    model = FakeScriptedModel((final_answer("unused"),))
    with pytest.raises(gh.HarnessError) as mismatch:
        _run_start(fixture, model)

    assert mismatch.value.code == gh.ErrorCode.SMOLAGENTS_VERSION_MISMATCH
    assert model.calls == []
    assert not fixture.database_path.exists()


def test_resume_without_persisted_baseline_blocks_old_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    config = gh.load_config(fixture.config_path)
    clock = MutableClock()
    sandbox = gh.Sandbox.from_resolved_config(config, clock=clock)
    baseline = sandbox.capture_baseline()
    all_migrations = storage_module.MIGRATIONS
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 6)
    monkeypatch.setattr(storage_module, "MIGRATIONS", all_migrations[:6])
    storage = gh.SQLiteStorage(fixture.database_path, fixture.workspace, clock)
    storage.initialize()
    run_id = f"run_{'a' * 32}"
    with storage.transaction(actor="legacy") as unit:
        lease = unit.state.create_run(
            gh.Run(
                run_id=run_id,
                trace_id="b" * 32,
                task_id=config.config.task.id,
                task_kind=config.config.task.kind,
                task_description=config.config.task.description,
                config_fingerprint=config.fingerprint,
                workspace=baseline.identity,
                budget=config.config.budget.as_run_budget(),
                created_at=clock.now(),
                updated_at=clock.now(),
            ),
            owner_id="legacy",
        )
        unit.state.transition_run(
            run_id,
            expected_status=gh.RunStatus.NEW,
            expected_revision=unit.state.get_run_revision(run_id),
            target=gh.RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
        unit.state.release_lease(lease)
    monkeypatch.setattr(storage_module, "SCHEMA_VERSION", 8)
    monkeypatch.setattr(storage_module, "MIGRATIONS", all_migrations)
    storage.initialize()

    model = FakeScriptedModel((final_answer("must not be called"),))
    result = gh.Harness(
        config_path=fixture.config_path,
        model=model,
        clock=clock,
    ).run(gh.RunRequest(mode=gh.RunMode.RESUME, run_id=run_id))

    assert result.status == gh.RunStatus.BLOCKED
    assert result.error_code == gh.ErrorCode.HARNESS_WORKSPACE_BASELINE_MISSING.value
    assert model.calls == []


def test_candidate_answer_audit_failure_never_enters_verification(
    tmp_path: Path,
) -> None:
    class OnceAuditFailure:
        def __init__(self) -> None:
            self.armed = False
            self.failed = False
            self.audit_calls = 0

        def arm(self) -> None:
            self.armed = True

        def trigger(self, point: str) -> None:
            if point == "audit:before-insert" and self.armed:
                self.audit_calls += 1
                if self.audit_calls == 3 and not self.failed:
                    self.failed = True
                    raise RuntimeError("synthetic audit failure")

        def record(self, _point: str) -> int:
            return 0

    fixture = _fixture(tmp_path)
    injector = OnceAuditFailure()
    model = FakeScriptedModel(
        (final_answer("candidate"),),
        before_generate=injector.arm,
    )
    result = gh.Harness(
        config_path=fixture.config_path,
        model=model,
        clock=gh.SystemClock(),
        fault_injector=injector,
    ).run(gh.RunRequest(mode=gh.RunMode.START))

    assert result.status == gh.RunStatus.FAILED
    assert result.error_code == gh.ErrorCode.AUDIT_WRITE_FAILED.value
    assert result.candidate_final_answer is None
    assert result.completion is None
