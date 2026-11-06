"""Trusted test-command handler backed by the Sandbox process boundary."""

from __future__ import annotations

from typing import Any

from ..domain import (
    ErrorCode,
    EvidenceDraft,
    EvidenceKind,
    HandlerResult,
    HandlerResultStatus,
    ToolExecutionContext,
)
from ..sandbox import Sandbox, SandboxProcessResult, SandboxProcessStatus, SandboxViolation
from .common import TOOL_SCHEMA_VERSION, canonical_json_digest, failure

ACCEPTANCE_TEST_COMMAND_ID = "acceptance_test"


def run_test_handler(sandbox: Sandbox):
    """Create a handler for the single trusted acceptance-test command."""

    def handler(
        arguments: dict[str, Any],
        _context: ToolExecutionContext,
    ) -> HandlerResult:
        if arguments != {"command_id": ACCEPTANCE_TEST_COMMAND_ID}:
            return failure(
                ErrorCode.GATE_SEMANTIC_INVALID,
                "run_test accepts only the configured acceptance_test command",
            )
        try:
            process = sandbox.execute(ACCEPTANCE_TEST_COMMAND_ID)
        except SandboxViolation as exc:
            return failure(exc.code, exc.message)
        return _map_process_result(process)

    return handler


def _map_process_result(process: SandboxProcessResult) -> HandlerResult:
    output = {
        "schema_version": TOOL_SCHEMA_VERSION,
        "tool": "run_test",
        "command_id": process.command_id,
        "status": process.status.value,
        "exit_code": process.exit_code,
        "signal": process.signal,
        "stdout": process.stdout,
        "stderr": process.stderr,
        "stdout_truncated": process.stdout_truncated,
        "stderr_truncated": process.stderr_truncated,
        "timed_out": process.timed_out,
        "cancelled": process.cancelled,
        "process_group_terminated": process.process_group_terminated,
        "duration_ms": process.duration_ms,
        "result_code": process.result_code.value if process.result_code else None,
    }
    evidence = _command_evidence(process, output)
    if process.status == SandboxProcessStatus.SUCCEEDED:
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output=output,
            summary="acceptance test command exited successfully",
            evidence=evidence,
        )
    if process.status == SandboxProcessStatus.FAILED:
        return HandlerResult(
            status=HandlerResultStatus.FAILED,
            output=output,
            summary="acceptance test command exited with a failure",
            error_code=ErrorCode.TOOL_TEST_FAILED.value,
            evidence=evidence,
        )
    if process.status == SandboxProcessStatus.START_FAILED:
        return HandlerResult(
            status=HandlerResultStatus.FAILED,
            output=output,
            summary="acceptance test command could not be started",
            error_code=(
                process.result_code.value
                if process.result_code
                else ErrorCode.SANDBOX_COMMAND_START_FAILED.value
            ),
        )
    if process.status == SandboxProcessStatus.TIMED_OUT:
        return HandlerResult(
            status=HandlerResultStatus.TIMED_OUT,
            output=output,
            summary="acceptance test command timed out",
            error_code=(
                process.result_code.value
                if process.result_code
                else ErrorCode.SANDBOX_COMMAND_TIMEOUT.value
            ),
        )
    return HandlerResult(
        status=HandlerResultStatus.UNKNOWN_RESULT,
        output=output,
        summary="acceptance test command result cannot be confirmed",
        error_code=(
            process.result_code.value
            if process.result_code
            else ErrorCode.SANDBOX_RESULT_UNKNOWN.value
        ),
    )


def _command_evidence(
    process: SandboxProcessResult,
    output: dict[str, Any],
) -> tuple[EvidenceDraft, ...]:
    if process.status not in {
        SandboxProcessStatus.SUCCEEDED,
        SandboxProcessStatus.FAILED,
    }:
        return ()
    if process.stdout_truncated or process.stderr_truncated:
        return ()
    return (
        EvidenceDraft(
            kind=EvidenceKind.COMMAND,
            command_id=process.command_id,
            content_digest=canonical_json_digest(
                {
                    "schema": "wp06-command-result-v1",
                    **output,
                }
            ),
        ),
    )


__all__ = ["ACCEPTANCE_TEST_COMMAND_ID", "run_test_handler"]
