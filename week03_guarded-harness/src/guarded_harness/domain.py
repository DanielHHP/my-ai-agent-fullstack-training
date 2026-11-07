"""Strict, versioned domain contracts shared by the guarded harness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Protocol, TypeVar, runtime_checkable
from uuid import uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PlainSerializer,
    StringConstraints,
    ValidationError,
    field_serializer,
)


class TaskKind(str, Enum):
    """Supported task families."""

    CODE_UNDERSTANDING = "code_understanding"
    TEST_REPAIR = "test_repair"


class RunStatus(str, Enum):
    """Persisted lifecycle state for one harness run."""

    NEW = "NEW"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class StopReason(str, Enum):
    """Stable reasons that explain why a run stopped."""

    FINAL_ANSWER = "FINAL_ANSWER"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    GATE_BLOCKED = "GATE_BLOCKED"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"
    WORKSPACE_CHANGED = "WORKSPACE_CHANGED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AcceptanceStatus(str, Enum):
    """Outcome of model-independent completion checks."""

    NOT_EVALUATED = "NOT_EVALUATED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class CheckpointBoundaryKind(str, Enum):
    """Stable safety boundaries that may produce a checkpoint."""

    STEP_COMPLETED = "STEP_COMPLETED"
    TOOL_SUCCEEDED = "TOOL_SUCCEEDED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_TIMED_OUT = "TOOL_TIMED_OUT"
    TOOL_UNKNOWN_RESULT = "TOOL_UNKNOWN_RESULT"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_SETTLED = "APPROVAL_SETTLED"
    COMPLETION_RECORDED = "COMPLETION_RECORDED"


class RecoveryAction(str, Enum):
    """Deterministic action selected by the recovery planner."""

    CONTINUE_AGENT = "CONTINUE_AGENT"
    WAIT_FOR_APPROVAL = "WAIT_FOR_APPROVAL"
    REENTER_GATE = "REENTER_GATE"
    RESUME_CLAIM_THEN_REENTER_GATE = "RESUME_CLAIM_THEN_REENTER_GATE"
    REBUILD_CHECKPOINT_THEN_CONTINUE = "REBUILD_CHECKPOINT_THEN_CONTINUE"
    MARK_UNKNOWN_RESULT = "MARK_UNKNOWN_RESULT"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


class RecoveryStatus(str, Enum):
    """Caller-visible disposition of a recovery plan."""

    RESUMABLE = "RESUMABLE"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ALREADY_TERMINAL = "ALREADY_TERMINAL"


class StepStatus(str, Enum):
    """Lifecycle state for an agent step."""

    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InvocationStatus(str, Enum):
    """Lifecycle state for one governed tool invocation."""

    PREPARED = "PREPARED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"


class ObservationStatus(str, Enum):
    """Statuses visible to the model in a tool observation."""

    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    FAILED = "FAILED"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"


class ExecutionMode(str, Enum):
    """Trusted execution mode for one tool-call context."""

    PLAN = "PLAN"
    EXECUTE = "EXECUTE"


class HandlerResultStatus(str, Enum):
    """Outcomes a trusted tool handler may report to the Gate."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"


class GatePhase(str, Enum):
    """Fixed gate phase names used for decisions and audit correlation."""

    VISIBILITY = "VISIBILITY"
    SCHEMA = "SCHEMA"
    SEMANTIC = "SEMANTIC"
    PERMISSION = "PERMISSION"
    RISK = "RISK"
    APPROVAL = "APPROVAL"
    REPEAT = "REPEAT"
    BUDGET = "BUDGET"
    EXECUTION = "EXECUTION"


class ToolRisk(str, Enum):
    """Risk classification for a trusted tool."""

    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"


class ToolPermission(str, Enum):
    """Permission required to invoke a trusted tool."""

    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"


class ToolOutputKind(str, Enum):
    """Normalized tool output categories."""

    JSON = "JSON"
    TEXT = "TEXT"
    NONE = "NONE"


class RetryPolicy(str, Enum):
    """Retry rules permitted by the execution contract."""

    NEVER = "NEVER"
    SAFE_READ_ONLY = "SAFE_READ_ONLY"
    IDEMPOTENT_ONLY = "IDEMPOTENT_ONLY"


class ApprovalRequirement(str, Enum):
    """Whether a tool requires an approved invocation."""

    NEVER = "NEVER"
    REQUIRED = "REQUIRED"


class ConcurrencyClass(str, Enum):
    """Concurrency class enforced by the future gate."""

    SERIAL = "SERIAL"
    READ_ONLY_PARALLEL = "READ_ONLY_PARALLEL"


class ApprovalStatus(str, Enum):
    """Persisted approval lifecycle state."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CLAIMED = "CLAIMED"
    CONSUMED = "CONSUMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class ApprovalDecision(str, Enum):
    """A human or channel approval decision."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class EvidenceKind(str, Enum):
    """Facts that can support execution or completion checks."""

    READ = "READ"
    DOCUMENT = "DOCUMENT"
    COMMAND = "COMMAND"
    TEST_REPORT = "TEST_REPORT"
    DIFF = "DIFF"
    AUDIT = "AUDIT"


class TestExecutionPhase(str, Enum):
    """Verifier-owned test execution phases."""

    BASELINE = "BASELINE"
    FINAL = "FINAL"


class TestExecutionStatus(str, Enum):
    """Confirmed lifecycle of one verifier test execution."""

    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"


