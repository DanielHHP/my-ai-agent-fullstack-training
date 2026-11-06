"""Trusted single-file write and exact text-patch handlers."""

from __future__ import annotations

from typing import Any

from ..config import ResolvedHarnessConfig
from ..domain import (
    ErrorCode,
    EvidenceDraft,
    EvidenceKind,
    HandlerResult,
    JsonValue,
    ToolExecutionContext,
)
from ..sandbox import MAX_IO_BYTES, Sandbox, SandboxViolation, SandboxWriteMode
from .common import (
    MAX_PATCH_EDITS,
    TOOL_SCHEMA_VERSION,
    failure,
    require_task_write_path,
    success,
)


def write_file_normalizer(
    sandbox: Sandbox,
    config: ResolvedHarnessConfig,
):
    """Create a Gate normalizer that enforces task-scoped write authorization."""

    def normalizer(
        arguments: dict[str, JsonValue],
        _context: ToolExecutionContext,
    ) -> dict[str, JsonValue]:
        path = str(arguments["path"])
        checked = sandbox.check_write(path)
        require_task_write_path(config, checked.workspace_relative)
        content = str(arguments["content"])
        _validate_text_content(content)
        if len(content.encode("utf-8")) > MAX_IO_BYTES:
            raise SandboxViolation(
                ErrorCode.SANDBOX_FILE_TOO_LARGE,
                "write",
                "content exceeds the 10 MiB hard limit",
                checked.workspace_relative,
            )
        return {**arguments, "path": checked.workspace_relative}

    return normalizer


def apply_patch_normalizer(
    sandbox: Sandbox,
    config: ResolvedHarnessConfig,
):
    """Create a pure semantic normalizer for one single-file patch."""

    def normalizer(
        arguments: dict[str, JsonValue],
        _context: ToolExecutionContext,
    ) -> dict[str, JsonValue]:
        path = str(arguments["path"])
        checked = sandbox.check_write(path)
        require_task_write_path(config, checked.workspace_relative)
        edits = arguments["edits"]
        if not isinstance(edits, list) or not edits:
            raise SandboxViolation(
                ErrorCode.TOOL_PATCH_EMPTY,
                "patch",
                "edits must contain at least one replacement",
                checked.workspace_relative,
            )
        if len(edits) > MAX_PATCH_EDITS:
            raise SandboxViolation(
                ErrorCode.TOOL_PATCH_INPUT_TOO_LARGE,
                "patch",
                "patch contains too many edits",
                checked.workspace_relative,
            )
        total_bytes = 0
        all_noop = True
        for edit in edits:
            if not isinstance(edit, dict):
                raise SandboxViolation(
                    ErrorCode.TOOL_PATCH_EMPTY,
                    "patch",
                    "each edit must be an object",
                    checked.workspace_relative,
                )
            old_text = str(edit["oldText"])
            new_text = str(edit["newText"])
            _validate_text_content(old_text)
            _validate_text_content(new_text)
            total_bytes += len(old_text.encode("utf-8")) + len(new_text.encode("utf-8"))
            all_noop = all_noop and old_text == new_text
        if total_bytes > MAX_IO_BYTES:
            raise SandboxViolation(
                ErrorCode.TOOL_PATCH_INPUT_TOO_LARGE,
                "patch",
                "patch input exceeds the 10 MiB hard limit",
                checked.workspace_relative,
            )
        if all_noop:
            raise SandboxViolation(
                ErrorCode.TOOL_PATCH_NO_CHANGE,
                "patch",
                "patch would not change file content",
                checked.workspace_relative,
            )
        return {**arguments, "path": checked.workspace_relative}

    return normalizer


