"""WP-05 readonly repository tools exercised through the governed Gate."""

from __future__ import annotations

import hashlib
import os
import subprocess
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
