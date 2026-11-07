"""Deterministic process-crash and persistence-fault helpers for WP-12 tests."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fake_model import FakeScriptedModel, tool_call

import guarded_harness as gh

CRASH_EXIT_CODE = 91
CRASH_POINT = "wp12:handler-after-side-effect"
CHECKPOINT_CRASH_POINT = "checkpoint:before-audit"
CRASH_CLOCK_START = datetime(2030, 1, 1, tzinfo=timezone.utc)


class OffsetClock:
    """A controllable UTC clock for crash and lease-window tests."""

    def __init__(self, value: datetime = CRASH_CLOCK_START) -> None:
        if value.tzinfo is None:
            raise ValueError("clock requires timezone-aware values")
        self.value = value

    def now(self) -> datetime:
        """Return the current test time."""

        return self.value

    def advance(self, seconds: int) -> None:
        """Advance the controlled clock."""

        from datetime import timedelta

        self.value += timedelta(seconds=seconds)


class HardExitFaultInjector:
    """Write a marker and terminate the process at one armed fault point."""

    def __init__(
        self,
        marker_path: Path,
        point: str,
        *,
        armed: bool = False,
    ) -> None:
        self.marker_path = marker_path
        self.point = point
        self._armed = armed
        self._count = 0

    def arm(self, point: str) -> None:
        """Arm the configured hard-exit point."""

        if point == self.point:
            self._armed = True

    def disable(self, point: str) -> None:
        """Disable the configured hard-exit point."""

        if point == self.point:
            self._armed = False

    def record(self, point: str) -> int:
        """Record one reached fault point."""

        if point == self.point:
            self._count += 1
        return self._count

    def call_count(self, point: str) -> int:
        """Return the number of recorded hits for one point."""

        return self._count if point == self.point else 0

    def trigger(self, point: str) -> None:
        """Hard-exit after persisting a marker when the point is armed."""

        count = self.record(point)
        if point != self.point or not self._armed:
            return
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        self.marker_path.write_text(
            json.dumps(
                {
                    "point": point,
                    "count": count,
                    "pid": os.getpid(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(CRASH_EXIT_CODE)


class SecondHitHardExitFaultInjector(HardExitFaultInjector):
    """Hard-exit on the second hit of one fault point."""

    def trigger(self, point: str) -> None:
        """Record the point and exit only on its second occurrence."""

        count = self.record(point)
        if point != self.point or count != 2:
            return
        self.marker_path.parent.mkdir(parents=True, exist_ok=True)
        self.marker_path.write_text(
            json.dumps(
                {
                    "point": point,
                    "count": count,
                    "pid": os.getpid(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(CRASH_EXIT_CODE)


def fault_write_definition(
    sandbox: gh.Sandbox,
    injector: HardExitFaultInjector | None,
) -> gh.ToolDefinition:
    """Build a trusted non-idempotent writer that can crash after its side effect."""

    def handler(
        arguments: dict[str, Any],
        _context: gh.ToolExecutionContext,
    ) -> gh.HandlerResult:
        checked = sandbox.check_write(str(arguments["path"]))
        result = sandbox.write_file_atomic(
            checked.workspace_relative,
            str(arguments["content"]).encode("utf-8"),
            mode=gh.SandboxWriteMode.CREATE,
            create_parents=True,
        )
        if injector is None:
            raise AssertionError("fault writer must never execute during recovery")
        injector.trigger(CRASH_POINT)
        return gh.HandlerResult(
            status=gh.HandlerResultStatus.SUCCEEDED,
            summary=f"fault writer updated {result.path.workspace_relative}",
        )

    return gh.ToolDefinition(
        spec=gh.ToolSpec(
            name="fault_write",
            description="Test-only side effect followed by a process crash.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "content": {"type": "string", "minLength": 1},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            output_kind=gh.ToolOutputKind.JSON,
            risk=gh.ToolRisk.WRITE,
            required_permissions=frozenset({gh.ToolPermission.WRITE}),
            timeout_seconds=5,
            retry_policy=gh.RetryPolicy.NEVER,
            idempotent=False,
            path_arguments=("path",),
            output_limit_bytes=4096,
            approval_requirement=gh.ApprovalRequirement.NEVER,
            concurrency_class=gh.ConcurrencyClass.SERIAL,
        ),
        handler=handler,
    )


def fault_tool_factory(
    injector: HardExitFaultInjector | None,
):
    """Return a trusted tool factory containing only the fault writer."""

    def factory(
        _config: gh.ResolvedHarnessConfig,
        sandbox: gh.Sandbox,
    ) -> gh.ToolRegistry:
        registry = gh.ToolRegistry()
        registry.register(fault_write_definition(sandbox, injector))
        return registry

    return factory


@contextmanager
def hold_database_write_lock(database_path: Path) -> Iterator[None]:
    """Hold SQLite's immediate-write lock without modifying business tables."""

    connection = sqlite3.connect(database_path, timeout=0)
    try:
        connection.execute("PRAGMA busy_timeout = 0")
        connection.execute("BEGIN IMMEDIATE")
        yield
    finally:
        if connection.in_transaction:
            connection.rollback()
        connection.close()


def minimal_subprocess_env(
    *,
    test_root: Path,
    tests_dir: Path,
) -> dict[str, str]:
    """Build an isolated child environment with no inherited credentials."""

    home = test_root / "child-home"
    temp_dir = test_root / "child-tmp"
    home.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(tests_dir),
        "HOME": str(home),
        "TMPDIR": str(temp_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
    }


def _run_crash_after_side_effect(
    config_path: Path,
    marker_path: Path,
) -> None:
    """Child-process entrypoint for a real hard exit after a file write."""

    clock = OffsetClock()
    injector = HardExitFaultInjector(
        marker_path,
        CRASH_POINT,
        armed=True,
    )
    model = FakeScriptedModel(
        (
            tool_call(
                "crash-write-1",
                "fault_write",
                {
                    "path": "artifacts/crash-result.txt",
                    "content": "written-before-crash\n",
                },
            ),
        )
    )
    gh.Harness(
        config_path=config_path,
        model=model,
        tool_factory=fault_tool_factory(injector),
        clock=clock,
        fault_injector=injector,
    ).run(gh.RunRequest(mode=gh.RunMode.START))
    raise AssertionError("fault point was not reached")


def _run_exit_after_result_before_checkpoint(
    config_path: Path,
    marker_path: Path,
) -> None:
    """Exit after a tool result commits but before its Checkpoint commits."""

    clock = OffsetClock()
    injector = SecondHitHardExitFaultInjector(
        marker_path,
        CHECKPOINT_CRASH_POINT,
        armed=True,
    )
    model = FakeScriptedModel(
        (
            tool_call(
                "checkpoint-read-1",
                "read_file",
                {"path": "src/app.py", "offset": 1, "limit": 20},
            ),
        )
    )
    gh.Harness(
        config_path=config_path,
        model=model,
        clock=clock,
        fault_injector=injector,
    ).run(gh.RunRequest(mode=gh.RunMode.START))
    raise AssertionError("checkpoint crash point was not reached")


def main(argv: list[str] | None = None) -> int:
    """Run one test-only child scenario selected by the parent test."""

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        raise SystemExit("usage: fault_injection.py MODE CONFIG MARKER")
    if args[0] == "crash-after-side-effect":
        _run_crash_after_side_effect(Path(args[1]), Path(args[2]))
    elif args[0] == "exit-after-result-before-checkpoint":
        _run_exit_after_result_before_checkpoint(Path(args[1]), Path(args[2]))
    else:
        raise SystemExit(f"unknown fault mode: {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