class TestNodeStatus(str, Enum):
    """Normalized JUnit testcase outcome."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"
    XFAIL = "XFAIL"
    XPASS = "XPASS"
    UNKNOWN = "UNKNOWN"


class BudgetSourceKind(str, Enum):
    """Trusted source categories for idempotent budget updates."""

    STEP = "STEP"
    TOOL_CALL = "TOOL_CALL"
    HARNESS = "HARNESS"


class BudgetCategory(str, Enum):
    """Budget dimensions tracked by the ledger."""

    STEPS = "STEPS"
    TOOL_CALLS = "TOOL_CALLS"
    TOKEN_COST = "TOKEN_COST"
    WALL_TIME = "WALL_TIME"


class ErrorCode(str, Enum):
    """Stable WP-00 error codes."""

    CONFIG_FILE_NOT_FOUND = "CONFIG_FILE_NOT_FOUND"
    CONFIG_IO_ERROR = "CONFIG_IO_ERROR"
    CONFIG_TOO_LARGE = "CONFIG_TOO_LARGE"
    CONFIG_YAML_INVALID = "CONFIG_YAML_INVALID"
    CONFIG_DUPLICATE_KEY = "CONFIG_DUPLICATE_KEY"
    CONFIG_MULTIPLE_DOCUMENTS = "CONFIG_MULTIPLE_DOCUMENTS"
    CONFIG_ROOT_NOT_MAPPING = "CONFIG_ROOT_NOT_MAPPING"
    CONFIG_SCHEMA_INVALID = "CONFIG_SCHEMA_INVALID"
    CONFIG_PATH_INVALID = "CONFIG_PATH_INVALID"
    CONFIG_PATH_ESCAPE = "CONFIG_PATH_ESCAPE"
    CONFIG_DUPLICATE_DELIVERABLE = "CONFIG_DUPLICATE_DELIVERABLE"
    CONFIG_KIND_STRATEGY_MISMATCH = "CONFIG_KIND_STRATEGY_MISMATCH"
    CONFIG_APPROVAL_INVALID = "CONFIG_APPROVAL_INVALID"
    CONFIG_FINGERPRINT_ERROR = "CONFIG_FINGERPRINT_ERROR"

    DOMAIN_INVALID_MODEL = "DOMAIN_INVALID_MODEL"
    DOMAIN_SERIALIZATION_ERROR = "DOMAIN_SERIALIZATION_ERROR"
    DOMAIN_ENUM_INVALID = "DOMAIN_ENUM_INVALID"
    DOMAIN_ID_INVALID = "DOMAIN_ID_INVALID"
    DOMAIN_TIME_INVALID = "DOMAIN_TIME_INVALID"
    DOMAIN_SCHEMA_VERSION_UNSUPPORTED = "DOMAIN_SCHEMA_VERSION_UNSUPPORTED"

    STATE_DATABASE_UNAVAILABLE = "STATE_DATABASE_UNAVAILABLE"
    STATE_MIGRATION_FAILED = "STATE_MIGRATION_FAILED"
    STATE_SCHEMA_UNSUPPORTED = "STATE_SCHEMA_UNSUPPORTED"
    STATE_TRANSACTION_FAILED = "STATE_TRANSACTION_FAILED"
    STATE_NOT_FOUND = "STATE_NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    STATE_INVALID_TRANSITION = "STATE_INVALID_TRANSITION"
    STATE_INVARIANT_VIOLATION = "STATE_INVARIANT_VIOLATION"
    STATE_LEASE_INVALID = "STATE_LEASE_INVALID"
    STATE_LEASE_EXPIRED = "STATE_LEASE_EXPIRED"
    STATE_LEASE_HELD = "STATE_LEASE_HELD"
    STATE_BUDGET_EXCEEDED = "STATE_BUDGET_EXCEEDED"
    STATE_INTEGRITY_ERROR = "STATE_INTEGRITY_ERROR"

    CHECKPOINT_NOT_FOUND = "CHECKPOINT_NOT_FOUND"
    CHECKPOINT_INVALID = "CHECKPOINT_INVALID"
    CHECKPOINT_HASH_MISMATCH = "CHECKPOINT_HASH_MISMATCH"
    CHECKPOINT_SCHEMA_UNSUPPORTED = "CHECKPOINT_SCHEMA_UNSUPPORTED"
    CHECKPOINT_SEQUENCE_CONFLICT = "CHECKPOINT_SEQUENCE_CONFLICT"
    CHECKPOINT_SNAPSHOT_TOO_LARGE = "CHECKPOINT_SNAPSHOT_TOO_LARGE"
    CHECKPOINT_WRITE_FAILED = "CHECKPOINT_WRITE_FAILED"

    RECOVERY_NOT_ALLOWED = "RECOVERY_NOT_ALLOWED"
    RECOVERY_STATE_AMBIGUOUS = "RECOVERY_STATE_AMBIGUOUS"
    RECOVERY_CONFIG_MISMATCH = "RECOVERY_CONFIG_MISMATCH"
    RECOVERY_WORKSPACE_CHANGED = "RECOVERY_WORKSPACE_CHANGED"
    RECOVERY_AUDIT_INVALID = "RECOVERY_AUDIT_INVALID"
    RECOVERY_APPROVAL_INVALID = "RECOVERY_APPROVAL_INVALID"
    RECOVERY_EVIDENCE_INVALID = "RECOVERY_EVIDENCE_INVALID"
    RECOVERY_CHECKPOINT_REQUIRED = "RECOVERY_CHECKPOINT_REQUIRED"
    RECOVERY_MANUAL_REVIEW_REQUIRED = "RECOVERY_MANUAL_REVIEW_REQUIRED"
    RECOVERY_WRITE_FAILED = "RECOVERY_WRITE_FAILED"
    RECOVERY_CONTEXT_TOO_LARGE = "RECOVERY_CONTEXT_TOO_LARGE"

    AUDIT_WRITE_FAILED = "AUDIT_WRITE_FAILED"
    AUDIT_READ_FAILED = "AUDIT_READ_FAILED"
    AUDIT_APPEND_ONLY = "AUDIT_APPEND_ONLY"
    AUDIT_SEQUENCE_CONFLICT = "AUDIT_SEQUENCE_CONFLICT"
    AUDIT_CHAIN_INVALID = "AUDIT_CHAIN_INVALID"
    AUDIT_EVENT_INVALID = "AUDIT_EVENT_INVALID"

    REDACTION_INVALID_INPUT = "REDACTION_INVALID_INPUT"
    REDACTION_FAILED = "REDACTION_FAILED"

    APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_BINDING_MISMATCH = "APPROVAL_BINDING_MISMATCH"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_NOT_EXECUTABLE = "APPROVAL_NOT_EXECUTABLE"
    APPROVAL_ALREADY_CLAIMED = "APPROVAL_ALREADY_CLAIMED"
    APPROVAL_REPLAYED = "APPROVAL_REPLAYED"
    APPROVAL_CONFLICT = "APPROVAL_CONFLICT"
    APPROVAL_APPROVER_DENIED = "APPROVAL_APPROVER_DENIED"
    APPROVAL_SELF_APPROVAL_DENIED = "APPROVAL_SELF_APPROVAL_DENIED"
    APPROVAL_CHANNEL_UNAVAILABLE = "APPROVAL_CHANNEL_UNAVAILABLE"
    APPROVAL_STATE_INVALID = "APPROVAL_STATE_INVALID"

    SANDBOX_WORKSPACE_INVALID = "SANDBOX_WORKSPACE_INVALID"
    SANDBOX_GIT_UNAVAILABLE = "SANDBOX_GIT_UNAVAILABLE"
    SANDBOX_GIT_ROOT_INVALID = "SANDBOX_GIT_ROOT_INVALID"
    SANDBOX_BASELINE_INVALID = "SANDBOX_BASELINE_INVALID"
    SANDBOX_WORKSPACE_DIRTY = "SANDBOX_WORKSPACE_DIRTY"
    SANDBOX_WORKSPACE_CHANGED = "SANDBOX_WORKSPACE_CHANGED"
    SANDBOX_EXTERNAL_WORKSPACE_CHANGED = "SANDBOX_EXTERNAL_WORKSPACE_CHANGED"
    SANDBOX_IDENTITY_CHANGED = "SANDBOX_IDENTITY_CHANGED"
    SANDBOX_UNSUPPORTED_PLATFORM = "SANDBOX_UNSUPPORTED_PLATFORM"
    SANDBOX_PATH_INVALID = "SANDBOX_PATH_INVALID"
    SANDBOX_PATH_OUT_OF_WORKSPACE = "SANDBOX_PATH_OUT_OF_WORKSPACE"
    SANDBOX_PATH_SENSITIVE = "SANDBOX_PATH_SENSITIVE"
    SANDBOX_PATH_DENY_READ = "SANDBOX_PATH_DENY_READ"
    SANDBOX_PATH_DENY_WRITE = "SANDBOX_PATH_DENY_WRITE"
    SANDBOX_PATH_PROTECTED = "SANDBOX_PATH_PROTECTED"
    SANDBOX_PATH_CHANGED = "SANDBOX_PATH_CHANGED"
    SANDBOX_UNSUPPORTED_FILE_TYPE = "SANDBOX_UNSUPPORTED_FILE_TYPE"
    SANDBOX_FILE_TOO_LARGE = "SANDBOX_FILE_TOO_LARGE"
    SANDBOX_WRITE_CONFLICT = "SANDBOX_WRITE_CONFLICT"
    SANDBOX_IO_ERROR = "SANDBOX_IO_ERROR"
    SANDBOX_COMMAND_UNKNOWN = "SANDBOX_COMMAND_UNKNOWN"
    SANDBOX_COMMAND_INVALID = "SANDBOX_COMMAND_INVALID"
    SANDBOX_EXECUTABLE_DENIED = "SANDBOX_EXECUTABLE_DENIED"
    SANDBOX_SHELL_DENIED = "SANDBOX_SHELL_DENIED"
    SANDBOX_ENVIRONMENT_INVALID = "SANDBOX_ENVIRONMENT_INVALID"
    SANDBOX_COMMAND_START_FAILED = "SANDBOX_COMMAND_START_FAILED"
    SANDBOX_COMMAND_TIMEOUT = "SANDBOX_COMMAND_TIMEOUT"
    SANDBOX_COMMAND_CANCELLED = "SANDBOX_COMMAND_CANCELLED"
    SANDBOX_PROCESS_TERMINATION_FAILED = "SANDBOX_PROCESS_TERMINATION_FAILED"
    SANDBOX_RESULT_UNKNOWN = "SANDBOX_RESULT_UNKNOWN"

    TOOL_BINARY_FILE = "TOOL_BINARY_FILE"
    TOOL_TEXT_ENCODING_INVALID = "TOOL_TEXT_ENCODING_INVALID"
    TOOL_RANGE_OUT_OF_BOUNDS = "TOOL_RANGE_OUT_OF_BOUNDS"
    TOOL_PATH_TYPE_MISMATCH = "TOOL_PATH_TYPE_MISMATCH"
    TOOL_GIT_FAILED = "TOOL_GIT_FAILED"
    TOOL_GIT_TIMEOUT = "TOOL_GIT_TIMEOUT"
    TOOL_GIT_OUTPUT_INVALID = "TOOL_GIT_OUTPUT_INVALID"
    TOOL_SCAN_LIMIT_EXCEEDED = "TOOL_SCAN_LIMIT_EXCEEDED"
    TOOL_WRITE_CONTENT_INVALID = "TOOL_WRITE_CONTENT_INVALID"
    TOOL_PATCH_EMPTY = "TOOL_PATCH_EMPTY"
    TOOL_PATCH_CONTEXT_NOT_FOUND = "TOOL_PATCH_CONTEXT_NOT_FOUND"
    TOOL_PATCH_AMBIGUOUS = "TOOL_PATCH_AMBIGUOUS"
    TOOL_PATCH_NO_CHANGE = "TOOL_PATCH_NO_CHANGE"
    TOOL_PATCH_INPUT_TOO_LARGE = "TOOL_PATCH_INPUT_TOO_LARGE"
    TOOL_TEST_COMMAND_MISSING = "TOOL_TEST_COMMAND_MISSING"
    TOOL_TEST_FAILED = "TOOL_TEST_FAILED"

    COMPLETION_UNSUPPORTED_TASK = "COMPLETION_UNSUPPORTED_TASK"
    COMPLETION_RUN_STATE_INVALID = "COMPLETION_RUN_STATE_INVALID"
    COMPLETION_CONFIG_MISMATCH = "COMPLETION_CONFIG_MISMATCH"
    COMPLETION_LEASE_INVALID = "COMPLETION_LEASE_INVALID"
    COMPLETION_INTERNAL_ACCESS_DENIED = "COMPLETION_INTERNAL_ACCESS_DENIED"
    COMPLETION_DELIVERABLE_MISSING = "COMPLETION_DELIVERABLE_MISSING"
    COMPLETION_DELIVERABLE_EMPTY = "COMPLETION_DELIVERABLE_EMPTY"
    COMPLETION_DELIVERABLE_TOO_LARGE = "COMPLETION_DELIVERABLE_TOO_LARGE"
    COMPLETION_DELIVERABLE_NOT_CHANGED = "COMPLETION_DELIVERABLE_NOT_CHANGED"
    COMPLETION_DOCUMENT_INVALID = "COMPLETION_DOCUMENT_INVALID"
    COMPLETION_SECTION_MISSING = "COMPLETION_SECTION_MISSING"
    COMPLETION_SECTION_DUPLICATE = "COMPLETION_SECTION_DUPLICATE"
    COMPLETION_FACT_WITHOUT_EVIDENCE = "COMPLETION_FACT_WITHOUT_EVIDENCE"
    COMPLETION_EVIDENCE_UNDEFINED = "COMPLETION_EVIDENCE_UNDEFINED"
    COMPLETION_EVIDENCE_DUPLICATE = "COMPLETION_EVIDENCE_DUPLICATE"
    COMPLETION_EVIDENCE_MALFORMED = "COMPLETION_EVIDENCE_MALFORMED"
    COMPLETION_EVIDENCE_PATH_INVALID = "COMPLETION_EVIDENCE_PATH_INVALID"
    COMPLETION_EVIDENCE_LINE_OUT_OF_RANGE = "COMPLETION_EVIDENCE_LINE_OUT_OF_RANGE"
    COMPLETION_EVIDENCE_NOT_READ = "COMPLETION_EVIDENCE_NOT_READ"
    COMPLETION_EVIDENCE_PARTIAL = "COMPLETION_EVIDENCE_PARTIAL"
    COMPLETION_EVIDENCE_STALE = "COMPLETION_EVIDENCE_STALE"
    COMPLETION_EVIDENCE_INVALID = "COMPLETION_EVIDENCE_INVALID"
    COMPLETION_WORKSPACE_OUT_OF_SCOPE = "COMPLETION_WORKSPACE_OUT_OF_SCOPE"
    COMPLETION_WORKSPACE_CHANGED = "COMPLETION_WORKSPACE_CHANGED"
    COMPLETION_EXTERNAL_WORKSPACE_CHANGED = "COMPLETION_EXTERNAL_WORKSPACE_CHANGED"
    COMPLETION_IDENTITY_CHANGED = "COMPLETION_IDENTITY_CHANGED"
    COMPLETION_AUDIT_INVALID = "COMPLETION_AUDIT_INVALID"
    COMPLETION_CLAIM_WITHOUT_EVIDENCE = "COMPLETION_CLAIM_WITHOUT_EVIDENCE"
    COMPLETION_RESULT_WRITE_FAILED = "COMPLETION_RESULT_WRITE_FAILED"
    COMPLETION_TEST_BASELINE_MISSING = "COMPLETION_TEST_BASELINE_MISSING"
    COMPLETION_TEST_BASELINE_INVALID = "COMPLETION_TEST_BASELINE_INVALID"
    COMPLETION_TEST_BASELINE_STALE = "COMPLETION_TEST_BASELINE_STALE"
    COMPLETION_TEST_BASELINE_NOT_FAILING = "COMPLETION_TEST_BASELINE_NOT_FAILING"
    COMPLETION_TEST_COMMAND_MISSING = "COMPLETION_TEST_COMMAND_MISSING"
    COMPLETION_TEST_COMMAND_MISMATCH = "COMPLETION_TEST_COMMAND_MISMATCH"
    COMPLETION_TEST_START_FAILED = "COMPLETION_TEST_START_FAILED"
    COMPLETION_TEST_TIMEOUT = "COMPLETION_TEST_TIMEOUT"
    COMPLETION_TEST_RESULT_UNKNOWN = "COMPLETION_TEST_RESULT_UNKNOWN"
    COMPLETION_TEST_EXIT_NONZERO = "COMPLETION_TEST_EXIT_NONZERO"
    COMPLETION_TEST_REPORT_MISSING = "COMPLETION_TEST_REPORT_MISSING"
    COMPLETION_TEST_REPORT_EMPTY = "COMPLETION_TEST_REPORT_EMPTY"
    COMPLETION_TEST_REPORT_TOO_LARGE = "COMPLETION_TEST_REPORT_TOO_LARGE"
    COMPLETION_TEST_REPORT_MALFORMED = "COMPLETION_TEST_REPORT_MALFORMED"
    COMPLETION_TEST_REPORT_UNSAFE_XML = "COMPLETION_TEST_REPORT_UNSAFE_XML"
    COMPLETION_TEST_REPORT_TRUNCATED = "COMPLETION_TEST_REPORT_TRUNCATED"
    COMPLETION_TEST_NODE_DUPLICATE = "COMPLETION_TEST_NODE_DUPLICATE"
    COMPLETION_TEST_NODE_LOST = "COMPLETION_TEST_NODE_LOST"
    COMPLETION_TEST_BASELINE_FAILURE_REMAINS = "COMPLETION_TEST_BASELINE_FAILURE_REMAINS"
    COMPLETION_TEST_NEW_NODE_FAILED = "COMPLETION_TEST_NEW_NODE_FAILED"
    COMPLETION_TEST_NEW_SKIP = "COMPLETION_TEST_NEW_SKIP"
    COMPLETION_TEST_FILE_MODIFIED = "COMPLETION_TEST_FILE_MODIFIED"
    COMPLETION_TEST_FILE_DELETED = "COMPLETION_TEST_FILE_DELETED"
    COMPLETION_TEST_WORKSPACE_UNSTABLE = "COMPLETION_TEST_WORKSPACE_UNSTABLE"
    COMPLETION_TEST_WORKSPACE_OUT_OF_SCOPE = "COMPLETION_TEST_WORKSPACE_OUT_OF_SCOPE"
    COMPLETION_TEST_EVIDENCE_STALE = "COMPLETION_TEST_EVIDENCE_STALE"
    COMPLETION_TEST_RESULT_WRITE_FAILED = "COMPLETION_TEST_RESULT_WRITE_FAILED"

    REGISTRY_INVALID_DEFINITION = "REGISTRY_INVALID_DEFINITION"
    REGISTRY_TOOL_CONFLICT = "REGISTRY_TOOL_CONFLICT"
    POLICY_CONFIG_INVALID = "POLICY_CONFIG_INVALID"

    GATE_ALLOWED = "GATE_ALLOWED"
    GATE_TOOL_NOT_FOUND = "GATE_TOOL_NOT_FOUND"
    GATE_TOOL_NOT_VISIBLE = "GATE_TOOL_NOT_VISIBLE"
    GATE_INVALID_ARGUMENT = "GATE_INVALID_ARGUMENT"
    GATE_SEMANTIC_INVALID = "GATE_SEMANTIC_INVALID"
    GATE_PERMISSION_DENIED = "GATE_PERMISSION_DENIED"
    GATE_RISK_DENIED = "GATE_RISK_DENIED"
    GATE_MODE_DENIED = "GATE_MODE_DENIED"
    GATE_REPEATED_CALL = "GATE_REPEATED_CALL"
    GATE_REPLAY_SUPPRESSED = "GATE_REPLAY_SUPPRESSED"
    GATE_TOOL_CALL_ID_CONFLICT = "GATE_TOOL_CALL_ID_CONFLICT"
    GATE_BUDGET_EXHAUSTED = "GATE_BUDGET_EXHAUSTED"
    GATE_WRITE_NOT_ALLOWED = "GATE_WRITE_NOT_ALLOWED"
    GATE_INTENT_WRITE_FAILED = "GATE_INTENT_WRITE_FAILED"
    GATE_START_WRITE_FAILED = "GATE_START_WRITE_FAILED"
    GATE_HANDLER_FAILED = "GATE_HANDLER_FAILED"
    GATE_HANDLER_TIMEOUT = "GATE_HANDLER_TIMEOUT"
    GATE_RESULT_UNKNOWN = "GATE_RESULT_UNKNOWN"
    GATE_RESULT_INVALID = "GATE_RESULT_INVALID"
    GATE_RESULT_WRITE_FAILED = "GATE_RESULT_WRITE_FAILED"
    GATE_OUTPUT_INVALID = "GATE_OUTPUT_INVALID"
    GATE_INTERNAL_ERROR = "GATE_INTERNAL_ERROR"


def _validate_utc_datetime(value: datetime) -> datetime:
    """Reject naive or non-UTC datetimes at the public contract boundary."""

    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(timezone.utc)


def _serialize_utc_datetime(value: datetime) -> str:
    """Serialize UTC datetimes with stable microsecond precision and a Z suffix."""

    normalized = value.astimezone(timezone.utc)
    return normalized.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _validate_tool_call_id(value: str) -> str:
    """Reject empty IDs, control characters, and IDs that cannot be safely echoed."""

    if not value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("tool_call_id must be non-empty without control characters")
    return value


def _validate_relative_path(value: str | None) -> str | None:
    """Reject absolute, traversal, and non-POSIX domain paths."""

    if value is None:
        return None
    parsed = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or parsed.is_absolute()
        or PureWindowsPath(value).is_absolute()
        or PureWindowsPath(value).drive
        or ".." in parsed.parts
        or parsed.as_posix() == "."
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("path must be a normalized relative POSIX path")
    return value


def _validate_workspace_prefix(value: str) -> str:
    """Validate a workspace path relative to the containing Git root."""

    if value == ".":
        return value
    checked = _validate_relative_path(value)
    if checked is None or checked == ".":
        raise ValueError("workspace_prefix must be '.' or a relative POSIX path")
    return checked


UtcDatetime = Annotated[
    datetime,
    AfterValidator(_validate_utc_datetime),
    PlainSerializer(_serialize_utc_datetime, return_type=str, when_used="json"),
]
Sha256Digest = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^sha256:[0-9a-f]{64}$"),
]
RunId = Annotated[str, StringConstraints(strict=True, pattern=r"^run_[0-9a-f]{32}$")]
TraceId = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{32}$")]
StepId = Annotated[str, StringConstraints(strict=True, pattern=r"^step_[0-9a-f]{32}$")]
EvidenceId = Annotated[str, StringConstraints(strict=True, pattern=r"^evidence_[0-9a-f]{32}$")]
ApprovalId = Annotated[str, StringConstraints(strict=True, pattern=r"^approval_[0-9a-f]{32}$")]
CheckpointId = Annotated[str, StringConstraints(strict=True, pattern=r"^checkpoint_[0-9a-f]{32}$")]
RecoveryId = Annotated[str, StringConstraints(strict=True, pattern=r"^recovery_[0-9a-f]{32}$")]
ToolCallId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=256),
    AfterValidator(_validate_tool_call_id),
]
BudgetSourceId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=256),
]
RelativePath = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=1024),
    AfterValidator(_validate_relative_path),
]
WorkspacePrefix = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=1024),
    AfterValidator(_validate_workspace_prefix),
]
ToolName = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
]
StableErrorCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$"),
]


class StrictDomainModel(BaseModel):
    """Base model enforcing strict fields across all public contracts."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        validate_default=True,
        use_enum_values=False,
        arbitrary_types_allowed=False,
        allow_inf_nan=False,
    )


