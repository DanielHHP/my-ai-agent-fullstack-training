"""Fail-closed SQLite storage, migrations, and transaction infrastructure."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .domain import Clock, ErrorCode, FaultInjector

if TYPE_CHECKING:
    from .approval import ApprovalStore
    from .audit import AuditStore
    from .state_store import StateStore

SCHEMA_VERSION = 4
_GENESIS_HASH = f"sha256:{'0' * 64}"


_MIGRATION_V1 = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    task_description TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    workspace_json TEXT NOT NULL,
    max_steps INTEGER NOT NULL CHECK (max_steps >= 1),
    max_tool_calls INTEGER NOT NULL CHECK (max_tool_calls >= 0),
    max_token_cost TEXT NOT NULL,
    max_wall_time_seconds INTEGER NOT NULL CHECK (max_wall_time_seconds >= 1),
    used_steps INTEGER NOT NULL DEFAULT 0 CHECK (used_steps >= 0),
    used_tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_tool_calls >= 0),
    used_token_cost TEXT NOT NULL DEFAULT '0',
    used_wall_time_seconds INTEGER NOT NULL DEFAULT 0 CHECK (used_wall_time_seconds >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('NEW', 'RUNNING', 'WAITING_APPROVAL', 'VERIFYING',
                   'COMPLETED', 'STOPPED', 'FAILED', 'BLOCKED')
    ),
    stop_reason TEXT CHECK (
        stop_reason IS NULL OR stop_reason IN (
            'FINAL_ANSWER', 'BUDGET_EXHAUSTED', 'APPROVAL_REQUIRED',
            'GATE_BLOCKED', 'UNKNOWN_RESULT', 'WORKSPACE_CHANGED', 'INTERNAL_ERROR'
        )
    ),
    acceptance_status TEXT NOT NULL CHECK (
        acceptance_status IN ('NOT_EVALUATED', 'ACCEPTED', 'REJECTED', 'UNVERIFIABLE')
    ),
    current_step_id TEXT,
    last_checkpoint_id TEXT,
    resumed_from_checkpoint_id TEXT,
    candidate_final_answer TEXT,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    CHECK (updated_at >= created_at),
    CHECK (started_at IS NULL OR started_at >= created_at),
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE run_plans (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    plan_schema_version INTEGER NOT NULL CHECK (plan_schema_version = 1),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    items_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    status TEXT NOT NULL CHECK (status IN ('PLANNED', 'RUNNING', 'COMPLETED', 'FAILED')),
    summary TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE (run_id, ordinal),
    CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE tool_calls (
    run_id TEXT NOT NULL REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    tool_call_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    raw_args_json TEXT NOT NULL,
    normalized_args_json TEXT NOT NULL,
    args_digest TEXT NOT NULL,
    risk TEXT NOT NULL CHECK (risk IN ('READ', 'WRITE', 'EXECUTE')),
    required_permissions_json TEXT NOT NULL,
    idempotent INTEGER NOT NULL CHECK (idempotent IN (0, 1)),
    status TEXT NOT NULL CHECK (
        status IN ('PREPARED', 'STARTED', 'SUCCEEDED', 'FAILED', 'TIMED_OUT', 'UNKNOWN_RESULT')
    ),
    approval_id TEXT,
    result_summary TEXT,
    error_code TEXT,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, tool_call_id),
    FOREIGN KEY (run_id, step_id) REFERENCES steps(run_id, step_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK (updated_at >= created_at)
);

CREATE INDEX idx_tool_calls_step ON tool_calls(run_id, step_id);
CREATE INDEX idx_tool_calls_status ON tool_calls(run_id, status);

CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    tool_call_id TEXT,
    kind TEXT NOT NULL CHECK (
        kind IN ('READ', 'DOCUMENT', 'COMMAND', 'TEST_REPORT', 'DIFF', 'AUDIT')
    ),
    path TEXT,
    command_id TEXT,
    line_start INTEGER,
    line_end INTEGER,
    content_digest TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    created_at TEXT NOT NULL,
    is_valid INTEGER NOT NULL DEFAULT 1 CHECK (is_valid IN (0, 1)),
    FOREIGN KEY (run_id, tool_call_id) REFERENCES tool_calls(run_id, tool_call_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK ((line_start IS NULL) = (line_end IS NULL)),
    CHECK (line_start IS NULL OR line_start >= 1),
    CHECK (line_end IS NULL OR line_end >= line_start)
);

CREATE INDEX idx_evidence_kind ON evidence(run_id, kind);
CREATE INDEX idx_evidence_tool_call ON evidence(run_id, tool_call_id);

CREATE TABLE tool_call_evidence (
    run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (run_id, tool_call_id, evidence_id),
    UNIQUE (run_id, tool_call_id, ordinal),
    FOREIGN KEY (run_id, tool_call_id) REFERENCES tool_calls(run_id, tool_call_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX idx_tool_call_evidence ON tool_call_evidence(run_id, evidence_id);

CREATE TABLE completion_results (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    acceptance_status TEXT NOT NULL,
    failure_summary TEXT,
    evidence_ids_json TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    checked_at TEXT NOT NULL
);

CREATE TABLE completion_items (
    run_id TEXT NOT NULL REFERENCES completion_results(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    item_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    PRIMARY KEY (run_id, item_id),
    UNIQUE (run_id, ordinal)
);

CREATE TABLE budget_ledger (
    run_id TEXT NOT NULL REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('STEP', 'TOOL_CALL', 'HARNESS')),
    source_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (
        category IN ('STEPS', 'TOOL_CALLS', 'TOKEN_COST', 'WALL_TIME')
    ),
    delta TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, source_kind, source_id, category),
    UNIQUE (operation_key)
);

CREATE TRIGGER budget_ledger_no_update
BEFORE UPDATE ON budget_ledger
BEGIN
    SELECT RAISE(ABORT, 'BUDGET_LEDGER_APPEND_ONLY');
END;

CREATE TRIGGER budget_ledger_no_delete
BEFORE DELETE ON budget_ledger
BEGIN
    SELECT RAISE(ABORT, 'BUDGET_LEDGER_APPEND_ONLY');
END;

CREATE TABLE run_leases (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    owner_id TEXT NOT NULL,
    token_digest TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    CHECK (heartbeat_at >= acquired_at),
    CHECK (expires_at > heartbeat_at)
);

CREATE INDEX idx_run_leases_expiry ON run_leases(expires_at);

CREATE TABLE audit_events (
    event_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    run_id TEXT NOT NULL REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    occurred_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    decision TEXT NOT NULL,
    code TEXT,
    step_id TEXT,
    tool_call_id TEXT,
    approval_id TEXT,
    actor TEXT NOT NULL,
    tool_name TEXT,
    rule_id TEXT,
    workspace_fingerprint TEXT NOT NULL,
    input_summary_json TEXT,
    output_summary_json TEXT,
    evidence_ref TEXT,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    UNIQUE (run_id, sequence)
);

CREATE INDEX idx_audit_tool ON audit_events(run_id, tool_call_id, sequence);
CREATE INDEX idx_audit_approval ON audit_events(run_id, approval_id, sequence);

CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_APPEND_ONLY');
END;

CREATE TRIGGER audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_APPEND_ONLY');
END;
"""

