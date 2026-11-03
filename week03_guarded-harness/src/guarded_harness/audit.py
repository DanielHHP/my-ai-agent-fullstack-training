"""Append-only, hash-chained audit events for WP-01."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field, JsonValue

from .domain import (
    ApprovalId,
    Clock,
    ErrorCode,
    EvidenceId,
    GatePhase,
    RunId,
    Sha256Digest,
    StableErrorCode,
    StepId,
    StrictDomainModel,
    ToolCallId,
    ToolName,
    UtcDatetime,
)
from .redaction import Redactor
from .storage import StorageError

if TYPE_CHECKING:
    from .storage import StorageUnitOfWork


GENESIS_HASH = f"sha256:{'0' * 64}"


class AuditEventType(str, Enum):
    """Stable event names shared by WP-01 and downstream writers."""

    RUN_CREATED = "RUN_CREATED"
    RUN_STARTED = "RUN_STARTED"
    RUN_WAITING_APPROVAL = "RUN_WAITING_APPROVAL"
    RUN_VERIFYING = "RUN_VERIFYING"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_STOPPED = "RUN_STOPPED"
    RUN_FAILED = "RUN_FAILED"
    RUN_BLOCKED = "RUN_BLOCKED"
    RUN_PLAN_UPDATED = "RUN_PLAN_UPDATED"
    RUN_LEASE_ACQUIRED = "RUN_LEASE_ACQUIRED"
    RUN_LEASE_TAKEOVER = "RUN_LEASE_TAKEOVER"
    RUN_LEASE_RELEASED = "RUN_LEASE_RELEASED"
    STEP_PLANNED = "STEP_PLANNED"
    STEP_STARTED = "STEP_STARTED"
    STEP_COMPLETED = "STEP_COMPLETED"
    STEP_FAILED = "STEP_FAILED"
    TOOL_PREPARED = "TOOL_PREPARED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_SUCCEEDED = "TOOL_SUCCEEDED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_TIMED_OUT = "TOOL_TIMED_OUT"
    TOOL_UNKNOWN_RESULT = "TOOL_UNKNOWN_RESULT"
    EVIDENCE_RECORDED = "EVIDENCE_RECORDED"
    BUDGET_UPDATED = "BUDGET_UPDATED"
    COMPLETION_RECORDED = "COMPLETION_RECORDED"
    GATE_DECISION = "GATE_DECISION"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    APPROVAL_CLAIMED = "APPROVAL_CLAIMED"
    APPROVAL_CONSUMED = "APPROVAL_CONSUMED"
    CHECKPOINT_WRITTEN = "CHECKPOINT_WRITTEN"


class AuditStatus(str, Enum):
    """Outcome attached to one audit fact."""

    PENDING = "PENDING"
    ALLOWED = "ALLOWED"
    DENIED = "DENIED"
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    UNKNOWN_RESULT = "UNKNOWN_RESULT"
    STOPPED = "STOPPED"
    BLOCKED = "BLOCKED"


class AuditDecision(str, Enum):
    """Decision dimension used primarily by Gate events."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AuditEventDraft(StrictDomainModel):
    """Caller-supplied audit facts before envelope and hash assignment."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    run_id: RunId
    event_type: AuditEventType
    phase: GatePhase
    status: AuditStatus
    decision: AuditDecision = AuditDecision.NOT_APPLICABLE
    code: StableErrorCode | None = None
    step_id: StepId | None = None
    tool_call_id: ToolCallId | None = None
    approval_id: ApprovalId | None = None
    tool_name: ToolName | None = None
    rule_id: str | None = Field(default=None, min_length=1, max_length=256)
    input_summary: JsonValue | None = None
    output_summary: JsonValue | None = None
    evidence_ref: EvidenceId | None = None

    def model_post_init(self, __context: Any) -> None:
        error_statuses = {
            AuditStatus.DENIED,
            AuditStatus.FAILED,
            AuditStatus.TIMED_OUT,
            AuditStatus.UNKNOWN_RESULT,
            AuditStatus.BLOCKED,
        }
        if self.status in error_statuses and self.code is None:
            raise ValueError("error audit status requires a stable code")


class AuditEventRecord(StrictDomainModel):
    """Persisted audit event with assigned sequence and hash-chain links."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=128)
    run_id: RunId
    sequence: int = Field(ge=1)
    occurred_at: UtcDatetime
    event_type: AuditEventType
    phase: GatePhase
    status: AuditStatus
    decision: AuditDecision
    code: StableErrorCode | None = None
    step_id: StepId | None = None
    tool_call_id: ToolCallId | None = None
    approval_id: ApprovalId | None = None
    actor: str = Field(min_length=1, max_length=256)
    tool_name: ToolName | None = None
    rule_id: str | None = Field(default=None, min_length=1, max_length=256)
    workspace_fingerprint: Sha256Digest
    input_summary: JsonValue | None = None
    output_summary: JsonValue | None = None
    evidence_ref: EvidenceId | None = None
    prev_hash: Sha256Digest
    event_hash: Sha256Digest


