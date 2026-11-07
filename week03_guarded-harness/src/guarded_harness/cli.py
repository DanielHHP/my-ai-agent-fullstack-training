"""Local guarded-harness CLI and module entrypoint."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from pydantic import TypeAdapter, ValidationError
from smolagents import Model

from .approval import ApprovalStoreError
from .audit import AuditStoreError
from .config import ResolvedHarnessConfig, load_config
from .control import (
    ApprovalControlService,
    ControlError,
    InspectionService,
    json_dumps,
)
from .domain import (
    AcceptanceStatus,
    ApprovalId,
    ApprovalStatus,
    Clock,
    ErrorCode,
    FaultInjector,
    IdFactory,
    RunId,
    RunMode,
    RunRequest,
    RunResult,
    RunStatus,
    SystemClock,
    ToolCallId,
)
from .harness import Harness, HarnessError, ToolFactory
from .redaction import Redactor
from .sandbox import CommandPolicy
from .state_store import StateStoreError
from .storage import StorageError

ModelFactory = Callable[[Path], Model]
Sleeper = Callable[[float], None]
Monotonic = Callable[[], float]

_APPROVAL_LIST_DEFAULT_LIMIT = 100
_AUDIT_DEFAULT_LIMIT = 100
_APPROVAL_POLL_SECONDS = 0.5


class CLIError(RuntimeError):
    """Stable fail-closed CLI failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


class _ParserExit(Exception):
    def __init__(self, status: int, message: str | None = None) -> None:
        self.status = status
        self.message = message


class _StrictArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIError(ErrorCode.CLI_USAGE_ERROR, message)

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise _ParserExit(status, message)