_MIGRATION_V2 = """
CREATE UNIQUE INDEX idx_steps_run_step ON steps(run_id, step_id);
"""

_MIGRATION_V3 = """
DROP TABLE tool_call_evidence;
"""

_MIGRATION_V4 = """
CREATE TABLE runs_v4 (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    task_kind TEXT NOT NULL,
    task_description TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    workspace_json TEXT NOT NULL,
    max_steps INTEGER NOT NULL CHECK (max_steps >= 1),
    max_tool_calls INTEGER NOT NULL CHECK (max_tool_calls >= 0),
    max_token_cost TEXT NOT NULL,
    max_wall_time_seconds INTEGER NOT NULL CHECK (max_wall_time_seconds >= 1),
    used_steps INTEGER NOT NULL DEFAULT 0 CHECK (used_steps >= 0),
    used_tool_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_tool_calls >= 0),
    used_token_cost TEXT NOT NULL DEFAULT '0',
    used_wall_time_seconds INTEGER NOT NULL DEFAULT 0 CHECK (used_wall_time_seconds >= 0),
    status TEXT NOT NULL CHECK (
        status IN ('NEW', 'RUNNING', 'WAITING_APPROVAL', 'VERIFYING',
                   'COMPLETED', 'STOPPED', 'FAILED', 'BLOCKED')
    ),
    stop_reason TEXT CHECK (
        stop_reason IS NULL OR stop_reason IN (
            'FINAL_ANSWER', 'BUDGET_EXHAUSTED', 'APPROVAL_REQUIRED',
            'APPROVAL_REJECTED', 'APPROVAL_EXPIRED',
            'GATE_BLOCKED', 'UNKNOWN_RESULT', 'WORKSPACE_CHANGED', 'INTERNAL_ERROR'
        )
    ),
    acceptance_status TEXT NOT NULL CHECK (
        acceptance_status IN ('NOT_EVALUATED', 'ACCEPTED', 'REJECTED', 'UNVERIFIABLE')
    ),
    current_step_id TEXT,
    last_checkpoint_id TEXT,
    resumed_from_checkpoint_id TEXT,
    candidate_final_answer TEXT,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    CHECK (updated_at >= created_at),
    CHECK (started_at IS NULL OR started_at >= created_at),
    CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
);

INSERT INTO runs_v4 SELECT * FROM runs;
DROP TABLE runs;
ALTER TABLE runs_v4 RENAME TO runs;

CREATE TABLE approvals (
    approval_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL CHECK (schema_version = 2),
    run_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_kind TEXT NOT NULL CHECK (task_kind IN ('code_understanding', 'test_repair')),
    git_root TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    workspace_prefix TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    canonical_args_digest TEXT NOT NULL,
    binding_digest TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    allowed_approver_roles_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'PENDING', 'APPROVED', 'CLAIMED', 'CONSUMED',
            'REJECTED', 'EXPIRED', 'INVALIDATED'
        )
    ),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    claimed_at TEXT,
    execution_intent_id TEXT,
    consumed_at TEXT,
    consumption_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (run_id, tool_call_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    FOREIGN KEY (run_id, tool_call_id)
        REFERENCES tool_calls(run_id, tool_call_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CHECK (expires_at > requested_at),
    CHECK (updated_at >= created_at),
    CHECK (
        status NOT IN ('CLAIMED', 'CONSUMED')
        OR (claimed_at IS NOT NULL AND execution_intent_id IS NOT NULL)
    ),
    CHECK (
        status NOT IN ('APPROVED', 'CLAIMED', 'CONSUMED', 'REJECTED')
        OR (decided_at IS NOT NULL AND decided_by IS NOT NULL)
    ),
    CHECK (status != 'CONSUMED' OR consumed_at IS NOT NULL)
);

CREATE INDEX idx_approvals_run_status
    ON approvals(run_id, status, requested_at);
CREATE INDEX idx_approvals_expiry
    ON approvals(status, expires_at);
CREATE INDEX idx_approvals_intent
    ON approvals(execution_intent_id);

CREATE TRIGGER approvals_binding_immutable
BEFORE UPDATE OF
    approval_id, schema_version, run_id, tool_call_id, task_id, task_kind,
    git_root, workspace_root, workspace_prefix, tool_name,
    canonical_args_digest, binding_digest, config_fingerprint,
    requested_by, allowed_approver_roles_json, requested_at, expires_at
ON approvals
BEGIN
    SELECT RAISE(ABORT, 'APPROVAL_BINDING_IMMUTABLE');
END;

CREATE TRIGGER approvals_no_delete
BEFORE DELETE ON approvals
BEGIN
    SELECT RAISE(ABORT, 'APPROVAL_HISTORY_APPEND_ONLY');
END;
"""


