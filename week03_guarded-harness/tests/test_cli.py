"""Offline CLI, approval control, and inspection tests."""

from __future__ import annotations

import io
import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fake_model import FakeScriptedModel, ScriptedResponse, final_answer, tool_call

import guarded_harness as gh
import guarded_harness.cli as cli_module
from guarded_harness.cli import main
from guarded_harness.domain import AcceptanceStatus, ErrorCode, RunStatus


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime.now(timezone.utc).replace(microsecond=0)

    def now(self) -> datetime:
        self.value += timedelta(microseconds=1)
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


class SequenceModelFactory:
    def __init__(self, scripts: tuple[tuple[ScriptedResponse, ...], ...]) -> None:
        self._scripts = iter(scripts)
        self.models: list[FakeScriptedModel] = []

    def __call__(self, _config_path: Path) -> FakeScriptedModel:
        responses = next(self._scripts)
        model = FakeScriptedModel(responses)
        self.models.append(model)
        return model


@dataclass(frozen=True)
class CliFixture:
    config_path: Path
    workspace: Path
    state_dir: Path
    report_path: Path
    original_report: str
    clock: MutableClock

    @property
    def database_path(self) -> Path:
        return self.state_dir / "harness.sqlite3"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _valid_document() -> str:
    return """## 事实

- `value()` 返回 1。[E1]

## 推断

- 无

## 代码依据

- E1: `src/app.py:1-2`
"""


def _fixture(tmp_path: Path) -> CliFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    (workspace / "src" / "app.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    original_report = "original report\n"
    report_path = workspace / "artifacts" / "report.md"
    report_path.write_text(original_report, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "CLI Tests")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "baseline")
    state_dir = tmp_path / "state"
    config_path = tmp_path / "harness.yaml"
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
  id: understand_app
  kind: code_understanding
  description: Explain value and update the report without changing source.
  allowed_tools:
    - read_file
    - write_file
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
      - write_file
    approver_roles:
      - operator
    ttl_seconds: 86400
    channel: local_cli
""",
        encoding="utf-8",
    )
    return CliFixture(
        config_path=config_path,
        workspace=workspace,
        state_dir=state_dir,
        report_path=report_path,
        original_report=original_report,
        clock=MutableClock(),
    )


def _waiting_script() -> tuple[ScriptedResponse, ...]:
    return (
        tool_call("read-1", "read_file", {"path": "src/app.py"}),
        tool_call(
            "write-1",
            "write_file",
            {"path": "artifacts/report.md", "content": _valid_document()},
        ),
    )


def _invoke(
    fixture: CliFixture,
    args: list[str],
    *,
    model_factory: Callable[[Path], FakeScriptedModel] | None = None,
    sleeper: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> tuple[int, dict[str, Any] | None, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        ["--config", str(fixture.config_path), *args],
        model_factory=model_factory,
        clock=fixture.clock,
        stdout=stdout,
        stderr=stderr,
        sleeper=sleeper,
        monotonic=monotonic,
        approval_poll_seconds=0.01,
    )
    text = stdout.getvalue()
    payload = json.loads(text) if "--json" in args and text.strip() else None
    return code, payload, text, stderr.getvalue()


def _make_v2(fixture: CliFixture) -> None:
    text = fixture.config_path.read_text(encoding="utf-8").replace("version: 1", "version: 2")
    model_block = """model:
  provider: openai_chat_completions_v1
  model_id: gpt-test-2026-01-01
  endpoint: https://api.example.invalid/v1
  credential_ref: MODEL_API_KEY
  tokenizer_id: tiktoken:o200k_base
  max_input_tokens: 1000
  max_output_tokens: 100
  pricing:
    version: test-price
    input_per_million: "1"
    output_per_million: "2"
