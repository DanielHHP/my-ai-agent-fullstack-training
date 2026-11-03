"""Deterministic, bounded redaction for state, audit, and error summaries."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from pydantic import JsonValue

from .domain import ErrorCode

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
CYCLE = "[CYCLE]"
UNSUPPORTED = "[UNSUPPORTED]"

_SENSITIVE_KEY_ROOTS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "privatekey",
    "secret",
    "token",
}
_AUTH_HEADER_PATTERN = re.compile(
    r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_COOKIE_HEADER_PATTERN = re.compile(r"(?i)\b(cookie\s*:\s*)[^\r\n]+")
_PEM_PATTERN = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PREFIXED_TOKEN_PATTERN = re.compile(r"\b(?:sk|gh[pousr]|xox[baprs])[-_][A-Za-z0-9_-]{16,}\b")
_AWS_ACCESS_KEY_PATTERN = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)"
    r"(\s*=\s*)([^&\s]+)"
)


@dataclass(frozen=True)
class RedactionLimits:
    """Bounds applied to every redacted value."""

    max_output_bytes: int = 65_536
    max_lines: int = 512
    max_depth: int = 16
    max_nodes: int = 10_000

    def __post_init__(self) -> None:
        for name, value, minimum in (
            ("max_output_bytes", self.max_output_bytes, 64),
            ("max_lines", self.max_lines, 1),
            ("max_depth", self.max_depth, 1),
            ("max_nodes", self.max_nodes, 1),
        ):
            if type(value) is not int or value < minimum:
                raise RedactionError(
                    ErrorCode.REDACTION_INVALID_INPUT,
                    operation="configure",
                    message=f"{name} must be an integer >= {minimum}",
                )


@dataclass(frozen=True)
class RedactionResult:
    """Redacted JSON-compatible output and its safety metadata."""

    value: JsonValue
    truncated: bool
    redaction_count: int


class RedactionError(RuntimeError):
    """Stable redaction failure without echoing the rejected value."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        operation: str,
        message: str,
    ) -> None:
        self.code = code
        self.operation = operation
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


