"""Program-owned completion tests for WP-08 and WP-09."""

from __future__ import annotations

import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import guarded_harness as gh
from guarded_harness.completion import build_test_report_argv

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
FINGERPRINT = f"sha256:{'a' * 64}"


class Clock:
    """Deterministic advancing UTC clock."""

    def __init__(self, start: datetime = NOW) -> None:
        self.current = start

    def now(self) -> datetime:
        value = self.current
        self.current += timedelta(microseconds=1)
        return value


class Ids:
    """Small deterministic ID source for public typed identifiers."""

    def __init__(self, start: int = 1) -> None:
        self.value = start

    def _take(self) -> str:
        result = f"{self.value:032x}"
        self.value += 1
        return result

    def new_trace_id(self) -> str:
        return self._take()

    def new_run_id(self) -> str:
        return f"run_{self._take()}"

    def new_step_id(self) -> str:
        return f"step_{self._take()}"

    def new_evidence_id(self) -> str:
        return f"evidence_{self._take()}"

    def new_approval_id(self) -> str:
        return f"approval_{self._take()}"

    def new_checkpoint_id(self) -> str:
        return f"checkpoint_{self._take()}"

    def new_recovery_id(self) -> str:
        return f"recovery_{self._take()}"


@dataclass
class CompletionFixture:
    storage: gh.SQLiteStorage
    sandbox: gh.Sandbox
    gate: gh.Gate
    checker: gh.CompletionChecker
    registry: gh.ToolRegistry
    policy: gh.PolicyEngine
    config: gh.ResolvedHarnessConfig
    run_id: str
    lease_token: str
    startup: gh.WorkspaceBaseline
    workspace: Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _resolved(
    tmp_path: Path,
    workspace: Path,
    *,
    kind: gh.TaskKind,
    allowed_tools: tuple[str, ...],
    test_command: tuple[str, ...] = ("python", "-m", "pytest", "-q"),
    protect_other_git_changes: bool = True,
    max_wall_time_seconds: int = 300,
) -> gh.ResolvedHarnessConfig:
    state_dir = tmp_path / "state"
    if kind == gh.TaskKind.CODE_UNDERSTANDING:
        acceptance = gh.CodeUnderstandingAcceptance(
            strategy="code_understanding",
            deliverables=("artifacts/report.md",),
        )
        paths = gh.PathPolicyConfig(
            deny_write=("src/**", "tests/**"),
            allow_write=("artifacts/**",),
        )
        task_id = "understand_app"
    else:
        acceptance = gh.TestRepairAcceptance(
            strategy="test_repair",
            test_command=test_command,
            report_format="pytest_junit_xml",
        )
        paths = gh.PathPolicyConfig(
            deny_write=("tests/**",),
            allow_write=("src/**", "tests/generated/**"),
        )
        task_id = "repair_app"
    config = gh.HarnessConfig(
        version=1,
        state_dir=str(state_dir),
        workspace=gh.WorkspaceConfig(
            root=str(workspace),
            require_clean_workspace=True,
            protect_other_git_changes=protect_other_git_changes,
        ),
        budget=gh.BudgetConfig(
            max_steps=20,
            max_tool_calls=80,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=max_wall_time_seconds,
        ),
        task=gh.TaskConfig(
            id=task_id,
            kind=kind,
            description="Completion fixture",
            allowed_tools=allowed_tools,
            paths=paths,
            acceptance=acceptance,
        ),
    )
    return gh.ResolvedHarnessConfig(
        config=config,
        workspace_root=str(workspace),
        state_dir=str(state_dir),
        deliverables=("artifacts/report.md",) if kind == gh.TaskKind.CODE_UNDERSTANDING else (),
        fingerprint=FINGERPRINT,
    )