def write_file_handler(sandbox: Sandbox, config: ResolvedHarnessConfig):
    """Create a handler that atomically creates or replaces one task file."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        path = str(arguments["path"])
        content = str(arguments["content"])
        try:
            checked = sandbox.check_write(path)
            require_task_write_path(config, checked.workspace_relative)
            data = content.encode("utf-8")
            _validate_text_content(content)
            if len(data) > MAX_IO_BYTES:
                raise SandboxViolation(
                    ErrorCode.SANDBOX_FILE_TOO_LARGE,
                    "write",
                    "content exceeds the 10 MiB hard limit",
                    checked.workspace_relative,
                )
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)

        try:
            result = sandbox.write_file_atomic(
                checked.workspace_relative,
                data,
                mode=SandboxWriteMode.CREATE,
                create_parents=True,
            )
        except SandboxViolation as exc:
            if exc.code != ErrorCode.SANDBOX_WRITE_CONFLICT:
                return failure(exc.code, exc.message)
            try:
                current = sandbox.read_file(checked.workspace_relative)
            except SandboxViolation as read_error:
                return failure(read_error.code, read_error.message)
            try:
                _validate_text_content_bytes(current.data)
                current.data.decode("utf-8")
            except (SandboxViolation, UnicodeDecodeError):
                return failure(
                    ErrorCode.TOOL_TEXT_ENCODING_INVALID,
                    "existing write target is not valid NUL-free UTF-8 text",
                )
            if current.data == data:
                return success(
                    {
                        "schema_version": TOOL_SCHEMA_VERSION,
                        "tool": "write_file",
                        "path": current.path.workspace_relative,
                        "mode": SandboxWriteMode.REPLACE.value,
                        "created": False,
                        "changed": False,
                        "byte_count": current.byte_count,
                        "previous_content_digest": current.content_digest,
                        "content_digest": current.content_digest,
                    },
                    summary=f"write_file left {current.path.workspace_relative} unchanged",
                )
            try:
                result = sandbox.write_file_atomic(
                    checked.workspace_relative,
                    data,
                    mode=SandboxWriteMode.REPLACE,
                    expected_content_digest=current.content_digest,
                    create_parents=False,
                )
            except SandboxViolation as replace_error:
                return failure(replace_error.code, replace_error.message)

        evidence = EvidenceDraft(
            kind=EvidenceKind.DIFF,
            path=result.path.workspace_relative,
            content_digest=result.content_digest,
        )
        return success(
            {
                "schema_version": TOOL_SCHEMA_VERSION,
                "tool": "write_file",
                "path": result.path.workspace_relative,
                "mode": result.mode.value,
                "created": result.created,
                "changed": True,
                "byte_count": result.byte_count,
                "previous_content_digest": result.previous_content_digest,
                "content_digest": result.content_digest,
            },
            summary=f"write_file updated {result.path.workspace_relative}",
            evidence=(evidence,),
        )

    return handler


def apply_patch_handler(sandbox: Sandbox, config: ResolvedHarnessConfig):
    """Create a handler for deterministic all-or-nothing text replacement."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        path = str(arguments["path"])
        edits = arguments["edits"]
        if not isinstance(edits, list) or not edits:
            return failure(ErrorCode.TOOL_PATCH_EMPTY, "edits must not be empty")
        try:
            checked = sandbox.check_write(path)
            require_task_write_path(config, checked.workspace_relative)
            current_file = sandbox.read_file(checked.workspace_relative)
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)
        try:
            _validate_text_content_bytes(current_file.data)
            current_text = current_file.data.decode("utf-8")
        except (SandboxViolation, UnicodeDecodeError):
            return failure(
                ErrorCode.TOOL_TEXT_ENCODING_INVALID,
                "patch target is not valid NUL-free UTF-8 text",
            )

        next_text = current_text
        total_bytes = 0
        for index, edit in enumerate(edits, start=1):
            if not isinstance(edit, dict):
                return failure(ErrorCode.TOOL_PATCH_EMPTY, "each edit must be an object")
            old_text = edit.get("oldText")
            new_text = edit.get("newText")
            if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
                return failure(ErrorCode.TOOL_PATCH_EMPTY, "edit text fields are invalid")
            try:
                _validate_text_content(old_text)
                _validate_text_content(new_text)
            except SandboxViolation as exc:
                return failure(exc.code, exc.message)
            total_bytes += len(old_text.encode("utf-8")) + len(new_text.encode("utf-8"))
            if total_bytes > MAX_IO_BYTES:
                return failure(
                    ErrorCode.TOOL_PATCH_INPUT_TOO_LARGE,
                    "patch input exceeds the 10 MiB hard limit",
                )
            occurrences = next_text.count(old_text)
            if occurrences == 0:
                return failure(
                    ErrorCode.TOOL_PATCH_CONTEXT_NOT_FOUND,
                    f"edit {index} context was not found",
                )
            if occurrences > 1:
                return failure(
                    ErrorCode.TOOL_PATCH_AMBIGUOUS,
                    f"edit {index} context matched more than once",
                )
            position = next_text.index(old_text)
            next_text = f"{next_text[:position]}{new_text}{next_text[position + len(old_text) :]}"

        if next_text == current_text:
            return failure(ErrorCode.TOOL_PATCH_NO_CHANGE, "patch would not change file content")
        next_data = next_text.encode("utf-8")
        if len(next_data) > MAX_IO_BYTES:
            return failure(
                ErrorCode.TOOL_PATCH_INPUT_TOO_LARGE,
                "patched file exceeds the 10 MiB hard limit",
            )
        try:
            result = sandbox.write_file_atomic(
                checked.workspace_relative,
                next_data,
                mode=SandboxWriteMode.REPLACE,
                expected_content_digest=current_file.content_digest,
                create_parents=False,
            )
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)

        evidence = EvidenceDraft(
            kind=EvidenceKind.DIFF,
            path=result.path.workspace_relative,
            content_digest=result.content_digest,
        )
        return success(
            {
                "schema_version": TOOL_SCHEMA_VERSION,
                "tool": "apply_patch",
                "path": result.path.workspace_relative,
                "edits_applied": len(edits),
                "previous_content_digest": current_file.content_digest,
                "content_digest": result.content_digest,
                "byte_count": result.byte_count,
                "byte_delta": result.byte_count - current_file.byte_count,
            },
            summary=f"apply_patch updated {result.path.workspace_relative}",
            evidence=(evidence,),
        )

    return handler


def _validate_text_content(value: str) -> None:
    if "\x00" in value:
        raise SandboxViolation(
            ErrorCode.TOOL_WRITE_CONTENT_INVALID,
            "write",
            "content must not contain NUL bytes",
        )


def _validate_text_content_bytes(value: bytes) -> None:
    if b"\x00" in value:
        raise SandboxViolation(
            ErrorCode.TOOL_WRITE_CONTENT_INVALID,
            "write",
            "target contains NUL bytes",
        )


__all__ = [
    "apply_patch_handler",
    "apply_patch_normalizer",
    "write_file_handler",
    "write_file_normalizer",
]