class AuditChainReport(StrictDomainModel):
    """Result of revalidating one run's complete audit chain."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    run_id: RunId
    event_count: int = Field(ge=0)
    last_sequence: int = Field(ge=0)
    valid: bool
    code: ErrorCode | None = None
    detail: str | None = Field(default=None, max_length=1000)


class AuditStoreError(RuntimeError):
    """Stable audit failure without leaking event values."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        operation: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.operation = operation
        self.message = message[:1000]
        self.retryable = retryable
        super().__init__(f"{code.value}: {self.message}")


class AuditStore:
    """Audit operations bound to one active UnitOfWork."""

    def __init__(self, unit_of_work: StorageUnitOfWork) -> None:
        unit_of_work.ensure_active()
        self._unit = unit_of_work
        self._connection = unit_of_work.connection
        self._clock: Clock = unit_of_work.storage.clock
        self._redactor = Redactor()

    def append(self, draft: AuditEventDraft) -> AuditEventRecord:
        """Append one redacted event after assigning sequence and hashes."""

        self._ensure_active()
        run_row = self._connection.execute(
            "SELECT workspace_json FROM runs WHERE run_id = ?",
            (draft.run_id,),
        ).fetchone()
        if run_row is None:
            self._raise(ErrorCode.STATE_NOT_FOUND, "append", "audit run does not exist")
        try:
            workspace = json.loads(str(run_row["workspace_json"]))
            workspace_fingerprint = str(workspace["baseline_fingerprint"])
        except (KeyError, TypeError, ValueError):
            self._raise(ErrorCode.STATE_INTEGRITY_ERROR, "append", "run workspace is invalid")
        previous = self._connection.execute(
            """
            SELECT sequence, event_hash FROM audit_events
            WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (draft.run_id,),
        ).fetchone()
        sequence = 1 if previous is None else int(previous["sequence"]) + 1
        prev_hash = GENESIS_HASH if previous is None else str(previous["event_hash"])
        occurred_at = self._clock.now()
        event_id = _event_id(draft.run_id, sequence)
        actor = self._redacted_text(self._unit.actor, "append")
        input_summary = self._redacted_value(draft.input_summary, "append")
        output_summary = self._redacted_value(draft.output_summary, "append")
        payload = {
            "audit_schema": 1,
            "schema_version": 1,
            "event_id": event_id,
            "run_id": draft.run_id,
            "sequence": sequence,
            "occurred_at": _serialize_datetime(occurred_at),
            "event_type": draft.event_type.value,
            "phase": draft.phase.value,
            "status": draft.status.value,
            "decision": draft.decision.value,
            "code": draft.code,
            "step_id": draft.step_id,
            "tool_call_id": draft.tool_call_id,
            "approval_id": draft.approval_id,
            "actor": actor,
            "tool_name": draft.tool_name,
            "rule_id": draft.rule_id,
            "workspace_fingerprint": workspace_fingerprint,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "evidence_ref": draft.evidence_ref,
            "prev_hash": prev_hash,
        }
        event_hash = _event_hash(payload)
        record = AuditEventRecord(
            event_id=event_id,
            run_id=draft.run_id,
            sequence=sequence,
            occurred_at=occurred_at,
            event_type=draft.event_type,
            phase=draft.phase,
            status=draft.status,
            decision=draft.decision,
            code=draft.code,
            step_id=draft.step_id,
            tool_call_id=draft.tool_call_id,
            approval_id=draft.approval_id,
            actor=actor,
            tool_name=draft.tool_name,
            rule_id=draft.rule_id,
            workspace_fingerprint=workspace_fingerprint,
            input_summary=input_summary,
            output_summary=output_summary,
            evidence_ref=draft.evidence_ref,
            prev_hash=prev_hash,
            event_hash=event_hash,
        )
        try:
            self._connection.execute(
                """
                INSERT INTO audit_events(
                    event_id, schema_version, run_id, sequence, occurred_at,
                    event_type, phase, status, decision, code,
                    step_id, tool_call_id, approval_id, actor, tool_name,
                    rule_id, workspace_fingerprint, input_summary_json,
                    output_summary_json, evidence_ref, prev_hash, event_hash
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    record.event_id,
                    record.schema_version,
                    record.run_id,
                    record.sequence,
                    _serialize_datetime(record.occurred_at),
                    record.event_type.value,
                    record.phase.value,
                    record.status.value,
                    record.decision.value,
                    record.code,
                    record.step_id,
                    record.tool_call_id,
                    record.approval_id,
                    record.actor,
                    record.tool_name,
                    record.rule_id,
                    record.workspace_fingerprint,
                    _json_or_none(record.input_summary),
                    _json_or_none(record.output_summary),
                    record.evidence_ref,
                    record.prev_hash,
                    record.event_hash,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AuditStoreError(
                ErrorCode.AUDIT_SEQUENCE_CONFLICT,
                operation="append",
                message="audit sequence or event ID already exists",
            ) from exc
        return record

    def get(self, run_id: str, sequence: int) -> AuditEventRecord | None:
        """Read one event by run and sequence."""

        self._ensure_active()
        row = self._connection.execute(
            "SELECT * FROM audit_events WHERE run_id = ? AND sequence = ?",
            (run_id, sequence),
        ).fetchone()
        return _record_from_row(row) if row is not None else None

    def list_run_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> tuple[AuditEventRecord, ...]:
        """List run events in stable sequence order."""

        self._ensure_active()
        self._validate_limit(limit)
        rows = self._connection.execute(
            """
            SELECT * FROM audit_events
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence LIMIT ?
            """,
            (run_id, after_sequence, limit),
        ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def list_tool_events(
        self,
        run_id: str,
        tool_call_id: str,
        *,
        limit: int = 1000,
    ) -> tuple[AuditEventRecord, ...]:
        """List all audit facts associated with one tool call."""

        self._ensure_active()
        self._validate_limit(limit)
        rows = self._connection.execute(
            """
            SELECT * FROM audit_events
            WHERE run_id = ? AND tool_call_id = ?
            ORDER BY sequence LIMIT ?
            """,
            (run_id, tool_call_id, limit),
        ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def list_approval_events(
        self,
        run_id: str,
        approval_id: str,
        *,
        limit: int = 1000,
    ) -> tuple[AuditEventRecord, ...]:
        """List all audit facts associated with one approval."""

        self._ensure_active()
        self._validate_limit(limit)
        rows = self._connection.execute(
            """
            SELECT * FROM audit_events
            WHERE run_id = ? AND approval_id = ?
            ORDER BY sequence LIMIT ?
            """,
            (run_id, approval_id, limit),
        ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def verify_chain(self, run_id: str) -> AuditChainReport:
        """Recompute all links and hashes for one run."""

        self._ensure_active()
        rows = self._connection.execute(
            "SELECT * FROM audit_events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        previous_hash = GENESIS_HASH
        for expected_sequence, row in enumerate(rows, start=1):
            record = _record_from_row(row)
            if record.sequence != expected_sequence:
                return AuditChainReport(
                    run_id=run_id,
                    event_count=len(rows),
                    last_sequence=int(rows[-1]["sequence"]) if rows else 0,
                    valid=False,
                    code=ErrorCode.AUDIT_SEQUENCE_CONFLICT,
                    detail="audit sequence is not contiguous",
                )
            if record.prev_hash != previous_hash:
                return AuditChainReport(
                    run_id=run_id,
                    event_count=len(rows),
                    last_sequence=int(rows[-1]["sequence"]) if rows else 0,
                    valid=False,
                    code=ErrorCode.AUDIT_CHAIN_INVALID,
                    detail="audit previous hash does not match",
                )
            if record.event_hash != _event_hash(_record_payload(record)):
                return AuditChainReport(
                    run_id=run_id,
                    event_count=len(rows),
                    last_sequence=int(rows[-1]["sequence"]) if rows else 0,
                    valid=False,
                    code=ErrorCode.AUDIT_CHAIN_INVALID,
                    detail="audit event hash does not match",
                )
            previous_hash = record.event_hash
        return AuditChainReport(
            run_id=run_id,
            event_count=len(rows),
            last_sequence=int(rows[-1]["sequence"]) if rows else 0,
            valid=True,
        )

    def _redacted_text(self, value: str, operation: str) -> str:
        result = self._redactor.redact(value).value
        if not isinstance(result, str):
            self._raise(ErrorCode.REDACTION_FAILED, operation, "text redaction failed")
        return result

    def _redacted_value(self, value: JsonValue | None, operation: str) -> JsonValue | None:
        if value is None:
            return None
        return self._redactor.redact(value).value

    def _validate_limit(self, limit: int) -> None:
        if not 1 <= limit <= 1000:
            self._raise(
                ErrorCode.AUDIT_EVENT_INVALID,
                "query",
                "audit query limit must be between 1 and 1000",
            )

    def _ensure_active(self) -> None:
        try:
            self._unit.ensure_active()
        except StorageError as exc:
            raise AuditStoreError(
                exc.code,
                operation=exc.operation,
                message=exc.message,
                retryable=exc.retryable,
            ) from exc

    @staticmethod
    def _raise(
        code: ErrorCode,
        operation: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        raise AuditStoreError(
            code,
            operation=operation,
            message=message,
            retryable=retryable,
        )


def _record_from_row(row: sqlite3.Row) -> AuditEventRecord:
    return AuditEventRecord(
        schema_version=int(row["schema_version"]),
        event_id=str(row["event_id"]),
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        occurred_at=_parse_datetime(str(row["occurred_at"])),
        event_type=AuditEventType(str(row["event_type"])),
        phase=GatePhase(str(row["phase"])),
        status=AuditStatus(str(row["status"])),
        decision=AuditDecision(str(row["decision"])),
        code=str(row["code"]) if row["code"] else None,
        step_id=str(row["step_id"]) if row["step_id"] else None,
        tool_call_id=str(row["tool_call_id"]) if row["tool_call_id"] else None,
        approval_id=str(row["approval_id"]) if row["approval_id"] else None,
        actor=str(row["actor"]),
        tool_name=str(row["tool_name"]) if row["tool_name"] else None,
        rule_id=str(row["rule_id"]) if row["rule_id"] else None,
        workspace_fingerprint=str(row["workspace_fingerprint"]),
        input_summary=json.loads(str(row["input_summary_json"]))
        if row["input_summary_json"]
        else None,
        output_summary=json.loads(str(row["output_summary_json"]))
        if row["output_summary_json"]
        else None,
        evidence_ref=str(row["evidence_ref"]) if row["evidence_ref"] else None,
        prev_hash=str(row["prev_hash"]),
        event_hash=str(row["event_hash"]),
    )


def _event_id(run_id: str, sequence: int) -> str:
    run_suffix = run_id.removeprefix("run_")
    return f"audit_{run_suffix}_{sequence:020d}"


def _record_payload(record: AuditEventRecord) -> dict[str, object]:
    return {
        "audit_schema": 1,
        "schema_version": record.schema_version,
        "event_id": record.event_id,
        "run_id": record.run_id,
        "sequence": record.sequence,
        "occurred_at": _serialize_datetime(record.occurred_at),
        "event_type": record.event_type.value,
        "phase": record.phase.value,
        "status": record.status.value,
        "decision": record.decision.value,
        "code": record.code,
        "step_id": record.step_id,
        "tool_call_id": record.tool_call_id,
        "approval_id": record.approval_id,
        "actor": record.actor,
        "tool_name": record.tool_name,
        "rule_id": record.rule_id,
        "workspace_fingerprint": record.workspace_fingerprint,
        "input_summary": record.input_summary,
        "output_summary": record.output_summary,
        "evidence_ref": record.evidence_ref,
        "prev_hash": record.prev_hash,
    }


def _event_hash(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _json_or_none(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_datetime(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
