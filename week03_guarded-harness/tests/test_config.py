"""Strict configuration, path boundary, and fingerprint contract tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from guarded_harness import (
    ConfigurationError,
    ErrorCode,
    config_fingerprint,
    load_config,
)
from guarded_harness.config import MAX_CONFIG_BYTES

FIXTURES = Path(__file__).parent / "fixtures" / "config"
PROJECT_ROOT = Path(__file__).parents[1]


def _payload(
    workspace_root: str = "workspace",
    *,
    state_dir: str = "state",
    description: str = "Explain the login flow.",
) -> dict[str, Any]:
    return {
        "version": 1,
        "state_dir": state_dir,
        "workspace": {
            "root": workspace_root,
            "require_clean_workspace": True,
            "protect_other_git_changes": True,
        },
        "budget": {
            "max_steps": 12,
            "max_tool_calls": 40,
            "max_token_cost": 0.25,
            "max_wall_time_seconds": 1800,
        },
        "task": {
            "id": "understand_login",
            "kind": "code_understanding",
            "description": description,
            "allowed_tools": ["write_file", "read_file"],
            "paths": {
                "deny_read": ["**/.env", "**/*.pem"],
                "deny_write": ["src/**", "tests/**"],
                "allow_write": ["artifacts/report.md"],
            },
            "acceptance": {
                "strategy": "code_understanding",
                "deliverables": ["artifacts/report.md"],
            },
            "approval": {
                "required_tools": ["write_file"],
                "approver_roles": ["local_operator"],
                "ttl_seconds": 900,
                "channel": "local_cli",
            },
        },
    }


def _write_yaml(
    path: Path,
    payload: dict[str, Any],
    *,
    text: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if text is None:
        path.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        path.write_text(text, encoding="utf-8")
    return path


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return workspace


def test_load_task_fixtures() -> None:
    code = load_config(FIXTURES / "code-understanding.yaml")
    repair = load_config(FIXTURES / "test-repair.yaml")

    assert code.workspace_root == str((FIXTURES).resolve())
    assert code.deliverables == ("artifacts/code-understanding.md",)
    assert code.fingerprint == config_fingerprint(code)
    assert repair.fingerprint == config_fingerprint(repair)
    assert repair.config.task.kind.value == "test_repair"
    assert repair.config.task.acceptance.test_command == ("python", "-m", "pytest", "-q")


def test_demo_task_configs_load_with_real_model_profiles() -> None:
    understanding = load_config(PROJECT_ROOT / "demo" / "config" / "1_login_demo.yaml")
    repair = load_config(PROJECT_ROOT / "demo" / "config" / "2_bug_fix.yaml")

    assert understanding.config.version == 2
    assert understanding.config.model is not None
    assert understanding.config.model.credential_ref == "OPENAI_API_KEY"
    assert understanding.config.model.tool_choice == "auto"
    assert understanding.config.task.kind.value == "code_understanding"
    assert understanding.deliverables == ("artifacts/login-flow.md",)

    assert repair.config.version == 2
    assert repair.config.model is not None
    assert repair.config.model.tool_choice == "auto"
    assert repair.config.task.kind.value == "test_repair"
    assert repair.config.task.acceptance.report_format == "node_test_junit_xml"
    assert repair.config.task.approval.ttl_seconds > 600
    assert "run_test" in repair.config.task.allowed_tools


@pytest.mark.parametrize(
    ("fixture", "code"),
    [
        ("invalid/unknown-field.yaml", ErrorCode.CONFIG_SCHEMA_INVALID),
        ("invalid/invalid-budget.yaml", ErrorCode.CONFIG_SCHEMA_INVALID),
        ("invalid/path-escape.yaml", ErrorCode.CONFIG_PATH_ESCAPE),
        ("invalid/duplicate-deliverable.yaml", ErrorCode.CONFIG_DUPLICATE_DELIVERABLE),
    ],
)
def test_invalid_fixtures_fail_closed(fixture: str, code: ErrorCode) -> None:
    with pytest.raises(ConfigurationError) as error:
        load_config(FIXTURES / fixture)
    assert error.value.code == code
    assert error.value.issues


def test_yaml_strict_errors(tmp_path: Path) -> None:
    _workspace(tmp_path)

    empty = _write_yaml(tmp_path / "empty.yaml", {}, text="")
    with pytest.raises(ConfigurationError) as empty_error:
        load_config(empty)
    assert empty_error.value.code == ErrorCode.CONFIG_YAML_INVALID

    multiple = _write_yaml(
        tmp_path / "multiple.yaml",
        {},
        text="---\nversion: 1\n---\nversion: 1\n",
    )
    with pytest.raises(ConfigurationError) as multi_error:
        load_config(multiple)
    assert multi_error.value.code == ErrorCode.CONFIG_MULTIPLE_DOCUMENTS

    root_list = _write_yaml(tmp_path / "root-list.yaml", {}, text="- version: 1\n")
    with pytest.raises(ConfigurationError) as root_error:
        load_config(root_list)
    assert root_error.value.code == ErrorCode.CONFIG_ROOT_NOT_MAPPING

    duplicate = _write_yaml(
        tmp_path / "duplicate.yaml",
        {},
        text="version: 1\nversion: 1\n",
    )
    with pytest.raises(ConfigurationError) as duplicate_error:
        load_config(duplicate)
    assert duplicate_error.value.code == ErrorCode.CONFIG_DUPLICATE_KEY

    unsafe = _write_yaml(
        tmp_path / "unsafe.yaml",
        {},
        text="!!python/object:os.system {}\n",
    )
    with pytest.raises(ConfigurationError) as unsafe_error:
        load_config(unsafe)
    assert unsafe_error.value.code == ErrorCode.CONFIG_YAML_INVALID

    too_large = tmp_path / "too-large.yaml"
    too_large.write_bytes(b"x" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigurationError) as large_error:
        load_config(too_large)
    assert large_error.value.code == ErrorCode.CONFIG_TOO_LARGE


def test_missing_config_and_non_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as missing:
        load_config(tmp_path / "missing.yaml")
    assert missing.value.code == ErrorCode.CONFIG_FILE_NOT_FOUND

    directory = tmp_path / "config-dir"
    directory.mkdir()
    with pytest.raises(ConfigurationError) as non_file:
        load_config(directory)
    assert non_file.value.code == ErrorCode.CONFIG_IO_ERROR


def test_cross_field_semantics(tmp_path: Path) -> None:
    _workspace(tmp_path)
    mismatch = _payload()
    mismatch["task"]["acceptance"]["strategy"] = "test_repair"
    mismatch["task"]["acceptance"]["test_command"] = ["python", "-m", "pytest"]
    mismatch_path = _write_yaml(tmp_path / "mismatch.yaml", mismatch)
    with pytest.raises(ConfigurationError) as mismatch_error:
        load_config(mismatch_path)
    assert mismatch_error.value.code == ErrorCode.CONFIG_SCHEMA_INVALID

    unapproved_tool = _payload()
    unapproved_tool["task"]["approval"]["required_tools"] = ["apply_patch"]
    unapproved_path = _write_yaml(tmp_path / "unapproved.yaml", unapproved_tool)
    with pytest.raises(ConfigurationError) as approval_error:
        load_config(unapproved_path)
    assert approval_error.value.code == ErrorCode.CONFIG_APPROVAL_INVALID

    duplicate_tool = _payload()
    duplicate_tool["task"]["allowed_tools"] = ["read_file", "read_file"]
    duplicate_tool_path = _write_yaml(tmp_path / "duplicate-tool.yaml", duplicate_tool)
    with pytest.raises(ConfigurationError) as duplicate_tool_error:
        load_config(duplicate_tool_path)
    assert duplicate_tool_error.value.code == ErrorCode.CONFIG_SCHEMA_INVALID


def test_test_repair_report_contract_is_strict_and_fingerprinted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    payload = _payload(str(workspace))
    payload["task"]["id"] = "repair_fixture"
    payload["task"]["kind"] = "test_repair"
    payload["task"]["allowed_tools"] = ["run_test"]
    payload["task"]["paths"] = {
        "deny_write": ["tests/**"],
        "allow_write": ["src/**", "tests/generated/**"],
    }
    payload["task"]["acceptance"] = {
        "strategy": "test_repair",
        "test_command": ["python", "-m", "pytest", "-q"],
        "baseline_required": True,
        "report_format": "pytest_junit_xml",
    }
    payload["task"]["approval"] = {
        "required_tools": [],
        "approver_roles": [],
        "ttl_seconds": 900,
        "channel": "local_cli",
    }
    valid = _write_yaml(tmp_path / "valid-repair.yaml", payload)
    loaded = load_config(valid)
    assert loaded.fingerprint == config_fingerprint(loaded)

    missing_format = _payload(str(workspace))
    missing_format["task"]["id"] = "missing_report"
    missing_format["task"]["kind"] = "test_repair"
    missing_format["task"]["allowed_tools"] = ["run_test"]
    missing_format["task"]["acceptance"] = {
        "strategy": "test_repair",
        "test_command": ["python", "-m", "pytest", "-q"],
        "baseline_required": True,
    }
    missing_format["task"]["approval"] = {
        "required_tools": [],
        "approver_roles": [],
        "ttl_seconds": 900,
        "channel": "local_cli",
    }
    with pytest.raises(ConfigurationError) as missing:
        load_config(_write_yaml(tmp_path / "missing-format.yaml", missing_format))
    assert missing.value.code == ErrorCode.CONFIG_SCHEMA_INVALID

    overridden = _payload(str(workspace))
    overridden["task"]["id"] = "report_override"
    overridden["task"]["kind"] = "test_repair"
    overridden["task"]["allowed_tools"] = ["run_test"]
    overridden["task"]["acceptance"] = {
        "strategy": "test_repair",
        "test_command": [
            "python",
            "-m",
            "pytest",
            "--junitxml=model.xml",
        ],
        "baseline_required": True,
        "report_format": "pytest_junit_xml",
    }
    overridden["task"]["approval"] = {
        "required_tools": [],
        "approver_roles": [],
        "ttl_seconds": 900,
        "channel": "local_cli",
    }
    with pytest.raises(ConfigurationError) as override:
        load_config(_write_yaml(tmp_path / "override.yaml", overridden))
    assert override.value.code == ErrorCode.CONFIG_SCHEMA_INVALID


def test_node_test_repair_report_contract_is_strict(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    payload = _payload(str(workspace))
    payload["task"]["id"] = "repair_node_fixture"
    payload["task"]["kind"] = "test_repair"
    payload["task"]["allowed_tools"] = ["run_test"]
    payload["task"]["paths"] = {
        "deny_write": ["tests/**"],
        "allow_write": ["src/**", "tests/generated/**"],
    }
    payload["task"]["acceptance"] = {
        "strategy": "test_repair",
        "test_command": [
            "node",
            "--import",
            "tsx",
            "--test",
            "tests/**/*.test.ts",
        ],
        "baseline_required": True,
        "report_format": "node_test_junit_xml",
    }
    payload["task"]["approval"] = {
        "required_tools": [],
        "approver_roles": [],
        "ttl_seconds": 900,
        "channel": "local_cli",
    }
    loaded = load_config(_write_yaml(tmp_path / "valid-node-repair.yaml", payload))
    acceptance = loaded.config.task.acceptance
    assert acceptance.report_format == "node_test_junit_xml"

    mismatched = _payload(str(workspace))
    mismatched["task"]["id"] = "mismatched_node_fixture"
    mismatched["task"]["kind"] = "test_repair"
    mismatched["task"]["allowed_tools"] = ["run_test"]
    mismatched["task"]["acceptance"] = {
        "strategy": "test_repair",
        "test_command": ["python", "-m", "pytest", "-q"],
        "baseline_required": True,
        "report_format": "node_test_junit_xml",
    }
    mismatched["task"]["approval"] = {
        "required_tools": [],
        "approver_roles": [],
        "ttl_seconds": 900,
        "channel": "local_cli",
    }
    with pytest.raises(ConfigurationError) as wrong_command:
        load_config(_write_yaml(tmp_path / "mismatched-node-repair.yaml", mismatched))
    assert wrong_command.value.code == ErrorCode.CONFIG_SCHEMA_INVALID

    overridden = dict(payload)
    overridden["task"] = dict(payload["task"])
    overridden["task"]["id"] = "overridden_node_fixture"
    overridden["task"]["acceptance"] = {
        "strategy": "test_repair",
        "test_command": [
            "node",
            "--import",
            "tsx",
            "--test",
            "--test-reporter=junit",
            "tests/**/*.test.ts",
        ],
        "baseline_required": True,
        "report_format": "node_test_junit_xml",
    }
    with pytest.raises(ConfigurationError) as reporter_override:
        load_config(_write_yaml(tmp_path / "overridden-node-repair.yaml", overridden))
    assert reporter_override.value.code == ErrorCode.CONFIG_SCHEMA_INVALID


def test_approval_roles_and_ttl_are_required_and_fingerprinted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    missing_roles = _payload(str(workspace))
    missing_roles["task"]["approval"]["approver_roles"] = []
    missing_config = _write_yaml(tmp_path / "missing-roles.yaml", missing_roles)
    with pytest.raises(ConfigurationError) as missing_error:
        load_config(missing_config)
    assert missing_error.value.code == ErrorCode.CONFIG_APPROVAL_INVALID

    invalid_ttl = _payload(str(workspace))
    invalid_ttl["task"]["approval"]["ttl_seconds"] = 0
    ttl_config = _write_yaml(tmp_path / "invalid-ttl.yaml", invalid_ttl)
    with pytest.raises(ConfigurationError) as ttl_error:
        load_config(ttl_config)
    assert ttl_error.value.code == ErrorCode.CONFIG_SCHEMA_INVALID

    first_payload = _payload(str(workspace))
    first = _write_yaml(tmp_path / "approval-first.yaml", first_payload)
    second_payload = _payload(str(workspace))
    second_payload["task"]["approval"]["ttl_seconds"] = 600
    second = _write_yaml(tmp_path / "approval-second.yaml", second_payload)
    assert load_config(first).fingerprint != load_config(second).fingerprint


def test_absolute_workspace_and_state_paths_are_supported(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    state = tmp_path / "external-state"
    config = _write_yaml(
        tmp_path / "absolute.yaml",
        _payload(str(workspace), state_dir=str(state)),
    )
    resolved = load_config(config)
    assert resolved.workspace_root == str(workspace.resolve())
    assert resolved.state_dir == str(state.resolve(strict=False))
    assert not state.exists()


def test_workspace_root_must_be_existing_directory(tmp_path: Path) -> None:
    missing = _write_yaml(tmp_path / "missing-root.yaml", _payload("missing"))
    with pytest.raises(ConfigurationError) as missing_error:
        load_config(missing)
    assert missing_error.value.code == ErrorCode.CONFIG_PATH_INVALID

    file_root = tmp_path / "workspace-file"
    file_root.write_text("not a directory", encoding="utf-8")
    file_config = _write_yaml(tmp_path / "file-root.yaml", _payload(str(file_root)))
    with pytest.raises(ConfigurationError) as file_error:
        load_config(file_config)
    assert file_error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_state_dir_rejects_existing_file(tmp_path: Path) -> None:
    _workspace(tmp_path)
    state_file = tmp_path / "state-file"
    state_file.write_text("not a directory", encoding="utf-8")
    config = _write_yaml(
        tmp_path / "state-file.yaml",
        _payload(state_dir=str(state_file)),
    )
    with pytest.raises(ConfigurationError) as error:
        load_config(config)
    assert error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_state_dir_is_required_and_outside_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    missing_payload = _payload(str(workspace))
    missing_payload.pop("state_dir")
    missing_config = _write_yaml(tmp_path / "missing-state.yaml", missing_payload)
    with pytest.raises(ConfigurationError) as missing_error:
        load_config(missing_config)
    assert missing_error.value.code == ErrorCode.CONFIG_SCHEMA_INVALID

    inside_workspace = workspace / "state"
    inside_config = _write_yaml(
        tmp_path / "inside-workspace.yaml",
        _payload(str(workspace), state_dir=str(inside_workspace)),
    )
    with pytest.raises(ConfigurationError) as inside_error:
        load_config(inside_config)
    assert inside_error.value.code == ErrorCode.CONFIG_PATH_INVALID
    assert not inside_workspace.exists()


def test_state_dir_symlink_into_workspace_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace_state = workspace / "state"
    workspace_state.mkdir()
    link = tmp_path / "state-link"
    try:
        link.symlink_to(workspace_state, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    config = _write_yaml(
        tmp_path / "state-link.yaml",
        _payload(str(workspace), state_dir=str(link)),
    )
    with pytest.raises(ConfigurationError) as error:
        load_config(config)
    assert error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_absolute_deliverable_and_pattern_are_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    absolute = str((workspace / "artifacts" / "report.md").resolve(strict=False))
    payload = _payload(str(workspace))
    payload["task"]["paths"]["allow_write"] = [absolute]
    config = _write_yaml(tmp_path / "absolute-deliverable.yaml", payload)
    with pytest.raises(ConfigurationError) as error:
        load_config(config)
    assert error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_windows_style_and_sensitive_deliverables_are_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    windows_payload = _payload(str(workspace))
    windows_payload["task"]["paths"]["allow_write"] = ["C:artifacts/report.md"]
    windows_payload["task"]["acceptance"]["deliverables"] = ["C:artifacts/report.md"]
    windows_config = _write_yaml(tmp_path / "windows.yaml", windows_payload)
    with pytest.raises(ConfigurationError) as windows_error:
        load_config(windows_config)
    assert windows_error.value.code == ErrorCode.CONFIG_PATH_INVALID

    sensitive_payload = _payload(str(workspace))
    sensitive_payload["task"]["paths"]["allow_write"] = [".env"]
    sensitive_payload["task"]["acceptance"]["deliverables"] = [".env"]
    sensitive_config = _write_yaml(tmp_path / "sensitive.yaml", sensitive_payload)
    with pytest.raises(ConfigurationError) as sensitive_error:
        load_config(sensitive_config)
    assert sensitive_error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_protected_state_directory_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    git_directory = workspace / ".git"
    git_directory.mkdir()
    config = _write_yaml(
        tmp_path / "state-in-git.yaml",
        _payload(str(workspace), state_dir=str(git_directory)),
    )
    with pytest.raises(ConfigurationError) as error:
        load_config(config)
    assert error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_allow_write_cannot_open_platform_sensitive_paths(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    for pattern in (".git/**", "**/.env", "secrets/**/*.key"):
        payload = _payload(str(workspace))
        payload["task"]["paths"]["allow_write"] = [pattern]
        payload["task"]["acceptance"]["deliverables"] = ["artifacts/report.md"]
        config = _write_yaml(
            tmp_path / f"protected-{pattern.replace('/', '-').replace('*', 'x')}.yaml",
            payload,
        )
        with pytest.raises(ConfigurationError) as error:
            load_config(config)
        assert error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_deliverable_must_match_allow_write(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    payload = _payload(str(workspace))
    payload["task"]["paths"]["allow_write"] = ["artifacts/other.md"]
    config = _write_yaml(tmp_path / "not-allowed.yaml", payload)
    with pytest.raises(ConfigurationError) as error:
        load_config(config)
    assert error.value.code == ErrorCode.CONFIG_PATH_INVALID


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "artifacts"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    config = _write_yaml(tmp_path / "symlink.yaml", _payload(str(workspace)))
    with pytest.raises(ConfigurationError) as error:
        load_config(config)
    assert error.value.code == ErrorCode.CONFIG_PATH_ESCAPE


def test_rejection_has_no_side_effects(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    payload = _payload(str(workspace))
    payload["task"]["acceptance"]["deliverables"] = ["artifacts/../../.env"]
    config = _write_yaml(tmp_path / "escape.yaml", payload)

    with pytest.raises(ConfigurationError):
        load_config(config)
    assert not (tmp_path / "state").exists()
    assert not (workspace / "artifacts").exists()


def test_equivalent_configs_share_fingerprint(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    first_payload = _payload("workspace")
    first = _write_yaml(
        tmp_path / "first.yaml",
        first_payload,
        text="# first form\n" + yaml.safe_dump(first_payload, sort_keys=False),
    )
    second_payload = _payload(str(workspace))
    second_payload["task"]["allowed_tools"] = ["read_file", "write_file"]
    second = _write_yaml(
        tmp_path / "second.yaml",
        second_payload,
        text=yaml.safe_dump(second_payload, sort_keys=True),
    )

    first_resolved = load_config(first)
    second_resolved = load_config(second)
    assert first_resolved.fingerprint == second_resolved.fingerprint
    assert first_resolved.config.task.allowed_tools == ("read_file", "write_file")


def test_order_and_semantic_changes_affect_fingerprint(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    first_payload = _payload(str(workspace))
    first_payload["task"]["paths"]["allow_write"].append("artifacts/second.md")
    first_payload["task"]["acceptance"]["deliverables"].append("artifacts/second.md")
    first = _write_yaml(tmp_path / "ordered.yaml", first_payload)

    second_payload = _payload(str(workspace))
    second_payload["task"]["paths"]["allow_write"].append("artifacts/second.md")
    second_payload["task"]["acceptance"]["deliverables"] = [
        "artifacts/second.md",
        "artifacts/report.md",
    ]
    second = _write_yaml(tmp_path / "reordered.yaml", second_payload)

    changed_payload = _payload(str(workspace), description="Changed description.")
    changed = _write_yaml(tmp_path / "changed.yaml", changed_payload)

    first_fingerprint = load_config(first).fingerprint
    second_fingerprint = load_config(second).fingerprint
    changed_fingerprint = load_config(changed).fingerprint
    assert first_fingerprint != second_fingerprint
    assert first_fingerprint != changed_fingerprint
    assert first_fingerprint.startswith("sha256:")
    assert len(first_fingerprint) == len("sha256:") + 64


def test_loader_does_not_follow_environment_dependent_paths(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    payload = _payload(str(workspace))
    config = _write_yaml(tmp_path / "environment.yaml", payload)
    before = os.environ.get("GUARDED_HARNESS_TEST_PATH")
    resolved = load_config(config)
    after = os.environ.get("GUARDED_HARNESS_TEST_PATH")
    assert before == after
    assert resolved.workspace_root == str(workspace.resolve())