def _wait_seconds(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("wait seconds must be an integer") from exc
    if not 1 <= parsed <= 86_400:
        raise argparse.ArgumentTypeError("wait seconds must be between 1 and 86400")
    return parsed


def _positive_limit(value: str, *, maximum: int = 1000) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer") from exc
    if not 1 <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {maximum}")
    return parsed


def _approval_status(value: str) -> ApprovalStatus:
    try:
        return ApprovalStatus(value.upper())
    except ValueError as exc:
        allowed = ", ".join(status.value for status in ApprovalStatus)
        raise argparse.ArgumentTypeError(f"status must be one of: {allowed}") from exc


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit one stable JSON envelope")


def build_parser() -> argparse.ArgumentParser:
    """Build the strict public CLI parser."""

    parser = _StrictArgumentParser(
        prog="guarded-harness",
        description="Run and inspect guarded coding-agent tasks.",
    )
    parser.add_argument("--config", type=Path, help="strict YAML task configuration")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="start a governed Agent run")
    run_parser.add_argument(
        "--wait-for-approval",
        type=_wait_seconds,
        metavar="SECONDS",
        help="wait up to SECONDS for approval before exiting",
    )
    _json_flag(run_parser)

    resume_parser = commands.add_parser("resume", help="resume an existing governed run")
    resume_parser.add_argument("run_id")
    resume_parser.add_argument(
        "--wait-for-approval",
        type=_wait_seconds,
        metavar="SECONDS",
        help="wait up to SECONDS for approval before exiting",
    )
    _json_flag(resume_parser)

    approvals_parser = commands.add_parser("approvals", help="inspect or decide approvals")
    approval_commands = approvals_parser.add_subparsers(
        dest="approval_command",
        required=True,
    )
    list_parser = approval_commands.add_parser("list", help="list approvals")
    list_parser.add_argument("--run-id")
    list_parser.add_argument(
        "--status",
        action="append",
        default=[],
        type=_approval_status,
        help="filter by persisted or effective approval status",
    )
    list_parser.add_argument(
        "--limit",
        type=_positive_limit,
        default=_APPROVAL_LIST_DEFAULT_LIMIT,
    )
    _json_flag(list_parser)

    for name in ("approve", "reject"):
        decision_parser = approval_commands.add_parser(
            name,
            help=f"{name} one pending approval",
        )
        decision_parser.add_argument("approval_id")
        decision_parser.add_argument("--actor", required=True)
        decision_parser.add_argument("--role", action="append", required=True)
        _json_flag(decision_parser)

    inspect_parser = commands.add_parser("inspect", help="inspect persisted run facts")
    inspect_parser.add_argument("run_id")
    inspect_parser.add_argument("--tool-call-id")
    inspect_parser.add_argument("--approval-id")
    inspect_parser.add_argument(
        "--audit-limit",
        type=_positive_limit,
        default=_AUDIT_DEFAULT_LIMIT,
    )
    _json_flag(inspect_parser)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    model_factory: ModelFactory | None = None,
    tool_factory: ToolFactory | None = None,
    clock: Clock | None = None,
    id_factory: IdFactory | None = None,
    command_policy: CommandPolicy | None = None,
    fault_injector: FaultInjector | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    sleeper: Sleeper | None = None,
    monotonic: Monotonic | None = None,
    approval_poll_seconds: float = _APPROVAL_POLL_SECONDS,
) -> int:
    """Execute one CLI command and return a stable process exit code."""

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    output = stdout or sys.stdout
    diagnostics = stderr or sys.stderr
    json_mode = "--json" in raw_argv
    command_name = _infer_command_name(raw_argv)
    parser = build_parser()
    try:
        args = parser.parse_args(raw_argv)
        if args.command == "approvals":
            command_name = f"approvals.{args.approval_command}"
        else:
            command_name = str(args.command)
        if args.config is None:
            raise CLIError(ErrorCode.CLI_CONFIG_REQUIRED, "--config is required")
        config = _load_config(args.config)
        active_clock = clock or SystemClock()
        if args.command == "run":
            result = _run_command(
                config_path=args.config,
                config=config,
                wait_seconds=args.wait_for_approval,
                model_factory=model_factory or _unavailable_model_factory,
                tool_factory=tool_factory,
                clock=active_clock,
                id_factory=id_factory,
                command_policy=command_policy,
                fault_injector=fault_injector,
                sleeper=sleeper or time.sleep,
                monotonic=monotonic or time.monotonic,
                approval_poll_seconds=approval_poll_seconds,
            )
            _emit_success(output, command_name, result.model_dump(mode="json"), json_mode)
            return exit_code_for_run_result(result)
        if args.command == "resume":
            run_id = _validate_id(RunId, args.run_id, "run_id")
            result = _resume_command(
                config_path=args.config,
                config=config,
                run_id=run_id,
                wait_seconds=args.wait_for_approval,
                model_factory=model_factory or _unavailable_model_factory,
                tool_factory=tool_factory,
                clock=active_clock,
                id_factory=id_factory,
                command_policy=command_policy,
                fault_injector=fault_injector,
                sleeper=sleeper or time.sleep,
                monotonic=monotonic or time.monotonic,
                approval_poll_seconds=approval_poll_seconds,
            )
            _emit_success(output, command_name, result.model_dump(mode="json"), json_mode)
            return exit_code_for_run_result(result)
        if args.command == "approvals":
            service = ApprovalControlService(args.config, clock=active_clock)
            if args.approval_command == "list":
                run_id = (
                    _validate_id(RunId, args.run_id, "run_id") if args.run_id is not None else None
                )
                approvals = service.list(
                    run_id=run_id,
                    statuses=tuple(args.status),
                    limit=args.limit,
                )
                data = {"approvals": [approval.as_dict() for approval in approvals]}
            else:
                approval_id = _validate_id(ApprovalId, args.approval_id, "approval_id")
                if args.approval_command == "approve":
                    mutation = service.approve(
                        approval_id,
                        actor=args.actor,
                        roles=tuple(args.role),
                    )
                else:
                    mutation = service.reject(
                        approval_id,
                        actor=args.actor,
                        roles=tuple(args.role),
                    )
                data = mutation.as_dict()
            _emit_success(output, command_name, data, json_mode)
            return 0
        if args.command == "inspect":
            run_id = _validate_id(RunId, args.run_id, "run_id")
            tool_call_id = (
                _validate_id(ToolCallId, args.tool_call_id, "tool_call_id")
                if args.tool_call_id is not None
                else None
            )
            approval_id = (
                _validate_id(ApprovalId, args.approval_id, "approval_id")
                if args.approval_id is not None
                else None
            )
            if tool_call_id is not None and approval_id is not None:
                raise CLIError(
                    ErrorCode.CLI_USAGE_ERROR,
                    "--tool-call-id and --approval-id are mutually exclusive",
                )
            data = InspectionService(args.config, clock=active_clock).inspect_run(
                run_id,
                tool_call_id=tool_call_id,
                approval_id=approval_id,
                audit_limit=args.audit_limit,
            )
            _emit_success(output, command_name, data, json_mode)
            return 0
        raise CLIError(ErrorCode.CLI_USAGE_ERROR, "unsupported command")
    except _ParserExit as exc:
        if exc.message:
            (output if exc.status == 0 else diagnostics).write(exc.message)
        return exc.status
    except (CLIError, ControlError, HarnessError, ApprovalStoreError) as exc:
        code = _stable_code(getattr(exc, "code", ErrorCode.CLI_INTERNAL_ERROR))
        _emit_error(
            diagnostics,
            output,
            command_name,
            code,
            _safe_message(exc),
            json_mode,
        )
        return exit_code_for_error(code)
    except (AuditStoreError, StateStoreError, StorageError) as exc:
        code = _stable_code(getattr(exc, "code", ErrorCode.CLI_INTERNAL_ERROR))
        _emit_error(
            diagnostics,
            output,
            command_name,
            code,
            _safe_message(exc),
            json_mode,
        )
        return exit_code_for_error(code)
    except ValidationError as exc:
        _emit_error(
            diagnostics,
            output,
            command_name,
            ErrorCode.CLI_USAGE_ERROR.value,
            _safe_message(exc),
            json_mode,
        )
        return 2
    except Exception as exc:
        _emit_error(
            diagnostics,
            output,
            command_name,
            ErrorCode.CLI_INTERNAL_ERROR.value,
            _safe_message(exc),
            json_mode,
        )
        return 9