"""
    fixture.config_path.write_text(
        text.replace("task:\n", f"{model_block}task:\n"),
        encoding="utf-8",
    )


def _start_waiting(fixture: CliFixture) -> tuple[int, dict[str, Any]]:
    factory = SequenceModelFactory((_waiting_script(),))
    code, payload, _text, _stderr = _invoke(fixture, ["run", "--json"], model_factory=factory)
    assert code == 3
    assert payload is not None and payload["ok"] is True
    return code, payload["data"]


def _approve(fixture: CliFixture, approval_id: str, *, role: str = "operator") -> dict[str, Any]:
    code, payload, _text, stderr = _invoke(
        fixture,
        [
            "approvals",
            "approve",
            approval_id,
            "--actor",
            "operator-1",
            "--role",
            role,
            "--json",
        ],
    )
    assert code == 0, stderr
    assert payload is not None and payload["ok"] is True
    return payload["data"]


def test_module_and_console_entrypoints_start_the_same_main() -> None:
    module = subprocess.run(
        [sys.executable, "-m", "guarded_harness", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    console = subprocess.run(
        [str(Path(sys.executable).with_name("guarded-harness")), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert module.returncode == 0
    assert console.returncode == 0
    assert module.stdout.splitlines()[0] == console.stdout.splitlines()[0]
    assert "guarded-harness" in module.stdout


def test_manual_acceptance_runner_prepares_and_waits(
    tmp_path: Path,
) -> None:
    runner = Path(__file__).with_name("manual_acceptance_runner.py")
    root = tmp_path / "manual-acceptance"
    prepared = subprocess.run(
        [sys.executable, str(runner), "prepare", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stderr

    started = subprocess.run(
        [sys.executable, str(runner), "start-code", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert started.returncode == 3, started.stderr
    payload = json.loads(started.stdout)
    assert payload["data"]["status"] == "WAITING_APPROVAL"
    assert not (
        root
        / "examples"
        / "workspaces"
        / "code-understanding"
        / "artifacts"
        / "code-understanding.md"
    ).exists()


def test_usage_errors_are_strict_and_json(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["run", "--wait-for-approval", "0", "--json"],
    )
    assert code == 2
    assert payload is not None
    assert payload["ok"] is False
    assert payload["error"]["code"] == ErrorCode.CLI_USAGE_ERROR.value

    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        ["run", "--json"],
        stdout=stdout,
        stderr=stderr,
    )
    assert code == 2
    payload = json.loads(stdout.getvalue())
    assert payload["error"]["code"] == ErrorCode.CLI_CONFIG_REQUIRED.value


def test_run_instruction_replaces_config_description_and_persists(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    instruction = "CLI_INSTRUCTION_MARKER: explain value before updating the report."
    factory = SequenceModelFactory((_waiting_script(),))

    code, payload, _text, stderr = _invoke(
        fixture,
        ["run", "--instruction", instruction, "--json"],
        model_factory=factory,
    )

    assert code == 3, stderr
    assert payload is not None
    rendered_messages = str(factory.models[0].calls[0].messages)
    assert instruction in rendered_messages
    assert "Explain value and update the report without changing source." not in rendered_messages
    with sqlite3.connect(fixture.database_path) as connection:
        persisted = connection.execute(
            "SELECT task_description FROM runs WHERE run_id = ?",
            (payload["data"]["run_id"],),
        ).fetchone()
    assert persisted == (instruction,)


def test_run_without_instruction_uses_config_description(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config = gh.load_config(fixture.config_path)
    factory = SequenceModelFactory(((final_answer("candidate"),),))

    code, _payload, _text, stderr = _invoke(
        fixture,
        ["run", "--json"],
        model_factory=factory,
    )

    assert code == 7, stderr
    rendered_messages = str(factory.models[0].calls[0].messages)
    assert config.config.task.description in rendered_messages


def test_instruction_validation_and_resume_rejection(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    for invalid_instruction in (" \n\t ", "x" * 4001, "safe\x00unsafe"):
        code, payload, _text, _stderr = _invoke(
            fixture,
            ["run", "--instruction", invalid_instruction, "--json"],
        )
        assert code == 2
        assert payload is not None
        assert payload["error"]["code"] == ErrorCode.CLI_USAGE_ERROR.value

    code, payload, _text, _stderr = _invoke(
        fixture,
        [
            "resume",
            f"run_{'0' * 32}",
            "--instruction",
            "replace the instruction",
            "--json",
        ],
    )
    assert code == 2
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.CLI_USAGE_ERROR.value

    for command in (
        ["approvals", "list", "--instruction", "not allowed", "--json"],
        ["inspect", f"run_{'0' * 32}", "--instruction", "not allowed", "--json"],
    ):
        code, payload, _text, _stderr = _invoke(fixture, command)
        assert code == 2
        assert payload is not None
        assert payload["error"]["code"] == ErrorCode.CLI_USAGE_ERROR.value
    assert not fixture.database_path.exists()


def test_run_without_model_factory_fails_before_workspace_side_effects(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    before = fixture.report_path.read_bytes()
    code, payload, _text, _stderr = _invoke(fixture, ["run", "--json"])

    assert code == 6
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.CLI_MODEL_UNAVAILABLE.value
    assert fixture.report_path.read_bytes() == before


def test_v2_run_without_credentials_fails_before_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path)
    _make_v2(fixture)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    before = fixture.report_path.read_bytes()

    code, payload, _text, _stderr = _invoke(fixture, ["run", "--json"])

    assert code == 6
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.MODEL_CREDENTIAL_UNAVAILABLE.value
    assert fixture.report_path.read_bytes() == before
    assert not fixture.database_path.exists()


def test_v2_approvals_do_not_create_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path)
    _make_v2(fixture)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    calls: list[Path] = []

    def forbidden_provider(path: Path):
        calls.append(path)
        raise AssertionError("provider must not be constructed")

    monkeypatch.setattr(cli_module, "create_model_from_config", forbidden_provider)
    code, payload, _text, _stderr = _invoke(fixture, ["approvals", "list", "--json"])

    assert code == 0
    assert payload is not None and payload["ok"] is True
    assert calls == []


def test_strict_config_failure_keeps_config_code_and_redacts_value(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    invalid = fixture.config_path.read_text(encoding="utf-8")
    invalid += "\napi_key: sk-secret-value-that-must-not-leak\n"
    fixture.config_path.write_text(invalid, encoding="utf-8")

    code, payload, text, _stderr = _invoke(fixture, ["inspect", "run_x", "--json"])
    assert code == 2
    assert payload is not None
    assert payload["error"]["code"].startswith("CONFIG_")
    assert "sk-secret-value-that-must-not-leak" not in text


def test_run_defaults_to_waiting_and_does_not_write(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    before = fixture.report_path.stat()
    code, data = _start_waiting(fixture)

    assert code == 3
    assert data["status"] == "WAITING_APPROVAL"
    assert data["acceptance_status"] == "NOT_EVALUATED"
    assert data["approval_id"].startswith("approval_")
    assert fixture.report_path.read_text(encoding="utf-8") == fixture.original_report
    assert fixture.report_path.stat().st_mtime_ns == before.st_mtime_ns


def test_approvals_list_is_bounded_and_does_not_execute(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    before = fixture.report_path.stat()
    _start_waiting(fixture)
    with sqlite3.connect(fixture.database_path) as connection:
        audit_before = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]

    code, payload, _text, stderr = _invoke(
        fixture,
        ["approvals", "list", "--status", "pending", "--limit", "1", "--json"],
    )
    assert code == 0, stderr
    assert payload is not None
    approvals = payload["data"]["approvals"]
    assert len(approvals) == 1
    assert approvals[0]["status"] == "PENDING"
    assert approvals[0]["effective_status"] == "PENDING"
    assert fixture.report_path.stat().st_mtime_ns == before.st_mtime_ns
    with sqlite3.connect(fixture.database_path) as connection:
        audit_after = connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
    assert audit_after == audit_before


def test_approve_then_resume_executes_write_once_and_accepts(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    approved = _approve(fixture, waiting["approval_id"])

    assert approved["approval"]["status"] == "APPROVED"
    assert fixture.report_path.read_text(encoding="utf-8") == fixture.original_report

    factory = SequenceModelFactory(((final_answer("report updated"),),))
    code, payload, _text, stderr = _invoke(
        fixture,
        ["resume", waiting["run_id"], "--json"],
        model_factory=factory,
    )
    assert code == 0, stderr
    assert payload is not None
    assert payload["data"]["acceptance_status"] == "ACCEPTED"
    first_content = fixture.report_path.read_text(encoding="utf-8")
    first_mtime = fixture.report_path.stat().st_mtime_ns
    assert first_content == _valid_document()
    assert len(factory.models) == 1
    assert len(factory.models[0].calls) == 1

    second_factory = SequenceModelFactory(((final_answer("must not run"),),))
    code, payload, _text, stderr = _invoke(
        fixture,
        ["resume", waiting["run_id"], "--json"],
        model_factory=second_factory,
    )
    assert code == 0, stderr
    assert payload is not None
    assert payload["data"]["acceptance_status"] == "ACCEPTED"
    assert fixture.report_path.read_text(encoding="utf-8") == first_content
    assert fixture.report_path.stat().st_mtime_ns == first_mtime
    assert second_factory.models[0].calls == []


def test_resume_without_approval_returns_waiting_without_model_call(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    before = fixture.report_path.stat()
    factory = SequenceModelFactory(((final_answer("must not run"),),))

    code, payload, _text, stderr = _invoke(
        fixture,
        ["resume", waiting["run_id"], "--json"],
        model_factory=factory,
    )

    assert code == 3, stderr
    assert payload is not None
    assert payload["data"]["status"] == "WAITING_APPROVAL"
    assert factory.models[0].calls == []
    assert fixture.report_path.read_text(encoding="utf-8") == fixture.original_report
    assert fixture.report_path.stat().st_mtime_ns == before.st_mtime_ns


def test_new_process_resume_executes_once_after_console_approval(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    console = Path(sys.executable).with_name("guarded-harness")
    approved = subprocess.run(
        [
            str(console),
            "--config",
            str(fixture.config_path),
            "approvals",
            "approve",
            waiting["approval_id"],
            "--actor",
            "operator-1",
            "--role",
            "operator",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert approved.returncode == 0, approved.stderr

    resumed = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("cli_model_runner.py")),
            str(fixture.config_path),
            waiting["run_id"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resumed.returncode == 0, resumed.stderr
    payload = json.loads(resumed.stdout)
    assert payload["data"]["acceptance_status"] == "ACCEPTED"
    assert fixture.report_path.read_text(encoding="utf-8") == _valid_document()
    first_mtime = fixture.report_path.stat().st_mtime_ns

    repeated = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("cli_model_runner.py")),
            str(fixture.config_path),
            waiting["run_id"],
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert repeated.returncode == 0, repeated.stderr
    assert fixture.report_path.read_text(encoding="utf-8") == _valid_document()
    assert fixture.report_path.stat().st_mtime_ns == first_mtime


def test_concurrent_resume_processes_execute_write_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    _approve(fixture, waiting["approval_id"])
    runner = Path(__file__).with_name("cli_model_runner.py")
    command = [
        sys.executable,
        str(runner),
        str(fixture.config_path),
        waiting["run_id"],
    ]
    first = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=60)
    second_stdout, second_stderr = second.communicate(timeout=60)

    exit_codes = sorted((first.returncode, second.returncode))
    assert exit_codes in ([0, 0], [0, 9])
    if exit_codes == [0, 9]:
        loser_output = first_stdout if first.returncode != 0 else second_stdout
        loser_payload = json.loads(loser_output)
        assert loser_payload["ok"] is False
        assert loser_payload["error"]["code"] == ErrorCode.STATE_LEASE_HELD.value
    assert fixture.report_path.read_text(encoding="utf-8") == _valid_document()
    with sqlite3.connect(fixture.database_path) as connection:
        write_calls = connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE run_id = ? AND tool_name = 'write_file'",
            (waiting["run_id"],),
        ).fetchone()[0]
    assert write_calls == 1


def test_reject_stops_run_without_executing_handler(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    before = fixture.report_path.stat()
    code, payload, _text, stderr = _invoke(
        fixture,
        [
            "approvals",
            "reject",
            waiting["approval_id"],
            "--actor",
            "operator-1",
            "--role",
            "operator",
            "--json",
        ],
    )

    assert code == 0, stderr
    assert payload is not None
    assert payload["data"]["approval"]["status"] == "REJECTED"
    assert payload["data"]["run"]["status"] == "STOPPED"
    assert payload["data"]["run"]["stop_reason"] == "APPROVAL_REJECTED"
    assert fixture.report_path.read_text(encoding="utf-8") == fixture.original_report
    assert fixture.report_path.stat().st_mtime_ns == before.st_mtime_ns


def test_approval_requires_an_allowed_non_self_role(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    code, payload, _text, _stderr = _invoke(
        fixture,
        [
            "approvals",
            "approve",
            waiting["approval_id"],
            "--actor",
            "operator-1",
            "--role",
            "viewer",
            "--json",
        ],
    )

    assert code == 5, payload
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.APPROVAL_APPROVER_DENIED.value
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["approvals", "list", "--run-id", waiting["run_id"], "--json"],
    )
    assert code == 0
    assert payload is not None
    assert payload["data"]["approvals"][0]["status"] == "PENDING"


def test_self_approval_and_repeated_approval_are_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    code, payload, _text, _stderr = _invoke(
        fixture,
        [
            "approvals",
            "approve",
            waiting["approval_id"],
            "--actor",
            "agent",
            "--role",
            "operator",
            "--json",
        ],
    )
    assert code == 5, payload
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.APPROVAL_SELF_APPROVAL_DENIED.value

    _approve(fixture, waiting["approval_id"])
    code, payload, _text, _stderr = _invoke(
        fixture,
        [
            "approvals",
            "approve",
            waiting["approval_id"],
            "--actor",
            "operator-1",
            "--role",
            "operator",
            "--json",
        ],
    )
    assert code == 5, payload
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.APPROVAL_NOT_EXECUTABLE.value
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["approvals", "list", "--run-id", waiting["run_id"], "--json"],
    )
    assert code == 0
    assert payload is not None
    assert payload["data"]["approvals"][0]["status"] == "APPROVED"


def test_wait_mode_approves_during_poll_and_resumes_once(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    approve_once = True

    def approve_in_other_action(_seconds: float) -> None:
        nonlocal approve_once
        if not approve_once:
            return
        approve_once = False
        _approve(fixture, waiting["approval_id"])

    factory = SequenceModelFactory(
        (
            (final_answer("unused while waiting"),),
            (final_answer("approved and resumed"),),
        )
    )
    code, payload, _text, stderr = _invoke(
        fixture,
        ["resume", waiting["run_id"], "--wait-for-approval", "5", "--json"],
        model_factory=factory,
        sleeper=approve_in_other_action,
    )

    assert code == 0, stderr
    assert payload is not None
    assert payload["data"]["acceptance_status"] == "ACCEPTED"
    assert len(factory.models) == 2
    assert factory.models[0].calls == []
    assert len(factory.models[1].calls) == 1
    assert fixture.report_path.read_text(encoding="utf-8") == _valid_document()


def test_wait_timeout_keeps_waiting_without_mutating_approval(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    factory = SequenceModelFactory(((final_answer("unused"),),))
    times = iter((0.0, 2.0))
    code, payload, _text, stderr = _invoke(
        fixture,
        ["resume", waiting["run_id"], "--wait-for-approval", "1", "--json"],
        model_factory=factory,
        sleeper=lambda _seconds: None,
        monotonic=lambda: next(times),
    )

    assert code == 3, stderr
    assert payload is not None
    assert payload["data"]["status"] == "WAITING_APPROVAL"
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["approvals", "list", "--run-id", waiting["run_id"], "--json"],
    )
    assert code == 0
    assert payload is not None
    assert payload["data"]["approvals"][0]["status"] == "PENDING"
    assert fixture.report_path.read_text(encoding="utf-8") == fixture.original_report


def test_workspace_change_blocks_resume_without_execution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    _approve(fixture, waiting["approval_id"])
    (fixture.workspace / "src" / "app.py").write_text(
        "def value():\n    return 9\n",
        encoding="utf-8",
    )
    factory = SequenceModelFactory(((final_answer("must not run"),),))
    code, payload, _text, stderr = _invoke(
        fixture,
        ["resume", waiting["run_id"], "--json"],
        model_factory=factory,
    )

    assert code == 5, stderr
    assert payload is not None
    assert payload["data"]["status"] == "BLOCKED"
    assert fixture.report_path.read_text(encoding="utf-8") == fixture.original_report
    assert factory.models[0].calls == []


def test_effective_expiry_is_visible_without_materialization(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    fixture.clock.advance(86_401)
    code, payload, _text, stderr = _invoke(
        fixture,
        ["approvals", "list", "--status", "expired", "--json"],
    )

    assert code == 0, stderr
    assert payload is not None
    approval = payload["data"]["approvals"][0]
    assert approval["status"] == "PENDING"
    assert approval["effective_status"] == "EXPIRED"
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["inspect", waiting["run_id"], "--json"],
    )
    assert code == 0
    assert payload is not None
    assert payload["data"]["run"]["status"] == "WAITING_APPROVAL"
    assert payload["data"]["approvals"][0]["status"] == "PENDING"


def test_expired_approval_action_materializes_stop_before_failing(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    fixture.clock.advance(86_401)
    code, payload, _text, _stderr = _invoke(
        fixture,
        [
            "approvals",
            "approve",
            waiting["approval_id"],
            "--actor",
            "operator-1",
            "--role",
            "operator",
            "--json",
        ],
    )

    assert code == 5, payload
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.APPROVAL_EXPIRED.value
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["inspect", waiting["run_id"], "--json"],
    )
    assert code == 0
    assert payload is not None
    assert payload["data"]["run"]["status"] == "STOPPED"
    assert payload["data"]["run"]["stop_reason"] == "APPROVAL_EXPIRED"
    assert payload["data"]["approvals"][0]["status"] == "EXPIRED"


def test_inspect_exposes_denied_call_audit_without_raw_arguments(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    factory = SequenceModelFactory(
        (
            (
                tool_call("bad-read", "read_file", {}),
                final_answer("no defensible deliverable"),
            ),
        )
    )
    code, payload, _text, stderr = _invoke(
        fixture,
        ["run", "--json"],
        model_factory=factory,
    )
    assert code == 7, stderr
    assert payload is not None
    run_id = payload["data"]["run_id"]

    code, payload, _text, stderr = _invoke(
        fixture,
        ["inspect", run_id, "--tool-call-id", "bad-read", "--json"],
    )
    assert code == 0, stderr
    assert payload is not None
    events = payload["data"]["audit"]["events"]
    denied = [event for event in events if event["decision"] == "DENY"]
    assert denied
    assert denied[0]["tool_call_id"] == "bad-read"
    assert denied[0]["actor"] == "agent"
    assert denied[0]["rule_id"]
    assert "api_key" not in json.dumps(payload)
    assert "secret-value" not in json.dumps(payload)


def test_inspect_can_filter_approval_audit_and_mark_truncation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    code, payload, _text, stderr = _invoke(
        fixture,
        [
            "inspect",
            waiting["run_id"],
            "--approval-id",
            waiting["approval_id"],
            "--audit-limit",
            "1",
            "--json",
        ],
    )

    assert code == 0, stderr
    assert payload is not None
    audit = payload["data"]["audit"]
    assert audit["events"]
    assert audit["events"][0]["approval_id"] == waiting["approval_id"]
    assert audit["truncated"] is True


def test_inspect_rejects_unknown_run_and_does_not_touch_workspace(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    before = fixture.report_path.stat()
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["inspect", "run_00000000000000000000000000000001", "--json"],
    )

    assert code == 9
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.STATE_NOT_FOUND.value
    assert fixture.report_path.stat().st_mtime_ns == before.st_mtime_ns


def test_inspect_corrupt_audit_chain_returns_internal_failure(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _code, waiting = _start_waiting(fixture)
    with sqlite3.connect(fixture.database_path) as connection:
        connection.execute("DROP TRIGGER audit_events_no_update")
        connection.execute(
            "UPDATE audit_events SET event_hash = ? WHERE sequence = 1",
            (f"sha256:{'0' * 64}",),
        )
        connection.commit()

    code, payload, _text, _stderr = _invoke(
        fixture,
        ["inspect", waiting["run_id"], "--json"],
    )

    assert code == 9
    assert payload is not None
    assert payload["error"]["code"] == ErrorCode.AUDIT_CHAIN_INVALID.value


def test_cli_candidate_output_redacts_secret_values(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    secret = "super-secret-token-value"
    factory = SequenceModelFactory(((final_answer(f"token={secret}"),),))

    code, payload, text, _stderr = _invoke(
        fixture,
        ["run", "--json"],
        model_factory=factory,
    )

    assert code == 7
    assert payload is not None
    assert secret not in text
    assert secret not in json.dumps(payload)


def test_cli_unverifiable_result_returns_exit_8(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path)
    result = gh.RunResult(
        run_id=f"run_{'1' * 32}",
        trace_id="2" * 32,
        status=RunStatus.COMPLETED,
        acceptance_status=AcceptanceStatus.UNVERIFIABLE,
        last_audit_sequence=1,
    )

    class FakeHarness:
        def __init__(self, **_kwargs) -> None:
            pass

        def run(self, _request) -> gh.RunResult:
            return result

    monkeypatch.setattr(cli_module, "Harness", FakeHarness)
    code, payload, _text, _stderr = _invoke(
        fixture,
        ["run", "--json"],
        model_factory=SequenceModelFactory(((final_answer("unused"),),)),
    )

    assert code == 8
    assert payload is not None
    assert payload["data"]["acceptance_status"] == "UNVERIFIABLE"


def test_exit_code_mapping_covers_all_terminal_results() -> None:
    from guarded_harness.cli import exit_code_for_error, exit_code_for_run_result
    from guarded_harness.domain import RunResult

    base = {
        "run_id": "run_00000000000000000000000000000001",
        "trace_id": "00000000000000000000000000000002",
        "last_audit_sequence": 1,
    }
    cases = (
        ("WAITING_APPROVAL", "NOT_EVALUATED", 3),
        ("STOPPED", "NOT_EVALUATED", 4),
        ("BLOCKED", "NOT_EVALUATED", 5),
        ("FAILED", "NOT_EVALUATED", 6),
        ("COMPLETED", "ACCEPTED", 0),
        ("COMPLETED", "REJECTED", 7),
        ("COMPLETED", "UNVERIFIABLE", 8),
    )
    for status, acceptance, expected in cases:
        result = RunResult(
            status=RunStatus(status),
            acceptance_status=AcceptanceStatus(acceptance),
            **base,
        )
        assert exit_code_for_run_result(result) == expected

    assert exit_code_for_error(ErrorCode.CLI_USAGE_ERROR.value) == 2
    assert exit_code_for_error(ErrorCode.APPROVAL_EXPIRED.value) == 5
    assert exit_code_for_error(ErrorCode.HARNESS_INTERNAL_ERROR.value) == 6
    assert exit_code_for_error(ErrorCode.AUDIT_CHAIN_INVALID.value) == 9
