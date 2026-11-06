"""Trusted read-only Git handlers."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ..domain import ErrorCode, EvidenceDraft, EvidenceKind, HandlerResult, ToolExecutionContext
from ..sandbox import (
    CommandEffectClass,
    Sandbox,
    SandboxProcessResult,
    SandboxProcessStatus,
    SandboxViolation,
    TrustedCommandSpec,
)
from .common import (
    DIFF_OUTPUT_BYTES,
    STATUS_OUTPUT_BYTES,
    TOOL_SCHEMA_VERSION,
    canonical_json_digest,
    clip_text_to_bytes,
    failure,
    json_size,
    success,
)

GIT_STATUS_COMMAND = "readonly_git_status"
GIT_DIFF_NAMES_COMMAND = "readonly_git_diff_names"
GIT_DIFF_NAMES_STAGED_COMMAND = "readonly_git_diff_names_staged"
GIT_DIFF_COMMAND = "readonly_git_diff"
GIT_DIFF_STAGED_COMMAND = "readonly_git_diff_staged"
MAX_GIT_PATHSPECS = 1_000


def git_command_specs() -> tuple[TrustedCommandSpec, ...]:
    """Return the fixed command allowlist used by the read-only Git tools."""

    common = (
        "git",
        "--no-optional-locks",
        "--no-pager",
        "--literal-pathspecs",
    )
    return (
        TrustedCommandSpec(
            command_id=GIT_STATUS_COMMAND,
            argv=(
                *common,
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=all",
                "--",
                ".",
            ),
            effect_class=CommandEffectClass.READ_ONLY,
        ),
        TrustedCommandSpec(
            command_id=GIT_DIFF_NAMES_COMMAND,
            argv=(
                *common,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--name-only",
                "-z",
                "--relative",
                "--",
                ".",
            ),
            effect_class=CommandEffectClass.READ_ONLY,
        ),
        TrustedCommandSpec(
            command_id=GIT_DIFF_NAMES_STAGED_COMMAND,
            argv=(
                *common,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--cached",
                "--name-only",
                "-z",
                "--relative",
                "--",
                ".",
            ),
            effect_class=CommandEffectClass.READ_ONLY,
        ),
        TrustedCommandSpec(
            command_id=GIT_DIFF_COMMAND,
            argv=(
                *common,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--full-index",
                "--",
            ),
            effect_class=CommandEffectClass.READ_ONLY,
            allow_pathspecs=True,
        ),
        TrustedCommandSpec(
            command_id=GIT_DIFF_STAGED_COMMAND,
            argv=(
                *common,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--no-color",
                "--full-index",
                "--cached",
                "--",
            ),
            effect_class=CommandEffectClass.READ_ONLY,
            allow_pathspecs=True,
        ),
    )


def git_status_handler(sandbox: Sandbox):
    """Create the fixed read-only status handler."""

    def handler(_arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        try:
            process = sandbox.execute(GIT_STATUS_COMMAND)
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)
        process_failure = _process_failure(process)
        if process_failure is not None:
            return process_failure
        if process.stdout_truncated:
            return failure(
                ErrorCode.TOOL_GIT_OUTPUT_INVALID,
                "Git status output exceeded the trusted capture limit",
            )
        try:
            raw_entries = _parse_status(process.stdout)
        except ValueError:
            return failure(
                ErrorCode.TOOL_GIT_OUTPUT_INVALID,
                "Git status output could not be parsed",
            )

        entries: list[dict[str, Any]] = []
        omitted = 0
        for status, path, original_path in raw_entries:
            translated = _git_root_path_to_workspace(sandbox, path)
            translated_original = (
                _git_root_path_to_workspace(sandbox, original_path)
                if original_path is not None
                else None
            )
            if translated is None or (original_path is not None and translated_original is None):
                omitted += 1
                continue
            try:
                checked = sandbox.check_read(translated)
                checked_original = (
                    sandbox.check_read(translated_original).workspace_relative
                    if translated_original is not None
                    else None
                )
            except SandboxViolation:
                omitted += 1
                continue
            entries.append(
                {
                    "status": status,
                    "path": checked.workspace_relative,
                    "original_path": checked_original,
                }
            )
        entries.sort(key=lambda item: (item["path"], item["status"]))
        truncated = False
        while entries and json_size(entries) > STATUS_OUTPUT_BYTES:
            entries.pop()
            truncated = True
        output = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool": "git_status",
            "clean": not entries and omitted == 0 and not truncated,
            "entries": entries,
            "returned_count": len(entries),
            "omitted_count": omitted,
            "truncated": truncated,
        }
        return success(
            output,
            summary=f"Git status contains {len(entries)} visible entries",
            evidence=(
                EvidenceDraft(
                    kind=EvidenceKind.COMMAND,
                    command_id=GIT_STATUS_COMMAND,
                    content_digest=canonical_json_digest(output),
                ),
            ),
        )

    return handler


def git_diff_handler(sandbox: Sandbox):
    """Create the fixed read-only diff handler with SafePath pathspecs."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        scope = str(arguments["path"])
        staged = bool(arguments["staged"])
        names_command = GIT_DIFF_NAMES_STAGED_COMMAND if staged else GIT_DIFF_NAMES_COMMAND
        try:
            names_process = sandbox.execute(names_command)
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)
        process_failure = _process_failure(names_process)
        if process_failure is not None:
            return process_failure
        if names_process.stdout_truncated:
            return failure(
                ErrorCode.TOOL_GIT_OUTPUT_INVALID,
                "Git changed-path output exceeded the trusted capture limit",
            )

        try:
            changed_paths = _parse_nul_paths(names_process.stdout)
        except ValueError:
            return failure(
                ErrorCode.TOOL_GIT_OUTPUT_INVALID,
                "Git changed-path output could not be parsed",
            )

        pathspecs = []
        for path in changed_paths:
            if not _within_scope(path, scope):
                continue
            try:
                checked = sandbox.check_read(path)
            except SandboxViolation:
                continue
            pathspecs.append(checked)
        pathspecs = sorted(
            {path.workspace_relative: path for path in pathspecs}.values(),
            key=lambda path: path.workspace_relative,
        )
        if len(pathspecs) > MAX_GIT_PATHSPECS:
            return failure(
                ErrorCode.TOOL_SCAN_LIMIT_EXCEEDED,
                "Git diff pathspec count exceeds the trusted limit",
            )

        patch = ""
        truncated = False
        command_id = GIT_DIFF_STAGED_COMMAND if staged else GIT_DIFF_COMMAND
        if pathspecs:
            try:
                process = sandbox.execute(command_id, pathspecs=tuple(pathspecs))
            except SandboxViolation as exc:
                return failure(exc.code, exc.message)
            process_failure = _process_failure(process)
            if process_failure is not None:
                return process_failure
            if process.stderr_truncated:
                return failure(
                    ErrorCode.TOOL_GIT_OUTPUT_INVALID,
                    "Git diff error output exceeded the trusted capture limit",
                )
            patch = clip_text_to_bytes(process.stdout, DIFF_OUTPUT_BYTES)
            truncated = process.stdout_truncated or patch != process.stdout

        output = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool": "git_diff",
            "staged": staged,
            "scope": scope,
            "patch": patch,
            "empty": patch == "",
            "content_digest": canonical_json_digest(patch),
            "truncated": truncated,
        }
        return success(
            output,
            summary=(
                "Git diff has no visible changes"
                if not patch
                else f"Git diff contains {len(pathspecs)} visible paths"
            ),
            evidence=(
                EvidenceDraft(
                    kind=EvidenceKind.DIFF,
                    command_id=command_id,
                    content_digest=canonical_json_digest(patch),
                ),
            ),
        )

    return handler