def exit_code_for_run_result(result: RunResult) -> int:
    """Map one persisted Run result to the stable CLI exit code."""

    if result.status == RunStatus.WAITING_APPROVAL:
        return 3
    if result.status == RunStatus.STOPPED:
        return 4
    if result.status == RunStatus.BLOCKED:
        return 5
    if result.status == RunStatus.FAILED:
        return 6
    if result.status == RunStatus.COMPLETED:
        if result.acceptance_status == AcceptanceStatus.ACCEPTED:
            return 0
        if result.acceptance_status == AcceptanceStatus.REJECTED:
            return 7
        if result.acceptance_status == AcceptanceStatus.UNVERIFIABLE:
            return 8
    return 9


def exit_code_for_error(code: str) -> int:
    """Map one stable failure code to a CLI exit code."""

    normalized = code.upper()
    if normalized.startswith("CONFIG_") or normalized in {
        ErrorCode.CLI_USAGE_ERROR.value,
        ErrorCode.CLI_CONFIG_REQUIRED.value,
        ErrorCode.CLI_APPROVAL_NOT_FOUND.value,
        ErrorCode.APPROVAL_NOT_FOUND.value,
    }:
        return 2
    if normalized == ErrorCode.CLI_MODEL_UNAVAILABLE.value:
        return 6
    if normalized.startswith("APPROVAL_"):
        return 5
    if normalized.startswith("HARNESS_"):
        return 6
    return 9


def _run_command(
    *,
    config_path: Path,
    config: ResolvedHarnessConfig,
    wait_seconds: int | None,
    model_factory: ModelFactory,
    tool_factory: ToolFactory | None,
    clock: Clock,
    id_factory: IdFactory | None,
    command_policy: CommandPolicy | None,
    fault_injector: FaultInjector | None,
    sleeper: Sleeper,
    monotonic: Monotonic,
    approval_poll_seconds: float,
) -> RunResult:
    del config
    result = _harness(
        config_path=config_path,
        model_factory=model_factory,
        tool_factory=tool_factory,
        clock=clock,
        id_factory=id_factory,
        command_policy=command_policy,
        fault_injector=fault_injector,
    ).run(RunRequest(mode=RunMode.START))
    return _wait_if_requested(
        result,
        wait_seconds=wait_seconds,
        config_path=config_path,
        model_factory=model_factory,
        tool_factory=tool_factory,
        clock=clock,
        id_factory=id_factory,
        command_policy=command_policy,
        fault_injector=fault_injector,
        sleeper=sleeper,
        monotonic=monotonic,
        approval_poll_seconds=approval_poll_seconds,
    )