class ValidationIssue(StrictDomainModel):
    """One bounded, redacted, machine-readable validation problem."""

    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    path: str = Field(min_length=1, max_length=512)
    message: str = Field(min_length=1, max_length=1000)


class ConfigurationError(ValueError):
    """Configuration failure with a stable code and ordered validation issues."""

    def __init__(self, code: ErrorCode, issues: tuple[ValidationIssue, ...] = ()) -> None:
        self.code = code
        self.issues = tuple(sorted(issues, key=lambda issue: (issue.path, issue.code.value)))
        detail = self.issues[0].message if self.issues else code.value
        super().__init__(f"{code.value}: {detail}")


class DomainContractError(ValueError):
    """Domain-model validation failure with stable issues."""

    def __init__(self, code: ErrorCode, issues: tuple[ValidationIssue, ...] = ()) -> None:
        self.code = code
        self.issues = tuple(sorted(issues, key=lambda issue: (issue.path, issue.code.value)))
        detail = self.issues[0].message if self.issues else code.value
        super().__init__(f"{code.value}: {detail}")


class WorkspaceIdentity(StrictDomainModel):
    """Filesystem and Git identity for a single run."""

    model_config = ConfigDict(frozen=True)

    git_root: str = Field(min_length=1, max_length=4096)
    workspace_root: str = Field(min_length=1, max_length=4096)
    workspace_prefix: WorkspacePrefix
    baseline_fingerprint: Sha256Digest
    external_dirty_baseline_fingerprint: Sha256Digest


