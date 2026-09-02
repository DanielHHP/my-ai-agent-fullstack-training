from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import ValidationError, validate

from app.core.errors import StructuredOutputError

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def extract_json_text(content: str) -> str:
    """Return the JSON text portion of a model response.

    Accepts either a bare JSON value or JSON wrapped in a Markdown code fence.
    The extraction remains local so model output is never trusted as-is.
    """
    stripped = content.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        return fenced.group(1).strip()
    return stripped


def validate_structured_content(content: str, schema: dict[str, Any]) -> Any:
    """Parse and validate model output against the requested JSON Schema."""
    json_text = extract_json_text(content)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(
            "Model output is not valid JSON",
            details={"line": exc.lineno, "column": exc.colno, "message": exc.msg},
        ) from exc

    try:
        validate(instance=parsed, schema=schema)
    except ValidationError as exc:
        raise StructuredOutputError(
            "Model output does not match the requested JSON Schema",
            details={"path": list(exc.absolute_path), "message": exc.message},
        ) from exc
    return parsed


def repair_instruction(error: StructuredOutputError, schema: dict[str, Any]) -> str:
    """Build a concise repair prompt for a failed structured-output attempt."""
    return (
        "Your previous response failed JSON Schema validation. Return only corrected JSON, "
        "with no Markdown fences or explanation.\n"
        f"Validation error: {error.message}; details={json.dumps(error.details, ensure_ascii=False)}\n"
        f"Required schema: {json.dumps(schema, ensure_ascii=False)}"
    )


__all__ = ["extract_json_text", "repair_instruction", "validate_structured_content"]