def _resume_command(
    *,
    config_path: Path,
    config: ResolvedHarnessConfig,
    run_id: str,
    wait_seconds: int | None,
    model_factory: ModelFactory,
    tool_factory: ToolFactory | None,
    clock: Clock,
    id_factory: IdFactory | None,
    command_policy: CommandPolicy | None,
    fault_injector: FaultInjector | None,
    sleeper: Sleeper,
    monotonic: Monotonic,
    approval_poll_seconds: float,
) -> RunResult:
    del config
    result = _harness(
        config_path=config_path,
        model_factory=model_factory,
        tool_factory=tool_factory,
        clock=clock,
        id_factory=id_factory,
        command_policy=command_policy,
        fault_injector=fault_injector,
    ).run(RunRequest(mode=RunMode.RESUME, run_id=run_id))
    return _wait_if_requested(
        result,
        wait_seconds=wait_seconds,
        config_path=config_path,
        model_factory=model_factory,
        tool_factory=tool_factory,
        clock=clock,
        id_factory=id_factory,
        command_policy=command_policy,
        fault_injector=fault_injector,
        sleeper=sleeper,
        monotonic=monotonic,
        approval_poll_seconds=approval_poll_seconds,
    )


def _harness(
    *,
    config_path: Path,
    model_factory: ModelFactory,
    tool_factory: ToolFactory | None,
    clock: Clock,
    id_factory: IdFactory | None,
    command_policy: CommandPolicy | None,
    fault_injector: FaultInjector | None,
) -> Harness:
    try:
        model = model_factory(Path(config_path))
    except CLIError:
        raise
    except Exception as exc:
        raise CLIError(
            ErrorCode.CLI_MODEL_UNAVAILABLE,
            _safe_message(exc),
        ) from exc
    if not isinstance(model, Model):
        raise CLIError(
            ErrorCode.CLI_MODEL_UNAVAILABLE,
            "model factory did not return a smolagents.Model",
        )
    return Harness(
        config_path=Path(config_path),
        model=model,
        tool_factory=tool_factory,
        clock=clock,
        id_factory=id_factory,
        command_policy=command_policy,
        fault_injector=fault_injector,
    )


def _wait_if_requested(
    result: RunResult,
    *,
    wait_seconds: int | None,
    config_path: Path,
    model_factory: ModelFactory,
    tool_factory: ToolFactory | None,
    clock: Clock,
    id_factory: IdFactory | None,
    command_policy: CommandPolicy | None,
    fault_injector: FaultInjector | None,
    sleeper: Sleeper,
    monotonic: Monotonic,
    approval_poll_seconds: float,
) -> RunResult:
    if wait_seconds is None or result.status != RunStatus.WAITING_APPROVAL:
        return result
    if not 1 <= wait_seconds <= 86_400:
        raise CLIError(ErrorCode.CLI_USAGE_ERROR, "wait seconds must be between 1 and 86400")
    if not 0 < approval_poll_seconds <= 1:
        raise CLIError(
            ErrorCode.CLI_INTERNAL_ERROR,
            "approval poll interval must be greater than 0 and at most 1 second",
        )
    current = result
    deadline = monotonic() + wait_seconds
    inspections = InspectionService(config_path, clock=clock)
    approvals = ApprovalControlService(config_path, clock=clock)
    while current.status == RunStatus.WAITING_APPROVAL:
        remaining = deadline - monotonic()
        if remaining <= 0:
            return inspections.get_run_result(current.run_id)
        approval_id = current.approval_id
        if approval_id is None:
            raise CLIError(
                ErrorCode.CLI_INTERNAL_ERROR,
                "waiting run did not expose an approval id",
            )
        refreshed = approvals.refresh(approval_id)
        if refreshed.effective_status == ApprovalStatus.APPROVED:
            current = _harness(
                config_path=config_path,
                model_factory=model_factory,
                tool_factory=tool_factory,
                clock=clock,
                id_factory=id_factory,
                command_policy=command_policy,
                fault_injector=fault_injector,
            ).run(RunRequest(mode=RunMode.RESUME, run_id=current.run_id))
            continue
        if refreshed.run.status != RunStatus.WAITING_APPROVAL:
            error_code = None
            if refreshed.approval.status == ApprovalStatus.REJECTED:
                error_code = ErrorCode.APPROVAL_REJECTED.value
            elif refreshed.approval.status == ApprovalStatus.EXPIRED:
                error_code = ErrorCode.APPROVAL_EXPIRED.value
            return inspections.get_run_result(
                current.run_id,
                error_code=error_code,
            )
        sleeper(min(approval_poll_seconds, max(0.0, remaining)))
    return current


