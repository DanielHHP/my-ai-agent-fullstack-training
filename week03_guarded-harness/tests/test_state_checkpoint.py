"""State persistence, migration, and run lease contracts for WP-01."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path

import pytest

from guarded_harness import ErrorCode, SQLiteStorage, StorageError


def _storage(
    tmp_path: Path,
    fixed_clock,
    *,
    fault_injector=None,
) -> tuple[SQLiteStorage, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    storage = SQLiteStorage(
        state_dir / "harness.sqlite3",
        workspace,
        fixed_clock,
        fault_injector=fault_injector,
    )
    return storage, state_dir


def test_storage_initializes_schema_and_pragmas(tmp_path: Path, fixed_clock) -> None:
    storage, state_dir = _storage(tmp_path, fixed_clock)

    storage.initialize()
    storage.initialize()

    report = storage.verify_integrity()
    assert report.ok
    assert report.schema_version == 1
    assert report.migration_count == 1
    assert report.journal_mode.casefold() == "wal"
    assert report.foreign_keys_enabled
    assert report.busy_timeout_ms == 5000
    assert report.required_tables_present
    assert report.quick_check.casefold() == "ok"
    assert storage.schema_version() == 1
    if "posix" in __import__("os").name:
        assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(storage.database_path.stat().st_mode) == 0o600


def test_migration_failure_rolls_back_schema(tmp_path: Path, fixed_clock, fault_injector) -> None:
    storage, _ = _storage(tmp_path, fixed_clock, fault_injector=fault_injector)
    fault_injector.arm("storage:migration-before-commit")

    with pytest.raises(RuntimeError, match="injected fault"):
        storage.initialize()

    with sqlite3.connect(storage.database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
    assert table is None


def test_migration_checksum_mismatch_fails_closed(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum = ? WHERE version = 1",
            (f"sha256:{'f' * 64}",),
        )

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_SCHEMA_UNSUPPORTED


def test_schema_version_metadata_mismatch_fails_closed(tmp_path: Path, fixed_clock) -> None:
    storage, _ = _storage(tmp_path, fixed_clock)
    storage.initialize()
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("PRAGMA user_version = 0")

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_SCHEMA_UNSUPPORTED


def test_storage_rejects_state_dir_inside_workspace(tmp_path: Path, fixed_clock) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = workspace / "state"

    with pytest.raises(StorageError) as error:
        SQLiteStorage(state_dir / "harness.sqlite3", workspace, fixed_clock)
    assert error.value.code == ErrorCode.STATE_DATABASE_UNAVAILABLE
    assert not state_dir.exists()


def test_storage_rejects_unsafe_existing_state_permissions(
    tmp_path: Path,
    fixed_clock,
) -> None:
    import os

    if os.name != "posix":
        pytest.skip("POSIX permissions are not available")
    storage, state_dir = _storage(tmp_path, fixed_clock)
    state_dir.mkdir(mode=0o755)
    state_dir.chmod(0o755)

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_DATABASE_UNAVAILABLE
    assert stat.S_IMODE(state_dir.stat().st_mode) == 0o755


def test_newer_schema_version_fails_closed(tmp_path: Path, fixed_clock) -> None:
    storage, state_dir = _storage(tmp_path, fixed_clock)
    state_dir.mkdir(mode=0o700)
    state_dir.chmod(0o700)
    with sqlite3.connect(storage.database_path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(StorageError) as error:
        storage.initialize()
    assert error.value.code == ErrorCode.STATE_SCHEMA_UNSUPPORTED