@dataclass(frozen=True)
class Migration:
    """One immutable SQLite schema migration."""

    version: int
    sql: str

    @property
    def checksum(self) -> str:
        """Return the SHA-256 checksum persisted for this migration."""

        digest = hashlib.sha256(self.sql.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"


MIGRATIONS = (
    Migration(version=1, sql=_MIGRATION_V1),
    Migration(version=2, sql=_MIGRATION_V2),
    Migration(version=3, sql=_MIGRATION_V3),
    Migration(version=4, sql=_MIGRATION_V4),
)


@dataclass(frozen=True)
class IntegrityReport:
    """Evidence collected by a non-destructive database integrity check."""

    schema_version: int
    migration_count: int
    journal_mode: str
    foreign_keys_enabled: bool
    busy_timeout_ms: int
    required_tables_present: bool
    budget_ledger_consistent: bool
    quick_check: str

    @property
    def ok(self) -> bool:
        """Return whether all mandatory integrity checks passed."""

        return (
            self.schema_version == SCHEMA_VERSION
            and self.migration_count == SCHEMA_VERSION
            and self.journal_mode.casefold() == "wal"
            and self.foreign_keys_enabled
            and self.required_tables_present
            and self.budget_ledger_consistent
            and self.quick_check.casefold() == "ok"
        )


class StorageError(RuntimeError):
    """Stable, bounded storage failure suitable for upstream mapping."""

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


class StorageUnitOfWork:
    """One active immediate SQLite write transaction."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        storage: SQLiteStorage,
        actor: str,
    ) -> None:
        self._connection = connection
        self._storage = storage
        self.actor = actor
        self._active = True

    @property
    def state(self) -> StateStore:
        """Return the state repository bound to this unit of work."""

        self.ensure_active()
        from .state_store import StateStore

        return StateStore(self)

    @property
    def audit(self) -> AuditStore:
        """Return the audit repository bound to this unit of work."""

        self.ensure_active()
        from .audit import AuditStore

        return AuditStore(self)

    @property
    def approvals(self) -> ApprovalStore:
        """Return the approval repository bound to this unit of work."""

        self.ensure_active()
        from .approval import ApprovalStore

        return ApprovalStore(self)

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the transaction connection for WP-01 repositories."""

        self.ensure_active()
        return self._connection

    @property
    def storage(self) -> SQLiteStorage:
        """Return the owning storage facade."""

        return self._storage

    @property
    def active(self) -> bool:
        """Return whether this unit of work may still be used."""

        return self._active

    def ensure_active(self) -> None:
        """Reject use after the transaction context has exited."""

        if not self._active:
            raise StorageError(
                ErrorCode.STATE_TRANSACTION_FAILED,
                operation="unit_of_work",
                message="unit of work is no longer active",
            )

    def close(self) -> None:
        """Mark the unit of work unusable."""

        self._active = False


class SQLiteStorage:
    """Own the SQLite schema, connection settings, and migration lifecycle."""

    def __init__(
        self,
        database_path: Path,
        workspace_root: Path,
        clock: Clock,
        *,
        busy_timeout_ms: int = 5000,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        if not 1 <= busy_timeout_ms <= 60_000:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="configure",
                message="busy_timeout_ms must be between 1 and 60000",
            )
        try:
            self.workspace_root = workspace_root.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="configure",
                message="workspace root cannot be resolved",
            ) from exc
        self.database_path = self._resolve_database_path(database_path)
        self.state_dir = self.database_path.parent
        self.clock = clock
        self.busy_timeout_ms = busy_timeout_ms
        self.fault_injector = fault_injector
        self._validate_workspace_isolation()

    def initialize(self) -> None:
        """Create missing state paths/database and apply immutable migrations."""

        self._prepare_state_directory()
        self._prepare_database_file()
        try:
            with self._connect() as connection:
                self._configure_connection(connection)
                self._run_migrations(connection)
                self._secure_database_files()
        except StorageError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="initialize",
                message="database initialization failed",
            ) from exc

    def schema_version(self) -> int:
        """Return the maximum applied migration version without mutating the database."""

        try:
            with self._connect() as connection:
                self._configure_connection(connection)
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="schema_version",
                message="database schema version is unavailable",
            ) from exc

    @contextmanager
    def transaction(self, *, actor: str) -> Iterator[StorageUnitOfWork]:
        """Open one fail-closed immediate-write unit of work."""

        normalized_actor = actor.strip()
        if not normalized_actor or len(normalized_actor) > 256:
            raise StorageError(
                ErrorCode.STATE_TRANSACTION_FAILED,
                operation="transaction",
                message="actor must be non-empty and bounded",
            )
        with self._connect() as connection:
            self._configure_connection(connection)
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise StorageError(
                    ErrorCode.STATE_TRANSACTION_FAILED,
                    operation="transaction",
                    message="write transaction cannot be started",
                    retryable=True,
                ) from exc
            unit = StorageUnitOfWork(
                connection=connection,
                storage=self,
                actor=normalized_actor,
            )
            try:
                yield unit
                if self.fault_injector is not None:
                    self.fault_injector.trigger("uow:before-commit")
                if connection.in_transaction:
                    connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                unit.close()

    def verify_integrity(self) -> IntegrityReport:
        """Verify pragmas, migration checksums, required tables, and SQLite quick_check."""

        try:
            with self._connect() as connection:
                self._configure_connection(connection)
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                applied = connection.execute(
                    "SELECT version, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
                expected = {migration.version: migration.checksum for migration in MIGRATIONS}
                if any(row["checksum"] != expected.get(row["version"]) for row in applied):
                    raise StorageError(
                        ErrorCode.STATE_SCHEMA_UNSUPPORTED,
                        operation="verify_integrity",
                        message="migration checksum mismatch",
                    )
                if version > SCHEMA_VERSION or any(
                    row["version"] > SCHEMA_VERSION for row in applied
                ):
                    raise StorageError(
                        ErrorCode.STATE_SCHEMA_UNSUPPORTED,
                        operation="verify_integrity",
                        message="database schema is newer than this implementation",
                    )
                required = {
                    "schema_migrations",
                    "runs",
                    "run_plans",
                    "steps",
                    "tool_calls",
                    "evidence",
                    "completion_results",
                    "completion_items",
                    "budget_ledger",
                    "run_leases",
                    "audit_events",
                    "approvals",
                }
                tables = {
                    row["name"]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
                foreign_keys = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
                busy_timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
                quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
                budget_ledger_consistent = _budget_ledger_consistent(connection)
                return IntegrityReport(
                    schema_version=version,
                    migration_count=len(applied),
                    journal_mode=journal_mode,
                    foreign_keys_enabled=foreign_keys,
                    busy_timeout_ms=busy_timeout,
                    required_tables_present=required.issubset(tables),
                    budget_ledger_consistent=budget_ledger_consistent,
                    quick_check=quick_check,
                )
        except StorageError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="verify_integrity",
                message="database integrity check failed",
            ) from exc

    def _resolve_database_path(self, database_path: Path) -> Path:
        candidate = Path(database_path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        try:
            resolved = _resolve_existing_or_nearest_ancestor(candidate)
        except (OSError, RuntimeError) as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="configure",
                message="database path cannot be resolved",
            ) from exc
        if resolved.name != "harness.sqlite3":
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="configure",
                message="database filename must be harness.sqlite3",
            )
        return resolved

    def _validate_workspace_isolation(self) -> None:
        try:
            self.state_dir.relative_to(self.workspace_root)
        except ValueError:
            return
        raise StorageError(
            ErrorCode.STATE_DATABASE_UNAVAILABLE,
            operation="configure",
            message="state directory must be outside workspace_root",
        )

    def _prepare_state_directory(self) -> None:
        if self.state_dir.exists():
            self._validate_private_path(self.state_dir, directory=True, allow_repair=False)
            return
        try:
            self.state_dir.mkdir(parents=True, mode=0o700)
        except OSError as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="initialize",
                message="state directory cannot be created",
            ) from exc
        self._validate_private_path(self.state_dir, directory=True, allow_repair=True)

    def _prepare_database_file(self) -> None:
        if self.database_path.exists() or self.database_path.is_symlink():
            self._validate_private_path(self.database_path, directory=False, allow_repair=True)
            return
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        try:
            descriptor = os.open(self.database_path, flags, 0o600)
            os.close(descriptor)
        except FileExistsError:
            self._validate_private_path(self.database_path, directory=False, allow_repair=True)
        except OSError as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="initialize",
                message="database file cannot be created",
            ) from exc

    def _validate_private_path(
        self,
        path: Path,
        *,
        directory: bool,
        allow_repair: bool,
    ) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="initialize",
                message="state path metadata is unavailable",
            ) from exc
        expected_type = (
            stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        )
        if stat.S_ISLNK(metadata.st_mode) or not expected_type:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="initialize",
                message="state path has an unsafe file type",
            )
        if os.name != "posix":
            return
        if metadata.st_uid != os.geteuid():
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="initialize",
                message="state path is not owned by the current user",
            )
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            if not allow_repair:
                raise StorageError(
                    ErrorCode.STATE_DATABASE_UNAVAILABLE,
                    operation="initialize",
                    message="state directory permissions are not sufficiently private",
                )
            try:
                path.chmod(0o700 if directory else 0o600)
            except OSError as exc:
                raise StorageError(
                    ErrorCode.STATE_DATABASE_UNAVAILABLE,
                    operation="initialize",
                    message="state path permissions cannot be tightened",
                ) from exc

    def _secure_database_files(self) -> None:
        if os.name != "posix":
            return
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self.database_path}{suffix}")
            if path.exists() or path.is_symlink():
                self._validate_private_path(path, directory=False, allow_repair=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _configure_connection(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA trusted_schema = OFF")
            journal_mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
            foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
            if journal_mode.casefold() != "wal" or foreign_keys != 1:
                raise StorageError(
                    ErrorCode.STATE_DATABASE_UNAVAILABLE,
                    operation="configure",
                    message="required SQLite safety pragmas are unavailable",
                )
        except StorageError:
            raise
        except sqlite3.Error as exc:
            raise StorageError(
                ErrorCode.STATE_DATABASE_UNAVAILABLE,
                operation="configure",
                message="SQLite connection configuration failed",
            ) from exc

    def _run_migrations(self, connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            current_user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_user_version > SCHEMA_VERSION:
                raise StorageError(
                    ErrorCode.STATE_SCHEMA_UNSUPPORTED,
                    operation="migrate",
                    message="database schema is newer than this implementation",
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied_rows = connection.execute(
                "SELECT version, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            applied = {int(row["version"]): str(row["checksum"]) for row in applied_rows}
            expected = {migration.version: migration.checksum for migration in MIGRATIONS}
            applied_version = max(applied, default=0)
            if applied and current_user_version != applied_version:
                raise StorageError(
                    ErrorCode.STATE_SCHEMA_UNSUPPORTED,
                    operation="migrate",
                    message="database schema version metadata is inconsistent",
                )
            if any(version > SCHEMA_VERSION for version in applied):
                raise StorageError(
                    ErrorCode.STATE_SCHEMA_UNSUPPORTED,
                    operation="migrate",
                    message="database schema is newer than this implementation",
                )
            if any(applied[version] != expected.get(version) for version in applied):
                raise StorageError(
                    ErrorCode.STATE_SCHEMA_UNSUPPORTED,
                    operation="migrate",
                    message="migration checksum mismatch",
                )
            for migration in MIGRATIONS:
                if migration.version in applied:
                    continue
                if migration.version == 3:
                    _validate_evidence_single_owner(connection)
                for statement in _split_sql_statements(migration.sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) VALUES (?, ?, ?)",
                    (
                        migration.version,
                        migration.checksum,
                        _serialize_datetime(self.clock.now()),
                    ),
                )
                connection.execute(f"PRAGMA user_version = {migration.version}")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise StorageError(
                    ErrorCode.STATE_MIGRATION_FAILED,
                    operation="migrate",
                    message="migration produced foreign key violations",
                )
            if self.fault_injector is not None:
                self.fault_injector.trigger("storage:migration-before-commit")
            connection.commit()
        except Exception as exc:
            if connection.in_transaction:
                connection.rollback()
            if isinstance(exc, StorageError):
                raise
            if isinstance(exc, sqlite3.Error):
                raise StorageError(
                    ErrorCode.STATE_MIGRATION_FAILED,
                    operation="migrate",
                    message="database migration failed",
                ) from exc
            raise
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.execute("PRAGMA foreign_keys = ON")


def _resolve_existing_or_nearest_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and not candidate.is_symlink():
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    resolved_ancestor = candidate.resolve(strict=True)
    return resolved_ancestor.joinpath(*path.relative_to(candidate).parts).resolve(strict=False)


def _serialize_datetime(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _split_sql_statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                yield statement
            buffer = ""
    if buffer.strip():
        raise StorageError(
            ErrorCode.STATE_MIGRATION_FAILED,
            operation="migrate",
            message="migration contains an incomplete SQL statement",
        )


def _budget_ledger_consistent(connection: sqlite3.Connection) -> bool:
    from decimal import Decimal

    sums: dict[str, dict[str, Decimal]] = {}
    for row in connection.execute("SELECT run_id, category, delta FROM budget_ledger").fetchall():
        run_sums = sums.setdefault(
            str(row["run_id"]),
            {
                "STEPS": Decimal(0),
                "TOOL_CALLS": Decimal(0),
                "TOKEN_COST": Decimal(0),
                "WALL_TIME": Decimal(0),
            },
        )
        run_sums[str(row["category"])] += Decimal(str(row["delta"]))
    for row in connection.execute("SELECT * FROM runs").fetchall():
        run_sums = sums.get(
            str(row["run_id"]),
            {
                "STEPS": Decimal(0),
                "TOOL_CALLS": Decimal(0),
                "TOKEN_COST": Decimal(0),
                "WALL_TIME": Decimal(0),
            },
        )
        if (
            run_sums["STEPS"] != Decimal(int(row["used_steps"]))
            or run_sums["TOOL_CALLS"] != Decimal(int(row["used_tool_calls"]))
            or run_sums["TOKEN_COST"] != Decimal(str(row["used_token_cost"]))
            or run_sums["WALL_TIME"] != Decimal(int(row["used_wall_time_seconds"]))
        ):
            return False
    return True


def _validate_evidence_single_owner(connection: sqlite3.Connection) -> None:
    table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'tool_call_evidence'
        """
    ).fetchone()
    if table is None:
        return
    conflict = connection.execute(
        """
        SELECT legacy.run_id, legacy.tool_call_id, legacy.evidence_id
        FROM tool_call_evidence AS legacy
        LEFT JOIN evidence ON evidence.evidence_id = legacy.evidence_id
        WHERE evidence.evidence_id IS NULL
           OR evidence.run_id != legacy.run_id
           OR evidence.tool_call_id IS NULL
           OR evidence.tool_call_id != legacy.tool_call_id
        LIMIT 1
        """
    ).fetchone()
    if conflict is not None:
        raise StorageError(
            ErrorCode.STATE_MIGRATION_FAILED,
            operation="migrate",
            message="legacy evidence association conflicts with its single producer",
        )