def _fixture(
    tmp_path: Path,
    *,
    kind: gh.TaskKind,
    candidate_final_answer: str | None = None,
    test_source: str | None = None,
    test_command: tuple[str, ...] = ("python", "-m", "pytest", "-q"),
    protect_other_git_changes: bool = True,
    max_wall_time_seconds: int = 300,
    fault_injector: object | None = None,
) -> CompletionFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "src" / "app.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_app.py").write_text(
        test_source or "from src.app import value\n\ndef test_value():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Harness Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")

    allowed_tools = (
        ("read_file",) if kind == gh.TaskKind.CODE_UNDERSTANDING else ("read_file", "run_test")
    )
    config = _resolved(
        tmp_path,
        workspace,
        kind=kind,
        allowed_tools=allowed_tools,
        test_command=test_command,
        protect_other_git_changes=protect_other_git_changes,
        max_wall_time_seconds=max_wall_time_seconds,
    )
    clock = Clock()
    ids = Ids()
    storage = gh.SQLiteStorage(
        tmp_path / "state" / "harness.sqlite3",
        workspace,
        clock,
        fault_injector=fault_injector,
    )
    storage.initialize()
    sandbox = gh.Sandbox.from_resolved_config(config, clock=clock)
    startup = sandbox.capture_baseline()
    registry = gh.ToolRegistry()
    if kind == gh.TaskKind.CODE_UNDERSTANDING:
        gh.register_readonly_tools(registry, sandbox)
    else:
        gh.register_readonly_tools(registry, sandbox)
    policy = gh.PolicyEngine(
        allowed_tool_names=registry.names(),
        role_permissions={
            "developer": {
                gh.ToolPermission.READ,
                gh.ToolPermission.WRITE,
                gh.ToolPermission.EXECUTE,
            }
        },
    )
    gate = gh.Gate(
        storage=storage,
        registry=registry,
        policy=policy,
        sandbox=sandbox,
        clock=clock,
        id_factory=ids,
    )
    checker = gh.CompletionChecker(
        storage,
        config,
        gate,
        sandbox,
        clock=clock,
        id_factory=ids,
    )
    run_id = ids.new_run_id()
    run = gh.Run(
        run_id=run_id,
        trace_id=ids.new_trace_id(),
        task_id=config.config.task.id,
        task_kind=kind,
        task_description=config.config.task.description,
        config_fingerprint=FINGERPRINT,
        workspace=startup.identity,
        budget=gh.RunBudget(
            max_steps=20,
            max_tool_calls=80,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=max_wall_time_seconds,
        ),
        candidate_final_answer=candidate_final_answer,
        created_at=clock.now(),
        updated_at=clock.now(),
    )
    with storage.transaction(actor="agent") as unit:
        lease = unit.state.create_run(run, owner_id="agent")
        step = gh.Step(
            step_id=ids.new_step_id(),
            run_id=run_id,
            ordinal=0,
            summary="agent work",
            started_at=clock.now(),
        )
        unit.state.create_step(step, expected_run_revision=0, lease_token=lease.token)
        unit.state.transition_step(
            step.step_id,
            expected_status=gh.StepStatus.PLANNED,
            expected_revision=0,
            target=gh.StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        unit.state.transition_run(
            run_id,
            expected_status=gh.RunStatus.NEW,
            expected_revision=2,
            target=gh.RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
    return CompletionFixture(
        storage=storage,
        sandbox=sandbox,
        gate=gate,
        checker=checker,
        registry=registry,
        policy=policy,
        config=config,
        run_id=run_id,
        lease_token=lease.token,
        startup=startup,
        workspace=workspace,
    )


def _read_source(fixture: CompletionFixture) -> None:
    _read_source_window(fixture, "call-read", offset=1, limit=100)


def _read_source_window(
    fixture: CompletionFixture,
    call_id: str,
    *,
    offset: int,
    limit: int,
) -> None:
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(fixture.run_id)
    step = snapshot.steps[0]
    result = fixture.gate.invoke(
        gh.ToolCallRequest(
            tool_call_id=call_id,
            tool_name="read_file",
            args={"path": "src/app.py", "offset": offset, "limit": limit},
            context=gh.ToolExecutionContext(
                run_id=fixture.run_id,
                step_id=step.step_id,
                task_id=fixture.config.config.task.id,
                task_kind=fixture.config.config.task.kind,
                mode=gh.ExecutionMode.EXECUTE,
                actor="agent",
                roles=("developer",),
            ),
            lease_token=fixture.lease_token,
        )
    )
    assert result.status == gh.ObservationStatus.ALLOWED


def _invalidate_read_evidence(fixture: CompletionFixture) -> None:
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(fixture.run_id)
        read = next(
            evidence for evidence in snapshot.evidence if evidence.kind == gh.EvidenceKind.READ
        )
        unit.state.invalidate_evidence(
            fixture.run_id,
            read.evidence_id,
            reason_code=gh.ErrorCode.COMPLETION_EVIDENCE_STALE,
            lease_token=fixture.lease_token,
        )


def _item_map(check: gh.CompletionCheck) -> dict[str, gh.CompletionItem]:
    return {item.item_id: item for item in check.items}


def _write_deliverable(fixture: CompletionFixture, body: str) -> None:
    _write_deliverable_bytes(fixture, body.encode("utf-8"))


def _write_deliverable_bytes(fixture: CompletionFixture, body: bytes) -> None:
    fixture.sandbox.write_file_atomic(
        "artifacts/report.md",
        body,
        mode=gh.SandboxWriteMode.CREATE,
        create_parents=True,
    )


def _transition_verifying(fixture: CompletionFixture) -> None:
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )


def _verify(fixture: CompletionFixture) -> gh.CompletionCheck:
    _transition_verifying(fixture)
    return fixture.checker.evaluate_code_understanding(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )


def _transition_test_verifying(fixture: CompletionFixture) -> None:
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )


def _repair_source(fixture: CompletionFixture) -> None:
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )


def test_parse_junit_report_preserves_nodes_and_rejects_unsafe_xml() -> None:
    report = b"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="2" failures="1" errors="0" skipped="0">
  <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="tests.test_app" file="tests/test_app.py" name="test_ok"/>
    <testcase classname="tests.test_app" file="tests/test_app.py" name="test_bad">
      <failure type="AssertionError">assert 1 == 2</failure>
    </testcase>
  </testsuite>
</testsuites>
"""
    parsed = gh.parse_junit_report(report)
    assert parsed.total_count == 2
    assert parsed.failed_count == 1
    assert {node.status for node in parsed.nodes} == {
        gh.TestNodeStatus.PASSED,
        gh.TestNodeStatus.FAILED,
    }
    with pytest.raises(gh.TestReportError) as unsafe:
        gh.parse_junit_report(b'<!DOCTYPE x [<!ENTITY y "z">]><testsuites/>')
    assert unsafe.value.code == gh.ErrorCode.COMPLETION_TEST_REPORT_UNSAFE_XML


def test_parse_node_junit_report_normalizes_workspace_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    test_file = workspace / "tests" / "session.test.ts"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("// test\n", encoding="utf-8")
    report = f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testcase name="expires now" classname="test" file="{test_file}"/>
  <testcase name="rejects bad state" classname="test" file="{test_file}">
    <failure type="testCodeFailure">expected true</failure>
  </testcase>
</testsuites>
""".encode()

    parsed = gh.parse_junit_report(
        report,
        report_format="node_test_junit_xml",
        workspace_root=workspace,
    )

    assert parsed.total_count == 2
    assert parsed.passed_count == 1
    assert parsed.failed_count == 1
    assert {node.file for node in parsed.nodes} == {"tests/session.test.ts"}
    assert all(
        node.node_key
        == gh.canonical_node_key(
            file=node.file,
            classname="test",
            name=node.label.removeprefix("test::"),
            report_format="node_test_junit_xml",
        )
        for node in parsed.nodes
    )

    report_path = tmp_path / "report.xml"
    argv = build_test_report_argv(
        ("node", "--import", "tsx", "--test", "tests/**/*.test.ts"),
        report_path,
        "node_test_junit_xml",
    )
    test_index = argv.index("--test")
    assert argv[test_index + 1 : test_index + 3] == (
        "--test-reporter=junit",
        f"--test-reporter-destination={report_path}",
    )
    assert gh.canonical_node_key(
        file="tests/session.test.ts",
        classname="test",
        name="expires now",
        report_format="node_test_junit_xml",
    ) != gh.canonical_node_key(
        file="tests/session.test.ts",
        classname="test",
        name="expires now",
    )

    outside = tmp_path / "outside.test.ts"
    outside.write_text("// outside\n", encoding="utf-8")
    outside_report = f"""<testsuites>
  <testcase name="outside" classname="test" file="{outside}"/>
</testsuites>""".encode()
    with pytest.raises(gh.TestReportError) as escaped:
        gh.parse_junit_report(
            outside_report,
            report_format="node_test_junit_xml",
            workspace_root=workspace,
        )
    assert escaped.value.code == gh.ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID


def test_report_rejects_duplicate_nodes_and_inconsistent_counts() -> None:
    duplicate = b"""<testsuite tests="2" failures="0" errors="0" skipped="0">
<testcase classname="tests.test_app" name="test_one"/>
<testcase classname="tests.test_app" name="test_one"/>
</testsuite>"""
    with pytest.raises(gh.TestReportError) as duplicated:
        gh.parse_junit_report(duplicate)
    assert duplicated.value.code == gh.ErrorCode.COMPLETION_TEST_NODE_DUPLICATE

    inconsistent = b"""<testsuite tests="2" failures="0" errors="0" skipped="0">
<testcase classname="tests.test_app" name="test_one"/>
</testsuite>"""
    with pytest.raises(gh.TestReportError) as counts:
        gh.parse_junit_report(inconsistent)
    assert counts.value.code == gh.ErrorCode.COMPLETION_TEST_REPORT_MALFORMED

    with pytest.raises(gh.TestReportError) as empty:
        gh.parse_junit_report(b"<testsuites/>")
    assert empty.value.code == gh.ErrorCode.COMPLETION_TEST_REPORT_EMPTY


def test_valid_code_understanding_is_accepted(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    result = _verify(fixture)
    failures = [item for item in result.items if not item.passed]
    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED, failures
    assert all(item.passed for item in result.items)
    with fixture.storage.transaction(actor="auditor") as unit:
        stored = unit.state.get_run_snapshot(fixture.run_id)
    assert stored.completion == result


def test_missing_deliverable_and_unsupported_claim_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.CODE_UNDERSTANDING,
        candidate_final_answer="任务已完成，代码已经解释清楚。",
    )
    _read_source(fixture)
    result = _verify(fixture)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    items = {item.item_id: item for item in result.items}
    assert not items["deliverable_present"].passed
    assert not items["claim_without_evidence"].passed