class RunBudget(StrictDomainModel):
    """Maximum resources allowed for one run."""

    model_config = ConfigDict(frozen=True)

    max_steps: int = Field(ge=1)
    max_tool_calls: int = Field(ge=0)
    max_token_cost: Decimal = Field(ge=0)
    max_wall_time_seconds: int = Field(ge=1)


class BudgetUsage(StrictDomainModel):
    """Resources consumed so far by one run."""

    steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    token_cost: Decimal = Field(default=Decimal("0"), ge=0)
    wall_time_seconds: int = Field(default=0, ge=0)


class Run(StrictDomainModel):
    """Persisted public run state."""

    schema_version: Literal[1] = 1
    run_id: RunId
    trace_id: TraceId
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind
    task_description: str = Field(min_length=1, max_length=4000)
    config_fingerprint: Sha256Digest
    workspace: WorkspaceIdentity
    budget: RunBudget
    usage: BudgetUsage = Field(default_factory=BudgetUsage)
    status: RunStatus = RunStatus.NEW
    stop_reason: StopReason | None = None
    acceptance_status: AcceptanceStatus = AcceptanceStatus.NOT_EVALUATED
    current_step_id: StepId | None = None
    last_checkpoint_id: CheckpointId | None = None
    resumed_from_checkpoint_id: CheckpointId | None = None
    candidate_final_answer: str | None = Field(default=None, max_length=16_000)
    created_at: UtcDatetime
    updated_at: UtcDatetime
    started_at: UtcDatetime | None = None
    ended_at: UtcDatetime | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.run_id == self.trace_id:
            raise ValueError("run_id and trace_id must differ")
        if self.usage.steps > self.budget.max_steps:
            raise ValueError("usage.steps exceeds budget.max_steps")
        if self.usage.tool_calls > self.budget.max_tool_calls:
            raise ValueError("usage.tool_calls exceeds budget.max_tool_calls")
        if self.usage.token_cost > self.budget.max_token_cost:
            raise ValueError("usage.token_cost exceeds budget.max_token_cost")
        if self.usage.wall_time_seconds > self.budget.max_wall_time_seconds:
            raise ValueError("usage.wall_time_seconds exceeds budget.max_wall_time_seconds")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at must not precede created_at")
        if self.ended_at is not None and self.started_at is not None:
            if self.ended_at < self.started_at:
                raise ValueError("ended_at must not precede started_at")


class Step(StrictDomainModel):
    """One bounded summary of an agent decision step."""

    schema_version: Literal[1] = 1
    step_id: StepId
    run_id: RunId
    ordinal: int = Field(ge=0)
    status: StepStatus = StepStatus.PLANNED
    summary: str = Field(default="", max_length=4000)
    started_at: UtcDatetime
    ended_at: UtcDatetime | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at must not precede started_at")


