"""WP-03 Sandbox path, Git, file-I/O, command, and process boundaries."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

import guarded_harness as gh
import guarded_harness.sandbox as sandbox_module
from guarded_harness.sandbox import _classify_process_result, _parse_git_status


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
    )


def _init_repo(tmp_path: Path, *, commit: bool = True) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir()
    (workspace / "README.md").write_text("demo\n", encoding="utf-8")
    (workspace / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "tests" / "existing_test.py").write_text(
        "def test_existing():\n    assert True\n",
        encoding="utf-8",
    )
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "outside.txt").write_text("outside-1\n", encoding="utf-8")
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Harness Tests")
    if commit:
        _git(root, "add", ".")
        _git(root, "commit", "-q", "-m", "baseline")
    return root, workspace


def _config_path(
    tmp_path: Path,
    root: Path,
    *,
    kind: str = "code_understanding",
    require_clean: bool = True,
    protect_external: bool = True,
    deny_write: tuple[str, ...] | None = None,
    allow_write: tuple[str, ...] | None = None,
    deny_read: tuple[str, ...] = (),
    test_command: tuple[str, ...] | None = None,
    state_dir: Path | None = None,
) -> Path:
    if deny_write is None:
        deny_write = ("src/**", "tests/**")
    if allow_write is None:
        allow_write = ("artifacts/**",)
    task: dict[str, object] = {
        "id": f"{kind}_task",
        "kind": kind,
        "description": "WP-03 sandbox fixture",
        "allowed_tools": [
            "read_file",
            "write_file",
            "git_status",
            "run_test",
        ],
        "paths": {
            "deny_read": list(deny_read),
            "deny_write": list(deny_write),
            "allow_write": list(allow_write),
        },
        "approval": {
            "required_tools": [],
            "approver_roles": [],
            "ttl_seconds": 900,
            "channel": "local_cli",
        },
    }
    if kind == "test_repair":
        task["acceptance"] = {
            "strategy": "test_repair",
            "test_command": list(test_command or ("python", "-c", "print('ok')")),
            "baseline_required": True,
        }
    else:
        task["acceptance"] = {
            "strategy": "code_understanding",
            "deliverables": ["artifacts/report.md"],
        }
    payload = {
        "version": 1,
        "state_dir": str(state_dir or (tmp_path / "state")),
        "workspace": {
            "root": str(root / "task"),
            "require_clean_workspace": require_clean,
            "protect_other_git_changes": protect_external,
        },
        "budget": {
            "max_steps": 10,
            "max_tool_calls": 20,
            "max_token_cost": 1,
            "max_wall_time_seconds": 300,
        },
        "task": task,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _sandbox(
    tmp_path: Path,
    *,
    kind: str = "code_understanding",
    require_clean: bool = True,
    protect_external: bool = True,
    deny_write: tuple[str, ...] | None = None,
    allow_write: tuple[str, ...] | None = None,
    deny_read: tuple[str, ...] = (),
    test_command: tuple[str, ...] | None = None,
    fixed_clock: object | None = None,
    fault_injector: object | None = None,
) -> tuple[gh.Sandbox, Path, Path]:
    root, workspace = _init_repo(tmp_path)
    config_path = _config_path(
        tmp_path,
        root,
        kind=kind,
        require_clean=require_clean,
        protect_external=protect_external,
        deny_write=deny_write,
        allow_write=allow_write,
        deny_read=deny_read,
        test_command=test_command,
    )
    config = gh.load_config(config_path)
    sandbox = gh.Sandbox.from_resolved_config(
        config,
        clock=fixed_clock or gh.SystemClock(),
        fault_injector=fault_injector,
    )
    return sandbox, root, workspace


def _command(
    command_id: str,
    code: str,
    *,
    effect: gh.CommandEffectClass = gh.CommandEffectClass.READ_ONLY,
    timeout: float = 5.0,
    stdout_limit: int = 4096,
    stderr_limit: int = 4096,
) -> gh.TrustedCommandSpec:
    return gh.TrustedCommandSpec(
        command_id=command_id,
        argv=("python", "-c", code),
        effect_class=effect,
        timeout_seconds=timeout,
        kill_grace_seconds=0.2,
        stdout_limit_bytes=stdout_limit,
        stderr_limit_bytes=stderr_limit,
    )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_pid_exit(pid: int, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return True
        time.sleep(0.02)
    return not _pid_exists(pid)


def test_public_sandbox_contracts_are_exported() -> None:
    expected = {
        "CancellationToken",
        "CommandEffectClass",
        "CommandPolicy",
        "ExternalProtection",
        "Sandbox",
        "SandboxAccess",
        "SandboxFile",
        "SandboxPath",
        "SandboxProcessResult",
        "SandboxProcessStatus",
        "SandboxViolation",
        "SandboxWriteMode",
        "SandboxWriteResult",
        "TrustedCommandSpec",
        "WorkspaceBaseline",
        "WorkspaceComparison",
        "WorkspaceEntry",
    }
    assert expected.issubset(gh.__all__)
    assert len(gh.__all__) == len(set(gh.__all__))
    assert gh.ErrorCode.SANDBOX_FILE_TOO_LARGE.value == "SANDBOX_FILE_TOO_LARGE"


def test_workspace_and_git_identity(tmp_path: Path, fixed_clock: object) -> None:
    sandbox, root, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)

    assert sandbox.workspace_root == workspace.resolve()
    assert sandbox.git_root == root.resolve()
    assert sandbox.workspace_prefix == "task"
    baseline = sandbox.capture_baseline()
    assert baseline.identity.workspace_prefix == "task"
    assert baseline.workspace_entries == ()
    assert baseline.external_protection == gh.ExternalProtection.ENABLED
    assert baseline.head_oid_or_unborn != "UNBORN"


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "bad\x00path",
        "dir\\file.py",
        r"C:\secret.txt",
        r"\\server\share\secret.txt",
        "~/.ssh/id_rsa",
        "~operator/.ssh/id_rsa",
        "reserved/name.",
        "reserved/CON",
        "x" * 5000,
    ],
)
def test_path_syntax_boundaries(
    candidate: str,
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    with pytest.raises(gh.SandboxViolation) as error:
        sandbox.check_read(candidate)
    assert error.value.code == gh.ErrorCode.SANDBOX_PATH_INVALID


def test_traversal_absolute_and_sibling_escape_are_rejected(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    outside = tmp_path / "outside.txt"
    outside.write_text("must-not-change\n", encoding="utf-8")
    sibling = root / "task-evil"
    sibling.mkdir()

    for candidate in ("../outside.txt", str(outside), "../task-evil", "missing/../../outside.txt"):
        with pytest.raises(gh.SandboxViolation) as error:
            sandbox.check_read(candidate)
        assert error.value.code == gh.ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE
    assert outside.read_text(encoding="utf-8") == "must-not-change\n"


def test_symlink_escape_and_internal_resolution(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    os.symlink(outside, workspace / "escape")
    os.symlink(workspace / "src" / "app.py", workspace / "internal")

    with pytest.raises(gh.SandboxViolation) as error:
        sandbox.check_read("escape")
    assert error.value.code == gh.ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE
    checked = sandbox.check_read("internal")
    assert checked.workspace_relative == "src/app.py"
    assert sandbox.read_file("internal").data == b"VALUE = 1\n"


def test_revalidation_detects_symlink_retarget(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    link = workspace / "link"
    os.symlink(workspace / "src" / "app.py", link)
    checked = sandbox.check_read("link")
    link.unlink()
    os.symlink(tmp_path / "outside.txt", link)

    with pytest.raises(gh.SandboxViolation) as error:
        sandbox.revalidate_path(checked)
    assert error.value.code == gh.ErrorCode.SANDBOX_PATH_CHANGED


def test_platform_sensitive_and_deny_write_rules(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    for path in (
        workspace / ".ENV",
        workspace / "keys" / "tls.key",
        workspace / ".ssh" / "id_rsa",
        workspace / ".aws" / "credentials",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret\n", encoding="utf-8")
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("gitconfig\n", encoding="utf-8")
    (workspace / "node_modules").mkdir()

    for candidate in (".ENV", "keys/tls.key", ".ssh/id_rsa", ".aws/credentials"):
        with pytest.raises(gh.SandboxViolation) as error:
            sandbox.check_read(candidate)
        assert error.value.code == gh.ErrorCode.SANDBOX_PATH_SENSITIVE
    for candidate in (".git/config", "node_modules/pkg.js"):
        with pytest.raises(gh.SandboxViolation) as error:
            sandbox.check_write(candidate)
        assert error.value.code == gh.ErrorCode.SANDBOX_PATH_PROTECTED


def test_task_deny_read_write_and_allow_exception(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(
        tmp_path,
        kind="test_repair",
        deny_read=("private/**",),
        deny_write=("src/**", "tests/**"),
        allow_write=("tests/generated/**",),
        fixed_clock=fixed_clock,
    )
    with pytest.raises(gh.SandboxViolation) as read_error:
        sandbox.check_read("private/note.txt")
    assert read_error.value.code == gh.ErrorCode.SANDBOX_PATH_DENY_READ
    with pytest.raises(gh.SandboxViolation) as write_error:
        sandbox.check_write("tests/existing_test.py")
    assert write_error.value.code == gh.ErrorCode.SANDBOX_PATH_DENY_WRITE
    assert sandbox.check_write("tests/generated/new_test.py").workspace_relative == (
        "tests/generated/new_test.py"
    )


def test_dirty_workspace_and_dirty_baseline_semantics(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    (workspace / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(gh.SandboxViolation) as error:
        sandbox.capture_baseline()
    assert error.value.code == gh.ErrorCode.SANDBOX_WORKSPACE_DIRTY

    second = tmp_path / "second"
    second.mkdir()
    dirty_sandbox, _, dirty_workspace = _sandbox(
        second,
        require_clean=False,
        fixed_clock=fixed_clock,
    )
    (dirty_workspace / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    baseline = dirty_sandbox.capture_baseline()
    assert baseline.workspace_entries
    (dirty_workspace / "src" / "app.py").write_text("VALUE = 4\n", encoding="utf-8")
    comparison = dirty_sandbox.compare_workspace(baseline)
    assert comparison.workspace_changed is True
    assert "WORKSPACE_CONTENT_CHANGED" in comparison.reason_codes


def test_checkpoint_workspace_snapshot_allows_expected_dirty_tree(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    sandbox.capture_baseline()
    target = workspace / "README.md"
    target.write_text("changed after a governed write\n", encoding="utf-8")

    snapshot = sandbox.capture_checkpoint_workspace()
    comparison = sandbox.compare_checkpoint_workspace(snapshot)

    assert snapshot.entries_truncated is False
    assert any(entry.path.endswith("task/README.md") for entry in snapshot.workspace_entries)
    assert comparison.equal


def test_external_dirty_baseline_detects_change(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    (root / "outside.txt").write_text("outside-2\n", encoding="utf-8")
    baseline = sandbox.capture_baseline()
    unchanged = sandbox.compare_workspace(baseline)
    assert unchanged.equal is True
    (root / "outside.txt").write_text("outside-3\n", encoding="utf-8")
    changed = sandbox.compare_workspace(baseline)
    assert changed.external_changed is True
    assert "EXTERNAL_CONTENT_CHANGED" in changed.reason_codes
    assert "outside.txt" in changed.changed_paths


def test_external_new_and_deleted_paths_are_detected(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    baseline = sandbox.capture_baseline()
    (root / "new.txt").write_text("new\n", encoding="utf-8")
    added = sandbox.compare_workspace(baseline)
    assert added.external_changed is True
    assert "new.txt" in added.changed_paths
    (root / "outside.txt").unlink()
    deleted = sandbox.compare_workspace(baseline)
    assert deleted.external_changed is True
    assert "outside.txt" in deleted.changed_paths


def test_exact_state_directory_inside_git_root_is_excluded(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    root, _ = _init_repo(tmp_path)
    state_dir = root / "state"
    config = gh.load_config(_config_path(tmp_path, root, state_dir=state_dir))
    sandbox = gh.Sandbox.from_resolved_config(config, clock=fixed_clock)
    state_dir.mkdir()
    (state_dir / "harness.sqlite3").write_text("state\n", encoding="utf-8")
    baseline = sandbox.capture_baseline()
    assert all(not entry.path.startswith("state/") for entry in baseline.external_entries)
    (state_dir / "harness.sqlite3").write_text("changed\n", encoding="utf-8")
    assert sandbox.compare_workspace(baseline).equal is True


def test_head_and_index_changes_are_identity_changes(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    baseline = sandbox.capture_baseline()
    (workspace / "src" / "app.py").write_text("VALUE = 9\n", encoding="utf-8")
    _git(root, "add", "task/src/app.py")
    index_changed = sandbox.compare_workspace(baseline)
    assert index_changed.identity_changed is True
    _git(root, "commit", "-q", "-m", "change")
    head_changed = sandbox.compare_workspace(baseline)
    assert head_changed.identity_changed is True


def test_ignored_external_file_is_not_claimed_as_protected(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    ignored = root / "ignored.txt"
    ignored.write_text("one\n", encoding="utf-8")
    baseline = sandbox.capture_baseline()
    ignored.write_text("two\n", encoding="utf-8")
    comparison = sandbox.compare_workspace(baseline)
    assert comparison.equal is True
    assert comparison.external_changed is False


def test_disabled_external_protection_is_explicit(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, _ = _sandbox(
        tmp_path,
        protect_external=False,
        fixed_clock=fixed_clock,
    )
    baseline = sandbox.capture_baseline()
    assert baseline.external_protection == gh.ExternalProtection.DISABLED
    assert baseline.external_entries == ()
    (root / "outside.txt").write_text("changed\n", encoding="utf-8")
    comparison = sandbox.compare_workspace(baseline)
    assert comparison.equal is True
    assert comparison.external_changed is None
    assert comparison.external_protection == gh.ExternalProtection.DISABLED


def test_unborn_repository_uses_stable_sentinel(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    root = tmp_path / "unborn"
    workspace = root / "task"
    workspace.mkdir(parents=True)
    _git(root.parent, "init", "-q", str(root))
    config_path = _config_path(tmp_path, root)
    config = gh.load_config(config_path)
    sandbox = gh.Sandbox.from_resolved_config(config, clock=fixed_clock)

    baseline = sandbox.capture_baseline()
    assert baseline.head_oid_or_unborn == "UNBORN"
    assert baseline.index_digest.startswith("sha256:")


def test_symlink_gitlink_and_special_file_baseline_edges(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, workspace = _sandbox(
        tmp_path,
        require_clean=False,
        fixed_clock=fixed_clock,
    )
    outside = tmp_path / "outside-target"
    outside.write_text("outside\n", encoding="utf-8")
    os.symlink(outside, workspace / "linked")
    head = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    _git(
        root,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{head},task/vendor/sub",
    )
    baseline = sandbox.capture_baseline()
    types = {entry.path: entry.file_type for entry in baseline.workspace_entries}
    assert types["task/linked"] == "symlink"
    assert types["task/vendor/sub"] == "gitlink"

    second = tmp_path / "special"
    second.mkdir()
    special_sandbox, _, special_workspace = _sandbox(
        second,
        require_clean=False,
        fixed_clock=fixed_clock,
    )
    os.mkfifo(special_workspace / "pipe")
    with pytest.raises(gh.SandboxViolation) as error:
        special_sandbox.check_read("pipe")
    assert error.value.code == gh.ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE


def test_read_file_is_bounded_and_regular_only(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    read = sandbox.read_file("src/app.py")
    assert read.data == b"VALUE = 1\n"
    assert read.byte_count == len(read.data)
    assert read.content_digest.startswith("sha256:")

    large = workspace / "large.bin"
    with large.open("wb") as handle:
        handle.truncate(gh.sandbox.MAX_IO_BYTES + 1)
    with pytest.raises(gh.SandboxViolation) as too_large:
        sandbox.read_file("large.bin")
    assert too_large.value.code == gh.ErrorCode.SANDBOX_FILE_TOO_LARGE
    with pytest.raises(gh.SandboxViolation) as directory:
        sandbox.read_file("src")
    assert directory.value.code == gh.ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE


def test_atomic_write_create_replace_and_expected_digest(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    created = sandbox.write_file_atomic(
        "artifacts/deep/report.md",
        b"first\n",
        mode=gh.SandboxWriteMode.CREATE,
        create_parents=True,
    )
    target = workspace / "artifacts" / "deep" / "report.md"
    assert created.created is True
    assert target.read_bytes() == b"first\n"

    old_umask = os.umask(0)
    os.umask(old_umask)
    assert stat.S_IMODE(target.stat().st_mode) == (0o666 & ~old_umask)
    os.chmod(target, 0o640)
    replaced = sandbox.write_file_atomic(
        "artifacts/deep/report.md",
        b"second\n",
        mode=gh.SandboxWriteMode.REPLACE,
        expected_content_digest=created.content_digest,
    )
    assert replaced.previous_content_digest == created.content_digest
    assert stat.S_IMODE(target.stat().st_mode) == 0o640

    with pytest.raises(gh.SandboxViolation) as conflict:
        sandbox.write_file_atomic(
            "artifacts/deep/report.md",
            b"must-not-land\n",
            mode=gh.SandboxWriteMode.REPLACE,
            expected_content_digest=created.content_digest,
        )
    assert conflict.value.code == gh.ErrorCode.SANDBOX_WRITE_CONFLICT
    assert target.read_bytes() == b"second\n"


def test_create_requires_absent_target_and_parent_policy(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    with pytest.raises(gh.SandboxViolation) as parent_error:
        sandbox.write_file_atomic(
            "artifacts/missing/report.md",
            b"data",
            mode=gh.SandboxWriteMode.CREATE,
        )
    assert parent_error.value.code == gh.ErrorCode.SANDBOX_IO_ERROR
    assert not (workspace / "artifacts").exists()

    sandbox.write_file_atomic(
        "artifacts/missing/report.md",
        b"data",
        mode=gh.SandboxWriteMode.CREATE,
        create_parents=True,
    )
    with pytest.raises(gh.SandboxViolation) as existing:
        sandbox.write_file_atomic(
            "artifacts/missing/report.md",
            b"other",
            mode=gh.SandboxWriteMode.CREATE,
            create_parents=True,
        )
    assert existing.value.code == gh.ErrorCode.SANDBOX_WRITE_CONFLICT


def test_create_parents_obeys_deny_and_allow_exception(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(
        tmp_path,
        kind="test_repair",
        deny_write=("tests/**",),
        allow_write=("tests/generated/**",),
        fixed_clock=fixed_clock,
    )
    created = sandbox.write_file_atomic(
        "tests/generated/deep/new_test.py",
        b"def test_new():\n    assert True\n",
        mode=gh.SandboxWriteMode.CREATE,
        create_parents=True,
    )
    assert created.created is True
    assert (workspace / "tests" / "generated" / "deep" / "new_test.py").is_file()


def test_atomic_write_injected_failure_leaves_no_partial_target(
    tmp_path: Path,
    fixed_clock: object,
    fault_injector: object,
) -> None:
    sandbox, _, workspace = _sandbox(
        tmp_path,
        fixed_clock=fixed_clock,
        fault_injector=fault_injector,
    )
    created = sandbox.write_file_atomic(
        "artifacts/report.md",
        b"before\n",
        mode=gh.SandboxWriteMode.CREATE,
        create_parents=True,
    )
    fault_injector.arm("sandbox.write.before_replace")
    with pytest.raises(RuntimeError):
        sandbox.write_file_atomic(
            "artifacts/report.md",
            b"after\n",
            mode=gh.SandboxWriteMode.REPLACE,
            expected_content_digest=created.content_digest,
        )
    target = workspace / "artifacts" / "report.md"
    assert target.read_bytes() == b"before\n"
    assert not list((workspace / "artifacts").glob(".report.md.*.tmp"))

    with pytest.raises(RuntimeError):
        sandbox.write_file_atomic(
            "artifacts/failing/deep/new.md",
            b"never\n",
            mode=gh.SandboxWriteMode.CREATE,
            create_parents=True,
        )
    assert not (workspace / "artifacts" / "failing").exists()


def test_task_repair_acceptance_command_is_registered(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(
        tmp_path,
        kind="test_repair",
        test_command=("python", "-c", "print('acceptance-ok')"),
        fixed_clock=fixed_clock,
    )
    result = sandbox.execute("acceptance_test")
    assert result.status == gh.SandboxProcessStatus.SUCCEEDED
    assert result.stdout == "acceptance-ok\n"


def test_command_registry_rejects_untrusted_executables(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    with pytest.raises(gh.SandboxViolation) as shell:
        sandbox.register_trusted_command(
            gh.TrustedCommandSpec(
                command_id="shell",
                argv=("/bin/sh", "-c", "touch marker"),
                effect_class=gh.CommandEffectClass.NON_IDEMPOTENT,
            )
        )
    assert shell.value.code == gh.ErrorCode.SANDBOX_SHELL_DENIED
    with pytest.raises(gh.SandboxViolation) as executable:
        sandbox.register_trusted_command(
            gh.TrustedCommandSpec(
                command_id="cat",
                argv=("/bin/cat", "README.md"),
                effect_class=gh.CommandEffectClass.READ_ONLY,
            )
        )
    assert executable.value.code == gh.ErrorCode.SANDBOX_EXECUTABLE_DENIED
    with pytest.raises(gh.SandboxViolation) as unknown:
        sandbox.execute("not_registered")
    assert unknown.value.code == gh.ErrorCode.SANDBOX_COMMAND_UNKNOWN


def test_fixed_cwd_and_minimal_environment(
    tmp_path: Path,
    fixed_clock: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    monkeypatch.setenv("HOST_TOKEN", "must-not-leak")
    code = (
        "import json, os; "
        "print(json.dumps({'cwd': os.getcwd(), 'keys': sorted(os.environ), "
        "'home': os.environ['HOME'], 'path': os.environ['PATH']}))"
    )
    sandbox.register_trusted_command(_command("inspect_env", code))
    result = sandbox.execute("inspect_env")
    assert result.status == gh.SandboxProcessStatus.SUCCEEDED
    payload = json.loads(result.stdout)
    assert payload["cwd"] == str(workspace)
    assert "HOST_TOKEN" not in payload["keys"]
    assert set(payload["keys"]) == {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "HOME",
        "LANG",
        "LC_ALL",
        "PAGER",
        "PATH",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONHASHSEED",
        "PYTHONNOUSERSITE",
        "TMPDIR",
        "TZ",
    }
    assert Path(payload["home"]).resolve().is_relative_to(workspace.resolve()) is False
    assert "must-not-leak" not in result.stdout


def test_shell_metacharacters_remain_literal_arguments(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    sandbox.register_trusted_command(
        gh.TrustedCommandSpec(
            command_id="literal",
            argv=("python", "-c", "import sys; print(sys.argv[1])", "; touch marker"),
            effect_class=gh.CommandEffectClass.READ_ONLY,
        )
    )
    result = sandbox.execute("literal")
    assert result.status == gh.SandboxProcessStatus.SUCCEEDED
    assert result.stdout == "; touch marker\n"
    assert not (workspace / "marker").exists()


def test_stdout_and_stderr_are_bounded(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    code = "import sys; sys.stdout.write('o'*20000); sys.stderr.write('e'*20000)"
    sandbox.register_trusted_command(
        _command(
            "noisy",
            code,
            stdout_limit=1024,
            stderr_limit=1024,
        )
    )
    result = sandbox.execute("noisy")
    assert result.status == gh.SandboxProcessStatus.SUCCEEDED
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert "truncated" in result.stdout
    assert "truncated" in result.stderr
    assert len(result.stdout.encode()) < 1200
    assert len(result.stderr.encode()) < 1200


def test_timeout_kills_process_group(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    code = (
        "import pathlib, subprocess, sys, time; "
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "pathlib.Path('child.pid').write_text(str(p.pid)); "
        "time.sleep(30)"
    )
    sandbox.register_trusted_command(
        _command(
            "timeout",
            code,
            effect=gh.CommandEffectClass.NON_IDEMPOTENT,
            timeout=0.2,
        )
    )
    result = sandbox.execute("timeout")
    assert result.timed_out is True
    assert result.process_group_terminated is True
    assert result.status == gh.SandboxProcessStatus.UNKNOWN_RESULT
    child_pid = int((workspace / "child.pid").read_text(encoding="utf-8"))
    assert _wait_for_pid_exit(child_pid)


def test_cancellation_kills_process_group(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    sandbox.register_trusted_command(
        _command(
            "cancel",
            "import time; time.sleep(30)",
            effect=gh.CommandEffectClass.NON_IDEMPOTENT,
            timeout=30,
        )
    )
    token = threading.Event()
    timer = threading.Timer(0.1, token.set)
    timer.start()
    try:
        result = sandbox.execute("cancel", cancellation=token)
    finally:
        timer.cancel()
    assert result.cancelled is True
    assert result.process_group_terminated is True
    assert result.status == gh.SandboxProcessStatus.UNKNOWN_RESULT

    pre_cancelled = threading.Event()
    pre_cancelled.set()
    assert sandbox.execute("cancel", cancellation=pre_cancelled).cancelled is True


def test_configured_acceptance_test_command_is_available(tmp_path: Path) -> None:
    sandbox, _, workspace = _sandbox(
        tmp_path,
        kind="test_repair",
        test_command=("python", "-c", "print('acceptance-ok')"),
    )

    result = sandbox.execute("acceptance_test")
    assert result.status == gh.SandboxProcessStatus.SUCCEEDED
    assert result.exit_code == 0
    assert result.stdout.strip() == "acceptance-ok"
    assert result.process_group_terminated is True


def test_start_failure_is_structured(
    tmp_path: Path,
    fixed_clock: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    sandbox.register_trusted_command(_command("fail_start", "print('never')"))

    def fail_popen(*args: object, **kwargs: object) -> None:
        raise OSError("injected")

    monkeypatch.setattr(sandbox_module.subprocess, "Popen", fail_popen)
    result = sandbox.execute("fail_start")
    assert result.status == gh.SandboxProcessStatus.START_FAILED
    assert result.result_code == gh.ErrorCode.SANDBOX_COMMAND_START_FAILED


def test_unsupported_platform_fails_before_popen(
    tmp_path: Path,
    fixed_clock: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _init_repo(tmp_path)
    config = gh.load_config(_config_path(tmp_path, root))
    monkeypatch.setattr(sandbox_module, "_process_platform_supported", lambda: False)
    with pytest.raises(gh.SandboxViolation) as error:
        gh.Sandbox.from_resolved_config(config, clock=fixed_clock)
    assert error.value.code == gh.ErrorCode.SANDBOX_UNSUPPORTED_PLATFORM


def test_non_git_workspace_fails_closed(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    root = tmp_path / "repo"
    workspace = root / "task"
    workspace.mkdir(parents=True)
    config = gh.load_config(_config_path(tmp_path, root))
    with pytest.raises(gh.SandboxViolation) as error:
        gh.Sandbox.from_resolved_config(config, clock=fixed_clock)
    assert error.value.code == gh.ErrorCode.SANDBOX_GIT_ROOT_INVALID


def test_malformed_git_status_fails_closed() -> None:
    with pytest.raises(gh.SandboxViolation) as error:
        _parse_git_status(b"x malformed-record\x00")
    assert error.value.code == gh.ErrorCode.SANDBOX_BASELINE_INVALID


def test_sandbox_never_auto_retries(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    code = "from pathlib import Path; Path('count.txt').write_text('one'); raise SystemExit(3)"
    sandbox.register_trusted_command(
        _command(
            "no_retry",
            code,
            effect=gh.CommandEffectClass.NON_IDEMPOTENT,
        )
    )
    result = sandbox.execute("no_retry")
    assert result.status == gh.SandboxProcessStatus.FAILED
    assert (workspace / "count.txt").read_text(encoding="utf-8") == "one"


@pytest.mark.parametrize(
    ("returncode", "timed_out", "cancelled", "start_failed", "reader_failed", "effect"),
    [
        (None, False, False, True, False, gh.CommandEffectClass.READ_ONLY),
        (0, False, False, False, False, gh.CommandEffectClass.READ_ONLY),
        (2, False, False, False, False, gh.CommandEffectClass.READ_ONLY),
        (None, True, False, False, False, gh.CommandEffectClass.READ_ONLY),
        (None, True, False, False, False, gh.CommandEffectClass.NON_IDEMPOTENT),
        (None, False, True, False, False, gh.CommandEffectClass.NON_IDEMPOTENT),
        (0, False, False, False, True, gh.CommandEffectClass.READ_ONLY),
    ],
)
def test_process_result_classification_matrix(
    returncode: int | None,
    timed_out: bool,
    cancelled: bool,
    start_failed: bool,
    reader_failed: bool,
    effect: gh.CommandEffectClass,
) -> None:
    status, code = _classify_process_result(
        returncode=returncode,
        timed_out=timed_out,
        cancelled=cancelled,
        start_failed=start_failed,
        reader_failed=reader_failed,
        effect_class=effect,
        process_group_terminated=True,
    )
    if start_failed:
        assert (status, code) == (
            gh.SandboxProcessStatus.START_FAILED,
            gh.ErrorCode.SANDBOX_COMMAND_START_FAILED,
        )
    elif reader_failed:
        assert (status, code) == (
            gh.SandboxProcessStatus.UNKNOWN_RESULT,
            gh.ErrorCode.SANDBOX_RESULT_UNKNOWN,
        )
    elif timed_out and effect == gh.CommandEffectClass.NON_IDEMPOTENT:
        assert (status, code) == (
            gh.SandboxProcessStatus.UNKNOWN_RESULT,
            gh.ErrorCode.SANDBOX_COMMAND_TIMEOUT,
        )
    elif timed_out:
        assert (status, code) == (
            gh.SandboxProcessStatus.TIMED_OUT,
            gh.ErrorCode.SANDBOX_COMMAND_TIMEOUT,
        )
    elif cancelled:
        assert status == gh.SandboxProcessStatus.UNKNOWN_RESULT
    elif returncode == 0:
        assert (status, code) == (gh.SandboxProcessStatus.SUCCEEDED, None)
    else:
        assert (status, code) == (gh.SandboxProcessStatus.FAILED, None)


def test_rejection_has_no_external_side_effect(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    target = root / "outside.txt"
    before = target.read_bytes()
    with pytest.raises(gh.SandboxViolation):
        sandbox.write_file_atomic(
            "../outside.txt",
            b"changed",
            mode=gh.SandboxWriteMode.CREATE,
        )
    assert target.read_bytes() == before
    with pytest.raises(gh.SandboxViolation):
        sandbox.execute("outside-command")
    assert not (workspace / "marker").exists()


def test_feishu_s1_s4_partial_mapping(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    with pytest.raises(gh.SandboxViolation) as s1:
        sandbox.check_read("artifacts/../../.env")
    assert s1.value.code in {
        gh.ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE,
        gh.ErrorCode.SANDBOX_PATH_SENSITIVE,
    }

    outside = root / "outside.txt"
    os.symlink(outside, workspace / "escape")
    with pytest.raises(gh.SandboxViolation) as s2:
        sandbox.check_read("escape")
    assert s2.value.code == gh.ErrorCode.SANDBOX_PATH_OUT_OF_WORKSPACE

    with pytest.raises(gh.SandboxViolation) as s3:
        sandbox.execute("not-config-command")
    assert s3.value.code == gh.ErrorCode.SANDBOX_COMMAND_UNKNOWN

    sandbox.register_trusted_command(
        _command(
            "s4",
            "import time; time.sleep(30)",
            effect=gh.CommandEffectClass.NON_IDEMPOTENT,
            timeout=0.15,
        )
    )
    s4 = sandbox.execute("s4")
    assert s4.timed_out is True
    assert s4.process_group_terminated is True


def test_list_directory_is_safe_sorted_and_bounded(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, root, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    (workspace / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
    (workspace / "z.txt").write_text("z\n", encoding="utf-8")
    (workspace / "a.txt").write_text("a\n", encoding="utf-8")
    os.symlink(root / "outside.txt", workspace / "outside-link")
    os.symlink(workspace / "src", workspace / "src-link")

    listing = sandbox.list_directory(".", max_entries=20)
    names = [entry.name for entry in listing.entries]

    directory_names = [
        entry.name for entry in listing.entries if entry.entry_type == gh.SandboxEntryType.DIRECTORY
    ]
    file_names = [
        entry.name for entry in listing.entries if entry.entry_type != gh.SandboxEntryType.DIRECTORY
    ]
    assert directory_names == sorted(directory_names)
    assert file_names == sorted(file_names)
    assert [
        entry.entry_type == gh.SandboxEntryType.DIRECTORY for entry in listing.entries
    ] == sorted(
        (entry.entry_type == gh.SandboxEntryType.DIRECTORY for entry in listing.entries),
        reverse=True,
    )
    assert ".env" not in names
    assert "outside-link" not in names
    assert "src-link" in names
    assert next(entry for entry in listing.entries if entry.name == "src-link").entry_type == (
        gh.SandboxEntryType.SYMLINK
    )
    assert listing.omitted_count == 2

    bounded = sandbox.list_directory(".", max_entries=2)
    assert bounded.returned_count == 2
    assert bounded.truncated is True


def test_walk_readable_files_reports_limit_reasons(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, workspace = _sandbox(tmp_path, fixed_clock=fixed_clock)
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")
    (workspace / "src" / "extra.py").write_text("VALUE = 2\n", encoding="utf-8")

    walked = sandbox.walk_readable_files(".", max_files=10, max_bytes=1024, max_entries=100)
    paths = [path.workspace_relative for path in walked.files]
    assert "node_modules/ignored.js" not in paths
    assert paths == sorted(paths)

    files_limited = sandbox.walk_readable_files(".", max_files=1, max_bytes=1024, max_entries=100)
    assert files_limited.truncated is True
    assert files_limited.limit_reason == gh.SandboxWalkLimitReason.FILES

    bytes_limited = sandbox.walk_readable_files(".", max_files=10, max_bytes=1, max_entries=100)
    assert bytes_limited.truncated is True
    assert bytes_limited.limit_reason == gh.SandboxWalkLimitReason.BYTES

    entries_limited = sandbox.walk_readable_files(
        ".",
        max_files=10,
        max_bytes=1024,
        max_entries=1,
    )
    assert entries_limited.truncated is True
    assert entries_limited.limit_reason == gh.SandboxWalkLimitReason.ENTRIES


def test_pathspec_execution_rejects_untrusted_paths(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    with pytest.raises(ValueError):
        gh.TrustedCommandSpec(
            command_id="legacy_pathspec",
            argv=("git", "diff", "--"),
            effect_class=gh.CommandEffectClass.READ_ONLY,
            schema_version=1,
            allow_pathspecs=True,
        )
    with pytest.raises(ValueError):
        gh.TrustedCommandSpec(
            command_id="missing_separator",
            argv=("git", "diff"),
            effect_class=gh.CommandEffectClass.READ_ONLY,
            allow_pathspecs=True,
        )
    sandbox.register_trusted_command(
        gh.TrustedCommandSpec(
            command_id="inspect_args",
            argv=(
                "python",
                "-c",
                "import json,sys; print(json.dumps(sys.argv[1:]))",
                "--",
            ),
            effect_class=gh.CommandEffectClass.READ_ONLY,
        )
    )
    read_path = sandbox.check_read("README.md")
    with pytest.raises(gh.SandboxViolation) as denied:
        sandbox.execute("inspect_args", pathspecs=(read_path,))
    assert denied.value.code == gh.ErrorCode.SANDBOX_COMMAND_INVALID

    sandbox.register_trusted_command(
        gh.TrustedCommandSpec(
            command_id="inspect_args_allowed",
            argv=(
                "python",
                "-c",
                "import json,sys; print(json.dumps(sys.argv[1:]))",
                "--",
            ),
            effect_class=gh.CommandEffectClass.READ_ONLY,
            allow_pathspecs=True,
        )
    )
    write_path = sandbox.check_write("README.md")
    with pytest.raises(gh.SandboxViolation):
        sandbox.execute("inspect_args_allowed", pathspecs=(write_path,))
    with pytest.raises(gh.SandboxViolation):
        sandbox.execute("inspect_args_allowed", pathspecs=(read_path, read_path))


def test_pathspec_execution_uses_validated_read_paths(
    tmp_path: Path,
    fixed_clock: object,
) -> None:
    sandbox, _, _ = _sandbox(tmp_path, fixed_clock=fixed_clock)
    sandbox.register_trusted_command(
        gh.TrustedCommandSpec(
            command_id="inspect_args",
            argv=(
                "python",
                "-c",
                "import json,sys; print(json.dumps(sys.argv[1:]))",
                "--",
            ),
            effect_class=gh.CommandEffectClass.READ_ONLY,
            allow_pathspecs=True,
        )
    )
    read_path = sandbox.check_read("src/app.py")
    result = sandbox.execute("inspect_args", pathspecs=(read_path,))
    assert result.status == gh.SandboxProcessStatus.SUCCEEDED
    assert json.loads(result.stdout) == ["--", "src/app.py"]

    legacy = sandbox.execute("inspect_args")
    assert legacy.status == gh.SandboxProcessStatus.SUCCEEDED
    assert json.loads(legacy.stdout) == ["--"]
