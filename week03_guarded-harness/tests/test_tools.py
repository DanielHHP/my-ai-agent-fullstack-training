"""WP-05 readonly repository tools exercised through the governed Gate."""

from __future__ import annotations

import hashlib
import os
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import guarded_harness as gh

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
TRACE_ID = "00000000000000000000000000000002"
STEP_ID = "step_00000000000000000000000000000003"
DIGEST = f"sha256:{'a' * 64}"


class ConstantClock:
    """Return one stable timestamp for all calls."""

    def now(self) -> datetime:
        return NOW


@dataclass
class ToolFixture:
    storage: gh.SQLiteStorage
    sandbox: gh.Sandbox
    registry: gh.ToolRegistry
    policy: gh.PolicyEngine
    gate: gh.Gate
    lease_token: str
    context: gh.ToolExecutionContext
    root: Path
    workspace: Path
    next_call: int = 0

    def invoke(self, tool: str, arguments: dict) -> gh.ToolObservation:
        self.next_call += 1
        return self.gate.invoke(
            gh.ToolCallRequest(
                tool_call_id=f"call-{self.next_call}",
                tool_name=tool,
                args=arguments,
                context=self.context,
                lease_token=self.lease_token,
            )
        )

    def snapshot(self) -> gh.RunSnapshot:
        with self.storage.transaction(actor="agent") as unit:
            return unit.state.get_run_snapshot(RUN_ID)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _fixture(tmp_path: Path) -> ToolFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src" / "secret").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "node_modules").mkdir()
    (workspace / "dist").mkdir()
    (workspace / "README.md").write_text("# demo\n", encoding="utf-8")
    (workspace / "src" / "login.py").write_text(
        "def login(user):\n    if not user:\n        return False\n    return True\n",
        encoding="utf-8",
    )
    (workspace / "src" / "secret" / "config.py").write_text(
        "SECRET_VALUE = 'do-not-leak'\n",
        encoding="utf-8",
    )
    (workspace / "src" / "profile.py").write_text(
        "def profile():\n    return None\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_login.py").write_text(
        "def test_login():\n    assert True\n",
        encoding="utf-8",
    )
    (workspace / "node_modules" / "dependency.js").write_text(
        "dependency\n",
        encoding="utf-8",
    )
    (workspace / "dist" / "bundle.js").write_text("generated\n", encoding="utf-8")
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Harness Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")

    state_dir = tmp_path / "state"
    config = gh.HarnessConfig(
        version=1,
        state_dir=str(state_dir),
        workspace=gh.WorkspaceConfig(
            root=str(workspace),
            require_clean_workspace=True,
            protect_other_git_changes=True,
        ),
        budget=gh.BudgetConfig(
            max_steps=10,
            max_tool_calls=60,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=3600,
        ),
        task=gh.TaskConfig(
            id="understand_login",
            kind=gh.TaskKind.CODE_UNDERSTANDING,
            description="Explain login",
            allowed_tools=(
                "list_files",
                "search_code",
                "read_file",
                "read_directory",
                "git_status",
                "git_diff",
            ),
            paths=gh.PathPolicyConfig(
                deny_read=("src/secret/**",),
                deny_write=("src/**", "tests/**"),
                allow_write=("artifacts/**",),
            ),
            acceptance=gh.CodeUnderstandingAcceptance(
                strategy="code_understanding",
                deliverables=("artifacts/report.md",),
            ),
        ),
    )
    resolved = gh.ResolvedHarnessConfig(
        config=config,
        workspace_root=str(workspace),
        state_dir=str(state_dir),
        deliverables=("artifacts/report.md",),
        fingerprint=DIGEST,
    )
    clock = ConstantClock()
    storage = gh.SQLiteStorage(state_dir / "harness.sqlite3", workspace, clock)
    storage.initialize()
    sandbox = gh.Sandbox.from_resolved_config(resolved, clock=clock)
    registry = gh.ToolRegistry()
    gh.register_readonly_tools(registry, sandbox)
    policy = gh.PolicyEngine(
        allowed_tool_names=registry.names(),
        role_permissions={"developer": {gh.ToolPermission.READ}},
    )
    run = gh.Run(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        task_id="understand_login",
        task_kind=gh.TaskKind.CODE_UNDERSTANDING,
        task_description="Explain login",
        config_fingerprint=DIGEST,
        workspace=gh.WorkspaceIdentity(
            git_root=str(sandbox.git_root),
            workspace_root=str(sandbox.workspace_root),
            workspace_prefix=sandbox.workspace_prefix,
            baseline_fingerprint=DIGEST,
            external_dirty_baseline_fingerprint=DIGEST,
        ),
        budget=gh.RunBudget(
            max_steps=10,
            max_tool_calls=60,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=3600,
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    with storage.transaction(actor="agent") as unit:
        lease = unit.state.create_run(run, owner_id="agent", lease_policy=gh.LeasePolicy())
        unit.state.create_step(
            gh.Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                summary="read repository",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        unit.state.transition_step(
            STEP_ID,
            expected_status=gh.StepStatus.PLANNED,
            expected_revision=0,
            target=gh.StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        unit.state.transition_run(
            RUN_ID,
            expected_status=gh.RunStatus.NEW,
            expected_revision=2,
            target=gh.RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
    context = gh.ToolExecutionContext(
        run_id=RUN_ID,
        step_id=STEP_ID,
        task_id="understand_login",
        task_kind=gh.TaskKind.CODE_UNDERSTANDING,
        mode=gh.ExecutionMode.EXECUTE,
        actor="agent",
        roles=("developer",),
    )
    return ToolFixture(
        storage=storage,
        sandbox=sandbox,
        registry=registry,
        policy=policy,
        gate=gh.Gate(
            storage=storage,
            registry=registry,
            policy=policy,
            sandbox=sandbox,
            clock=clock,
        ),
        lease_token=lease.token,
        context=context,
        root=root,
        workspace=workspace,
    )


@dataclass
class SideEffectFixture:
    storage: gh.SQLiteStorage
    sandbox: gh.Sandbox
    registry: gh.ToolRegistry
    policy: gh.PolicyEngine
    gate: gh.Gate
    lease_token: str
    context: gh.ToolExecutionContext
    root: Path
    workspace: Path
    config: gh.ResolvedHarnessConfig
    task_kind: gh.TaskKind
    next_call: int = 0

    def request(
        self,
        tool: str,
        arguments: dict,
        *,
        call_id: str | None = None,
    ) -> gh.ToolCallRequest:
        if call_id is None:
            self.next_call += 1
            call_id = f"side-effect-{self.next_call}"
        return gh.ToolCallRequest(
            tool_call_id=call_id,
            tool_name=tool,
            args=arguments,
            context=self.context,
            lease_token=self.lease_token,
        )

    def invoke(self, tool: str, arguments: dict) -> gh.ToolObservation:
        return self.gate.invoke(self.request(tool, arguments))

    def approve(self, approval_id: str) -> None:
        with self.storage.transaction(actor="operator") as unit:
            current = gh.ApprovalStore(unit).load(
                approval_id,
                lease_token=self.lease_token,
            )
            gh.ApprovalStore(unit).approve(
                approval_id,
                expected_revision=current.revision,
                approver=gh.Approver(actor="operator", roles=("operator",)),
                lease_token=self.lease_token,
            )

    def invoke_approved(
        self,
        tool: str,
        arguments: dict,
        *,
        call_id: str | None = None,
    ) -> gh.ToolObservation:
        request = self.request(tool, arguments, call_id=call_id)
        waiting = self.gate.invoke(request)
        assert waiting.status == gh.ObservationStatus.WAITING_APPROVAL
        assert waiting.approval_id is not None
        self.approve(waiting.approval_id)
        return self.gate.invoke(request)

    def snapshot(self) -> gh.RunSnapshot:
        with self.storage.transaction(actor="agent") as unit:
            return unit.state.get_run_snapshot(RUN_ID)


def _side_effect_fixture(
    tmp_path: Path,
    *,
    task_kind: gh.TaskKind = gh.TaskKind.CODE_UNDERSTANDING,
    test_command: tuple[str, ...] = ("python", "-c", "print('ok')"),
    command_timeout: float = 5.0,
    stdout_limit: int = 4096,
    stderr_limit: int = 4096,
    fault_injector: object | None = None,
) -> SideEffectFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "src" / "app.py").write_text(
        "VALUE = 1\nprint(VALUE)\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_app.py").write_text(
        "def test_app():\n    assert True\n",
        encoding="utf-8",
    )
    (workspace / "artifacts" / "report.md").write_text("# report\n", encoding="utf-8")
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Harness Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")

    state_dir = tmp_path / "state"
    if task_kind == gh.TaskKind.CODE_UNDERSTANDING:
        acceptance = gh.CodeUnderstandingAcceptance(
            strategy="code_understanding",
            deliverables=("artifacts/report.md",),
        )
        allowed_tools = ("read_file", "write_file", "apply_patch")
        paths = gh.PathPolicyConfig(
            deny_read=("tests/**",),
            deny_write=("src/**", "tests/**"),
            allow_write=("artifacts/report.md",),
        )
        task_id = "understand_app"
    else:
        acceptance = gh.TestRepairAcceptance(
            strategy="test_repair",
            test_command=test_command,
            baseline_required=True,
        )
        allowed_tools = ("read_file", "write_file", "apply_patch", "run_test")
        paths = gh.PathPolicyConfig(
            deny_write=("tests/**",),
            allow_write=("src/**", "tests/generated/**"),
        )
        task_id = "repair_app"
    required_tools = ["write_file", "apply_patch"]
    if task_kind == gh.TaskKind.TEST_REPAIR:
        required_tools.append("run_test")
    config = gh.HarnessConfig(
        version=1,
        state_dir=str(state_dir),
        workspace=gh.WorkspaceConfig(
            root=str(workspace),
            require_clean_workspace=True,
            protect_other_git_changes=True,
        ),
        budget=gh.BudgetConfig(
            max_steps=10,
            max_tool_calls=60,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=3600,
        ),
        task=gh.TaskConfig(
            id=task_id,
            kind=task_kind,
            description="Exercise WP-06 side effects",
            allowed_tools=allowed_tools,
            paths=paths,
            acceptance=acceptance,
            approval=gh.ApprovalConfig(
                required_tools=tuple(required_tools),
                approver_roles=("operator",),
                ttl_seconds=900,
                channel="local_cli",
            ),
        ),
    )
    resolved = gh.ResolvedHarnessConfig(
        config=config,
        workspace_root=str(workspace),
        state_dir=str(state_dir),
        deliverables=("artifacts/report.md",)
        if task_kind == gh.TaskKind.CODE_UNDERSTANDING
        else (),
        fingerprint=DIGEST,
    )
    resolved = resolved.model_copy(update={"fingerprint": gh.config_fingerprint(resolved)})
    clock = ConstantClock()
    storage = gh.SQLiteStorage(
        state_dir / "harness.sqlite3",
        workspace,
        clock,
        fault_injector=fault_injector,
    )
    storage.initialize()
    command_policy = gh.CommandPolicy(
        allowed_executables=("python", "git"),
        default_timeout_seconds=command_timeout,
        default_kill_grace_seconds=0.2,
        default_stdout_limit_bytes=stdout_limit,
        default_stderr_limit_bytes=stderr_limit,
    )
    sandbox = gh.Sandbox.from_resolved_config(
        resolved,
        command_policy=command_policy,
        clock=clock,
        fault_injector=fault_injector,
    )
    registry = gh.ToolRegistry()
    gh.register_readonly_tools(registry, sandbox)
    gh.register_side_effect_tools(registry, sandbox, resolved)
    policy = gh.PolicyEngine(
        allowed_tool_names=registry.names(),
        role_permissions={
            "developer": {
                gh.ToolPermission.READ,
                gh.ToolPermission.WRITE,
                gh.ToolPermission.EXECUTE,
            }
        },
        task_approval_tools=tuple(required_tools),
        approval_roles=("operator",),
    )
    run = gh.Run(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        task_id=task_id,
        task_kind=task_kind,
        task_description="Exercise WP-06 side effects",
        config_fingerprint=resolved.fingerprint,
        workspace=gh.WorkspaceIdentity(
            git_root=str(sandbox.git_root),
            workspace_root=str(sandbox.workspace_root),
            workspace_prefix=sandbox.workspace_prefix,
            baseline_fingerprint=DIGEST,
            external_dirty_baseline_fingerprint=DIGEST,
        ),
        budget=gh.RunBudget(
            max_steps=10,
            max_tool_calls=60,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=3600,
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    with storage.transaction(actor="agent") as unit:
        lease = unit.state.create_run(run, owner_id="agent", lease_policy=gh.LeasePolicy())
        unit.state.create_step(
            gh.Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                summary="apply change",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        unit.state.transition_step(
            STEP_ID,
            expected_status=gh.StepStatus.PLANNED,
            expected_revision=0,
            target=gh.StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        unit.state.transition_run(
            RUN_ID,
            expected_status=gh.RunStatus.NEW,
            expected_revision=2,
            target=gh.RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )
    context = gh.ToolExecutionContext(
        run_id=RUN_ID,
        step_id=STEP_ID,
        task_id=task_id,
        task_kind=task_kind,
        mode=gh.ExecutionMode.EXECUTE,
        actor="agent",
        roles=("developer",),
    )
    return SideEffectFixture(
        storage=storage,
        sandbox=sandbox,
        registry=registry,
        policy=policy,
        gate=gh.Gate(
            storage=storage,
            registry=registry,
            policy=policy,
            sandbox=sandbox,
            clock=clock,
        ),
        lease_token=lease.token,
        context=context,
        root=root,
        workspace=workspace,
        config=resolved,
        task_kind=task_kind,
    )


def test_readonly_factory_registers_stable_snapshot(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    assert fixture.registry.names() == (
        "git_diff",
        "git_status",
        "list_files",
        "read_directory",
        "read_file",
        "search_code",
    )
    snapshot = fixture.registry.snapshot(fixture.context, fixture.policy)
    assert tuple(spec.name for spec in snapshot) == fixture.registry.names()
    for spec in snapshot:
        assert spec.risk == gh.ToolRisk.READ
        assert spec.approval_requirement == gh.ApprovalRequirement.NEVER
        assert spec.concurrency_class == gh.ConcurrencyClass.SERIAL
        serialized = spec.model_dump_json()
        assert "handler" not in serialized
        assert str(fixture.workspace) not in serialized


def test_strict_tool_schemas_reject_before_handler(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    for arguments in (
        {},
        {"path": 7},
        {"path": "README.md", "injected": True},
    ):
        observation = fixture.invoke("read_file", arguments)
        assert observation.status == gh.ObservationStatus.DENIED
        assert observation.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value
        assert observation.phase == gh.GatePhase.SCHEMA
    injected_git = fixture.invoke(
        "git_diff",
        {"path": ".", "revision": "HEAD~1"},
    )
    assert injected_git.status == gh.ObservationStatus.DENIED
    assert injected_git.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value
    invalid_glob = fixture.invoke(
        "list_files",
        {"path": ".", "pattern": "../**/*.py"},
    )
    assert invalid_glob.status == gh.ObservationStatus.DENIED
    assert invalid_glob.code == gh.ErrorCode.GATE_SEMANTIC_INVALID.value
    assert fixture.snapshot().invocations == ()


def test_readonly_path_escape_denied_before_handler(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    observation = fixture.invoke(
        "read_file",
        {"path": "artifacts/../../.env"},
    )
    assert observation.status == gh.ObservationStatus.DENIED
    assert observation.phase == gh.GatePhase.SEMANTIC
    assert observation.code in {
        gh.ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE.value,
        gh.ErrorCode.SANDBOX_PATH_SENSITIVE.value,
    }
    assert fixture.snapshot().invocations == ()
    with fixture.storage.transaction(actor="agent") as unit:
        denied = [
            event
            for event in unit.audit.list_run_events(RUN_ID)
            if event.tool_call_id == observation.tool_call_id
        ]
    assert denied
    assert denied[-1].decision == gh.AuditDecision.DENY
    assert denied[-1].rule_id
    assert denied[-1].actor == "agent"


def test_third_equivalent_read_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    arguments = {"path": "README.md"}

    first = fixture.invoke("read_file", arguments)
    second = fixture.invoke("read_file", arguments)
    third = fixture.invoke("read_file", arguments)

    assert first.status == gh.ObservationStatus.ALLOWED
    assert second.status == gh.ObservationStatus.ALLOWED
    assert third.status == gh.ObservationStatus.DENIED
    assert third.code == gh.ErrorCode.GATE_REPEATED_CALL.value
    assert len(fixture.snapshot().invocations) == 2


def test_default_arguments_have_one_canonical_digest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    implicit = fixture.invoke("read_file", {"path": "README.md"})
    explicit = fixture.invoke(
        "read_file",
        {"path": "README.md", "offset": 1, "limit": 2_000},
    )

    assert implicit.status == gh.ObservationStatus.ALLOWED
    assert explicit.status == gh.ObservationStatus.ALLOWED
    invocations = fixture.snapshot().invocations
    assert invocations[0].normalized_args == {
        "path": "README.md",
        "offset": 1,
        "limit": 2_000,
    }
    assert invocations[0].args_digest == invocations[1].args_digest


def test_read_file_paginates_with_versioned_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    observation = fixture.invoke(
        "read_file",
        {"path": "src/login.py", "offset": 2, "limit": 2},
    )
    assert observation.status == gh.ObservationStatus.ALLOWED
    output = observation.output
    assert isinstance(output, dict)
    assert output["start_line"] == 2
    assert output["end_line"] == 3
    assert output["next_offset"] == 4
    assert output["complete"] is False
    assert "2|     if not user:" in output["content"]
    assert len(observation.evidence_ids) == 1

    snapshot = fixture.snapshot()
    evidence = next(
        item for item in snapshot.evidence if item.evidence_id == observation.evidence_id
    )
    assert evidence.tool_call_id == observation.tool_call_id
    assert evidence.run_id == RUN_ID
    assert evidence.kind == gh.EvidenceKind.READ
    assert evidence.path == "src/login.py"
    assert (evidence.line_start, evidence.line_end) == (2, 3)
    assert evidence.content_digest == output["content_digest"]


def test_literal_search_scope_glob_case_and_context(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    observation = fixture.invoke(
        "search_code",
        {
            "path": "src",
            "pattern": "LOGIN",
            "glob": "**/*.py",
            "ignore_case": True,
            "context": 1,
            "limit": 10,
        },
    )
    assert observation.status == gh.ObservationStatus.ALLOWED
    output = observation.output
    assert isinstance(output, dict)
    match = next(item for item in output["matches"] if item["path"] == "src/login.py")
    assert match["line_number"] == 1
    assert (match["context_start_line"], match["context_end_line"]) == (1, 2)
    assert len(observation.evidence_ids) >= 1
    assert all(item["path"].startswith("src/") for item in output["matches"])
    assert "src/secret/config.py" not in {item["path"] for item in output["matches"]}
    evidence = {item.evidence_id: item for item in fixture.snapshot().evidence}
    for evidence_id in observation.evidence_ids:
        assert evidence[evidence_id].kind == gh.EvidenceKind.READ
        assert evidence[evidence_id].line_start is not None
        assert evidence[evidence_id].line_end is not None


def test_listing_is_sorted_and_bounded(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    listing = fixture.invoke(
        "list_files",
        {"path": ".", "pattern": "**/*.py", "limit": 2},
    )
    assert listing.status == gh.ObservationStatus.ALLOWED
    output = listing.output
    assert isinstance(output, dict)
    assert output["entries"] == sorted(output["entries"])
    assert not any("node_modules" in entry or "dist/" in entry for entry in output["entries"])
    assert output["returned_count"] <= 2
    assert output["has_more"] is True

    root_file = fixture.invoke(
        "list_files",
        {"path": ".", "pattern": "**/*.md"},
    )
    assert root_file.status == gh.ObservationStatus.ALLOWED
    assert root_file.output["entries"] == ["README.md"]

    directory = fixture.invoke("read_directory", {"path": ".", "limit": 3})
    assert directory.status == gh.ObservationStatus.ALLOWED
    directory_output = directory.output
    assert isinstance(directory_output, dict)
    assert directory_output["returned_count"] <= 3
    assert directory_output["has_more"] is True


def test_git_status_filters_denied_paths(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.workspace / "src" / "login.py").write_text("changed\n", encoding="utf-8")
    (fixture.workspace / "src" / "secret" / "config.py").write_text(
        "TOPSECRET = 'leak'\n",
        encoding="utf-8",
    )
    (fixture.workspace / ".env").write_text("TOKEN=leak\n", encoding="utf-8")

    observation = fixture.invoke("git_status", {})
    assert observation.status == gh.ObservationStatus.ALLOWED
    output = observation.output
    assert isinstance(output, dict)
    paths = {entry["path"] for entry in output["entries"]}
    assert "src/login.py" in paths
    assert "src/secret/config.py" not in paths
    assert ".env" not in paths
    assert output["omitted_count"] == 2
    assert output["clean"] is False
    assert len(observation.evidence_ids) == 1


def test_git_diff_pathspec_and_staged_modes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    login = fixture.workspace / "src" / "login.py"
    secret = fixture.workspace / "src" / "secret" / "config.py"
    login.write_text("def login(user):\n    return bool(user)\n", encoding="utf-8")
    secret.write_text("TOPSECRET = 'changed'\n", encoding="utf-8")

    unstaged = fixture.invoke(
        "git_diff",
        {"path": "src/login.py"},
    )
    assert unstaged.status == gh.ObservationStatus.ALLOWED
    assert "+    return bool(user)" in unstaged.output["patch"]

    _git(fixture.workspace, "add", "src/login.py", "src/secret/config.py")
    index_before = (fixture.root / ".git" / "index").read_bytes()
    staged = fixture.invoke("git_diff", {"path": "src", "staged": True})
    index_after = (fixture.root / ".git" / "index").read_bytes()

    assert staged.status == gh.ObservationStatus.ALLOWED
    assert "src/login.py" in staged.output["patch"]
    assert "src/secret/config.py" not in staged.output["patch"]
    assert "TOPSECRET" not in staged.output["patch"]
    assert hashlib.sha256(index_before).digest() == hashlib.sha256(index_after).digest()


def test_git_failures_are_not_reported_as_empty(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cases = (
        (
            gh.SandboxProcessResult(
                command_id="readonly_git_status",
                status=gh.SandboxProcessStatus.FAILED,
                exit_code=1,
                signal=None,
                stdout="",
                stderr="failed",
                stdout_truncated=False,
                stderr_truncated=False,
                timed_out=False,
                cancelled=False,
                process_group_terminated=False,
                duration_ms=1,
                result_code=None,
            ),
            gh.ErrorCode.TOOL_GIT_FAILED,
        ),
        (
            gh.SandboxProcessResult(
                command_id="readonly_git_status",
                status=gh.SandboxProcessStatus.TIMED_OUT,
                exit_code=None,
                signal=15,
                stdout="",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                timed_out=True,
                cancelled=False,
                process_group_terminated=True,
                duration_ms=10_000,
                result_code=gh.ErrorCode.SANDBOX_COMMAND_TIMEOUT,
            ),
            gh.ErrorCode.TOOL_GIT_TIMEOUT,
        ),
        (
            gh.SandboxProcessResult(
                command_id="readonly_git_status",
                status=gh.SandboxProcessStatus.SUCCEEDED,
                exit_code=0,
                signal=None,
                stdout="malformed\x00",
                stderr="",
                stdout_truncated=False,
                stderr_truncated=False,
                timed_out=False,
                cancelled=False,
                process_group_terminated=False,
                duration_ms=1,
                result_code=None,
            ),
            gh.ErrorCode.TOOL_GIT_OUTPUT_INVALID,
        ),
    )
    for index, (process, expected_code) in enumerate(cases):
        fixture = _fixture(tmp_path / f"case-{index}")
        monkeypatch.setattr(
            fixture.sandbox,
            "execute",
            lambda *args, result=process, **kwargs: result,
        )
        observation = fixture.invoke("git_status", {})
        assert observation.status == gh.ObservationStatus.FAILED
        assert observation.code == expected_code.value
        assert observation.evidence_ids == ()


def test_read_errors_and_redaction_are_structured(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.workspace / "binary.dat").write_bytes(b"PK\x00\x00")
    (fixture.workspace / "secret.txt").write_text(
        'access_token = "super-secret-token"\n',
        encoding="utf-8",
    )

    binary = fixture.invoke("read_file", {"path": "binary.dat"})
    assert binary.status == gh.ObservationStatus.FAILED
    assert binary.code == gh.ErrorCode.TOOL_BINARY_FILE.value

    directory = fixture.invoke("read_file", {"path": "src"})
    assert directory.status == gh.ObservationStatus.FAILED
    assert directory.code == gh.ErrorCode.TOOL_PATH_TYPE_MISMATCH.value

    redacted = fixture.invoke("read_file", {"path": "secret.txt"})
    assert redacted.status == gh.ObservationStatus.ALLOWED
    assert "super-secret-token" not in str(redacted.output)
    assert "[REDACTED]" in str(redacted.output)


def test_file_change_produces_new_content_digest(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    target = fixture.workspace / "src" / "login.py"

    first = fixture.invoke("read_file", {"path": "src/login.py"})
    target.write_text("def login(user):\n    return bool(user)\n", encoding="utf-8")
    second = fixture.invoke("read_file", {"path": "src/login.py"})

    assert first.status == gh.ObservationStatus.ALLOWED
    assert second.status == gh.ObservationStatus.ALLOWED
    assert first.output["content_digest"] != second.output["content_digest"]
    evidence = {item.evidence_id: item for item in fixture.snapshot().evidence}
    assert evidence[first.evidence_id].content_digest == first.output["content_digest"]
    assert evidence[second.evidence_id].content_digest == second.output["content_digest"]


def test_read_and_search_bounds_are_explicit(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    long_file = fixture.workspace / "src" / "many.py"
    long_file.write_text(
        "\n".join(f"MATCH_{index:04d} = {index}" for index in range(3_000)),
        encoding="utf-8",
    )

    read = fixture.invoke("read_file", {"path": "src/many.py", "limit": 2_000})
    assert read.status == gh.ObservationStatus.ALLOWED
    assert read.output["complete"] is False
    assert read.output["next_offset"] is not None
    assert read.output["truncated"] is True

    search = fixture.invoke(
        "search_code",
        {"path": "src", "pattern": "MATCH_", "limit": 1},
    )
    assert search.status == gh.ObservationStatus.ALLOWED
    assert search.output["returned_matches"] == 1
    assert search.output["hit_limit_reached"] is True
    assert search.output["has_more"] is True


def test_oversized_and_non_utf8_files_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.workspace / "invalid.txt").write_bytes(b"\xff\xfe")
    (fixture.workspace / "large.txt").write_bytes(b"x" * (10 * 1024 * 1024 + 1))

    invalid = fixture.invoke("read_file", {"path": "invalid.txt"})
    assert invalid.status == gh.ObservationStatus.FAILED
    assert invalid.code == gh.ErrorCode.TOOL_TEXT_ENCODING_INVALID.value

    large = fixture.invoke("read_file", {"path": "large.txt"})
    assert large.status == gh.ObservationStatus.FAILED
    assert large.code == gh.ErrorCode.SANDBOX_FILE_TOO_LARGE.value


def test_traversal_omits_symlink_and_sensitive_targets(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.workspace / ".env").write_text("TOKEN=hidden\n", encoding="utf-8")
    os.symlink(fixture.root / "outside.txt", fixture.workspace / "outside-link")
    (fixture.root / "outside.txt").write_text("outside\n", encoding="utf-8")

    listing = fixture.invoke("read_directory", {"path": ".", "limit": 200})
    assert listing.status == gh.ObservationStatus.ALLOWED
    names = {entry["name"] for entry in listing.output["entries"]}
    assert ".env" not in names
    assert "outside-link" not in names
    assert listing.output["omitted_count"] == 2


def test_side_effect_factory_registers_stable_snapshot(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)

    assert tuple(
        name
        for name in fixture.registry.names()
        if name in {"apply_patch", "run_test", "write_file"}
    ) == ("apply_patch", "run_test", "write_file")
    snapshot = {
        spec.name: spec for spec in fixture.registry.snapshot(fixture.context, fixture.policy)
    }
    assert snapshot["write_file"].risk == gh.ToolRisk.WRITE
    assert snapshot["write_file"].approval_requirement == gh.ApprovalRequirement.REQUIRED
    assert snapshot["apply_patch"].risk == gh.ToolRisk.WRITE
    assert snapshot["apply_patch"].retry_policy == gh.RetryPolicy.NEVER
    assert snapshot["run_test"].risk == gh.ToolRisk.EXECUTE
    assert snapshot["run_test"].approval_requirement == gh.ApprovalRequirement.NEVER
    for spec in snapshot.values():
        serialized = spec.model_dump_json()
        assert "handler" not in serialized
        assert str(fixture.workspace) not in serialized
        assert "python" not in serialized


def test_side_effect_schemas_reject_before_handler(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)

    cases = (
        ("write_file", {"path": "src/app.py"}),
        ("write_file", {"path": "src/app.py", "content": "x", "argv": ["sh"]}),
        ("apply_patch", {"path": "src/app.py", "edits": []}),
        ("apply_patch", {"path": "src/app.py", "edits": [{"oldText": "x"}]}),
        ("run_test", {"command_id": "not_acceptance"}),
        ("run_test", {"command_id": "acceptance_test", "timeout": 1}),
    )
    for tool, arguments in cases:
        observation = fixture.invoke(tool, arguments)
        assert observation.status == gh.ObservationStatus.DENIED
        assert observation.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value
        assert observation.phase == gh.GatePhase.SCHEMA
    assert fixture.snapshot().invocations == ()


def test_write_scope_path_escape_and_platform_denials(tmp_path: Path) -> None:
    code_fixture = _side_effect_fixture(tmp_path / "code")

    escape = code_fixture.invoke(
        "write_file",
        {"path": "artifacts/../../.env", "content": "TOKEN=bad\n"},
    )
    assert escape.status == gh.ObservationStatus.DENIED
    assert escape.phase == gh.GatePhase.SEMANTIC

    undeclared = code_fixture.invoke(
        "write_file",
        {"path": "artifacts/other.md", "content": "no\n"},
    )
    assert undeclared.code == gh.ErrorCode.GATE_WRITE_NOT_ALLOWED.value
    assert not (code_fixture.workspace / "artifacts" / "other.md").exists()

    repair_fixture = _side_effect_fixture(
        tmp_path / "repair",
        task_kind=gh.TaskKind.TEST_REPAIR,
    )
    outside = repair_fixture.invoke(
        "write_file",
        {"path": "README.md", "content": "no\n"},
    )
    assert outside.code == gh.ErrorCode.GATE_WRITE_NOT_ALLOWED.value
    protected = repair_fixture.invoke(
        "write_file",
        {"path": ".git/config", "content": "no\n"},
    )
    assert protected.code == gh.ErrorCode.SANDBOX_PATH_PROTECTED.value
    denied_test = repair_fixture.invoke(
        "write_file",
        {"path": "tests/test_app.py", "content": "weakened\n"},
    )
    assert denied_test.code == gh.ErrorCode.SANDBOX_PATH_DENY_WRITE.value
    assert (
        (repair_fixture.workspace / "tests" / "test_app.py")
        .read_text(encoding="utf-8")
        .startswith("def test_app")
    )


def test_write_symlink_escape_is_denied_and_audited(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path)
    outside = fixture.root / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    target = fixture.workspace / "artifacts" / "report.md"
    target.unlink()
    os.symlink(outside, target)

    request = fixture.request(
        "write_file",
        {"path": "artifacts/report.md", "content": "overwritten\n"},
        call_id="write-symlink-escape",
    )
    observation = fixture.gate.invoke(request)
    assert observation.status == gh.ObservationStatus.DENIED
    assert observation.code == gh.ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE.value
    assert outside.read_text(encoding="utf-8") == "outside\n"
    with fixture.storage.transaction(actor="agent") as unit:
        events = unit.audit.list_tool_events(RUN_ID, "write-symlink-escape")
    denied = [event for event in events if event.decision == gh.AuditDecision.DENY]
    assert denied
    assert denied[-1].rule_id
    assert denied[-1].actor == "agent"
    assert denied[-1].tool_call_id == "write-symlink-escape"


def test_unapproved_write_waits_without_side_effect(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path)
    target = fixture.workspace / "artifacts" / "report.md"
    original = target.read_bytes()
    request = fixture.request(
        "write_file",
        {"path": "artifacts/report.md", "content": "# approved report\n"},
    )

    waiting = fixture.gate.invoke(request)
    assert waiting.status == gh.ObservationStatus.WAITING_APPROVAL
    assert waiting.approval_id is not None
    assert target.read_bytes() == original

    fixture.approve(waiting.approval_id)
    result = fixture.gate.invoke(request)
    assert result.status == gh.ObservationStatus.ALLOWED
    assert target.read_text(encoding="utf-8") == "# approved report\n"
    assert result.evidence_ids


def test_unapproved_patch_waits_without_side_effect(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    target = fixture.workspace / "src" / "app.py"
    original = target.read_bytes()
    waiting = fixture.invoke(
        "apply_patch",
        {
            "path": "src/app.py",
            "edits": [{"oldText": "VALUE = 1", "newText": "VALUE = 2"}],
        },
    )
    assert waiting.status == gh.ObservationStatus.WAITING_APPROVAL
    assert target.read_bytes() == original


def test_approved_write_is_suppressed_after_external_change(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    target = fixture.workspace / "src" / "app.py"
    request = fixture.request(
        "write_file",
        {"path": "src/app.py", "content": "VALUE = 2\n"},
    )
    waiting = fixture.gate.invoke(request)
    fixture.approve(waiting.approval_id)
    first = fixture.gate.invoke(request)
    assert first.status == gh.ObservationStatus.ALLOWED
    original_evidence = first.evidence_ids

    target.write_text("external change\n", encoding="utf-8")
    repeated = fixture.gate.invoke(request)
    assert repeated.code == gh.ErrorCode.GATE_REPLAY_SUPPRESSED.value
    assert repeated.evidence_ids == original_evidence
    assert target.read_text(encoding="utf-8") == "external change\n"
    assert len(fixture.snapshot().invocations) == 1


def test_changed_write_content_invalidates_existing_approval(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path)
    request = fixture.request(
        "write_file",
        {"path": "artifacts/report.md", "content": "# approved\n"},
        call_id="changed-write",
    )
    waiting = fixture.gate.invoke(request)
    fixture.approve(waiting.approval_id)
    changed = gh.ToolCallRequest(
        tool_call_id="changed-write",
        tool_name="write_file",
        args={"path": "artifacts/report.md", "content": "# changed\n"},
        context=fixture.context,
        lease_token=fixture.lease_token,
    )
    observation = fixture.gate.invoke(changed)
    assert observation.code == gh.ErrorCode.GATE_TOOL_CALL_ID_CONFLICT.value
    assert (fixture.workspace / "artifacts" / "report.md").read_text(
        encoding="utf-8"
    ) == "# report\n"


def test_write_file_create_replace_and_noop(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    created_path = fixture.workspace / "tests" / "generated" / "new_test.py"
    created = fixture.invoke_approved(
        "write_file",
        {
            "path": "tests/generated/new_test.py",
            "content": "def test_new():\n    assert True\n",
        },
    )
    assert created.status == gh.ObservationStatus.ALLOWED
    assert created.output["created"] is True
    assert created.output["changed"] is True
    assert created_path.exists()

    replaced = fixture.invoke_approved(
        "write_file",
        {"path": "src/app.py", "content": "VALUE = 3\n"},
    )
    assert replaced.status == gh.ObservationStatus.ALLOWED
    assert replaced.output["mode"] == gh.SandboxWriteMode.REPLACE.value
    assert replaced.output["previous_content_digest"] != replaced.output["content_digest"]

    noop_fixture = _side_effect_fixture(tmp_path / "noop")
    noop = noop_fixture.invoke_approved(
        "write_file",
        {"path": "artifacts/report.md", "content": "# report\n"},
    )
    assert noop.status == gh.ObservationStatus.ALLOWED
    assert noop.output["changed"] is False
    assert noop.evidence_ids == ()


def test_write_rejects_oversize_and_special_targets(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    oversized = fixture.invoke(
        "write_file",
        {"path": "src/large.py", "content": "x" * (10 * 1024 * 1024 + 1)},
    )
    assert oversized.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value

    special_dir = fixture.workspace / "src" / "generated_dir"
    special_dir.mkdir()
    directory = fixture.invoke_approved(
        "write_file",
        {"path": "src/generated_dir", "content": "x\n"},
    )
    assert directory.status == gh.ObservationStatus.FAILED
    assert directory.code in {
        gh.ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE.value,
        gh.ErrorCode.GATE_WRITE_NOT_ALLOWED.value,
    }

    invalid = fixture.workspace / "src" / "invalid.py"
    invalid.write_bytes(b"\xff\xfe")
    non_text = fixture.invoke_approved(
        "write_file",
        {"path": "src/invalid.py", "content": "valid\n"},
    )
    assert non_text.status == gh.ObservationStatus.FAILED
    assert non_text.code == gh.ErrorCode.TOOL_TEXT_ENCODING_INVALID.value
    assert invalid.read_bytes() == b"\xff\xfe"


def test_patch_empty_and_noop_rejected_before_handler(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    empty = fixture.invoke("apply_patch", {"path": "src/app.py", "edits": []})
    assert empty.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value

    noop = fixture.invoke(
        "apply_patch",
        {
            "path": "src/app.py",
            "edits": [{"oldText": "VALUE", "newText": "VALUE"}],
        },
    )
    assert noop.code == gh.ErrorCode.TOOL_PATCH_NO_CHANGE.value
    assert fixture.snapshot().invocations == ()
    assert (
        (fixture.workspace / "src" / "app.py").read_text(encoding="utf-8").startswith("VALUE = 1")
    )


def test_patch_context_missing_and_ambiguous(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    target = fixture.workspace / "src" / "app.py"

    missing = fixture.invoke_approved(
        "apply_patch",
        {
            "path": "src/app.py",
            "edits": [{"oldText": "NOT PRESENT", "newText": "replacement"}],
        },
    )
    assert missing.status == gh.ObservationStatus.FAILED
    assert missing.code == gh.ErrorCode.TOOL_PATCH_CONTEXT_NOT_FOUND.value
    original = target.read_bytes()

    target.write_text("same\nsame\n", encoding="utf-8")
    ambiguous = fixture.invoke_approved(
        "apply_patch",
        {
            "path": "src/app.py",
            "edits": [{"oldText": "same", "newText": "changed"}],
        },
    )
    assert ambiguous.status == gh.ObservationStatus.FAILED
    assert ambiguous.code == gh.ErrorCode.TOOL_PATCH_AMBIGUOUS.value
    assert target.read_text(encoding="utf-8") == "same\nsame\n"
    assert original != target.read_bytes()


def test_patch_later_failure_does_not_commit_earlier_edits(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    target = fixture.workspace / "src" / "app.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    before = target.read_bytes()

    failed = fixture.invoke_approved(
        "apply_patch",
        {
            "path": "src/app.py",
            "edits": [
                {"oldText": "alpha", "newText": "ALPHA"},
                {"oldText": "missing", "newText": "value"},
            ],
        },
    )
    assert failed.status == gh.ObservationStatus.FAILED
    assert failed.code == gh.ErrorCode.TOOL_PATCH_CONTEXT_NOT_FOUND.value
    assert target.read_bytes() == before


def test_patch_sequential_edits_and_versioned_evidence(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    result = fixture.invoke_approved(
        "apply_patch",
        {
            "path": "src/app.py",
            "edits": [
                {"oldText": "VALUE = 1", "newText": "VALUE = 2"},
                {"oldText": "print(VALUE)", "newText": "print(VALUE + 1)"},
            ],
        },
    )
    assert result.status == gh.ObservationStatus.ALLOWED
    assert result.output["edits_applied"] == 2
    assert (fixture.workspace / "src" / "app.py").read_text(encoding="utf-8") == (
        "VALUE = 2\nprint(VALUE + 1)\n"
    )
    evidence = next(
        item for item in fixture.snapshot().evidence if item.evidence_id == result.evidence_id
    )
    assert evidence.kind == gh.EvidenceKind.DIFF
    assert evidence.path == "src/app.py"
    assert evidence.content_digest == result.output["content_digest"]


def test_run_test_rejects_command_injection_before_popen(tmp_path: Path) -> None:
    marker = tmp_path / "repo" / "task" / "should-not-exist"
    fixture = _side_effect_fixture(
        tmp_path,
        task_kind=gh.TaskKind.TEST_REPAIR,
        test_command=(
            "python",
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')",
        ),
    )
    wrong = fixture.invoke("run_test", {"command_id": "shell"})
    assert wrong.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value
    injected = fixture.invoke(
        "run_test",
        {"command_id": "acceptance_test", "argv": ["sh", "-c", "touch marker"]},
    )
    assert injected.code == gh.ErrorCode.GATE_INVALID_ARGUMENT.value
    assert not marker.exists()


def test_run_test_reports_success_and_nonzero_exit(tmp_path: Path) -> None:
    success_fixture = _side_effect_fixture(
        tmp_path / "success",
        task_kind=gh.TaskKind.TEST_REPAIR,
    )
    success = success_fixture.invoke_approved(
        "run_test",
        {"command_id": "acceptance_test"},
    )
    assert success.status == gh.ObservationStatus.ALLOWED
    assert success.output["exit_code"] == 0
    assert success.evidence_ids
    success_evidence = next(
        item
        for item in success_fixture.snapshot().evidence
        if item.evidence_id == success.evidence_id
    )
    assert success_evidence.kind == gh.EvidenceKind.COMMAND
    assert success_evidence.command_id == "acceptance_test"

    failed_fixture = _side_effect_fixture(
        tmp_path / "failed",
        task_kind=gh.TaskKind.TEST_REPAIR,
        test_command=("python", "-c", "print('failure'); raise SystemExit(3)"),
    )
    failed = failed_fixture.invoke_approved(
        "run_test",
        {"command_id": "acceptance_test"},
    )
    assert failed.status == gh.ObservationStatus.FAILED
    assert failed.code == gh.ErrorCode.TOOL_TEST_FAILED.value
    assert failed.output["exit_code"] == 3
    assert "failure" in failed.output["stdout"]
    assert failed.evidence_ids


def test_run_test_timeout_is_unknown_and_not_retried(tmp_path: Path) -> None:
    marker = "starts.txt"
    fixture = _side_effect_fixture(
        tmp_path,
        task_kind=gh.TaskKind.TEST_REPAIR,
        test_command=(
            "python",
            "-c",
            (
                "from pathlib import Path; import time; "
                f"p=Path({marker!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); "
                "print('started', flush=True); time.sleep(30)"
            ),
        ),
        command_timeout=0.3,
    )
    request = fixture.request("run_test", {"command_id": "acceptance_test"})
    waiting = fixture.gate.invoke(request)
    fixture.approve(waiting.approval_id)
    result = fixture.gate.invoke(request)
    assert result.status == gh.ObservationStatus.UNKNOWN_RESULT
    assert result.code == gh.ErrorCode.SANDBOX_COMMAND_TIMEOUT.value
    assert result.output["timed_out"] is True
    assert result.output["process_group_terminated"] is True
    assert not result.evidence_ids
    assert (fixture.workspace / marker).read_text(encoding="utf-8") == "x"

    repeated = fixture.gate.invoke(request)
    assert repeated.status == gh.ObservationStatus.DENIED
    assert (fixture.workspace / marker).read_text(encoding="utf-8") == "x"


def test_run_test_cancellation_maps_unknown(tmp_path: Path, monkeypatch) -> None:
    fixture = _side_effect_fixture(tmp_path, task_kind=gh.TaskKind.TEST_REPAIR)
    cancelled = gh.SandboxProcessResult(
        command_id="acceptance_test",
        status=gh.SandboxProcessStatus.CANCELLED,
        exit_code=None,
        signal=15,
        stdout="",
        stderr="",
        stdout_truncated=False,
        stderr_truncated=False,
        timed_out=False,
        cancelled=True,
        process_group_terminated=True,
        duration_ms=5,
        result_code=gh.ErrorCode.SANDBOX_COMMAND_CANCELLED,
    )
    monkeypatch.setattr(fixture.sandbox, "execute", lambda *args, **kwargs: cancelled)
    result = fixture.invoke_approved("run_test", {"command_id": "acceptance_test"})
    assert result.status == gh.ObservationStatus.UNKNOWN_RESULT
    assert result.code == gh.ErrorCode.SANDBOX_COMMAND_CANCELLED.value
    assert result.evidence_ids == ()


def test_command_output_is_bounded_and_redacted(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(
        tmp_path,
        task_kind=gh.TaskKind.TEST_REPAIR,
        test_command=(
            "python",
            "-c",
            "print('access_token=super-secret-token'); print('x' * 4096)",
        ),
        stdout_limit=256,
    )
    result = fixture.invoke_approved("run_test", {"command_id": "acceptance_test"})
    assert result.status == gh.ObservationStatus.ALLOWED
    assert result.output["stdout_truncated"] is True
    assert "super-secret-token" not in str(result.output)
    assert result.evidence_ids == ()
    with fixture.storage.transaction(actor="agent") as unit:
        events = unit.audit.list_tool_events(RUN_ID, result.tool_call_id)
    assert all("super-secret-token" not in str(event.model_dump()) for event in events)


def test_result_persistence_failure_never_claims_success(
    tmp_path: Path,
    monkeypatch,
    fault_injector,
) -> None:
    fixture = _side_effect_fixture(tmp_path, fault_injector=fault_injector)
    target = fixture.workspace / "artifacts" / "report.md"
    original = fixture.sandbox.write_file_atomic

    def write_then_fail(*args, **kwargs):
        result = original(*args, **kwargs)
        fault_injector.arm("audit:before-insert")
        return result

    monkeypatch.setattr(fixture.sandbox, "write_file_atomic", write_then_fail)
    request = fixture.request(
        "write_file",
        {"path": "artifacts/report.md", "content": "# changed\n"},
    )
    waiting = fixture.gate.invoke(request)
    fixture.approve(waiting.approval_id)
    response = fixture.gate.invoke(request)
    assert response.status != gh.ObservationStatus.ALLOWED
    assert target.read_text(encoding="utf-8") == "# changed\n"
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == gh.InvocationStatus.STARTED


def test_later_file_change_invalidates_old_write_evidence(tmp_path: Path) -> None:
    fixture = _side_effect_fixture(tmp_path)
    result = fixture.invoke_approved(
        "write_file",
        {"path": "artifacts/report.md", "content": "# first\n"},
    )
    evidence = next(
        item for item in fixture.snapshot().evidence if item.evidence_id == result.evidence_id
    )
    (fixture.workspace / "artifacts" / "report.md").write_text("# second\n", encoding="utf-8")
    current = fixture.invoke("read_file", {"path": "artifacts/report.md"})
    assert current.status == gh.ObservationStatus.ALLOWED
    assert evidence.content_digest != current.output["content_digest"]


def test_parallel_writes_are_serialized(tmp_path: Path, monkeypatch) -> None:
    fixture = _side_effect_fixture(tmp_path)
    physical_writes: list[str] = []
    original = fixture.sandbox.write_file_atomic

    def counted_write(*args, **kwargs):
        result = original(*args, **kwargs)
        physical_writes.append(result.mode.value)
        return result

    monkeypatch.setattr(fixture.sandbox, "write_file_atomic", counted_write)
    request = fixture.request(
        "write_file",
        {"path": "artifacts/report.md", "content": "# concurrent\n"},
        call_id="parallel-write",
    )
    waiting = fixture.gate.invoke(request)
    fixture.approve(waiting.approval_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        observations = list(executor.map(fixture.gate.invoke, (request, request)))
    assert sum(item.status == gh.ObservationStatus.ALLOWED for item in observations) == 1
    assert sum(item.status == gh.ObservationStatus.DENIED for item in observations) == 1
    assert physical_writes == [gh.SandboxWriteMode.REPLACE.value]


def test_write_command_suite_is_offline_and_repeatable(tmp_path: Path, monkeypatch) -> None:
    def deny_network(*_args, **_kwargs):
        raise AssertionError("WP-06 tests must not access the network")

    monkeypatch.setattr(socket, "create_connection", deny_network)
    first = _side_effect_fixture(tmp_path / "first")
    second = _side_effect_fixture(tmp_path / "second")
    first_result = first.invoke_approved(
        "write_file",
        {"path": "artifacts/report.md", "content": "# deterministic\n"},
    )
    second_result = second.invoke_approved(
        "write_file",
        {"path": "artifacts/report.md", "content": "# deterministic\n"},
    )
    assert first_result.status == gh.ObservationStatus.ALLOWED
    assert second_result.status == gh.ObservationStatus.ALLOWED
    assert first_result.output["content_digest"] == second_result.output["content_digest"]