class ToolSpec(StrictDomainModel):
    """Trusted tool registry metadata without handler implementation details."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    name: ToolName
    description: str = Field(min_length=1, max_length=4000)
    input_schema: dict[str, JsonValue]
    output_kind: ToolOutputKind
    risk: ToolRisk
    required_permissions: frozenset[ToolPermission]
    timeout_seconds: int = Field(ge=1)
    retry_policy: RetryPolicy
    idempotent: bool
    path_arguments: tuple[str, ...] = ()
    output_limit_bytes: int = Field(ge=1)
    approval_requirement: ApprovalRequirement
    concurrency_class: ConcurrencyClass

    @field_serializer("required_permissions", when_used="json")
    def _serialize_required_permissions(
        self,
        permissions: frozenset[ToolPermission],
    ) -> list[str]:
        return sorted(permission.value for permission in permissions)

    def model_post_init(self, __context: Any) -> None:
        try:
            encoded = json.dumps(
                self.input_schema,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("input_schema must contain JSON values only") from exc
        if len(encoded) > 65_536:
            raise ValueError("input_schema exceeds 64 KiB")
        if len(set(self.path_arguments)) != len(self.path_arguments):
            raise ValueError("path_arguments must not contain duplicates")


class ToolInvocation(StrictDomainModel):
    """Candidate tool call and its persisted execution facts."""

    schema_version: Literal[1] = 1
    tool_call_id: ToolCallId
    run_id: RunId
    step_id: StepId
    tool_name: ToolName
    raw_args: dict[str, JsonValue]
    normalized_args: dict[str, JsonValue]
    args_digest: Sha256Digest
    risk: ToolRisk
    required_permissions: frozenset[ToolPermission]
    idempotent: bool
    status: InvocationStatus = InvocationStatus.PREPARED
    approval_id: ApprovalId | None = None
    result_summary: str | None = Field(default=None, max_length=8000)
    evidence_ids: tuple[EvidenceId, ...] = ()
    error_code: StableErrorCode | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime

    @field_serializer("required_permissions", when_used="json")
    def _serialize_required_permissions(
        self,
        permissions: frozenset[ToolPermission],
    ) -> list[str]:
        return sorted(permission.value for permission in permissions)

    def model_post_init(self, __context: Any) -> None:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must not contain duplicates")


class ToolObservation(StrictDomainModel):
    """Bounded observation returned to the model."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[2] = 2
    status: ObservationStatus
    code: StableErrorCode
    tool_call_id: ToolCallId
    phase: GatePhase
    message: str = Field(min_length=1, max_length=4000)
    retryable: bool = False
    approval_id: ApprovalId | None = None
    evidence_id: EvidenceId | None = None
    evidence_ids: tuple[EvidenceId, ...] = ()
    truncated: bool = False
    output: JsonValue | None = None

    def model_post_init(self, __context: Any) -> None:
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must not contain duplicates")
        if self.evidence_id is not None and self.evidence_id not in self.evidence_ids:
            object.__setattr__(
                self,
                "evidence_ids",
                (self.evidence_id, *self.evidence_ids),
            )
        if self.evidence_id is None and self.evidence_ids:
            object.__setattr__(self, "evidence_id", self.evidence_ids[0])


class ToolExecutionContext(StrictDomainModel):
    """Trusted, model-independent context passed to one tool handler."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    step_id: StepId
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind
    mode: ExecutionMode
    actor: str = Field(min_length=1, max_length=256)
    roles: tuple[str, ...] = ()

    def model_post_init(self, __context: Any) -> None:
        role_keys = [role.casefold() for role in self.roles]
        if any(not role or role != role.strip() for role in self.roles):
            raise ValueError("roles must be non-empty trimmed strings")
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("roles must not contain duplicates")
        for value in (self.task_id, self.actor, *self.roles):
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("tool execution context must not contain control characters")


class ToolCallRequest(StrictDomainModel):
    """One model-proposed tool call awaiting Gate authorization."""

    model_config = ConfigDict(frozen=True)

    tool_call_id: ToolCallId
    tool_name: ToolName
    args: dict[str, JsonValue]
    context: ToolExecutionContext
    lease_token: str = Field(min_length=1, max_length=512, repr=False)

    def model_post_init(self, __context: Any) -> None:
        if any(ord(character) < 32 or ord(character) == 127 for character in self.lease_token):
            raise ValueError("lease_token must not contain control characters")


class EvidenceDraft(StrictDomainModel):
    """Trusted handler evidence before Gate assigns identity and ownership."""

    model_config = ConfigDict(frozen=True)

    kind: EvidenceKind
    path: RelativePath | None = None
    command_id: str | None = Field(default=None, max_length=256)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    content_digest: Sha256Digest

    def model_post_init(self, __context: Any) -> None:
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be provided together")
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must not precede line_start")
        if self.kind in {EvidenceKind.READ, EvidenceKind.DOCUMENT} and self.path is None:
            raise ValueError("file evidence requires a path")


class HandlerResult(StrictDomainModel):
    """Structured result returned by a trusted tool handler."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    status: HandlerResultStatus
    output: JsonValue | None = None
    summary: str = Field(default="", max_length=8000)
    error_code: StableErrorCode | None = None
    evidence: tuple[EvidenceDraft, ...] = ()
    retryable: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.status == HandlerResultStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("successful handler results cannot carry an error code")
        if self.status != HandlerResultStatus.SUCCEEDED and self.error_code is None:
            raise ValueError("failed or indeterminate handler results require an error code")
        if self.retryable and self.status != HandlerResultStatus.FAILED:
            raise ValueError("only deterministic failed handler results can be retryable")
        if self.status == HandlerResultStatus.UNKNOWN_RESULT and self.evidence:
            raise ValueError("unknown handler results cannot claim new evidence")


class Approval(StrictDomainModel):
    """Approval binding and decision state for one execution intent."""

    schema_version: Literal[2] = 2
    approval_id: ApprovalId
    run_id: RunId
    tool_call_id: ToolCallId
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind
    git_root: str = Field(min_length=1, max_length=4096)
    workspace_root: str = Field(min_length=1, max_length=4096)
    workspace_prefix: WorkspacePrefix
    tool_name: ToolName
    canonical_args_digest: Sha256Digest
    binding_digest: Sha256Digest
    config_fingerprint: Sha256Digest
    requested_by: str = Field(min_length=1, max_length=256)
    allowed_approver_roles: tuple[str, ...] = Field(min_length=1)
    status: ApprovalStatus = ApprovalStatus.PENDING
    revision: int = Field(default=0, ge=0)
    requested_at: UtcDatetime
    expires_at: UtcDatetime
    decided_at: UtcDatetime | None = None
    decided_by: str | None = Field(default=None, max_length=256)
    claimed_at: UtcDatetime | None = None
    execution_intent_id: str | None = Field(default=None, max_length=256)
    consumed_at: UtcDatetime | None = None
    consumption_code: StableErrorCode | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.expires_at <= self.requested_at:
            raise ValueError("expires_at must be after requested_at")
        role_keys = [role.casefold() for role in self.allowed_approver_roles]
        if any(not role or role != role.strip() for role in self.allowed_approver_roles):
            raise ValueError("approval roles must be non-empty trimmed strings")
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("approval roles must not contain duplicates")
        requires_decision = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.CLAIMED,
            ApprovalStatus.CONSUMED,
            ApprovalStatus.REJECTED,
        }
        if self.status in requires_decision and self.decided_at is None:
            raise ValueError("decided_at is required after an approval decision")
        if self.status in requires_decision and self.decided_by is None:
            raise ValueError("decided_by is required after an approval decision")
        requires_claim = {ApprovalStatus.CLAIMED, ApprovalStatus.CONSUMED}
        if self.status in requires_claim and (
            self.claimed_at is None or self.execution_intent_id is None
        ):
            raise ValueError("claimed approvals require claimed_at and execution_intent_id")
        if self.status not in requires_claim and (
            self.claimed_at is not None or self.execution_intent_id is not None
        ):
            raise ValueError("only claimed or consumed approvals carry claim fields")
        if self.status == ApprovalStatus.PENDING and (
            self.decided_at is not None or self.decided_by is not None
        ):
            raise ValueError("pending approvals cannot carry decision fields")
        if self.status == ApprovalStatus.CONSUMED and self.consumed_at is None:
            raise ValueError("consumed_at is required for CONSUMED approvals")
        if self.status != ApprovalStatus.CONSUMED and self.consumed_at is not None:
            raise ValueError("only CONSUMED approvals carry consumed_at")
        if self.status != ApprovalStatus.CONSUMED and self.consumption_code is not None:
            raise ValueError("only CONSUMED approvals carry consumption_code")
        if self.decided_at is not None and self.decided_at < self.requested_at:
            raise ValueError("decided_at must not precede requested_at")
        if self.claimed_at is not None and self.decided_at is not None:
            if self.claimed_at < self.decided_at:
                raise ValueError("claimed_at must not precede decided_at")
        if self.consumed_at is not None and self.decided_at is not None:
            if self.consumed_at < self.decided_at:
                raise ValueError("consumed_at must not precede decided_at")
        if self.consumed_at is not None and self.claimed_at is not None:
            if self.consumed_at < self.claimed_at:
                raise ValueError("consumed_at must not precede claimed_at")


