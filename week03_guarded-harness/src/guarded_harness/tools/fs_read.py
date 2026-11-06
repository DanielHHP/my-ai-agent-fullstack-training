"""Trusted read-only filesystem tool handlers."""

from __future__ import annotations

from typing import Any

from ..domain import (
    ErrorCode,
    EvidenceDraft,
    EvidenceKind,
    HandlerResult,
    ToolExecutionContext,
)
from ..sandbox import Sandbox, SandboxViolation
from .common import (
    LIST_OUTPUT_BYTES,
    MAX_SEARCH_LINE_CHARS,
    READ_OUTPUT_BYTES,
    SEARCH_OUTPUT_BYTES,
    TOOL_SCHEMA_VERSION,
    clip_line,
    failure,
    glob_matches,
    json_size,
    success,
)

SEARCH_MAX_FILES = 5_000
SEARCH_MAX_BYTES = 64 * 1024 * 1024
SEARCH_MAX_ENTRIES = 20_000


def read_file_handler(sandbox: Sandbox):
    """Create a handler bound to one trusted Sandbox."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        path = str(arguments["path"])
        offset = int(arguments["offset"])
        limit = int(arguments["limit"])
        try:
            file = sandbox.read_file(path)
        except SandboxViolation as exc:
            code = (
                ErrorCode.TOOL_PATH_TYPE_MISMATCH
                if exc.code == ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE
                else exc.code
            )
            return failure(code, exc.message)
        if b"\x00" in file.data:
            return failure(ErrorCode.TOOL_BINARY_FILE, "file contains NUL bytes")
        try:
            text = file.data.decode("utf-8")
        except UnicodeDecodeError:
            return failure(
                ErrorCode.TOOL_TEXT_ENCODING_INVALID,
                "file is not valid UTF-8 text",
            )

        lines = text.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            if offset != 1:
                return failure(
                    ErrorCode.TOOL_RANGE_OUT_OF_BOUNDS,
                    "offset is outside the empty file",
                )
            return success(
                {
                    "schema_version": TOOL_SCHEMA_VERSION,
                    "tool": "read_file",
                    "path": file.path.workspace_relative,
                    "total_lines": 0,
                    "start_line": 1,
                    "end_line": 0,
                    "content": "",
                    "line_numbered_content": True,
                    "content_digest": file.content_digest,
                    "byte_count": file.byte_count,
                    "complete": True,
                    "next_offset": None,
                    "truncated": False,
                    "clipped_line_count": 0,
                },
                summary=f"read empty file {file.path.workspace_relative}",
                evidence=(
                    EvidenceDraft(
                        kind=EvidenceKind.READ,
                        path=file.path.workspace_relative,
                        content_digest=file.content_digest,
                    ),
                ),
            )
        if offset > total_lines:
            return failure(
                ErrorCode.TOOL_RANGE_OUT_OF_BOUNDS,
                f"offset {offset} exceeds {total_lines} lines",
            )

        selected_end = min(total_lines, offset - 1 + limit)
        width = len(str(selected_end))
        rendered: list[str] = []
        rendered_end = offset - 1
        clipped_line_count = 0
        for index in range(offset - 1, selected_end):
            line, clipped = clip_line(lines[index])
            rendered_line = f"{index + 1:>{width}}| {line}"
            candidate_size = len("\n".join([*rendered, rendered_line]).encode("utf-8"))
            if rendered and candidate_size > READ_OUTPUT_BYTES:
                break
            rendered.append(rendered_line)
            rendered_end = index + 1
            clipped_line_count += int(clipped)

        complete = rendered_end == total_lines
        output = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool": "read_file",
            "path": file.path.workspace_relative,
            "total_lines": total_lines,
            "start_line": offset,
            "end_line": rendered_end,
            "content": "\n".join(rendered),
            "line_numbered_content": True,
            "content_digest": file.content_digest,
            "byte_count": file.byte_count,
            "complete": complete,
            "next_offset": None if complete else rendered_end + 1,
            "truncated": not complete or clipped_line_count > 0,
            "clipped_line_count": clipped_line_count,
        }
        return success(
            output,
            summary=(f"read {file.path.workspace_relative} lines {offset}-{rendered_end}"),
            evidence=(
                EvidenceDraft(
                    kind=EvidenceKind.READ,
                    path=file.path.workspace_relative,
                    line_start=offset,
                    line_end=rendered_end,
                    content_digest=file.content_digest,
                ),
            ),
        )

    return handler


def list_files_handler(sandbox: Sandbox):
    """Create a deterministic bounded file listing handler."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        path = str(arguments["path"])
        pattern = str(arguments["pattern"])
        limit = int(arguments["limit"])
        try:
            walk = sandbox.walk_readable_files(
                path,
                max_files=20_000,
                max_bytes=1024 * 1024 * 1024,
                max_entries=20_000,
            )
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)
        matches = [
            file.workspace_relative
            for file in walk.files
            if glob_matches(pattern, file.workspace_relative)
        ]
        selected = matches[:limit]
        while selected and json_size(selected) > LIST_OUTPUT_BYTES:
            selected.pop()
        output = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool": "list_files",
            "pattern": pattern,
            "scope": walk.root.workspace_relative,
            "entries": selected,
            "returned_count": len(selected),
            "scanned_count": walk.examined_entries,
            "has_more": len(selected) < len(matches) or walk.truncated,
            "scan_limit_reached": walk.truncated,
        }
        return success(
            output,
            summary=f"listed {len(selected)} files under {walk.root.workspace_relative}",
        )

    return handler


