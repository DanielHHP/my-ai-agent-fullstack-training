"""Deterministic, offline fixtures shared by WP-00 tests."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@dataclass(frozen=True)
class TempRepo:
    """Paths for an isolated temporary Git repository."""

    root: Path
    workspace: Path


class FixedClock:
    """Return a finite deterministic sequence of UTC datetimes."""

    def __init__(self, values: tuple[datetime, ...]) -> None:
        self._values = list(values)

    def now(self) -> datetime:
        if not self._values:
            raise AssertionError("fixed clock sequence exhausted")
        return self._values.pop(0)


class FixedIdFactory:
    """Return deterministic IDs in the frozen public formats."""

    def __init__(self, start: int = 1) -> None:
        self._next = start

    def _take(self) -> str:
        value = f"{self._next:032x}"
        self._next += 1
        return value

    def new_trace_id(self) -> str:
        return self._take()

    def new_run_id(self) -> str:
        return f"run_{self._take()}"

    def new_step_id(self) -> str:
        return f"step_{self._take()}"

    def new_evidence_id(self) -> str:
        return f"evidence_{self._take()}"

    def new_approval_id(self) -> str:
        return f"approval_{self._take()}"

    def new_checkpoint_id(self) -> str:
        return f"checkpoint_{self._take()}"


class RecordingFaultInjector:
    """Record fault points and raise only when a point is explicitly armed."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = {}
        self._armed: set[str] = set()

    def arm(self, point: str) -> None:
        self._armed.add(point)

    def disable(self, point: str) -> None:
        self._armed.discard(point)

    def record(self, point: str) -> int:
        self._counts[point] = self._counts.get(point, 0) + 1
        return self._counts[point]

    def trigger(self, point: str) -> None:
        self.record(point)
        if point in self._armed:
            raise RuntimeError(f"injected fault: {point}")

    def call_count(self, point: str) -> int:
        return self._counts.get(point, 0)


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> TempRepo:
    """Create a temporary Git root with an isolated task workspace."""

    root = tmp_path / "repo"
    workspace = root / "task"
    workspace.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Harness Tests"],
        check=True,
        capture_output=True,
        text=True,
    )
    return TempRepo(root=root, workspace=workspace)


@pytest.fixture
def temp_sqlite_path(tmp_path: Path) -> Path:
    """Return a non-created SQLite path for downstream fixtures."""

    return tmp_path / "state" / "harness.sqlite3"


@pytest.fixture
def fixed_clock() -> FixedClock:
    """Return a deterministic UTC clock."""

    start = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    values = tuple(start + timedelta(seconds=offset) for offset in range(64))
    return FixedClock(values)


@pytest.fixture
def fixed_ids() -> FixedIdFactory:
    """Return a deterministic ID factory."""

    return FixedIdFactory()


@pytest.fixture
def fault_injector() -> RecordingFaultInjector:
    """Return an isolated deterministic fault injector."""

    return RecordingFaultInjector()