class Redactor:
    """Recursively redact JSON-like values before persistence or display."""

    def __init__(
        self,
        sensitive_keys: Iterable[str] = (),
        sensitive_values: Iterable[str] = (),
        limits: RedactionLimits | None = None,
    ) -> None:
        try:
            self._sensitive_keys = frozenset(
                self._normalize_key(key) for key in sensitive_keys if key
            )
            self._sensitive_values = tuple(
                sorted(
                    {value for value in sensitive_values if value},
                    key=lambda value: (-len(value), value),
                )
            )
        except (AttributeError, TypeError) as exc:
            raise RedactionError(
                ErrorCode.REDACTION_INVALID_INPUT,
                operation="configure",
                message="sensitive keys and values must be strings",
            ) from exc
        self._limits = limits or RedactionLimits()

    @property
    def limits(self) -> RedactionLimits:
        """Return the immutable limits used by this redactor."""

        return self._limits

    def redact(self, value: object) -> RedactionResult:
        """Redact a value and enforce deterministic, JSON-safe output bounds."""

        state = _RedactionState()
        redacted = self._redact_value(value, depth=0, active=set(), state=state)
        try:
            canonical = json.dumps(
                redacted,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            encoded_size = len(canonical.encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise RedactionError(
                ErrorCode.REDACTION_FAILED,
                operation="redact",
                message="redacted value is not JSON serializable",
            ) from exc
        if encoded_size > self._limits.max_output_bytes:
            return RedactionResult(
                value=self._bounded_preview(canonical),
                truncated=True,
                redaction_count=state.redaction_count,
            )
        return RedactionResult(
            value=redacted,
            truncated=state.truncated,
            redaction_count=state.redaction_count,
        )

    def _redact_value(
        self,
        value: object,
        *,
        depth: int,
        active: set[int],
        state: _RedactionState,
    ) -> JsonValue:
        state.nodes += 1
        if state.nodes > self._limits.max_nodes:
            state.truncated = True
            return TRUNCATED
        if depth > self._limits.max_depth:
            state.truncated = True
            return TRUNCATED
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            return self._limit_lines(self._redact_text(value, state), state)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else UNSUPPORTED
        if isinstance(value, Decimal):
            return str(value)

        if isinstance(value, Mapping):
            identity = id(value)
            if identity in active:
                state.truncated = True
                return CYCLE
            active.add(identity)
            try:
                result: dict[str, JsonValue] = {}
                for key, item in value.items():
                    if state.nodes >= self._limits.max_nodes:
                        state.truncated = True
                        break
                    if not isinstance(key, str):
                        result[UNSUPPORTED] = UNSUPPORTED
                        state.truncated = True
                        continue
                    redacted_key = self._limit_lines(
                        self._redact_text(key, state),
                        state,
                    )
                    if self._is_sensitive_key(key):
                        state.redaction_count += 1
                        result[redacted_key] = REDACTED
                    else:
                        result[redacted_key] = self._redact_value(
                            item,
                            depth=depth + 1,
                            active=active,
                            state=state,
                        )
                return result
            finally:
                active.remove(identity)

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            identity = id(value)
            if identity in active:
                state.truncated = True
                return CYCLE
            active.add(identity)
            try:
                result: list[JsonValue] = []
                for item in value:
                    if state.nodes >= self._limits.max_nodes:
                        state.truncated = True
                        result.append(TRUNCATED)
                        break
                    result.append(
                        self._redact_value(
                            item,
                            depth=depth + 1,
                            active=active,
                            state=state,
                        )
                    )
                return result
            finally:
                active.remove(identity)

        return UNSUPPORTED

    def _redact_text(self, value: str, state: _RedactionState) -> str:
        redacted = value
        for secret in self._sensitive_values:
            count = redacted.count(secret)
            if count:
                redacted = redacted.replace(secret, REDACTED)
                state.redaction_count += count
        for pattern, replacement in (
            (_PEM_PATTERN, REDACTED),
            (_AUTH_HEADER_PATTERN, r"\1[REDACTED]"),
            (_BEARER_PATTERN, r"\1 [REDACTED]"),
            (_COOKIE_HEADER_PATTERN, r"\1[REDACTED]"),
            (_JWT_PATTERN, REDACTED),
            (_PREFIXED_TOKEN_PATTERN, REDACTED),
            (_AWS_ACCESS_KEY_PATTERN, REDACTED),
            (_KEY_VALUE_PATTERN, r"\1\2[REDACTED]"),
        ):
            redacted, count = pattern.subn(replacement, redacted)
            state.redaction_count += count
        return redacted

    def _limit_lines(self, value: str, state: _RedactionState) -> str:
        if not value:
            return value
        lines = value.splitlines()
        if len(lines) <= self._limits.max_lines:
            return value
        state.truncated = True
        if self._limits.max_lines == 1:
            return TRUNCATED
        return "\n".join((*lines[: self._limits.max_lines - 1], TRUNCATED))

    def _is_sensitive_key(self, key: str) -> bool:
        normalized = self._normalize_key(key)
        if normalized in self._sensitive_keys:
            return True
        return any(
            normalized == root or normalized.endswith(f"_{root}") or normalized.endswith(root)
            for root in _SENSITIVE_KEY_ROOTS
        )

    @staticmethod
    def _normalize_key(key: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")

    def _bounded_preview(self, canonical: str) -> str:
        marker_bytes = len(json.dumps(TRUNCATED, ensure_ascii=False).encode("utf-8"))
        available = max(0, self._limits.max_output_bytes - marker_bytes)
        encoded = canonical.encode("utf-8")
        prefix = encoded[:available].decode("utf-8", errors="ignore")
        candidate = f"{prefix}{TRUNCATED}"
        while (
            candidate
            and len(
                json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            > self._limits.max_output_bytes
        ):
            prefix = prefix[:-1]
            candidate = f"{prefix}{TRUNCATED}"
        return candidate


@dataclass
class _RedactionState:
    nodes: int = 0
    truncated: bool = False
    redaction_count: int = 0