class ApprovalRequest(StrictDomainModel):
    """Trusted input used to request approval for one prepared invocation."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    tool_call_id: ToolCallId
    normalized_args: dict[str, JsonValue]
    requested_by: str = Field(min_length=1, max_length=256)
    allowed_approver_roles: tuple[str, ...] = Field(min_length=1)
    ttl_seconds: int = Field(default=900, ge=1, le=86_400)

    def model_post_init(self, __context: Any) -> None:
        role_keys = [role.casefold() for role in self.allowed_approver_roles]
        if any(not role or role != role.strip() for role in self.allowed_approver_roles):
            raise ValueError("approval roles must be non-empty trimmed strings")
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("approval roles must not contain duplicates")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.requested_by):
            raise ValueError("requested_by must not contain control characters")


class Approver(StrictDomainModel):
    """Trusted local approver context supplied by a controlled entry point."""

    model_config = ConfigDict(frozen=True)

    actor: str = Field(min_length=1, max_length=256)
    roles: tuple[str, ...] = Field(min_length=1)

    def model_post_init(self, __context: Any) -> None:
        role_keys = [role.casefold() for role in self.roles]
        if any(not role or role != role.strip() for role in self.roles):
            raise ValueError("approver roles must be non-empty trimmed strings")
        if len(role_keys) != len(set(role_keys)):
            raise ValueError("approver roles must not contain duplicates")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.actor):
            raise ValueError("approver actor must not contain control characters")


class ChannelDecision(StrictDomainModel):
    """Untrusted decision observation returned by an approval channel."""

    model_config = ConfigDict(frozen=True)

    approval_id: ApprovalId
    decision: ApprovalDecision
    actor: str = Field(min_length=1, max_length=256)
    roles: tuple[str, ...] = Field(default=())


class Evidence(StrictDomainModel):
    """A versioned fact supporting tool or completion decisions."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    evidence_id: EvidenceId
    run_id: RunId
    tool_call_id: ToolCallId | None = None
    kind: EvidenceKind
    path: RelativePath | None = None
    command_id: str | None = Field(default=None, max_length=256)
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    content_digest: Sha256Digest
    created_at: UtcDatetime
    is_valid: bool = True

    def model_post_init(self, __context: Any) -> None:
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be provided together")
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must not precede line_start")
        if self.kind in {EvidenceKind.READ, EvidenceKind.DOCUMENT} and self.path is None:
            raise ValueError("file evidence requires a path")


class CompletionItem(StrictDomainModel):
    """One model-independent completion check."""

    model_config = ConfigDict(frozen=True)

    item_id: str = Field(min_length=1, max_length=128)
    passed: bool
    code: StableErrorCode
    message: str = Field(min_length=1, max_length=2000)
    evidence_ids: tuple[EvidenceId, ...] = ()


class CompletionCheck(StrictDomainModel):
    """Program-owned acceptance result for one run."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    run_id: RunId
    acceptance_status: AcceptanceStatus
    items: tuple[CompletionItem, ...] = ()
    failure_summary: str | None = Field(default=None, max_length=4000)
    checked_at: UtcDatetime
    evidence_ids: tuple[EvidenceId, ...] = ()

    def model_post_init(self, __context: Any) -> None:
        if self.acceptance_status == AcceptanceStatus.ACCEPTED:
            if not self.items or not all(item.passed for item in self.items):
                raise ValueError("ACCEPTED requires at least one passing completion item")
            if self.failure_summary is not None:
                raise ValueError("ACCEPTED cannot include a failure summary")


class TestExecutionRecord(StrictDomainModel):
    """Append-only verifier-owned evidence for one baseline or final test run."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    test_execution_id: str = Field(min_length=1, max_length=128)
    run_id: RunId
    phase: TestExecutionPhase
    command_id: str = Field(min_length=1, max_length=256)
    tool_call_id: ToolCallId | None = None
    command_evidence_id: EvidenceId | None = None
    report_evidence_id: EvidenceId
    declared_argv: tuple[str, ...] = Field(min_length=1)
    effective_argv_digest: Sha256Digest
    cwd: str = Field(min_length=1, max_length=4096)
    config_fingerprint: Sha256Digest
    workspace_pre_digest: Sha256Digest
    workspace_post_digest: Sha256Digest
    execution_status: TestExecutionStatus
    exit_code: int | None = None
    report_digest: Sha256Digest
    report_size_bytes: int = Field(ge=1)
    report_truncated: bool = False
    parser_version: Literal[1] = 1
    total_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    unknown_count: int = Field(ge=0)
    failure_summary: str | None = Field(default=None, max_length=8192)
    created_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        if self.total_count != (
            self.passed_count
            + self.failed_count
            + self.error_count
            + self.skipped_count
            + self.unknown_count
        ):
            raise ValueError("test execution counts must sum to total_count")
        if self.report_truncated and self.execution_status != TestExecutionStatus.UNKNOWN_RESULT:
            raise ValueError("truncated reports require UNKNOWN_RESULT")


