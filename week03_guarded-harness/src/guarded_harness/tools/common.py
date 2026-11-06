"""Pure helpers shared by trusted read-only repository tools."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ..domain import (
    ErrorCode,
    HandlerResult,
    HandlerResultStatus,
    JsonValue,
    ToolExecutionContext,
)

TOOL_SCHEMA_VERSION = 1
MAX_PATTERN_CHARS = 512
MAX_PATH_CHARS = 4_096
MAX_LINE_CHARS = 2_000
MAX_SEARCH_LINE_CHARS = 500
READ_OUTPUT_BYTES = 48 * 1024
SEARCH_OUTPUT_BYTES = 48 * 1024
LIST_OUTPUT_BYTES = 32 * 1024
STATUS_OUTPUT_BYTES = 32 * 1024
DIFF_OUTPUT_BYTES = 60 * 1024

_CONTROL_FREE_STRING = r"^[^\u0000-\u001f\u007f]+$"


def object_schema(
    properties: dict[str, JsonValue],
    required: tuple[str, ...],
) -> dict[str, JsonValue]:
    """Build a strict JSON Schema object for one tool."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def text_schema(*, max_length: int = MAX_PATTERN_CHARS) -> dict[str, JsonValue]:
    """Return a bounded, control-free string schema."""

    return {
        "type": "string",
        "minLength": 1,
        "maxLength": max_length,
        "pattern": _CONTROL_FREE_STRING,
    }


def integer_schema(*, minimum: int, maximum: int) -> dict[str, JsonValue]:
    """Return a strict JSON integer schema."""

    return {
        "type": "integer",
        "minimum": minimum,
        "maximum": maximum,
    }


def normalize_list_files(
    arguments: dict[str, JsonValue],
    _context: ToolExecutionContext,
) -> dict[str, JsonValue]:
    """Add deterministic defaults for ``list_files``."""

    validate_workspace_glob(arguments["pattern"])
    return {**arguments, "limit": arguments.get("limit", 200)}


def normalize_search_code(
    arguments: dict[str, JsonValue],
    _context: ToolExecutionContext,
) -> dict[str, JsonValue]:
    """Add deterministic defaults and validate the fixed glob subset."""

    normalized: dict[str, JsonValue] = {
        **arguments,
        "ignore_case": arguments.get("ignore_case", False),
        "context": arguments.get("context", 0),
        "limit": arguments.get("limit", 50),
    }
    glob = normalized.get("glob")
    if glob is not None:
        validate_workspace_glob(glob)
    return normalized


def normalize_read_file(
    arguments: dict[str, JsonValue],
    _context: ToolExecutionContext,
) -> dict[str, JsonValue]:
    """Add deterministic defaults for ``read_file``."""

    return {
        **arguments,
        "offset": arguments.get("offset", 1),
        "limit": arguments.get("limit", 2_000),
    }


def normalize_read_directory(
    arguments: dict[str, JsonValue],
    _context: ToolExecutionContext,
) -> dict[str, JsonValue]:
    """Add deterministic defaults for ``read_directory``."""

    return {**arguments, "limit": arguments.get("limit", 200)}


def normalize_git_diff(
    arguments: dict[str, JsonValue],
    _context: ToolExecutionContext,
) -> dict[str, JsonValue]:
    """Add deterministic defaults for ``git_diff``."""

    return {**arguments, "staged": arguments.get("staged", False)}


def validate_workspace_glob(pattern: object) -> str:
    """Validate the fixed, workspace-relative glob syntax."""

    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError("glob must be a bounded non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in pattern):
        raise ValueError("glob must not contain control characters")
    parsed = PurePosixPath(pattern)
    if (
        "\\" in pattern
        or ":" in pattern
        or parsed.is_absolute()
        or PureWindowsPath(pattern).is_absolute()
        or PureWindowsPath(pattern).drive
        or ".." in parsed.parts
    ):
        raise ValueError("glob must be a workspace-relative POSIX pattern")
    return pattern


def glob_matches(pattern: str, path: str) -> bool:
    """Match the supported ``*``, ``?`` and ``**`` glob subset."""

    validate_workspace_glob(pattern)
    expression: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                if index + 2 < len(pattern) and pattern[index + 2] == "/":
                    expression.append("(?:.*/)?")
                    index += 3
                else:
                    expression.append(".*")
                    index += 2
                continue
            expression.append("[^/]*")
        elif character == "?":
            expression.append("[^/]")
        else:
            expression.append(re.escape(character))
        index += 1
    expression.append("$")
    return re.fullmatch("".join(expression), path) is not None


def canonical_json_digest(value: object) -> str:
    """Return a stable SHA-256 digest for a JSON-compatible value."""

    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def json_size(value: object) -> int:
    """Return the canonical UTF-8 JSON size of a value."""

    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


def clip_text_to_bytes(text: str, limit: int, *, marker: str = "\n[TRUNCATED]") -> str:
    """Clip UTF-8 text without splitting a code point."""

    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    marker_bytes = marker.encode("utf-8")
    budget = max(0, limit - len(marker_bytes))
    prefix = encoded[:budget].decode("utf-8", errors="ignore")
    return f"{prefix}{marker}"


def clip_line(line: str, limit: int = MAX_LINE_CHARS) -> tuple[str, bool]:
    """Clip one source line and report whether clipping occurred."""

    if len(line) <= limit:
        return line, False
    return f"{line[:limit]}...[LINE TRUNCATED]", True


def success(
    output: dict[str, Any],
    *,
    summary: str,
    evidence=(),
) -> HandlerResult:
    """Build one successful structured handler result."""

    return HandlerResult(
        status=HandlerResultStatus.SUCCEEDED,
        output=output,
        summary=summary[:4_000],
        evidence=tuple(evidence),
    )


def failure(
    code: ErrorCode | str,
    message: str,
    *,
    retryable: bool = False,
) -> HandlerResult:
    """Build one deterministic failed handler result."""

    return HandlerResult(
        status=HandlerResultStatus.FAILED,
        summary=message[:4_000],
        error_code=code.value if isinstance(code, ErrorCode) else code,
        retryable=retryable,
    )