def _load_config(config_path: Path) -> ResolvedHarnessConfig:
    try:
        return load_config(Path(config_path))
    except Exception as exc:
        raise CLIError(_error_code_or(exc, ErrorCode.CLI_USAGE_ERROR), _safe_message(exc)) from exc


def _unavailable_model_factory(config_path: Path) -> Model:
    del config_path
    raise CLIError(
        ErrorCode.CLI_MODEL_UNAVAILABLE,
        "no trusted P0 model factory is configured; inject smolagents.Model in-process",
    )


def _validate_id(annotation: Any, value: str, name: str) -> str:
    try:
        return TypeAdapter(annotation).validate_python(value)
    except ValidationError as exc:
        raise CLIError(
            ErrorCode.CLI_USAGE_ERROR,
            f"{name} has an invalid format",
        ) from exc


def _emit_success(output: TextIO, command: str, data: object, json_mode: bool) -> None:
    if json_mode:
        output.write(
            json_dumps(
                {
                    "ok": True,
                    "command": command,
                    "data": data,
                    "error": None,
                }
            )
        )
        output.write("\n")
        return
    if command in {"run", "resume"} and isinstance(data, dict):
        output.write(
            f"{command}: status={data.get('status')} "
            f"acceptance={data.get('acceptance_status')} "
            f"run_id={data.get('run_id')}\n"
        )
        if data.get("approval_id"):
            output.write(f"approval_id={data.get('approval_id')}\n")
    elif command.startswith("approvals.") and isinstance(data, dict):
        if "approvals" in data:
            for approval in data["approvals"]:
                output.write(
                    f"{approval['approval_id']} {approval['effective_status']} "
                    f"{approval['tool_name']} run={approval['run_id']}\n"
                )
        else:
            approval = data["approval"]
            run = data["run"]
            output.write(
                f"{approval['approval_id']} {approval['status']} "
                f"run={run['run_id']} run_status={run['status']}\n"
            )
    else:
        output.write(f"{json_dumps(data)}\n")


def _emit_error(
    diagnostics: TextIO,
    output: TextIO,
    command: str,
    code: str,
    message: str,
    json_mode: bool,
) -> None:
    if json_mode:
        output.write(
            json_dumps(
                {
                    "ok": False,
                    "command": command,
                    "data": None,
                    "error": {"code": code, "message": message},
                }
            )
        )
        output.write("\n")
        return
    diagnostics.write(f"{code}: {message}\n")


def _infer_command_name(argv: Sequence[str]) -> str:
    skip_next = False
    known = {"run", "resume", "approvals", "inspect"}
    for item in argv:
        if skip_next:
            skip_next = False
            continue
        if item == "--config":
            skip_next = True
            continue
        if item in known:
            return item
    return "unknown"


def _stable_code(value: object) -> str:
    if isinstance(value, ErrorCode):
        return value.value
    try:
        return ErrorCode(str(value)).value
    except ValueError:
        return str(value)[:128]


def _safe_message(exc: BaseException) -> str:
    message = getattr(exc, "message", None) or str(exc)
    normalized = " ".join(str(message).split())
    redacted = Redactor().redact(normalized).value
    text = redacted if isinstance(redacted, str) else normalized
    return text[:1000]


def _error_code_or(exc: BaseException, fallback: ErrorCode) -> ErrorCode:
    value = getattr(exc, "code", fallback)
    if isinstance(value, ErrorCode):
        return value
    try:
        return ErrorCode(str(value))
    except ValueError:
        return fallback


__all__ = [
    "CLIError",
    "ModelFactory",
    "build_parser",
    "exit_code_for_error",
    "exit_code_for_run_result",
    "main",
]
