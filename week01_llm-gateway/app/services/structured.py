from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import ValidationError, validate

from app.core.errors import StructuredOutputError

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def extract_json_text(content: str) -> str:
    """Return the JSON text portion of a model response.

    Accepts either a bare JSON value or JSON wrapped in a Markdown code fence.
    The extraction remains local so model output is never trusted as-is.
    """
    stripped = content.strip()
    matches = list(_FENCE.finditer(stripped))
    if matches:
        return matches[0].group(1).strip()
    return stripped


def _json_candidates(content: str) -> list[str]:
    stripped = content.strip()
    matches = list(_FENCE.finditer(stripped))
    if matches:
        return [match.group(1).strip() for match in matches]
    return [stripped]


def validate_structured_content(content: str, schema: dict[str, Any]) -> Any:
    """Parse and validate model output against the requested JSON Schema."""
    last_error: StructuredOutputError | None = None
    for json_text in _json_candidates(content):
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as exc:
            last_error = StructuredOutputError(
                "Model output is not valid JSON",
                details={"line": exc.lineno, "column": exc.colno, "message": exc.msg},
            )
            continue

        try:
            validate(instance=parsed, schema=schema)
        except ValidationError as exc:
            last_error = StructuredOutputError(
                "Model output does not match the requested JSON Schema",
                details={"path": list(exc.absolute_path), "message": exc.message},
            )
            continue
        return parsed

    if last_error is not None:
        raise last_error
    raise StructuredOutputError(
        "Model output is not valid JSON",
        details={"message": "No JSON content was found"},
    )


def repair_instruction(error: StructuredOutputError, schema: dict[str, Any]) -> str:
    """Build a concise repair prompt for a failed structured-output attempt."""
    return (
        "Your previous response failed JSON Schema validation. Return only corrected JSON, "
        "with no Markdown fences or explanation.\n"
        f"Validation error: {error.message}; details={json.dumps(error.details, ensure_ascii=False)}\n"
        f"Required schema: {json.dumps(schema, ensure_ascii=False)}"
    )


__all__ = ["extract_json_text", "repair_instruction", "validate_structured_content"]