def _process_failure(process: SandboxProcessResult) -> HandlerResult | None:
    if process.status == SandboxProcessStatus.TIMED_OUT:
        return failure(
            ErrorCode.TOOL_GIT_TIMEOUT,
            "read-only Git command timed out after process-group termination",
            retryable=True,
        )
    if process.status != SandboxProcessStatus.SUCCEEDED or process.exit_code != 0:
        return failure(
            ErrorCode.TOOL_GIT_FAILED,
            f"read-only Git command failed with exit code {process.exit_code}",
        )
    return None


def _parse_status(stdout: str) -> list[tuple[str, str, str | None]]:
    if "\x00" not in stdout and stdout:
        raise ValueError("porcelain -z status must be NUL terminated")
    records = stdout.split("\x00")
    entries: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise ValueError("malformed porcelain status record")
        status = record[:2]
        path = record[3:]
        original_path = None
        if status[0] in {"R", "C"} or status[1] in {"R", "C"}:
            if index >= len(records) or not records[index]:
                raise ValueError("rename/copy status is missing its original path")
            original_path = records[index]
            index += 1
        entries.append((status, path, original_path))
    return entries


def _parse_nul_paths(stdout: str) -> list[str]:
    if "\x00" not in stdout and stdout:
        raise ValueError("Git -z path output must be NUL terminated")
    return [path for path in stdout.split("\x00") if path]


def _within_scope(path: str, scope: str) -> bool:
    if scope == ".":
        return True
    candidate = PurePosixPath(path)
    boundary = PurePosixPath(scope)
    return candidate == boundary or boundary in candidate.parents


def _git_root_path_to_workspace(sandbox: Sandbox, path: str) -> str | None:
    prefix = sandbox.workspace_prefix
    if prefix == ".":
        return path
    if path == prefix:
        return "."
    leading = f"{prefix}/"
    if not path.startswith(leading):
        return None
    return path[len(leading) :]