def read_directory_handler(sandbox: Sandbox):
    """Create a bounded immediate-directory listing handler."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        path = str(arguments["path"])
        limit = int(arguments["limit"])
        try:
            listing = sandbox.list_directory(path, max_entries=limit)
        except SandboxViolation as exc:
            code = (
                ErrorCode.TOOL_PATH_TYPE_MISMATCH
                if exc.code == ErrorCode.SANDBOX_UNSUPPORTED_FILE_TYPE
                else exc.code
            )
            return failure(code, exc.message)
        entries = [
            {
                "name": entry.name,
                "path": entry.path.workspace_relative,
                "entry_type": entry.entry_type.value,
                "size_bytes": entry.size_bytes,
            }
            for entry in listing.entries
        ]
        while entries and json_size(entries) > LIST_OUTPUT_BYTES:
            entries.pop()
        output = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool": "read_directory",
            "scope": listing.root.workspace_relative,
            "entries": entries,
            "visible_count": listing.visible_count,
            "omitted_count": listing.omitted_count,
            "returned_count": len(entries),
            "has_more": listing.truncated or len(entries) < listing.returned_count,
        }
        return success(
            output,
            summary=f"listed directory {listing.root.workspace_relative}",
        )

    return handler


def search_code_handler(sandbox: Sandbox):
    """Create a literal, bounded repository search handler."""

    def handler(arguments: dict[str, Any], _context: ToolExecutionContext) -> HandlerResult:
        path = str(arguments["path"])
        needle = str(arguments["pattern"])
        glob = arguments.get("glob")
        ignore_case = bool(arguments["ignore_case"])
        context_lines = int(arguments["context"])
        limit = int(arguments["limit"])
        try:
            walk = sandbox.walk_readable_files(
                path,
                max_files=SEARCH_MAX_FILES,
                max_bytes=SEARCH_MAX_BYTES,
                max_entries=SEARCH_MAX_ENTRIES,
            )
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)

        matches: list[dict[str, Any]] = []
        evidence: list[EvidenceDraft] = []
        evidence_keys: set[tuple[str, int, int]] = set()
        files_scanned = 0
        bytes_scanned = 0
        skipped_binary = 0
        skipped_large = 0
        output_truncated = False
        comparable_needle = needle.casefold() if ignore_case else needle

        for file_path in walk.files:
            relative = file_path.workspace_relative
            if glob is not None and not glob_matches(str(glob), relative):
                continue
            try:
                file = sandbox.read_file(relative)
            except SandboxViolation as exc:
                if exc.code == ErrorCode.SANDBOX_FILE_TOO_LARGE:
                    skipped_large += 1
                continue
            files_scanned += 1
            bytes_scanned += file.byte_count
            if b"\x00" in file.data:
                skipped_binary += 1
                continue
            try:
                text = file.data.decode("utf-8")
            except UnicodeDecodeError:
                skipped_binary += 1
                continue
            lines = text.splitlines()
            for index, line in enumerate(lines):
                comparable = line.casefold() if ignore_case else line
                if comparable_needle not in comparable:
                    continue
                start_index = max(0, index - context_lines)
                end_index = min(len(lines), index + context_lines + 1)
                context = [
                    {
                        "line_number": context_index + 1,
                        "text": clip_line(lines[context_index], MAX_SEARCH_LINE_CHARS)[0],
                    }
                    for context_index in range(start_index, end_index)
                ]
                match = {
                    "path": relative,
                    "line_number": index + 1,
                    "line_text": clip_line(line, MAX_SEARCH_LINE_CHARS)[0],
                    "context_start_line": start_index + 1,
                    "context_end_line": end_index,
                    "context": context,
                    "content_digest": file.content_digest,
                }
                if json_size([*matches, match]) > SEARCH_OUTPUT_BYTES:
                    output_truncated = True
                    break
                matches.append(match)
                key = (relative, start_index + 1, end_index)
                if key not in evidence_keys:
                    evidence_keys.add(key)
                    evidence.append(
                        EvidenceDraft(
                            kind=EvidenceKind.READ,
                            path=relative,
                            line_start=start_index + 1,
                            line_end=end_index,
                            content_digest=file.content_digest,
                        )
                    )
                if len(matches) >= limit:
                    output_truncated = True
                    break
            if output_truncated or len(matches) >= limit:
                break

        output = {
            "schema_version": TOOL_SCHEMA_VERSION,
            "tool": "search_code",
            "pattern": needle,
            "scope": walk.root.workspace_relative,
            "glob": glob,
            "ignore_case": ignore_case,
            "matches": matches,
            "returned_matches": len(matches),
            "files_scanned": files_scanned,
            "bytes_scanned": bytes_scanned,
            "has_more": output_truncated or walk.truncated,
            "hit_limit_reached": len(matches) >= limit,
            "scan_limit_reached": walk.truncated,
            "scan_limit_reason": (
                walk.limit_reason.value if walk.limit_reason is not None else None
            ),
            "skipped_binary_files": skipped_binary,
            "skipped_large_files": skipped_large,
            "skipped_denied_entries": walk.omitted_count,
        }
        return success(
            output,
            summary=f"found {len(matches)} matches in {files_scanned} files",
            evidence=evidence,
        )

    return handler