class TestBaseline(StrictDomainModel):
    """Persisted startup baseline for one test-repair run."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    baseline_id: str = Field(min_length=1, max_length=128)
    run_id: RunId
    test_execution_id: str = Field(min_length=1, max_length=128)
    command_id: str = Field(min_length=1, max_length=256)
    declared_argv: tuple[str, ...] = Field(min_length=1)
    effective_argv_digest: Sha256Digest
    cwd: str = Field(min_length=1, max_length=4096)
    config_fingerprint: Sha256Digest
    workspace_baseline_digest: Sha256Digest
    report_evidence_id: EvidenceId
    created_at: UtcDatetime


class TestNodeRecord(StrictDomainModel):
    """One normalized persisted JUnit testcase."""

    model_config = ConfigDict(frozen=True)

    test_execution_id: str = Field(min_length=1, max_length=128)
    node_key: Sha256Digest
    label: str = Field(min_length=1, max_length=512)
    file: RelativePath
    status: TestNodeStatus
    failure_code: str | None = Field(default=None, max_length=128)
    failure_summary: str | None = Field(default=None, max_length=8192)


class PlanItem(StrictDomainModel):
    """One bounded, publicly visible plan item."""

    model_config = ConfigDict(frozen=True)

    ordinal: int = Field(ge=0)
    summary: str = Field(min_length=1, max_length=1000)


class RunPlan(StrictDomainModel):
    """The current bounded plan for a run."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    run_id: RunId
    revision: int = Field(ge=0)
    items: tuple[PlanItem, ...] = Field(min_length=1, max_length=50)
    updated_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        if tuple(item.ordinal for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("plan item ordinals must be contiguous from zero")
        encoded = json.dumps(
            [item.model_dump(mode="json") for item in self.items],
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > 32_768:
            raise ValueError("run plan exceeds 32 KiB")


@dataclass(frozen=True)
class LeasePolicy:
    """Bounded run-lease ownership policy."""

    ttl_seconds: int = 30
    heartbeat_interval_seconds: int | None = None

    def __post_init__(self) -> None:
        if type(self.ttl_seconds) is not int or not 10 <= self.ttl_seconds <= 300:
            raise ValueError("lease TTL must be between 10 and 300 seconds")
        interval = (
            self.heartbeat_interval_seconds
            if self.heartbeat_interval_seconds is not None
            else min(10, self.ttl_seconds // 3)
        )
        if not 1 <= interval < self.ttl_seconds:
            raise ValueError("lease heartbeat interval must be positive and shorter than TTL")
        object.__setattr__(self, "heartbeat_interval_seconds", interval)


class RunLease(StrictDomainModel):
    """An acquired run-scoped ownership lease."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    owner_id: str = Field(min_length=1, max_length=256)
    token: str = Field(min_length=32, max_length=256)
    acquired_at: UtcDatetime
    heartbeat_at: UtcDatetime
    expires_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        if self.heartbeat_at < self.acquired_at:
            raise ValueError("lease heartbeat must not precede acquisition")
        if self.expires_at <= self.heartbeat_at:
            raise ValueError("lease expiry must follow heartbeat")


class RunSnapshot(StrictDomainModel):
    """Consistent read of persisted harness state for one run."""

    model_config = ConfigDict(frozen=True)

    run: Run
    plan: RunPlan | None = None
    steps: tuple[Step, ...] = ()
    invocations: tuple[ToolInvocation, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    approvals: tuple[Approval, ...] = ()
    completion: CompletionCheck | None = None
    read_at: UtcDatetime


class CheckpointWorkspaceEntry(StrictDomainModel):
    """One bounded Git-visible workspace entry in a checkpoint."""

    model_config = ConfigDict(frozen=True)

    status: str = Field(min_length=1, max_length=32)
    path: RelativePath
    file_type: str = Field(min_length=1, max_length=32)
    content_digest: Sha256Digest


class CheckpointWorkspace(StrictDomainModel):
    """Serializable workspace facts captured at a safety boundary."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    identity: WorkspaceIdentity
    head_oid_or_unborn: str = Field(min_length=1, max_length=128)
    index_digest: Sha256Digest
    workspace_status_digest: Sha256Digest
    workspace_diff_digest: Sha256Digest
    external_dirty_baseline_digest: Sha256Digest
    external_protection: Literal["ENABLED", "DISABLED"]
    workspace_entries: tuple[CheckpointWorkspaceEntry, ...] = ()
    external_entries: tuple[CheckpointWorkspaceEntry, ...] = ()
    entries_truncated: bool = False
    captured_at: UtcDatetime


class CheckpointBoundary(StrictDomainModel):
    """Trusted description of the safe boundary captured by a checkpoint."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    kind: CheckpointBoundaryKind
    boundary_ref: str = Field(min_length=1, max_length=256)
    step_id: StepId | None = None
    tool_call_id: ToolCallId | None = None
    approval_id: ApprovalId | None = None
    public_summary: str = Field(default="", max_length=4000)

    def model_post_init(self, __context: Any) -> None:
        if any(ord(character) < 32 or ord(character) == 127 for character in self.boundary_ref):
            raise ValueError("boundary_ref must not contain control characters")
        if self.kind == CheckpointBoundaryKind.STEP_COMPLETED and self.step_id is None:
            raise ValueError("step boundaries require step_id")
        if (
            self.kind
            in {
                CheckpointBoundaryKind.TOOL_SUCCEEDED,
                CheckpointBoundaryKind.TOOL_FAILED,
                CheckpointBoundaryKind.TOOL_TIMED_OUT,
                CheckpointBoundaryKind.TOOL_UNKNOWN_RESULT,
            }
            and self.tool_call_id is None
        ):
            raise ValueError("tool boundaries require tool_call_id")
        if (
            self.kind
            in {
                CheckpointBoundaryKind.APPROVAL_REQUESTED,
                CheckpointBoundaryKind.APPROVAL_SETTLED,
            }
            and self.approval_id is None
        ):
            raise ValueError("approval boundaries require approval_id")


class CheckpointSnapshot(StrictDomainModel):
    """Complete structured state from which recovery may continue."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    run: Run
    plan: RunPlan | None = None
    steps: tuple[Step, ...] = ()
    invocations: tuple[ToolInvocation, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    approvals: tuple[Approval, ...] = ()
    completion: CompletionCheck | None = None
    last_audit_sequence: int = Field(ge=1)
    last_audit_hash: Sha256Digest
    workspace: CheckpointWorkspace
    recent_observations: tuple[str, ...] = Field(default=(), max_length=10)
    created_at: UtcDatetime


class Checkpoint(StrictDomainModel):
    """Append-only checkpoint metadata and its validated snapshot."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    checkpoint_id: CheckpointId
    run_id: RunId
    sequence: int = Field(ge=1)
    boundary: CheckpointBoundary
    run_revision: int = Field(ge=0)
    last_audit_sequence: int = Field(ge=1)
    last_audit_hash: Sha256Digest
    snapshot_digest: Sha256Digest
    snapshot: CheckpointSnapshot
    created_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        if self.snapshot.run.run_id != self.run_id:
            raise ValueError("checkpoint snapshot run_id does not match")
        if (
            self.snapshot.last_audit_sequence != self.last_audit_sequence
            or self.snapshot.last_audit_hash != self.last_audit_hash
        ):
            raise ValueError("checkpoint audit head does not match snapshot")


class CheckpointVerification(StrictDomainModel):
    """Result of validating one checkpoint against persisted state."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    checkpoint_id: CheckpointId
    run_id: RunId
    valid: bool
    checked_audit_sequence: int = Field(ge=0)
    code: StableErrorCode | None = None
    reason: str | None = Field(default=None, max_length=1000)
    checked_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        if not self.valid and self.code is None:
            raise ValueError("invalid checkpoint verification requires a code")


class RemainingBudget(StrictDomainModel):
    """Non-negative remaining resource amounts."""

    model_config = ConfigDict(frozen=True)

    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    token_cost: Decimal = Field(ge=0)
    wall_time_seconds: int = Field(ge=0)


class ContinuationStep(StrictDomainModel):
    """One bounded publicly visible step summary."""

    model_config = ConfigDict(frozen=True)

    step_id: StepId
    ordinal: int = Field(ge=0)
    status: StepStatus
    summary: str = Field(max_length=4000)


class ContinuationEvidence(StrictDomainModel):
    """One currently valid evidence fact available after recovery."""

    model_config = ConfigDict(frozen=True)

    evidence_id: EvidenceId
    kind: EvidenceKind
    path: RelativePath | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    content_digest: Sha256Digest

    def model_post_init(self, __context: Any) -> None:
        if (self.line_start is None) != (self.line_end is None):
            raise ValueError("line_start and line_end must be provided together")
        if self.line_start is not None and self.line_end is not None:
            if self.line_end < self.line_start:
                raise ValueError("line_end must not precede line_start")


class ContinuationInvocation(StrictDomainModel):
    """A prepared invocation awaiting a governed recovery decision."""

    model_config = ConfigDict(frozen=True)

    tool_call_id: ToolCallId
    tool_name: ToolName
    status: InvocationStatus
    args_digest: Sha256Digest
    approval_id: ApprovalId | None = None


class ContinuationApproval(StrictDomainModel):
    """A non-terminal approval relevant to resumed work."""

    model_config = ConfigDict(frozen=True)

    approval_id: ApprovalId
    tool_call_id: ToolCallId
    tool_name: ToolName
    status: ApprovalStatus
    binding_digest: Sha256Digest
    expires_at: UtcDatetime


class ContinuationContext(StrictDomainModel):
    """Bounded deterministic context for constructing a new agent."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    run_id: RunId
    checkpoint_id: CheckpointId | None = None
    task_id: str = Field(min_length=1, max_length=128)
    task_kind: TaskKind
    task_description: str = Field(min_length=1, max_length=4000)
    config_fingerprint: Sha256Digest
    workspace_root: str = Field(min_length=1, max_length=4096)
    git_root: str = Field(min_length=1, max_length=4096)
    current_status: RunStatus
    stop_reason: StopReason | None = None
    budget: RunBudget
    usage: BudgetUsage
    remaining_budget: RemainingBudget
    plan: RunPlan | None = None
    completed_steps: tuple[ContinuationStep, ...] = Field(default=(), max_length=10)
    valid_evidence: tuple[ContinuationEvidence, ...] = ()
    invalid_evidence_ids: tuple[EvidenceId, ...] = ()
    pending_invocation: ContinuationInvocation | None = None
    pending_approval: ContinuationApproval | None = None
    prohibited_actions: tuple[str, ...] = Field(min_length=1)
    context_digest: Sha256Digest
    generated_at: UtcDatetime


class RecoveryPlan(StrictDomainModel):
    """Persisted deterministic recovery decision; never an execution grant."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    recovery_id: RecoveryId
    run_id: RunId
    checkpoint_id: CheckpointId | None = None
    source_run_revision: int = Field(ge=0)
    status: RecoveryStatus
    action: RecoveryAction
    reason_code: StableErrorCode | None = None
    invocation_id: ToolCallId | None = None
    approval_id: ApprovalId | None = None
    valid_evidence_ids: tuple[EvidenceId, ...] = ()
    invalid_evidence_ids: tuple[EvidenceId, ...] = ()
    continuation_context_digest: Sha256Digest | None = None
    created_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        expected_status = {
            RecoveryAction.CONTINUE_AGENT: RecoveryStatus.RESUMABLE,
            RecoveryAction.REENTER_GATE: RecoveryStatus.RESUMABLE,
            RecoveryAction.RESUME_CLAIM_THEN_REENTER_GATE: RecoveryStatus.RESUMABLE,
            RecoveryAction.REBUILD_CHECKPOINT_THEN_CONTINUE: RecoveryStatus.RESUMABLE,
            RecoveryAction.WAIT_FOR_APPROVAL: RecoveryStatus.WAITING_APPROVAL,
            RecoveryAction.MARK_UNKNOWN_RESULT: RecoveryStatus.MANUAL_REVIEW,
            RecoveryAction.MANUAL_REVIEW: RecoveryStatus.MANUAL_REVIEW,
            RecoveryAction.ALREADY_TERMINAL: RecoveryStatus.ALREADY_TERMINAL,
        }[self.action]
        if self.status != expected_status:
            raise ValueError("recovery action and status do not match")
        if len(set(self.valid_evidence_ids)) != len(self.valid_evidence_ids):
            raise ValueError("valid_evidence_ids must not contain duplicates")
        if len(set(self.invalid_evidence_ids)) != len(self.invalid_evidence_ids):
            raise ValueError("invalid_evidence_ids must not contain duplicates")
        if set(self.valid_evidence_ids) & set(self.invalid_evidence_ids):
            raise ValueError("evidence cannot be both valid and invalid")
        if (
            self.action
            in {
                RecoveryAction.REENTER_GATE,
                RecoveryAction.RESUME_CLAIM_THEN_REENTER_GATE,
                RecoveryAction.REBUILD_CHECKPOINT_THEN_CONTINUE,
                RecoveryAction.MARK_UNKNOWN_RESULT,
            }
            and self.invocation_id is None
        ):
            raise ValueError("recovery action requires an invocation_id")
        if self.action == RecoveryAction.WAIT_FOR_APPROVAL and self.approval_id is None:
            raise ValueError("waiting recovery requires an approval_id")


class RecoveryRecord(StrictDomainModel):
    """Append-only recovery decision record."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    recovery_id: RecoveryId
    run_id: RunId
    checkpoint_id: CheckpointId | None = None
    plan: RecoveryPlan
    idempotency_key: str = Field(min_length=1, max_length=128)
    created_at: UtcDatetime

    def model_post_init(self, __context: Any) -> None:
        if (
            self.plan.recovery_id != self.recovery_id
            or self.plan.run_id != self.run_id
            or self.plan.checkpoint_id != self.checkpoint_id
        ):
            raise ValueError("recovery record payload does not match indexed fields")


class BudgetDeltaCommand(StrictDomainModel):
    """One idempotent budget increment from a trusted source."""

    model_config = ConfigDict(frozen=True)

    run_id: RunId
    source_kind: BudgetSourceKind
    source_id: BudgetSourceId
    category: BudgetCategory
    expected_run_revision: int = Field(ge=0)
    delta: Decimal = Field(ge=0)

    def model_post_init(self, __context: Any) -> None:
        if any(ord(character) < 32 or ord(character) == 127 for character in self.source_id):
            raise ValueError("source_id must not contain control characters")
        if self.category in {
            BudgetCategory.STEPS,
            BudgetCategory.TOOL_CALLS,
            BudgetCategory.WALL_TIME,
        }:
            if self.delta != self.delta.to_integral_value():
                raise ValueError(f"{self.category.value} delta must be an integer")


@runtime_checkable
class Clock(Protocol):
    """Injectable UTC clock."""

    def now(self) -> datetime:
        """Return the current UTC time."""


@runtime_checkable
class IdFactory(Protocol):
    """Injectable identifier factory for deterministic tests."""

    def new_trace_id(self) -> str:
        """Create a trace ID."""

    def new_run_id(self) -> str:
        """Create a run ID."""

    def new_step_id(self) -> str:
        """Create a step ID."""

    def new_evidence_id(self) -> str:
        """Create an evidence ID."""

    def new_approval_id(self) -> str:
        """Create an approval ID."""

    def new_checkpoint_id(self) -> str:
        """Create a checkpoint ID."""

    def new_recovery_id(self) -> str:
        """Create a recovery decision ID."""


@runtime_checkable
class FaultInjector(Protocol):
    """Test-only deterministic fault injection contract."""

    def record(self, point: str) -> int:
        """Record that a fault point was reached and return its call count."""

    def trigger(self, point: str) -> None:
        """Raise a controlled exception if the fault point is armed."""

    def call_count(self, point: str) -> int:
        """Return how many times the point was recorded."""

    def arm(self, point: str) -> None:
        """Arm a fault for the next matching point."""

    def disable(self, point: str) -> None:
        """Disable an armed fault point."""


@runtime_checkable
class ApprovalChannel(Protocol):
    """Transport boundary for human approval decisions."""

    @property
    def name(self) -> str:
        """Return the stable channel name."""

    def submit(self, approval: Approval) -> None:
        """Submit an approval for human handling."""

    def poll(self, approval_id: ApprovalId) -> ChannelDecision | None:
        """Return an observed decision without granting execution authority."""


class SystemClock:
    """Production clock returning timezone-aware UTC values."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class Uuid4IdFactory:
    """Production ID factory using unpredictable UUIDv4 values."""

    def new_trace_id(self) -> str:
        return uuid4().hex

    def new_run_id(self) -> str:
        return f"run_{uuid4().hex}"

    def new_step_id(self) -> str:
        return f"step_{uuid4().hex}"

    def new_evidence_id(self) -> str:
        return f"evidence_{uuid4().hex}"

    def new_approval_id(self) -> str:
        return f"approval_{uuid4().hex}"

    def new_checkpoint_id(self) -> str:
        return f"checkpoint_{uuid4().hex}"

    def new_recovery_id(self) -> str:
        return f"recovery_{uuid4().hex}"


ModelT = TypeVar("ModelT", bound=BaseModel)


def _format_location(location: tuple[int | str, ...]) -> str:
    if not location:
        return "$"
    rendered = ""
    for part in location:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else str(part)
    return rendered


def _domain_error_code(error: dict[str, Any]) -> ErrorCode:
    error_type = str(error.get("type", ""))
    location = tuple(error.get("loc", ()))
    enum_fields = {
        "acceptance_status",
        "action",
        "approval_requirement",
        "category",
        "concurrency_class",
        "decision",
        "kind",
        "output_kind",
        "phase",
        "retry_policy",
        "risk",
        "status",
        "stop_reason",
        "strategy",
        "source_kind",
        "task_kind",
        "recovery_status",
    }
    if "schema_version" in location:
        return ErrorCode.DOMAIN_SCHEMA_VERSION_UNSUPPORTED
    if error_type == "enum" or (
        error_type == "is_instance_of"
        and any(str(part).split(".")[-1] in enum_fields for part in location)
    ):
        return ErrorCode.DOMAIN_ENUM_INVALID
    if "datetime" in error_type or "timezone" in str(error.get("msg", "")).lower():
        return ErrorCode.DOMAIN_TIME_INVALID
    if error_type == "string_pattern_mismatch" and any(
        str(part).endswith("_id") or str(part) == "trace_id" for part in location
    ):
        return ErrorCode.DOMAIN_ID_INVALID
    return ErrorCode.DOMAIN_INVALID_MODEL


def validate_contract(model_type: type[ModelT], payload: object) -> ModelT:
    """Validate a payload and convert Pydantic errors into stable domain issues."""

    try:
        return model_type.model_validate(payload, strict=True)
    except ValidationError as exc:
        issues = tuple(
            sorted(
                (
                    ValidationIssue(
                        code=_domain_error_code(error),
                        path=_format_location(tuple(error.get("loc", ()))),
                        message=str(error.get("msg", "invalid model value"))[:1000],
                    )
                    for error in exc.errors(include_input=False, include_url=False)[:100]
                ),
                key=lambda issue: (issue.path, issue.code.value),
            )
        )
        raise DomainContractError(issues[0].code, issues) from exc


def canonical_json_dump(model: BaseModel) -> str:
    """Serialize a model to deterministic UTF-8 JSON text."""

    try:
        payload = model.model_dump(mode="json")
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise DomainContractError(ErrorCode.DOMAIN_SERIALIZATION_ERROR) from exc