def test_stale_source_evidence_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 3\n",
        encoding="utf-8",
    )
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    result = _verify(fixture)
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status != gh.AcceptanceStatus.ACCEPTED
    assert not items["evidence_current"].passed


@pytest.mark.parametrize(
    ("payload", "failed_item"),
    (
        pytest.param(b"", "deliverable_nonempty", id="empty"),
        pytest.param(b"\x00binary", "document_structure", id="nul"),
        pytest.param(b"\xff\xfe", "document_structure", id="invalid-utf8"),
        pytest.param(
            b"x" * (1024 * 1024 + 1),
            "deliverable_nonempty",
            id="oversized",
        ),
    ),
)
def test_code_understanding_deliverable_content_rejections(
    tmp_path: Path,
    payload: bytes,
    failed_item: str,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _write_deliverable_bytes(fixture, payload)

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items[failed_item].passed


@pytest.mark.parametrize(
    "body",
    (
        """## 事实

- `value()` 返回 1。[E1]

## 代码依据

- E1: `src/app.py:1-2`
""",
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 事实

- 重复事实区 [E1]

## 代码依据

- E1: `src/app.py:1-2`
""",
        """## 事实

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
        """## 事实

`value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    ),
)
def test_code_understanding_markdown_structure_rejections(
    tmp_path: Path,
    body: str,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(fixture, body)

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["document_structure"].passed


@pytest.mark.parametrize(
    ("body", "failed_item"),
    (
        (
            """## 事实

- `value()` 返回 1。[E2]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
            "facts_cited",
        ),
        (
            """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-1`
- E1: `src/app.py:2-2`
""",
            "document_structure",
        ),
        (
            """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-99`
""",
            "evidence_current",
        ),
        (
            """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:2-1`
""",
            "document_structure",
        ),
    ),
)
def test_code_understanding_citation_graph_rejections(
    tmp_path: Path,
    body: str,
    failed_item: str,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(fixture, body)

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items[failed_item].passed


def test_code_understanding_partial_evidence_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source_window(fixture, "partial-read", offset=1, limit=1)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["evidence_current"].passed
    assert "partially" in items["evidence_current"].message or "not fully" in (
        items["evidence_current"].message
    )


def test_completion_self_read_cannot_prove_agent_read(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert not items["evidence_read"].passed


def test_non_read_evidence_cannot_prove_agent_read(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.add_evidence(
            gh.Evidence(
                evidence_id=f"evidence_{'d' * 32}",
                run_id=fixture.run_id,
                kind=gh.EvidenceKind.DOCUMENT,
                path="src/app.py",
                content_digest=f"sha256:{'d' * 64}",
                created_at=fixture.storage.clock.now(),
            ),
            lease_token=fixture.lease_token,
        )
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["evidence_read"].passed


def test_evidence_from_another_run_cannot_prove_agent_read(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    other_run_id = f"run_{'c' * 32}"
    other_step_id = f"step_{'c' * 32}"
    clock = fixture.storage.clock
    with fixture.storage.transaction(actor="other-agent") as unit:
        lease = unit.state.create_run(
            gh.Run(
                run_id=other_run_id,
                trace_id="e" * 32,
                task_id=fixture.config.config.task.id,
                task_kind=gh.TaskKind.CODE_UNDERSTANDING,
                task_description=fixture.config.config.task.description,
                config_fingerprint=fixture.config.fingerprint,
                workspace=fixture.startup.identity,
                budget=fixture.config.config.budget.as_run_budget(),
                created_at=clock.now(),
                updated_at=clock.now(),
            ),
            owner_id="other-agent",
        )
        unit.state.create_step(
            gh.Step(
                step_id=other_step_id,
                run_id=other_run_id,
                ordinal=0,
                summary="other run read",
                started_at=clock.now(),
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        unit.state.transition_step(
            other_step_id,
            expected_status=gh.StepStatus.PLANNED,
            expected_revision=0,
            target=gh.StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        unit.state.transition_run(
            other_run_id,
            expected_status=gh.RunStatus.NEW,
            expected_revision=2,
            target=gh.RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
    other_read = fixture.gate.invoke(
        gh.ToolCallRequest(
            tool_call_id="other-run-read",
            tool_name="read_file",
            args={"path": "src/app.py", "offset": 1, "limit": 100},
            context=gh.ToolExecutionContext(
                run_id=other_run_id,
                step_id=other_step_id,
                task_id=fixture.config.config.task.id,
                task_kind=gh.TaskKind.CODE_UNDERSTANDING,
                mode=gh.ExecutionMode.EXECUTE,
                actor="agent",
                roles=("developer",),
            ),
            lease_token=lease.token,
        )
    )
    assert other_read.status == gh.ObservationStatus.ALLOWED
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["evidence_read"].passed


def test_invalidated_read_evidence_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _invalidate_read_evidence(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["evidence_read"].passed


def test_adjacent_paginated_reads_form_complete_evidence(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source_window(fixture, "page-1", offset=1, limit=1)
    _read_source_window(fixture, "page-2", offset=2, limit=1)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED, result.failure_summary


def test_code_understanding_undeclared_file_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    (fixture.workspace / "artifacts" / "unexpected.txt").write_text(
        "unexpected\n",
        encoding="utf-8",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["workspace_scope"].passed


def test_code_understanding_source_change_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 7\n",
        encoding="utf-8",
    )
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    result = _verify(fixture)

    items = _item_map(result)
    assert not items["workspace_scope"].passed
    assert not items["evidence_current"].passed


def test_code_understanding_index_change_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    _git(fixture.workspace.parent, "add", "task/artifacts/report.md")

    result = _verify(fixture)

    items = _item_map(result)
    assert not items["workspace_scope"].passed
    assert items["workspace_scope"].code == gh.ErrorCode.COMPLETION_IDENTITY_CHANGED.value


def test_code_understanding_external_change_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    (fixture.workspace.parent / "external.txt").write_text("changed\n", encoding="utf-8")

    result = _verify(fixture)

    items = _item_map(result)
    assert not items["workspace_scope"].passed
    assert items["workspace_scope"].code == (
        gh.ErrorCode.COMPLETION_EXTERNAL_WORKSPACE_CHANGED.value
    )


def test_code_understanding_disabled_external_protection_is_explicit(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.CODE_UNDERSTANDING,
        protect_other_git_changes=False,
    )
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    (fixture.workspace.parent / "external.txt").write_text("changed\n", encoding="utf-8")

    result = _verify(fixture)

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED, result.failure_summary
    assert "not monitored" in items["workspace_scope"].message


def test_code_understanding_requires_verifying_state(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )

    with pytest.raises(gh.CompletionCheckerError) as invalid_state:
        fixture.checker.evaluate_code_understanding(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    assert invalid_state.value.code == gh.ErrorCode.COMPLETION_RUN_STATE_INVALID


def test_code_understanding_completion_audit_failure_rolls_back(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.CODE_UNDERSTANDING,
        fault_injector=fault_injector,
    )
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    _transition_verifying(fixture)
    fault_injector.arm("audit:before-insert")

    with pytest.raises(gh.CompletionCheckerError) as failed:
        fixture.checker.evaluate_code_understanding(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    assert failed.value.code in {
        gh.ErrorCode.COMPLETION_INTERNAL_ACCESS_DENIED,
        gh.ErrorCode.COMPLETION_RESULT_WRITE_FAILED,
    }
    with fixture.storage.transaction(actor="auditor") as unit:
        assert unit.state.get_run_snapshot(fixture.run_id).completion is None


def test_code_understanding_completion_is_idempotent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    _transition_verifying(fixture)
    first = fixture.checker.evaluate_code_understanding(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    second = fixture.checker.evaluate_code_understanding(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    assert first == second


def test_code_understanding_concurrent_completion_is_stable(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.CODE_UNDERSTANDING)
    _read_source(fixture)
    _write_deliverable(
        fixture,
        """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
""",
    )
    _transition_verifying(fixture)

    def evaluate():
        return fixture.checker.evaluate_code_understanding(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(evaluate) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except gh.CompletionCheckerError as exc:
                outcomes.append(exc)

    successes = [item for item in outcomes if isinstance(item, gh.CompletionCheck)]
    errors = [item for item in outcomes if isinstance(item, gh.CompletionCheckerError)]
    assert successes
    assert all(item == successes[0] for item in successes)
    assert all(
        item.code
        in {
            gh.ErrorCode.COMPLETION_RESULT_WRITE_FAILED,
            gh.ErrorCode.STATE_CONFLICT,
        }
        for item in errors
    )
    with fixture.storage.transaction(actor="auditor") as unit:
        stored = unit.state.get_run_snapshot(fixture.run_id).completion
    assert stored == successes[0]


def test_test_repair_baseline_and_final_report_are_bound(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    baseline = fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    assert baseline.report_evidence_id.startswith("evidence_")
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED, result.failure_summary
    assert all(item.passed for item in result.items)
    with fixture.storage.transaction(actor="auditor") as unit:
        execution = unit.state.get_test_execution(
            fixture.run_id,
            gh.TestExecutionPhase.FINAL,
        )
        nodes = unit.state.get_test_nodes(execution.test_execution_id)
    assert execution is not None
    assert nodes and all(node.status == gh.TestNodeStatus.PASSED for node in nodes)


def test_all_green_baseline_is_not_a_test_repair(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    _git(fixture.workspace.parent, "add", ".")
    _git(fixture.workspace.parent, "commit", "-q", "-m", "already green")
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["test_baseline_has_failure"].passed


def test_missing_baseline_never_reaches_final_test(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.UNVERIFIABLE
    assert not items["test_baseline_present"].passed
    with fixture.storage.transaction(actor="auditor") as unit:
        assert unit.state.get_test_execution(fixture.run_id, gh.TestExecutionPhase.FINAL) is None


def test_test_baseline_failure_modes_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)
    with sqlite3.connect(fixture.storage.database_path) as connection:
        connection.execute("DROP TRIGGER test_repair_baselines_no_update")
        connection.execute("UPDATE test_repair_baselines SET workspace_baseline_json = '{}'")
        connection.commit()

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.UNVERIFIABLE
    assert not items["test_baseline_present"].passed


def test_test_baseline_command_binding_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)
    with sqlite3.connect(fixture.storage.database_path) as connection:
        connection.execute("DROP TRIGGER test_repair_baselines_no_update")
        connection.execute(
            "UPDATE test_repair_baselines SET config_fingerprint = ?",
            (f"sha256:{'b' * 64}",),
        )
        connection.commit()

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["test_command_consistent"].passed


def test_test_command_start_failure_prevents_agent_and_completion(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_command=("python", "-m", "wp09_missing_test_module"),
    )

    with pytest.raises(gh.CompletionCheckerError) as failed:
        fixture.checker.capture_test_baseline(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    assert failed.value.code in {
        gh.ErrorCode.COMPLETION_TEST_REPORT_MISSING,
        gh.ErrorCode.COMPLETION_TEST_REPORT_EMPTY,
        gh.ErrorCode.GATE_RESULT_UNKNOWN,
    }
    with fixture.storage.transaction(actor="auditor") as unit:
        assert unit.state.get_test_baseline(fixture.run_id) is None
        assert (
            unit.state.get_test_execution(
                fixture.run_id,
                gh.TestExecutionPhase.FINAL,
            )
            is None
        )


def test_test_command_timeout_is_unknown_and_not_retried(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_command=(
            "python",
            "-c",
            "import time; time.sleep(5)",
        ),
        max_wall_time_seconds=1,
    )

    with pytest.raises(gh.CompletionCheckerError):
        fixture.checker.capture_test_baseline(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    with fixture.storage.transaction(actor="auditor") as unit:
        assert unit.state.get_test_baseline(fixture.run_id) is None
        snapshot = unit.state.get_run_snapshot(fixture.run_id)
    assert (
        len(
            [
                invocation
                for invocation in snapshot.invocations
                if invocation.tool_name == "test_verification"
            ]
        )
        == 1
    )


def test_junit_report_size_and_node_limits_fail_closed() -> None:
    oversized = b"x" * (gh.MAX_REPORT_BYTES + 1)
    with pytest.raises(gh.TestReportError) as size_error:
        gh.parse_junit_report(oversized)
    assert size_error.value.code == gh.ErrorCode.COMPLETION_TEST_REPORT_TOO_LARGE

    report = b"""<testsuite tests="3" failures="0" errors="0" skipped="0">
<testcase classname="tests.test_app" name="test_one"/>
<testcase classname="tests.test_app" name="test_two"/>
<testcase classname="tests.test_app" name="test_three"/>
</testsuite>"""
    with pytest.raises(gh.TestReportError) as nodes_error:
        gh.parse_junit_report(report, max_nodes=2)
    assert nodes_error.value.code == gh.ErrorCode.COMPLETION_TEST_REPORT_TOO_LARGE


def test_new_failing_generated_test_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    generated = fixture.workspace / "tests" / "generated"
    generated.mkdir()
    (generated / "test_extra.py").write_text(
        "def test_new_failure():\n    assert False\n",
        encoding="utf-8",
    )
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["final_nodes_passed"].passed


def test_existing_skip_may_remain_while_failure_is_repaired(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_source=(
            "import pytest\n"
            "from src.app import value\n\n"
            "@pytest.mark.skip(reason='legacy debt')\n"
            "def test_legacy_skip():\n"
            "    assert False\n\n"
            "def test_value():\n"
            "    assert value() == 2\n"
        ),
    )
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED, result.failure_summary


def test_parameterized_node_reduction_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_source=(
            "import pytest\n"
            "from src.app import value\n\n"
            "@pytest.mark.parametrize('expected', [1, 2])\n"
            "def test_value(expected):\n"
            "    assert value() == expected\n"
        ),
    )
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    (fixture.workspace / "tests" / "test_app.py").write_text(
        "import pytest\n"
        "from src.app import value\n\n"
        "@pytest.mark.parametrize('expected', [2])\n"
        "def test_value(expected):\n"
        "    assert value() == expected\n",
        encoding="utf-8",
    )
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["baseline_nodes_preserved"].passed


def test_allowed_generated_test_is_part_of_superset(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    generated = fixture.workspace / "tests" / "generated"
    generated.mkdir()
    (generated / "test_extra.py").write_text(
        "from src.app import value\n\ndef test_extra():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED
    with fixture.storage.transaction(actor="auditor") as unit:
        execution = unit.state.get_test_execution(
            fixture.run_id,
            gh.TestExecutionPhase.FINAL,
        )
        nodes = unit.state.get_test_nodes(execution.test_execution_id)
    assert len(nodes) >= 2


def test_external_change_before_test_repair_completion_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    (fixture.workspace.parent / "external.txt").write_text("changed\n", encoding="utf-8")
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["workspace_scope"].passed


def test_model_claims_do_not_override_program_test_verdict(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        candidate_final_answer="测试全部通过，任务已完成。",
    )
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["baseline_failures_repaired"].passed


def test_concurrent_test_repair_completion_executes_final_test_once(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)

    def evaluate():
        return fixture.checker.evaluate_test_repair(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(evaluate) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except gh.CompletionCheckerError as exc:
                outcomes.append(exc)

    successes = [item for item in outcomes if isinstance(item, gh.CompletionCheck)]
    assert successes
    assert all(item == successes[0] for item in successes)
    with fixture.storage.transaction(actor="auditor") as unit:
        snapshot = unit.state.get_run_snapshot(fixture.run_id)
    final_verifications = [
        invocation
        for invocation in snapshot.invocations
        if invocation.tool_name == "test_verification"
    ]
    assert len(final_verifications) == 2
    assert snapshot.completion == successes[0]


def test_existing_test_file_rename_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    original = fixture.workspace / "tests" / "test_app.py"
    original.rename(fixture.workspace / "tests" / "test_moved.py")
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["test_files_scope"].passed


def test_existing_test_file_symlink_replacement_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    test_file = fixture.workspace / "tests" / "test_app.py"
    replacement = fixture.workspace / "tests" / "replacement.py"
    replacement.write_text(test_file.read_text(encoding="utf-8"), encoding="utf-8")
    test_file.unlink()
    test_file.symlink_to(replacement.name)
    _transition_test_verifying(fixture)

    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["test_files_scope"].passed


def test_final_exit_zero_with_empty_report_is_unverifiable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)
    executor = fixture.checker._test_executor
    original_take = executor.take

    def take_empty_final(run_id, phase):
        captured = original_take(run_id, phase)
        if phase == gh.TestExecutionPhase.FINAL:
            return replace(captured, report=b"")
        return captured

    monkeypatch.setattr(executor, "take", take_empty_final)
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    items = _item_map(result)
    assert result.acceptance_status == gh.AcceptanceStatus.UNVERIFIABLE
    assert not items["final_report_valid"].passed


def test_truncated_command_output_does_not_invalidate_complete_junit_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)
    executor = fixture.checker._test_executor
    original_take = executor.take

    def take_truncated_final(run_id, phase):
        captured = original_take(run_id, phase)
        if phase == gh.TestExecutionPhase.FINAL:
            process = replace(
                captured.process,
                stdout="head...tail",
                stderr="",
                stdout_truncated=True,
                stderr_truncated=False,
            )
            return replace(captured, process=process)
        return captured

    monkeypatch.setattr(executor, "take", take_truncated_final)
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )

    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED, result.failure_summary


def test_test_repair_completion_transaction_failure_rolls_back(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        fault_injector=fault_injector,
    )
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    _repair_source(fixture)
    _transition_test_verifying(fixture)
    fault_injector.arm("audit:before-insert")

    with pytest.raises(gh.CompletionCheckerError) as failed:
        fixture.checker.evaluate_test_repair(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )

    assert failed.value.code in {
        gh.ErrorCode.COMPLETION_INTERNAL_ACCESS_DENIED,
        gh.ErrorCode.COMPLETION_TEST_RESULT_WRITE_FAILED,
    }
    with fixture.storage.transaction(actor="auditor") as unit:
        assert unit.state.get_run_snapshot(fixture.run_id).completion is None


def test_weakening_existing_test_into_skip_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    (fixture.workspace / "tests" / "test_app.py").write_text(
        "import pytest\n\n@pytest.mark.skip(reason='weakened')\ndef test_value():\n    pass\n",
        encoding="utf-8",
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["no_new_skips"].passed
    assert not items["test_files_scope"].passed


def test_internal_verifier_tools_are_not_model_visible(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(fixture.run_id)
    context = gh.ToolExecutionContext(
        run_id=fixture.run_id,
        step_id=snapshot.steps[0].step_id,
        task_id=fixture.config.config.task.id,
        task_kind=fixture.config.config.task.kind,
        mode=gh.ExecutionMode.EXECUTE,
        actor="agent",
        roles=("developer",),
    )
    visible = {spec.name for spec in fixture.registry.snapshot(context, fixture.policy)}
    assert "test_verification" not in visible
    assert "completion_inspect_file" not in visible
    denied = fixture.gate.invoke(
        gh.ToolCallRequest(
            tool_call_id="model-internal-probe",
            tool_name="test_verification",
            args={"phase": "BASELINE"},
            context=context,
            lease_token=fixture.lease_token,
        )
    )
    assert denied.status == gh.ObservationStatus.DENIED


def test_test_execution_rows_are_append_only(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    connection = sqlite3.connect(fixture.storage.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE test_nodes SET status = 'PASSED'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM test_executions")
    finally:
        connection.close()


def test_reopened_checker_reuses_persisted_baseline(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )

    reopened_clock = Clock(NOW + timedelta(seconds=1))
    reopened_storage = gh.SQLiteStorage(
        fixture.storage.database_path,
        fixture.workspace,
        reopened_clock,
    )
    reopened_storage.initialize()
    reopened_sandbox = gh.Sandbox.from_resolved_config(
        fixture.config,
        clock=reopened_clock,
    )
    reopened_registry = gh.ToolRegistry()
    gh.register_readonly_tools(reopened_registry, reopened_sandbox)
    reopened_policy = gh.PolicyEngine(
        allowed_tool_names=reopened_registry.names(),
        role_permissions={"developer": {gh.ToolPermission.READ}},
    )
    reopened_gate = gh.Gate(
        storage=reopened_storage,
        registry=reopened_registry,
        policy=reopened_policy,
        sandbox=reopened_sandbox,
        clock=reopened_clock,
        id_factory=Ids(1000),
    )
    reopened_checker = gh.CompletionChecker(
        reopened_storage,
        fixture.config,
        reopened_gate,
        reopened_sandbox,
        clock=reopened_clock,
        id_factory=Ids(2000),
    )
    result = reopened_checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    assert result.acceptance_status == gh.AcceptanceStatus.ACCEPTED


def test_removing_a_baseline_test_node_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_source=(
            "from src.app import value\n\n"
            "def test_one():\n"
            "    assert value() == 2\n\n"
            "def test_two():\n"
            "    assert value() == 2\n"
        ),
    )
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    (fixture.workspace / "tests" / "test_app.py").write_text(
        "from src.app import value\n\ndef test_one():\n    assert value() == 2\n",
        encoding="utf-8",
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["baseline_nodes_preserved"].passed
    assert not items["test_files_scope"].passed


def test_test_command_that_changes_workspace_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_source=(
            "from pathlib import Path\n"
            "from src.app import value\n\n"
            "def test_value():\n"
            "    if value() == 2:\n"
            "        Path('src/app.py').write_text('def value():\\n    return 3\\n')\n"
            "    assert value() == 2\n"
        ),
    )
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["test_workspace_stable"].passed


def test_workspace_change_before_completion_commit_invalidates_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    original = fixture.checker._persist_test_completion

    def persist_after_change(*args, **kwargs):
        (fixture.workspace / "src" / "app.py").write_text(
            "def value():\n    return 4\n",
            encoding="utf-8",
        )
        return original(*args, **kwargs)

    monkeypatch.setattr(fixture.checker, "_persist_test_completion", persist_after_change)
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["evidence_current"].passed


def test_unrepaired_failure_remains_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["baseline_failures_repaired"].passed


def test_unexpected_untracked_file_is_out_of_scope(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, kind=gh.TaskKind.TEST_REPAIR)
    fixture.checker.capture_test_baseline(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 2\n",
        encoding="utf-8",
    )
    (fixture.workspace / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    with fixture.storage.transaction(actor="agent") as unit:
        unit.state.transition_run(
            fixture.run_id,
            expected_status=gh.RunStatus.RUNNING,
            expected_revision=unit.state.get_run_revision(fixture.run_id),
            target=gh.RunStatus.VERIFYING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=fixture.lease_token,
        )
    result = fixture.checker.evaluate_test_repair(
        fixture.run_id,
        fixture.lease_token,
        startup_workspace=fixture.startup,
    )
    items = {item.item_id: item for item in result.items}
    assert result.acceptance_status == gh.AcceptanceStatus.REJECTED
    assert not items["workspace_scope"].passed


def test_baseline_side_effect_stops_before_agent(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        kind=gh.TaskKind.TEST_REPAIR,
        test_source=(
            "from pathlib import Path\n"
            "from src.app import value\n\n"
            "def test_value():\n"
            "    Path('baseline-side-effect.txt').write_text('changed')\n"
            "    assert value() == 2\n"
        ),
    )
    with pytest.raises(gh.CompletionCheckerError) as unstable:
        fixture.checker.capture_test_baseline(
            fixture.run_id,
            fixture.lease_token,
            startup_workspace=fixture.startup,
        )
    assert unstable.value.code == gh.ErrorCode.COMPLETION_TEST_WORKSPACE_UNSTABLE
    with fixture.storage.transaction(actor="auditor") as unit:
        assert unit.state.get_test_baseline(fixture.run_id) is None
