"""Model-independent completion verification and workspace-bound evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .audit import AuditDecision, AuditEventDraft, AuditEventType, AuditStatus
from .config import (
    CodeUnderstandingAcceptance,
    ResolvedHarnessConfig,
    TestRepairAcceptance,
)
from .domain import (
    AcceptanceStatus,
    ApprovalRequirement,
    Clock,
    CompletionCheck,
    CompletionItem,
    ConcurrencyClass,
    ErrorCode,
    Evidence,
    EvidenceDraft,
    EvidenceKind,
    GatePhase,
    HandlerResult,
    HandlerResultStatus,
    IdFactory,
    ObservationStatus,
    RetryPolicy,
    RunSnapshot,
    RunStatus,
    Step,
    StepStatus,
    TaskKind,
    TestBaseline,
    TestExecutionPhase,
    TestExecutionRecord,
    TestExecutionStatus,
    TestNodeRecord,
    TestNodeStatus,
    ToolExecutionContext,
    ToolOutputKind,
    ToolPermission,
    ToolRisk,
    ToolSpec,
    Uuid4IdFactory,
    WorkspaceIdentity,
)
from .gate import Gate
from .registry import ToolDefinition, ToolRegistry
from .sandbox import (
    CommandEffectClass,
    ExternalProtection,
    Sandbox,
    SandboxProcessResult,
    SandboxProcessStatus,
    SandboxViolation,
    TrustedCommandSpec,
    WorkspaceBaseline,
    WorkspaceEntry,
)
from .state_store import StateStoreError
from .storage import SQLiteStorage, StorageError
from .test_reports import (
    MAX_REPORT_BYTES,
    ParsedTestReport,
    TestReportError,
    compare_test_nodes,
    parse_junit_report,
)
from .tools.common import glob_matches

_SECTION_FACTS = "事实"
_SECTION_INFERENCE = "推断"
_SECTION_EVIDENCE = "代码依据"
_REQUIRED_SECTIONS = (_SECTION_FACTS, _SECTION_INFERENCE, _SECTION_EVIDENCE)
_FACT_REFERENCE = re.compile(r"\[E([1-9][0-9]*)\]")
_EVIDENCE_DEFINITION = re.compile(r"^- E([1-9][0-9]*): `([^`]+):([1-9][0-9]*)(?:-([1-9][0-9]*))?`$")
_LIST_ITEM = re.compile(r"^\s*-\s+(.+)$")
_COMPLETION_MARKERS = (
    "已完成",
    "完成修复",
    "修复完成",
    "测试通过",
    "全部通过",
    "all tests pass",
    "tests pass",
    "task complete",
    "completed successfully",
)
_NEGATED_MARKERS = (
    "未完成",
    "没有完成",
    "尚未完成",
    "not complete",
    "not done",
    "did not finish",
)
_MAX_DELIVERABLE_BYTES = 1024 * 1024
_INTERNAL_STEP_SUMMARY = "Completion verification"
_INSPECT_TOOL = "completion_inspect_file"
_TEST_VERIFICATION_TOOL = "test_verification"


class CompletionCheckerError(RuntimeError):
    """Stable Completion infrastructure failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


@dataclass(frozen=True)
class _ParsedDocument:
    facts: tuple[str, ...]
    inference: str
    evidence_definitions: dict[int, _EvidenceDefinition]
    fact_references: dict[int, tuple[int, ...]]


@dataclass(frozen=True)
class _EvidenceDefinition:
    evidence_number: int
    path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class _Check:
    item_id: str
    passed: bool
    code: ErrorCode | str
    message: str
    evidence_ids: tuple[str, ...] = ()
    unverifiable: bool = False


@dataclass(frozen=True)
class _CapturedTestCommand:
    phase: TestExecutionPhase
    process: SandboxProcessResult
    report: bytes
    report_path: str
    effective_argv_digest: str


class _TestVerificationExecutor:
    """Execute the configured pytest command and retain bounded report bytes."""

    def __init__(self, sandbox: Sandbox, config: ResolvedHarnessConfig) -> None:
        self._sandbox = sandbox
        self._config = config
        self._captured: dict[tuple[str, TestExecutionPhase], _CapturedTestCommand] = {}

    def handler(
        self,
        arguments: dict[str, Any],
        context: ToolExecutionContext,
    ) -> HandlerResult:
        try:
            phase = TestExecutionPhase(str(arguments["phase"]))
        except ValueError:
            return HandlerResult(
                status=HandlerResultStatus.FAILED,
                summary="unsupported test verification phase",
                error_code=ErrorCode.COMPLETION_TEST_COMMAND_MISSING.value,
            )
        acceptance = self._config.config.task.acceptance
        if not isinstance(acceptance, TestRepairAcceptance):
            return HandlerResult(
                status=HandlerResultStatus.FAILED,
                summary="test verification requires a test-repair task",
                error_code=ErrorCode.COMPLETION_UNSUPPORTED_TASK.value,
            )
        report_directory = tempfile.mkdtemp(prefix="guarded-harness-report-", dir="/tmp")
        os.chmod(report_directory, 0o700)
        report_path = Path(report_directory) / f"{phase.value.lower()}.xml"
        effective_argv = build_pytest_report_argv(
            acceptance.test_command,
            report_path,
        )
        command_id = (
            "test_verify_"
            f"{phase.value.lower()}_"
            f"{hashlib.sha256(str(report_path).encode()).hexdigest()[:16]}"
        )
        try:
            self._sandbox.register_trusted_command(
                TrustedCommandSpec(
                    command_id=command_id,
                    argv=effective_argv,
                    effect_class=CommandEffectClass.NON_IDEMPOTENT,
                    timeout_seconds=min(
                        self._config.config.budget.max_wall_time_seconds,
                        3_600,
                    ),
                )
            )
            process = self._sandbox.execute(command_id)
        except SandboxViolation as exc:
            return HandlerResult(
                status=HandlerResultStatus.FAILED,
                summary=exc.message,
                error_code=exc.code.value,
            )
        try:
            if process.status not in {
                SandboxProcessStatus.SUCCEEDED,
                SandboxProcessStatus.FAILED,
            }:
                if process.status == SandboxProcessStatus.START_FAILED:
                    return HandlerResult(
                        status=HandlerResultStatus.FAILED,
                        output=_process_output(process),
                        summary="test verification command could not be started",
                        error_code=(
                            process.result_code.value
                            if process.result_code
                            else ErrorCode.COMPLETION_TEST_START_FAILED.value
                        ),
                    )
                return HandlerResult(
                    status=(
                        HandlerResultStatus.TIMED_OUT
                        if process.status
                        in {
                            SandboxProcessStatus.TIMED_OUT,
                            SandboxProcessStatus.CANCELLED,
                        }
                        else HandlerResultStatus.UNKNOWN_RESULT
                    ),
                    output=_process_output(process),
                    summary="test verification did not produce a confirmed result",
                    error_code=(
                        process.result_code.value
                        if process.result_code
                        else ErrorCode.COMPLETION_TEST_RESULT_UNKNOWN.value
                    ),
                )
            if not report_path.is_file():
                return HandlerResult(
                    status=HandlerResultStatus.UNKNOWN_RESULT,
                    output=_process_output(process),
                    summary="test verification did not create a JUnit report",
                    error_code=ErrorCode.COMPLETION_TEST_REPORT_MISSING.value,
                )
            os.chmod(report_path, 0o600)
            size = report_path.stat().st_size
            if size <= 0 or size > MAX_REPORT_BYTES:
                return HandlerResult(
                    status=HandlerResultStatus.UNKNOWN_RESULT,
                    output=_process_output(process),
                    summary="test verification report is empty or exceeds the limit",
                    error_code=(
                        ErrorCode.COMPLETION_TEST_REPORT_EMPTY.value
                        if size <= 0
                        else ErrorCode.COMPLETION_TEST_REPORT_TOO_LARGE.value
                    ),
                )
            report = report_path.read_bytes()
            report_digest = f"sha256:{hashlib.sha256(report).hexdigest()}"
            evidence = [
                EvidenceDraft(
                    kind=EvidenceKind.TEST_REPORT,
                    command_id="acceptance_test",
                    content_digest=report_digest,
                )
            ]
            if not process.stdout_truncated and not process.stderr_truncated:
                evidence.insert(
                    0,
                    EvidenceDraft(
                        kind=EvidenceKind.COMMAND,
                        command_id="acceptance_test",
                        content_digest=_digest_json(_process_output(process)),
                    ),
                )
            self._captured[(context.run_id, phase)] = _CapturedTestCommand(
                phase=phase,
                process=process,
                report=report,
                report_path=str(report_path),
                effective_argv_digest=_digest_json(list(effective_argv)),
            )
            return HandlerResult(
                status=HandlerResultStatus.SUCCEEDED,
                output={
                    **_process_output(process),
                    "report_digest": report_digest,
                    "report_size_bytes": size,
                },
                summary="test verification command reached a confirmed result",
                evidence=tuple(evidence),
            )
        except OSError:
            return HandlerResult(
                status=HandlerResultStatus.UNKNOWN_RESULT,
                output=_process_output(process),
                summary="test verification report could not be read",
                error_code=ErrorCode.COMPLETION_TEST_REPORT_MISSING.value,
            )
        finally:
            try:
                report_path.unlink()
            except FileNotFoundError:
                pass
            try:
                Path(report_directory).rmdir()
            except OSError:
                pass

    def take(self, run_id: str, phase: TestExecutionPhase) -> _CapturedTestCommand:
        try:
            return self._captured.pop((run_id, phase))
        except KeyError as exc:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_TEST_RESULT_UNKNOWN,
                "test command result is unavailable",
            ) from exc


class CompletionChecker:
    """Evaluate coded completion facts outside the Agent decision loop."""

    def __init__(
        self,
        storage: SQLiteStorage,
        config: ResolvedHarnessConfig,
        gate: Gate,
        sandbox: Sandbox,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self._storage = storage
        self._config = config
        self._gate = gate
        self._sandbox = sandbox
        self._clock = clock or storage.clock
        self._ids = id_factory or Uuid4IdFactory()
        self._test_executor = _TestVerificationExecutor(sandbox, config)
        self._internal_registry = ToolRegistry()
        self._internal_registry.register(_inspect_file_definition(sandbox))
        self._internal_registry.register(_test_verification_definition(self._test_executor))
        self._gate.install_internal_registry(self._internal_registry)

    def capture_test_baseline(
        self,
        run_id: str,
        lease_token: str,
        *,
        startup_workspace: WorkspaceBaseline,
    ) -> TestBaseline:
        """Execute and persist the single pre-Agent test baseline."""

        self._require_test_repair_task()
        run, snapshot = self._load_run_and_snapshot(
            run_id,
            lease_token,
            allowed_statuses={RunStatus.RUNNING},
        )
        self._validate_startup_workspace(run.workspace, startup_workspace)
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                existing = unit.state.get_test_baseline(run_id)
        except Exception as exc:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_TEST_BASELINE_INVALID,
                _safe_error(exc),
            ) from exc
        if existing is not None:
            return existing[0]
        step_id, _ = self._ensure_internal_step(
            run_id,
            lease_token,
            summary="Test baseline capture",
        )
        pre_workspace = self._sandbox.capture_checkpoint_workspace()
        revision = self._current_revision(run_id, lease_token)
        observation = self._gate.invoke_test_verification(
            run_id,
            step_id,
            TestExecutionPhase.BASELINE,
            lease_token=lease_token,
            expected_run_revision=revision,
        )
        if observation.status != ObservationStatus.ALLOWED:
            raise CompletionCheckerError(
                _observation_error_code(observation),
                observation.message,
            )
        captured = self._test_executor.take(run_id, TestExecutionPhase.BASELINE)
        try:
            parsed = parse_junit_report(captured.report)
        except TestReportError as exc:
            raise CompletionCheckerError(exc.code, exc.message) from exc
        post_workspace = self._sandbox.capture_checkpoint_workspace()
        pre_digest = _checkpoint_digest(pre_workspace)
        post_digest = _checkpoint_digest(post_workspace)
        if pre_digest != post_digest:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_TEST_WORKSPACE_UNSTABLE,
                "baseline test command changed the Git-visible workspace",
            )
        with self._storage.transaction(actor="completion-checker") as unit:
            snapshot = unit.state.get_run_snapshot(run_id)
        report_evidence_id = _evidence_id_of_kind(
            snapshot,
            observation.evidence_ids,
            EvidenceKind.TEST_REPORT,
        )
        if report_evidence_id is None:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_TEST_REPORT_MISSING,
                "baseline report evidence is missing",
            )
        command_evidence_id = _evidence_id_of_kind(
            snapshot,
            observation.evidence_ids,
            EvidenceKind.COMMAND,
        )
        now = self._clock.now()
        execution = _test_execution_record(
            run_id=run_id,
            phase=TestExecutionPhase.BASELINE,
            command_id="acceptance_test",
            tool_call_id=observation.tool_call_id,
            command_evidence_id=command_evidence_id,
            report_evidence_id=report_evidence_id,
            declared_argv=self._test_command(),
            effective_argv_digest=captured.effective_argv_digest,
            config_fingerprint=self._config.fingerprint,
            workspace_pre_digest=pre_digest,
            workspace_post_digest=post_digest,
            process=captured.process,
            parsed=parsed,
            now=now,
            execution_id=f"testexec_{self._ids.new_trace_id()}",
        )
        nodes = _test_node_records(execution.test_execution_id, parsed)
        workspace_json = workspace_baseline_to_json(startup_workspace)
        baseline = TestBaseline(
            baseline_id=f"baseline_{self._ids.new_trace_id()}",
            run_id=run_id,
            test_execution_id=execution.test_execution_id,
            command_id="acceptance_test",
            declared_argv=self._test_command(),
            effective_argv_digest=captured.effective_argv_digest,
            cwd=".",
            config_fingerprint=self._config.fingerprint,
            workspace_baseline_digest=_digest_text(workspace_json),
            report_evidence_id=report_evidence_id,
            created_at=now,
        )
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                unit.state.record_test_execution(execution, nodes, lease_token=lease_token)
                return unit.state.record_test_baseline(
                    baseline,
                    workspace_baseline_json=workspace_json,
                    lease_token=lease_token,
                )
        except Exception as exc:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_TEST_BASELINE_INVALID,
                _safe_error(exc),
            ) from exc

    def evaluate_test_repair(
        self,
        run_id: str,
        lease_token: str,
        *,
        startup_workspace: WorkspaceBaseline,
    ) -> CompletionCheck:
        """Execute final tests and persist the program-owned test-repair verdict."""

        self._require_test_repair_task()
        run, snapshot = self._load_run_and_snapshot(
            run_id,
            lease_token,
            allowed_statuses={RunStatus.VERIFYING},
        )
        self._validate_startup_workspace(run.workspace, startup_workspace)
        if snapshot.completion is not None:
            return snapshot.completion
        self._ensure_internal_step(
            run_id,
            lease_token,
            summary=_INTERNAL_STEP_SUMMARY,
        )
        with self._storage.transaction(actor="completion-checker") as unit:
            stored_baseline = unit.state.get_test_baseline(run_id)
            baseline_execution = unit.state.get_test_execution(
                run_id,
                TestExecutionPhase.BASELINE,
            )
            baseline_nodes = (
                unit.state.get_test_nodes(baseline_execution.test_execution_id)
                if baseline_execution is not None
                else ()
            )
            snapshot = unit.state.get_run_snapshot(run_id)
        checks: list[_Check] = []
        if stored_baseline is None or baseline_execution is None:
            checks.append(
                _Check(
                    "test_baseline_present",
                    False,
                    ErrorCode.COMPLETION_TEST_BASELINE_MISSING,
                    "test baseline is missing",
                    unverifiable=True,
                )
            )
            return self._persist_test_completion(
                run_id,
                lease_token,
                startup_workspace,
                checks,
            )
        try:
            persisted_startup = workspace_baseline_from_json(stored_baseline[1])
            if _digest_text(stored_baseline[1]) != stored_baseline[0].workspace_baseline_digest:
                raise ValueError("workspace baseline digest does not match")
            self._validate_startup_workspace(run.workspace, persisted_startup)
            startup_workspace = persisted_startup
        except Exception as exc:
            checks.append(
                _Check(
                    "test_baseline_present",
                    False,
                    ErrorCode.COMPLETION_TEST_BASELINE_INVALID,
                    _safe_error(exc),
                    unverifiable=True,
                )
            )
            return self._persist_test_completion(
                run_id,
                lease_token,
                startup_workspace,
                checks,
            )
        if (
            stored_baseline[0].config_fingerprint != self._config.fingerprint
            or stored_baseline[0].declared_argv != self._test_command()
        ):
            checks.append(
                _Check(
                    "test_command_consistent",
                    False,
                    ErrorCode.COMPLETION_TEST_COMMAND_MISMATCH,
                    "baseline command or configuration no longer matches",
                )
            )
            return self._persist_test_completion(
                run_id,
                lease_token,
                startup_workspace,
                checks,
            )
        checks.append(
            _Check(
                "test_baseline_present",
                _evidence_is_valid(snapshot, baseline_execution.report_evidence_id),
                (
                    "OK"
                    if _evidence_is_valid(snapshot, baseline_execution.report_evidence_id)
                    else ErrorCode.COMPLETION_TEST_BASELINE_STALE
                ),
                (
                    "test baseline is present"
                    if _evidence_is_valid(snapshot, baseline_execution.report_evidence_id)
                    else "baseline report evidence is invalid"
                ),
            )
        )
        baseline_has_failure = any(
            node.status in {TestNodeStatus.FAILED, TestNodeStatus.ERROR} for node in baseline_nodes
        )
        checks.append(
            _Check(
                "test_baseline_has_failure",
                baseline_has_failure,
                "OK" if baseline_has_failure else ErrorCode.COMPLETION_TEST_BASELINE_NOT_FAILING,
                (
                    "baseline contains a failing test to repair"
                    if baseline_has_failure
                    else "baseline contains no failing or error test node"
                ),
            )
        )
        if not baseline_has_failure:
            return self._persist_test_completion(
                run_id,
                lease_token,
                startup_workspace,
                checks,
            )
        checks.append(
            _Check(
                "test_command_consistent",
                True,
                "OK",
                "baseline and final use the same trusted command and configuration",
            )
        )
        if baseline_execution.workspace_pre_digest != baseline_execution.workspace_post_digest:
            checks.append(
                _Check(
                    "baseline_workspace_stable",
                    False,
                    ErrorCode.COMPLETION_TEST_WORKSPACE_UNSTABLE,
                    "baseline test command changed the workspace",
                )
            )
            return self._persist_test_completion(
                run_id,
                lease_token,
                startup_workspace,
                checks,
            )
        checks.append(
            _Check(
                "baseline_workspace_stable",
                True,
                "OK",
                "baseline workspace was stable",
            )
        )

        existing_final = self._get_test_execution(run_id, TestExecutionPhase.FINAL)
        parsed_final: ParsedTestReport | None = None
        final_execution: TestExecutionRecord | None = existing_final
        final_nodes: tuple[TestNodeRecord, ...] = ()
        if existing_final is None:
            step = next(
                (item.step_id for item in snapshot.steps if item.summary == _INTERNAL_STEP_SUMMARY),
                None,
            )
            if step is None:
                raise CompletionCheckerError(
                    ErrorCode.COMPLETION_INTERNAL_ACCESS_DENIED,
                    "completion verification step is unavailable",
                )
            pre_workspace = self._sandbox.capture_checkpoint_workspace()
            revision = self._current_revision(run_id, lease_token)
            observation = self._gate.invoke_test_verification(
                run_id,
                step,
                TestExecutionPhase.FINAL,
                lease_token=lease_token,
                expected_run_revision=revision,
            )
            if observation.status != ObservationStatus.ALLOWED:
                checks.append(
                    _Check(
                        "final_command_executed",
                        False,
                        _observation_error_code(observation),
                        observation.message,
                        unverifiable=observation.status
                        in {
                            ObservationStatus.UNKNOWN_RESULT,
                            ObservationStatus.DENIED,
                        },
                    )
                )
                return self._persist_test_completion(
                    run_id,
                    lease_token,
                    startup_workspace,
                    checks,
                )
            captured = self._test_executor.take(run_id, TestExecutionPhase.FINAL)
            try:
                parsed_final = parse_junit_report(captured.report)
            except TestReportError as exc:
                checks.append(
                    _Check(
                        "final_report_valid",
                        False,
                        exc.code,
                        exc.message,
                        unverifiable=True,
                    )
                )
                return self._persist_test_completion(
                    run_id,
                    lease_token,
                    startup_workspace,
                    checks,
                )
            post_workspace = self._sandbox.capture_checkpoint_workspace()
            pre_digest = _checkpoint_digest(pre_workspace)
            post_digest = _checkpoint_digest(post_workspace)
            with self._storage.transaction(actor="completion-checker") as unit:
                snapshot = unit.state.get_run_snapshot(run_id)
            report_evidence_id = _evidence_id_of_kind(
                snapshot,
                observation.evidence_ids,
                EvidenceKind.TEST_REPORT,
            )
            if report_evidence_id is None:
                checks.append(
                    _Check(
                        "final_report_valid",
                        False,
                        ErrorCode.COMPLETION_TEST_REPORT_MISSING,
                        "final report evidence is missing",
                        unverifiable=True,
                    )
                )
                return self._persist_test_completion(
                    run_id,
                    lease_token,
                    startup_workspace,
                    checks,
                )
            command_evidence_id = _evidence_id_of_kind(
                snapshot,
                observation.evidence_ids,
                EvidenceKind.COMMAND,
            )
            now = self._clock.now()
            final_execution = _test_execution_record(
                run_id=run_id,
                phase=TestExecutionPhase.FINAL,
                command_id="acceptance_test",
                tool_call_id=observation.tool_call_id,
                command_evidence_id=command_evidence_id,
                report_evidence_id=report_evidence_id,
                declared_argv=self._test_command(),
                effective_argv_digest=captured.effective_argv_digest,
                config_fingerprint=self._config.fingerprint,
                workspace_pre_digest=pre_digest,
                workspace_post_digest=post_digest,
                process=captured.process,
                parsed=parsed_final,
                now=now,
                execution_id=f"testexec_{self._ids.new_trace_id()}",
            )
            final_nodes = _test_node_records(final_execution.test_execution_id, parsed_final)
            try:
                with self._storage.transaction(actor="completion-checker") as unit:
                    unit.state.record_test_execution(
                        final_execution,
                        final_nodes,
                        lease_token=lease_token,
                    )
            except Exception as exc:
                checks.append(
                    _Check(
                        "final_command_executed",
                        False,
                        ErrorCode.COMPLETION_TEST_RESULT_WRITE_FAILED,
                        _safe_error(exc),
                        unverifiable=True,
                    )
                )
                return self._persist_test_completion(
                    run_id,
                    lease_token,
                    startup_workspace,
                    checks,
                )
        else:
            final_nodes = self._get_test_nodes(existing_final.test_execution_id)

        assert final_execution is not None
        checks.append(
            _Check(
                "final_command_executed",
                final_execution.execution_status == TestExecutionStatus.CAPTURED
                and final_execution.exit_code == 0,
                (
                    "OK"
                    if final_execution.execution_status == TestExecutionStatus.CAPTURED
                    and final_execution.exit_code == 0
                    else ErrorCode.COMPLETION_TEST_EXIT_NONZERO
                ),
                (
                    "final test command exited successfully"
                    if final_execution.execution_status == TestExecutionStatus.CAPTURED
                    and final_execution.exit_code == 0
                    else "final test command did not exit successfully"
                ),
            )
        )
        checks.append(
            _Check(
                "final_report_valid",
                bool(final_nodes),
                "OK" if final_nodes else ErrorCode.COMPLETION_TEST_REPORT_EMPTY,
                "final report contains test nodes" if final_nodes else "final report is empty",
                unverifiable=not bool(final_nodes),
            )
        )
        comparison = compare_test_nodes(
            _parsed_from_nodes(baseline_execution, baseline_nodes),
            _parsed_from_nodes(final_execution, final_nodes),
        )
        checks.extend(
            [
                _Check(
                    "baseline_nodes_preserved",
                    not comparison.missing_baseline_keys,
                    ErrorCode.COMPLETION_TEST_NODE_LOST
                    if comparison.missing_baseline_keys
                    else "OK",
                    (
                        "baseline test nodes are preserved"
                        if not comparison.missing_baseline_keys
                        else "one or more baseline test nodes disappeared"
                    ),
                ),
                _Check(
                    "baseline_failures_repaired",
                    not comparison.remaining_baseline_failures,
                    ErrorCode.COMPLETION_TEST_BASELINE_FAILURE_REMAINS
                    if comparison.remaining_baseline_failures
                    else "OK",
                    (
                        "baseline failures were repaired"
                        if not comparison.remaining_baseline_failures
                        else "one or more baseline failures remain"
                    ),
                ),
                _Check(
                    "final_nodes_passed",
                    not comparison.new_failed_keys,
                    ErrorCode.COMPLETION_TEST_NEW_NODE_FAILED
                    if comparison.new_failed_keys
                    else "OK",
                    (
                        "all final test nodes passed"
                        if not comparison.new_failed_keys
                        else "one or more final test nodes failed"
                    ),
                ),
                _Check(
                    "no_new_skips",
                    not comparison.new_skipped_keys,
                    ErrorCode.COMPLETION_TEST_NEW_SKIP if comparison.new_skipped_keys else "OK",
                    (
                        "no executed test became skipped"
                        if not comparison.new_skipped_keys
                        else "one or more tests were newly skipped"
                    ),
                ),
            ]
        )
        current_workspace = self._sandbox.capture_checkpoint_workspace()
        current_digest = _checkpoint_digest(current_workspace)
        stable = final_execution.workspace_pre_digest == final_execution.workspace_post_digest
        checks.append(
            _Check(
                "test_workspace_stable",
                stable,
                "OK" if stable else ErrorCode.COMPLETION_TEST_WORKSPACE_UNSTABLE,
                (
                    "final test command did not change the workspace"
                    if stable
                    else "final test command changed the Git-visible workspace"
                ),
            )
        )
        report_current = current_digest == final_execution.workspace_post_digest
        final_evidence_valid = _evidence_is_valid(
            snapshot,
            final_execution.report_evidence_id,
        )
        report_current = report_current and final_evidence_valid
        if not report_current:
            try:
                with self._storage.transaction(actor="completion-checker") as unit:
                    unit.state.invalidate_evidence(
                        run_id,
                        final_execution.report_evidence_id,
                        reason_code=ErrorCode.COMPLETION_TEST_EVIDENCE_STALE,
                        lease_token=lease_token,
                    )
            except Exception as exc:
                checks.append(
                    _Check(
                        "evidence_current",
                        False,
                        ErrorCode.COMPLETION_TEST_RESULT_WRITE_FAILED,
                        _safe_error(exc),
                        unverifiable=True,
                    )
                )
        checks.append(
            _Check(
                "evidence_current",
                report_current,
                "OK" if report_current else ErrorCode.COMPLETION_TEST_EVIDENCE_STALE,
                (
                    "final report still matches the current workspace"
                    if report_current
                    else "workspace changed or the final report evidence is invalid"
                ),
            )
        )
        scope_check, file_check = self._test_workspace_scope(
            startup_workspace,
            current_workspace,
            baseline_nodes,
        )
        checks.extend([scope_check, file_check])
        with self._storage.transaction(actor="completion-checker") as unit:
            audit_report = unit.audit.verify_chain(run_id)
        checks.append(
            _Check(
                "audit_chain",
                audit_report.valid,
                "OK" if audit_report.valid else ErrorCode.COMPLETION_AUDIT_INVALID,
                audit_report.detail or "audit chain is valid",
                unverifiable=not audit_report.valid,
            )
        )
        return self._persist_test_completion(
            run_id,
            lease_token,
            startup_workspace,
            checks,
        )

    def _require_test_repair_task(self) -> None:
        if self._config.config.task.kind != TaskKind.TEST_REPAIR or not isinstance(
            self._config.config.task.acceptance,
            TestRepairAcceptance,
        ):
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_UNSUPPORTED_TASK,
                "test-repair completion requires a test-repair task",
            )

    def _test_command(self) -> tuple[str, ...]:
        acceptance = self._config.config.task.acceptance
        if not isinstance(acceptance, TestRepairAcceptance):
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_UNSUPPORTED_TASK,
                "test command is unavailable for this task",
            )
        return acceptance.test_command

    def _load_run_and_snapshot(
        self,
        run_id: str,
        lease_token: str,
        *,
        allowed_statuses: set[RunStatus],
    ) -> tuple[Any, RunSnapshot]:
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                unit.state.verify_lease(run_id, lease_token)
                snapshot = unit.state.get_run_snapshot(run_id)
        except (StateStoreError, StorageError) as exc:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_LEASE_INVALID,
                exc.message,
            ) from exc
        if snapshot.run.status not in allowed_statuses:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_RUN_STATE_INVALID,
                "run state does not permit this verification phase",
            )
        if snapshot.run.config_fingerprint != self._config.fingerprint:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_CONFIG_MISMATCH,
                "run configuration fingerprint does not match",
            )
        return snapshot.run, snapshot

    def _get_test_execution(
        self,
        run_id: str,
        phase: TestExecutionPhase,
    ) -> TestExecutionRecord | None:
        with self._storage.transaction(actor="completion-checker") as unit:
            return unit.state.get_test_execution(run_id, phase)

    def _get_test_nodes(self, execution_id: str) -> tuple[TestNodeRecord, ...]:
        with self._storage.transaction(actor="completion-checker") as unit:
            return unit.state.get_test_nodes(execution_id)

    def _test_workspace_scope(
        self,
        startup_workspace: WorkspaceBaseline,
        current_workspace: Any,
        baseline_nodes: tuple[TestNodeRecord, ...],
    ) -> tuple[_Check, _Check]:
        baseline_entries = {
            _workspace_relative(entry.path, startup_workspace.identity.workspace_prefix): entry
            for entry in startup_workspace.workspace_entries
        }
        current_entries = {
            _workspace_relative(entry.path, startup_workspace.identity.workspace_prefix): entry
            for entry in current_workspace.workspace_entries
        }
        changed = {
            path
            for path in baseline_entries.keys() | current_entries.keys()
            if baseline_entries.get(path) != current_entries.get(path)
        }
        allow_write = self._config.config.task.paths.allow_write
        outside = tuple(
            sorted(
                path
                for path in changed
                if not any(glob_matches(pattern, path) for pattern in allow_write)
            )
        )
        scope = _Check(
            "workspace_scope",
            not outside and not current_workspace.entries_truncated,
            (
                "OK"
                if not outside and not current_workspace.entries_truncated
                else (
                    ErrorCode.COMPLETION_TEST_WORKSPACE_OUT_OF_SCOPE
                    if outside
                    else ErrorCode.COMPLETION_TEST_WORKSPACE_UNSTABLE
                )
            ),
            (
                "workspace changes are within allow_write"
                if not outside and not current_workspace.entries_truncated
                else "workspace changes are outside allow_write or were truncated"
            ),
            unverifiable=current_workspace.entries_truncated,
        )
        baseline_files = {node.file for node in baseline_nodes}
        deleted = tuple(
            sorted(
                path for path in baseline_files if path in changed and path not in current_entries
            )
        )
        modified = tuple(
            sorted(
                path
                for path in baseline_files
                if path in changed
                and path in current_entries
                and not any(glob_matches(pattern, path) for pattern in allow_write)
            )
        )
        file_check = _Check(
            "test_files_scope",
            not deleted and not modified,
            (
                "OK"
                if not deleted and not modified
                else (
                    ErrorCode.COMPLETION_TEST_FILE_DELETED
                    if deleted
                    else ErrorCode.COMPLETION_TEST_FILE_MODIFIED
                )
            ),
            (
                "existing test files were not deleted or modified outside allow_write"
                if not deleted and not modified
                else "existing test files were deleted or modified outside allow_write"
            ),
        )
        return scope, file_check

    def _persist_test_completion(
        self,
        run_id: str,
        lease_token: str,
        startup_workspace: WorkspaceBaseline,
        checks: list[_Check],
    ) -> CompletionCheck:
        now = self._clock.now()
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                unit.state.verify_lease(run_id, lease_token)
                final_execution = unit.state.get_test_execution(
                    run_id,
                    TestExecutionPhase.FINAL,
                )
                baseline_execution = unit.state.get_test_execution(
                    run_id,
                    TestExecutionPhase.BASELINE,
                )
                if final_execution is not None:
                    current_digest = _checkpoint_digest(
                        self._sandbox.capture_checkpoint_workspace()
                    )
                    if current_digest != final_execution.workspace_post_digest:
                        unit.state.invalidate_evidence(
                            run_id,
                            final_execution.report_evidence_id,
                            reason_code=ErrorCode.COMPLETION_TEST_EVIDENCE_STALE,
                            lease_token=lease_token,
                        )
                        checks = _replace_check(
                            checks,
                            _Check(
                                "evidence_current",
                                False,
                                ErrorCode.COMPLETION_TEST_EVIDENCE_STALE,
                                "workspace changed after the final test report",
                            ),
                        )
                checks = _collapse_checks(checks)
                acceptance = _acceptance_status(checks)
                items = tuple(
                    CompletionItem(
                        item_id=check.item_id,
                        passed=check.passed,
                        code=_code_value(check.code),
                        message=check.message[:2000],
                    )
                    for check in checks
                )
                comparison = self._sandbox.compare_workspace(startup_workspace)
                diff_evidence = Evidence(
                    evidence_id=self._ids.new_evidence_id(),
                    run_id=run_id,
                    kind=EvidenceKind.DIFF,
                    content_digest=_digest_json(
                        {
                            "changed_paths": list(comparison.changed_paths),
                            "equal": comparison.equal,
                            "identity_changed": comparison.identity_changed,
                        }
                    ),
                    created_at=now,
                )
                unit.state.add_evidence(diff_evidence, lease_token=lease_token)
                completion_evidence_ids = [diff_evidence.evidence_id]
                for execution in (baseline_execution, final_execution):
                    if execution is None:
                        continue
                    completion_evidence_ids.append(execution.report_evidence_id)
                    if execution.command_evidence_id is not None:
                        completion_evidence_ids.append(execution.command_evidence_id)
                for item in items:
                    unit.audit.append(
                        AuditEventDraft(
                            run_id=run_id,
                            event_type=AuditEventType.COMPLETION_CHECKED,
                            phase=GatePhase.EXECUTION,
                            status=AuditStatus.SUCCEEDED if item.passed else AuditStatus.FAILED,
                            decision=AuditDecision.ALLOW if item.passed else AuditDecision.DENY,
                            code=None if item.passed else item.code,
                            rule_id=f"COMPLETION_{item.item_id.upper()}_V1",
                            input_summary={"item_id": item.item_id},
                            output_summary={"passed": item.passed, "code": item.code},
                        )
                    )
                check = CompletionCheck(
                    run_id=run_id,
                    acceptance_status=acceptance,
                    items=items,
                    failure_summary=_failure_summary(items),
                    checked_at=now,
                    evidence_ids=tuple(dict.fromkeys(completion_evidence_ids)),
                )
                return unit.state.record_completion(
                    check,
                    expected_run_revision=unit.state.get_run_revision(run_id),
                    lease_token=lease_token,
                )
        except Exception as exc:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_TEST_RESULT_WRITE_FAILED,
                _safe_error(exc),
            ) from exc

    def evaluate_code_understanding(
        self,
        run_id: str,
        lease_token: str,
        *,
        startup_workspace: WorkspaceBaseline,
    ) -> CompletionCheck:
        """Evaluate one code-understanding run and persist the bound result."""

        if self._config.config.task.kind != TaskKind.CODE_UNDERSTANDING or not isinstance(
            self._config.config.task.acceptance,
            CodeUnderstandingAcceptance,
        ):
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_UNSUPPORTED_TASK,
                "code-understanding completion requires a code-understanding task",
            )
        run, snapshot = self._load_preflight(run_id, lease_token)
        self._validate_startup_workspace(run.workspace, startup_workspace)
        self._ensure_internal_step(
            run_id,
            lease_token,
            summary=_INTERNAL_STEP_SUMMARY,
        )
        run, snapshot = self._load_preflight(run_id, lease_token)

        checks: list[_Check] = []
        completion_evidence: list[EvidenceKind] = []
        parsed_documents: dict[str, _ParsedDocument] = {}
        document_digests: dict[str, str] = {}
        for deliverable in self._config.deliverables:
            try:
                file = self._sandbox.read_file(deliverable)
            except SandboxViolation as exc:
                checks.append(
                    _Check(
                        "deliverable_present",
                        False,
                        ErrorCode.COMPLETION_DELIVERABLE_MISSING,
                        exc.message,
                    )
                )
                continue
            if len(file.data) == 0:
                checks.append(
                    _Check(
                        "deliverable_nonempty",
                        False,
                        ErrorCode.COMPLETION_DELIVERABLE_EMPTY,
                        "deliverable is empty",
                    )
                )
                continue
            if len(file.data) > _MAX_DELIVERABLE_BYTES:
                checks.append(
                    _Check(
                        "deliverable_nonempty",
                        False,
                        ErrorCode.COMPLETION_DELIVERABLE_TOO_LARGE,
                        "deliverable exceeds 1 MiB",
                    )
                )
                continue
            try:
                text = file.data.decode("utf-8")
            except UnicodeDecodeError:
                checks.append(
                    _Check(
                        "document_structure",
                        False,
                        ErrorCode.COMPLETION_DOCUMENT_INVALID,
                        "deliverable is not valid UTF-8",
                    )
                )
                continue
            if "\x00" in text:
                checks.append(
                    _Check(
                        "document_structure",
                        False,
                        ErrorCode.COMPLETION_DOCUMENT_INVALID,
                        "deliverable is not text",
                    )
                )
                continue
            checks.append(
                _Check(
                    "deliverable_present",
                    True,
                    "OK",
                    "deliverable exists",
                )
            )
            checks.append(
                _Check(
                    "deliverable_nonempty",
                    True,
                    "OK",
                    "deliverable is non-empty bounded UTF-8 text",
                )
            )
            document_digests[deliverable] = file.content_digest
            completion_evidence.append(EvidenceKind.DOCUMENT)
            try:
                parsed_documents[deliverable] = parse_code_understanding_document(text)
            except _DocumentViolation as exc:
                checks.append(
                    _Check(
                        "document_structure",
                        False,
                        exc.code,
                        exc.message,
                    )
                )

        checks.extend(
            self._deliverable_checks(
                startup_workspace,
            )
        )
        checks.extend(
            self._document_and_evidence_checks(
                run_id,
                lease_token,
                snapshot,
                parsed_documents,
                document_digests,
            )
        )
        checks.extend(
            self._workspace_checks(
                startup_workspace,
            )
        )
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                audit_report = unit.audit.verify_chain(run_id)
            if audit_report.valid:
                checks.append(_Check("audit_chain", True, "OK", "audit chain is valid"))
            else:
                checks.append(
                    _Check(
                        "audit_chain",
                        False,
                        ErrorCode.COMPLETION_AUDIT_INVALID,
                        audit_report.detail or "audit chain is invalid",
                        unverifiable=True,
                    )
                )
        except Exception as exc:
            checks.append(
                _Check(
                    "audit_chain",
                    False,
                    ErrorCode.COMPLETION_AUDIT_INVALID,
                    _safe_error(exc),
                    unverifiable=True,
                )
            )

        claim_detected = detect_completion_claim(run.candidate_final_answer)
        all_prior_pass = all(check.passed for check in checks)
        checks.append(
            _Check(
                "claim_without_evidence",
                not claim_detected or all_prior_pass,
                ErrorCode.COMPLETION_CLAIM_WITHOUT_EVIDENCE
                if claim_detected and not all_prior_pass
                else "OK",
                (
                    "candidate completion claim is unsupported by program facts"
                    if claim_detected and not all_prior_pass
                    else "candidate final answer is not completion evidence"
                ),
            )
        )

        checks = _collapse_checks(checks)
        acceptance = _acceptance_status(checks)
        items = tuple(
            CompletionItem(
                item_id=check.item_id,
                passed=check.passed,
                code=_code_value(check.code),
                message=check.message[:2000],
            )
            for check in checks
        )
        failure_summary = _failure_summary(items)
        return self._persist_completion(
            run_id=run_id,
            lease_token=lease_token,
            expected_run_revision=self._current_revision(run_id, lease_token),
            acceptance=acceptance,
            items=items,
            failure_summary=failure_summary,
            completion_evidence=completion_evidence,
            document_digests=document_digests,
            startup_workspace=startup_workspace,
        )

    def _load_preflight(self, run_id: str, lease_token: str) -> tuple[Any, RunSnapshot]:
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                unit.state.verify_lease(run_id, lease_token)
                snapshot = unit.state.get_run_snapshot(run_id)
        except (StateStoreError, StorageError) as exc:
            raise CompletionCheckerError(ErrorCode.COMPLETION_LEASE_INVALID, exc.message) from exc
        if snapshot.run.status.value != "VERIFYING":
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_RUN_STATE_INVALID,
                "completion requires a VERIFYING run",
            )
        if snapshot.run.config_fingerprint != self._config.fingerprint:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_CONFIG_MISMATCH,
                "run configuration fingerprint does not match",
            )
        return snapshot.run, snapshot

    def _validate_startup_workspace(
        self,
        run_workspace: WorkspaceIdentity,
        startup_workspace: WorkspaceBaseline,
    ) -> None:
        if run_workspace != startup_workspace.identity:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_IDENTITY_CHANGED,
                "startup workspace identity does not match the run",
            )

    def _deliverable_checks(
        self,
        startup_workspace: WorkspaceBaseline,
    ) -> list[_Check]:
        baseline_entries = {
            _workspace_relative(entry.path, startup_workspace.identity.workspace_prefix): entry
            for entry in startup_workspace.workspace_entries
        }
        checks: list[_Check] = []
        for deliverable in self._config.deliverables:
            try:
                current = self._sandbox.read_file(deliverable)
            except SandboxViolation:
                continue
            prior = baseline_entries.get(deliverable)
            if prior is None or prior.content_digest != current.content_digest:
                checks.append(
                    _Check(
                        "deliverable_changed",
                        True,
                        "OK",
                        "deliverable changed relative to the startup baseline",
                    )
                )
            else:
                checks.append(
                    _Check(
                        "deliverable_changed",
                        False,
                        ErrorCode.COMPLETION_DELIVERABLE_NOT_CHANGED,
                        "deliverable exists but was not changed in this run",
                    )
                )
        if not any(check.item_id == "deliverable_changed" for check in checks):
            checks.append(
                _Check(
                    "deliverable_changed",
                    False,
                    ErrorCode.COMPLETION_DELIVERABLE_MISSING,
                    "no deliverable was available for comparison",
                )
            )
        return checks

    def _document_and_evidence_checks(
        self,
        run_id: str,
        lease_token: str,
        snapshot: RunSnapshot,
        parsed_documents: dict[str, _ParsedDocument],
        document_digests: dict[str, str],
    ) -> list[_Check]:
        if len(parsed_documents) != len(self._config.deliverables):
            return [
                _Check(
                    "facts_cited",
                    False,
                    ErrorCode.COMPLETION_DOCUMENT_INVALID,
                    "not all deliverables could be parsed",
                ),
                _Check(
                    "evidence_read",
                    False,
                    ErrorCode.COMPLETION_EVIDENCE_NOT_READ,
                    "evidence cannot be checked until all documents parse",
                ),
            ]
        checks: list[_Check] = []
        all_definitions: dict[int, _EvidenceDefinition] = {}
        for document in parsed_documents.values():
            for number, definition in document.evidence_definitions.items():
                if number in all_definitions and all_definitions[number] != definition:
                    checks.append(
                        _Check(
                            "evidence_defined",
                            False,
                            ErrorCode.COMPLETION_EVIDENCE_DUPLICATE,
                            "the same evidence number is defined inconsistently",
                        )
                    )
                all_definitions[number] = definition
        if not checks:
            checks.append(_Check("evidence_defined", True, "OK", "evidence definitions are unique"))

        read_evidence = tuple(
            evidence
            for evidence in snapshot.evidence
            if evidence.kind == EvidenceKind.READ
            and evidence.is_valid
            and evidence.path is not None
            and evidence.tool_call_id is not None
        )
        agent_reads = tuple(
            evidence for evidence in read_evidence if _is_agent_read(evidence, snapshot)
        )
        unread: list[int] = []
        stale: list[int] = []
        partial: list[int] = []
        invalid_paths: list[int] = []
        inspected: dict[str, tuple[str, int] | None] = {}
        for number, definition in sorted(all_definitions.items()):
            if definition.path in self._config.deliverables:
                invalid_paths.append(number)
                continue
            if definition.path not in inspected:
                inspected[definition.path] = self._inspect_current_file(
                    run_id,
                    lease_token,
                    definition.path,
                )
            current = inspected[definition.path]
            if current is None:
                invalid_paths.append(number)
                continue
            current_digest, current_lines = current
            if definition.end_line > current_lines:
                invalid_paths.append(number)
                continue
            matching = tuple(
                evidence
                for evidence in agent_reads
                if evidence.path == definition.path
                and evidence.content_digest == current_digest
                and evidence.line_start is not None
                and evidence.line_end is not None
            )
            if not matching:
                if any(evidence.path == definition.path for evidence in read_evidence):
                    stale.append(number)
                else:
                    unread.append(number)
                continue
            if not _ranges_cover(
                (definition.start_line, definition.end_line),
                tuple((int(evidence.line_start), int(evidence.line_end)) for evidence in matching),
            ):
                partial.append(number)
        if invalid_paths:
            checks.append(
                _Check(
                    "evidence_current",
                    False,
                    ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID,
                    "one or more evidence definitions are outside readable source files",
                )
            )
        if unread:
            checks.append(
                _Check(
                    "evidence_read",
                    False,
                    ErrorCode.COMPLETION_EVIDENCE_NOT_READ,
                    "one or more evidence definitions were not read by the Agent",
                )
            )
        if stale:
            checks.append(
                _Check(
                    "evidence_current",
                    False,
                    ErrorCode.COMPLETION_EVIDENCE_STALE,
                    "one or more evidence definitions no longer match current content",
                )
            )
        if partial:
            checks.append(
                _Check(
                    "evidence_current",
                    False,
                    ErrorCode.COMPLETION_EVIDENCE_PARTIAL,
                    "one or more evidence ranges were not fully read",
                )
            )
        if not unread and not stale and not invalid_paths and not partial:
            checks.append(_Check("evidence_read", True, "OK", "evidence is read and current"))
            checks.append(_Check("evidence_current", True, "OK", "evidence content is current"))

        for document in parsed_documents.values():
            for fact_number, references in document.fact_references.items():
                if any(reference not in all_definitions for reference in references):
                    checks.append(
                        _Check(
                            "facts_cited",
                            False,
                            ErrorCode.COMPLETION_EVIDENCE_UNDEFINED,
                            f"fact {fact_number} references undefined evidence",
                        )
                    )
                    break
        if not any(check.item_id == "facts_cited" for check in checks):
            checks.append(_Check("facts_cited", True, "OK", "all facts cite defined evidence"))
        return checks

    def _inspect_current_file(
        self,
        run_id: str,
        lease_token: str,
        path: str,
    ) -> tuple[str, int] | None:
        with self._storage.transaction(actor="completion-checker") as unit:
            snapshot = unit.state.get_run_snapshot(run_id)
            revision = unit.state.get_run_revision(run_id)
        step_id = next(
            (step.step_id for step in snapshot.steps if step.summary == _INTERNAL_STEP_SUMMARY),
            None,
        )
        if step_id is None:
            return None
        observation = self._gate.invoke_internal(
            run_id,
            step_id,
            _INSPECT_TOOL,
            {"path": path},
            lease_token=lease_token,
            expected_run_revision=revision,
        )
        if observation.status != ObservationStatus.ALLOWED or not isinstance(
            observation.output,
            dict,
        ):
            return None
        digest = observation.output.get("content_digest")
        line_count = observation.output.get("line_count")
        if not isinstance(digest, str) or type(line_count) is not int:
            return None
        return digest, line_count

    def _workspace_checks(
        self,
        startup_workspace: WorkspaceBaseline,
    ) -> list[_Check]:
        checks: list[_Check] = []
        comparison = self._sandbox.compare_workspace(startup_workspace)
        current = self._sandbox.capture_checkpoint_workspace()
        current_entries = {
            _workspace_relative(entry.path, startup_workspace.identity.workspace_prefix): entry
            for entry in current.workspace_entries
        }
        baseline_entries = {
            _workspace_relative(entry.path, startup_workspace.identity.workspace_prefix): entry
            for entry in startup_workspace.workspace_entries
        }
        changed = {
            path
            for path in baseline_entries.keys() | current_entries.keys()
            if baseline_entries.get(path) != current_entries.get(path)
        }
        allowed = set(self._config.deliverables)
        if comparison.identity_changed:
            checks.append(
                _Check(
                    "workspace_scope",
                    False,
                    ErrorCode.COMPLETION_IDENTITY_CHANGED,
                    "workspace identity, HEAD, or index changed",
                )
            )
        elif comparison.external_changed:
            checks.append(
                _Check(
                    "workspace_scope",
                    False,
                    ErrorCode.COMPLETION_EXTERNAL_WORKSPACE_CHANGED,
                    "Git-visible content outside the workspace changed",
                )
            )
        elif not changed.issubset(allowed):
            outside = sorted(changed - allowed)[:20]
            checks.append(
                _Check(
                    "workspace_scope",
                    False,
                    ErrorCode.COMPLETION_WORKSPACE_OUT_OF_SCOPE,
                    "workspace changes include paths outside configured deliverables: "
                    + ", ".join(outside),
                )
            )
        elif current.entries_truncated:
            checks.append(
                _Check(
                    "workspace_scope",
                    False,
                    ErrorCode.COMPLETION_WORKSPACE_CHANGED,
                    "workspace snapshot was truncated",
                    unverifiable=True,
                )
            )
        elif comparison.external_protection == ExternalProtection.DISABLED:
            checks.append(
                _Check(
                    "workspace_scope",
                    True,
                    "OK",
                    "workspace scope passed; external Git changes were not monitored",
                )
            )
        else:
            checks.append(_Check("workspace_scope", True, "OK", "workspace scope is valid"))
        return checks

    def _persist_completion(
        self,
        *,
        run_id: str,
        lease_token: str,
        expected_run_revision: int,
        acceptance: AcceptanceStatus,
        items: tuple[CompletionItem, ...],
        failure_summary: str | None,
        completion_evidence: list[EvidenceKind],
        document_digests: dict[str, str],
        startup_workspace: WorkspaceBaseline,
    ) -> CompletionCheck:
        now = self._clock.now()
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                unit.state.verify_lease(run_id, lease_token)
                new_evidence_ids: list[str] = []
                for deliverable, digest in sorted(document_digests.items()):
                    evidence = Evidence(
                        evidence_id=self._ids.new_evidence_id(),
                        run_id=run_id,
                        kind=EvidenceKind.DOCUMENT,
                        path=deliverable,
                        content_digest=digest,
                        created_at=now,
                    )
                    unit.state.add_evidence(evidence, lease_token=lease_token)
                    new_evidence_ids.append(evidence.evidence_id)
                comparison = self._sandbox.compare_workspace(startup_workspace)
                diff_digest = _digest_json(
                    {
                        "changed_paths": list(comparison.changed_paths),
                        "equal": comparison.equal,
                        "identity_changed": comparison.identity_changed,
                    }
                )
                evidence = Evidence(
                    evidence_id=self._ids.new_evidence_id(),
                    run_id=run_id,
                    kind=EvidenceKind.DIFF,
                    content_digest=diff_digest,
                    created_at=now,
                )
                unit.state.add_evidence(evidence, lease_token=lease_token)
                new_evidence_ids.append(evidence.evidence_id)
                for item in items:
                    unit.audit.append(
                        AuditEventDraft(
                            run_id=run_id,
                            event_type=AuditEventType.COMPLETION_CHECKED,
                            phase=GatePhase.EXECUTION,
                            status=AuditStatus.SUCCEEDED if item.passed else AuditStatus.FAILED,
                            decision=AuditDecision.ALLOW if item.passed else AuditDecision.DENY,
                            code=None if item.passed else item.code,
                            rule_id=f"COMPLETION_{item.item_id.upper()}_V1",
                            input_summary={"item_id": item.item_id},
                            output_summary={"passed": item.passed, "code": item.code},
                        )
                    )
                check = CompletionCheck(
                    run_id=run_id,
                    acceptance_status=acceptance,
                    items=items,
                    failure_summary=failure_summary,
                    checked_at=now,
                    evidence_ids=tuple(new_evidence_ids),
                )
                return unit.state.record_completion(
                    check,
                    expected_run_revision=unit.state.get_run_revision(run_id),
                    lease_token=lease_token,
                )
        except Exception as exc:
            if isinstance(exc, CompletionCheckerError):
                raise
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_RESULT_WRITE_FAILED,
                _safe_error(exc),
            ) from exc

    def _current_revision(self, run_id: str, lease_token: str) -> int:
        with self._storage.transaction(actor="completion-checker") as unit:
            unit.state.verify_lease(run_id, lease_token)
            return unit.state.get_run_revision(run_id)

    def _ensure_internal_step(
        self,
        run_id: str,
        lease_token: str,
        *,
        summary: str,
    ) -> tuple[str, int]:
        try:
            with self._storage.transaction(actor="completion-checker") as unit:
                unit.state.verify_lease(run_id, lease_token)
                snapshot = unit.state.get_run_snapshot(run_id)
                for step in snapshot.steps:
                    if step.summary == summary:
                        return step.step_id, unit.state.get_run_revision(run_id)
                ordinal = max((step.ordinal for step in snapshot.steps), default=-1) + 1
                step = Step(
                    step_id=self._ids.new_step_id(),
                    run_id=run_id,
                    ordinal=ordinal,
                    status=StepStatus.PLANNED,
                    summary=summary,
                    started_at=self._clock.now(),
                )
                unit.state.create_step(
                    step,
                    expected_run_revision=unit.state.get_run_revision(run_id),
                    lease_token=lease_token,
                )
                return step.step_id, unit.state.get_run_revision(run_id)
        except Exception as exc:
            raise CompletionCheckerError(
                ErrorCode.COMPLETION_INTERNAL_ACCESS_DENIED,
                _safe_error(exc),
            ) from exc


class _DocumentViolation(ValueError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def parse_code_understanding_document(text: str) -> _ParsedDocument:
    """Parse the frozen three-section code-understanding document format."""

    if not isinstance(text, str) or not text.strip():
        raise _DocumentViolation(ErrorCode.COMPLETION_DOCUMENT_INVALID, "document is empty")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    order: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading not in _REQUIRED_SECTIONS:
                current = None
                continue
            if heading in sections:
                raise _DocumentViolation(
                    ErrorCode.COMPLETION_SECTION_DUPLICATE,
                    f"section {heading} appears more than once",
                )
            sections[heading] = []
            order.append(heading)
            current = heading
            continue
        if current is not None:
            sections[current].append(line)
    for heading in _REQUIRED_SECTIONS:
        if heading not in sections:
            raise _DocumentViolation(
                ErrorCode.COMPLETION_SECTION_MISSING,
                f"section {heading} is missing",
            )
    if tuple(order) != _REQUIRED_SECTIONS:
        raise _DocumentViolation(
            ErrorCode.COMPLETION_DOCUMENT_INVALID,
            "required sections must appear once in facts/inference/evidence order",
        )

    facts: list[str] = []
    fact_refs: dict[int, tuple[int, ...]] = {}
    for line in sections[_SECTION_FACTS]:
        match = _LIST_ITEM.match(line)
        if match is None:
            continue
        fact = match.group(1).strip()
        if not fact:
            continue
        references = tuple(int(value) for value in _FACT_REFERENCE.findall(fact))
        if not references:
            raise _DocumentViolation(
                ErrorCode.COMPLETION_FACT_WITHOUT_EVIDENCE,
                "a fact item does not cite evidence",
            )
        facts.append(fact)
        fact_refs[len(facts)] = references
    if not facts:
        raise _DocumentViolation(
            ErrorCode.COMPLETION_FACT_WITHOUT_EVIDENCE,
            "facts section requires at least one cited list item",
        )
    inference = "\n".join(line.strip() for line in sections[_SECTION_INFERENCE]).strip()
    if not inference:
        raise _DocumentViolation(
            ErrorCode.COMPLETION_DOCUMENT_INVALID,
            "inference section is empty; write 无 when there is no inference",
        )
    definitions: dict[int, _EvidenceDefinition] = {}
    for line in sections[_SECTION_EVIDENCE]:
        stripped = line.strip()
        if not stripped:
            continue
        match = _EVIDENCE_DEFINITION.match(stripped)
        if match is None:
            raise _DocumentViolation(
                ErrorCode.COMPLETION_EVIDENCE_MALFORMED,
                "code evidence must use E<n>: `path:start[-end]`",
            )
        number = int(match.group(1))
        path = match.group(2)
        start = int(match.group(3))
        end = int(match.group(4) or start)
        if end < start:
            raise _DocumentViolation(
                ErrorCode.COMPLETION_EVIDENCE_MALFORMED,
                "evidence line range is reversed",
            )
        path_syntax = PurePosixPath(path)
        if (
            path_syntax.is_absolute()
            or any(part in {"", ".", ".."} for part in path_syntax.parts)
            or "\\" in path
        ):
            raise _DocumentViolation(
                ErrorCode.COMPLETION_EVIDENCE_PATH_INVALID,
                "evidence path must be normalized and workspace-relative",
            )
        if number in definitions:
            raise _DocumentViolation(
                ErrorCode.COMPLETION_EVIDENCE_DUPLICATE,
                "evidence number is defined more than once",
            )
        definitions[number] = _EvidenceDefinition(
            evidence_number=number,
            path=path_syntax.as_posix(),
            start_line=start,
            end_line=end,
        )
    if not definitions:
        raise _DocumentViolation(
            ErrorCode.COMPLETION_EVIDENCE_MALFORMED,
            "code evidence section requires at least one definition",
        )
    for references in fact_refs.values():
        if any(reference not in definitions for reference in references):
            raise _DocumentViolation(
                ErrorCode.COMPLETION_EVIDENCE_UNDEFINED,
                "a fact references undefined evidence",
            )
    return _ParsedDocument(
        facts=tuple(facts),
        inference=inference,
        evidence_definitions=definitions,
        fact_references=fact_refs,
    )


def detect_completion_claim(text: str | None) -> bool:
    """Detect a bounded, conservative positive completion claim."""

    if not isinstance(text, str) or not text.strip():
        return False
    normalized = " ".join(text.casefold().split())
    if any(marker in normalized for marker in _NEGATED_MARKERS):
        return False
    return any(marker in normalized for marker in _COMPLETION_MARKERS)


def workspace_baseline_to_json(baseline: WorkspaceBaseline) -> str:
    """Serialize a WorkspaceBaseline into deterministic bounded JSON."""

    payload = {
        "schema_version": baseline.schema_version,
        "identity": baseline.identity.model_dump(mode="json"),
        "head_oid_or_unborn": baseline.head_oid_or_unborn,
        "index_digest": baseline.index_digest,
        "workspace_status_digest": baseline.workspace_status_digest,
        "workspace_diff_digest": baseline.workspace_diff_digest,
        "external_dirty_baseline_digest": baseline.external_dirty_baseline_digest,
        "external_protection": baseline.external_protection.value,
        "workspace_entries": [
            {
                "status": entry.status,
                "path": entry.path,
                "file_type": entry.file_type,
                "content_digest": entry.content_digest,
            }
            for entry in baseline.workspace_entries
        ],
        "external_entries": [
            {
                "status": entry.status,
                "path": entry.path,
                "file_type": entry.file_type,
                "content_digest": entry.content_digest,
            }
            for entry in baseline.external_entries
        ],
        "captured_at": baseline.captured_at.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def workspace_baseline_from_json(value: str) -> WorkspaceBaseline:
    """Rebuild one WorkspaceBaseline from persisted canonical JSON."""

    payload = json.loads(value)
    return WorkspaceBaseline(
        identity=WorkspaceIdentity.model_validate(payload["identity"], strict=True),
        head_oid_or_unborn=str(payload["head_oid_or_unborn"]),
        index_digest=str(payload["index_digest"]),
        workspace_status_digest=str(payload["workspace_status_digest"]),
        workspace_diff_digest=str(payload["workspace_diff_digest"]),
        external_dirty_baseline_digest=str(payload["external_dirty_baseline_digest"]),
        external_protection=ExternalProtection(str(payload["external_protection"])),
        workspace_entries=tuple(
            WorkspaceEntry(
                status=str(item["status"]),
                path=str(item["path"]),
                file_type=str(item["file_type"]),
                content_digest=str(item["content_digest"]),
            )
            for item in payload["workspace_entries"]
        ),
        external_entries=tuple(
            WorkspaceEntry(
                status=str(item["status"]),
                path=str(item["path"]),
                file_type=str(item["file_type"]),
                content_digest=str(item["content_digest"]),
            )
            for item in payload["external_entries"]
        ),
        captured_at=datetime.strptime(
            str(payload["captured_at"]),
            "%Y-%m-%dT%H:%M:%S.%fZ",
        ).replace(tzinfo=timezone.utc),
        schema_version=int(payload["schema_version"]),
    )


def build_pytest_report_argv(
    base_argv: tuple[str, ...],
    report_path: Path,
) -> tuple[str, ...]:
    """Return one trusted pytest argv with verifier-owned report arguments."""

    if (
        not base_argv
        or Path(base_argv[0]).name.casefold() != "python"
        or tuple(base_argv[1:3]) != ("-m", "pytest")
    ):
        raise ValueError("P0 test verification requires python -m pytest")
    return (
        *base_argv,
        "-o",
        "junit_family=xunit2",
        f"--junitxml={report_path}",
    )


def _process_output(process: SandboxProcessResult) -> dict[str, Any]:
    return {
        "command_id": process.command_id,
        "status": process.status.value,
        "exit_code": process.exit_code,
        "signal": process.signal,
        "stdout_truncated": process.stdout_truncated,
        "stderr_truncated": process.stderr_truncated,
        "timed_out": process.timed_out,
        "cancelled": process.cancelled,
        "process_group_terminated": process.process_group_terminated,
        "duration_ms": process.duration_ms,
        "result_code": process.result_code.value if process.result_code else None,
    }


def _test_verification_definition(
    executor: _TestVerificationExecutor,
) -> ToolDefinition:
    return ToolDefinition(
        spec=ToolSpec(
            name=_TEST_VERIFICATION_TOOL,
            description="Run the trusted test-repair baseline or final acceptance command.",
            input_schema={
                "type": "object",
                "properties": {
                    "phase": {
                        "type": "string",
                        "enum": ["BASELINE", "FINAL"],
                    }
                },
                "required": ["phase"],
                "additionalProperties": False,
            },
            output_kind=ToolOutputKind.JSON,
            risk=ToolRisk.EXECUTE,
            required_permissions=frozenset({ToolPermission.EXECUTE}),
            timeout_seconds=3_635,
            retry_policy=RetryPolicy.NEVER,
            idempotent=False,
            path_arguments=(),
            output_limit_bytes=16_384,
            approval_requirement=ApprovalRequirement.NEVER,
            concurrency_class=ConcurrencyClass.SERIAL,
        ),
        handler=executor.handler,
    )


def _test_execution_record(
    *,
    run_id: str,
    phase: TestExecutionPhase,
    command_id: str,
    tool_call_id: str,
    command_evidence_id: str | None,
    report_evidence_id: str,
    declared_argv: tuple[str, ...],
    effective_argv_digest: str,
    config_fingerprint: str,
    workspace_pre_digest: str,
    workspace_post_digest: str,
    process: SandboxProcessResult,
    parsed: ParsedTestReport,
    now: datetime,
    execution_id: str,
) -> TestExecutionRecord:
    return TestExecutionRecord(
        test_execution_id=execution_id,
        run_id=run_id,
        phase=phase,
        command_id=command_id,
        tool_call_id=tool_call_id,
        command_evidence_id=command_evidence_id,
        report_evidence_id=report_evidence_id,
        declared_argv=declared_argv,
        effective_argv_digest=effective_argv_digest,
        cwd=".",
        config_fingerprint=config_fingerprint,
        workspace_pre_digest=workspace_pre_digest,
        workspace_post_digest=workspace_post_digest,
        execution_status=TestExecutionStatus.CAPTURED,
        exit_code=process.exit_code,
        report_digest=parsed.report_digest,
        report_size_bytes=parsed.report_size_bytes,
        report_truncated=False,
        parser_version=parsed.parser_version,
        total_count=parsed.total_count,
        passed_count=parsed.passed_count,
        failed_count=parsed.failed_count,
        error_count=parsed.error_count,
        skipped_count=parsed.skipped_count,
        unknown_count=parsed.unknown_count,
        failure_summary=parsed.failure_summary,
        created_at=now,
    )


def _test_node_records(
    execution_id: str,
    parsed: ParsedTestReport,
) -> tuple[TestNodeRecord, ...]:
    return tuple(
        TestNodeRecord(
            test_execution_id=execution_id,
            node_key=node.node_key,
            label=node.label,
            file=node.file,
            status=node.status,
            failure_code=node.failure_code,
            failure_summary=node.failure_summary,
        )
        for node in parsed.nodes
    )


def _parsed_from_nodes(
    execution: TestExecutionRecord,
    nodes: tuple[TestNodeRecord, ...],
) -> ParsedTestReport:
    from .test_reports import ParsedTestCase

    parsed_nodes = tuple(
        ParsedTestCase(
            node_key=node.node_key,
            label=node.label,
            file=node.file,
            status=node.status,
            failure_code=node.failure_code,
            failure_summary=node.failure_summary,
        )
        for node in nodes
    )
    return ParsedTestReport(
        report_digest=execution.report_digest,
        report_size_bytes=execution.report_size_bytes,
        nodes=parsed_nodes,
        passed_count=execution.passed_count,
        failed_count=execution.failed_count,
        error_count=execution.error_count,
        skipped_count=execution.skipped_count,
        unknown_count=execution.unknown_count,
        failure_summary=execution.failure_summary,
        parser_version=execution.parser_version,
    )


def _evidence_id_of_kind(
    snapshot: RunSnapshot,
    evidence_ids: tuple[str, ...],
    kind: EvidenceKind,
) -> str | None:
    wanted = set(evidence_ids)
    for evidence in snapshot.evidence:
        if evidence.evidence_id in wanted and evidence.kind == kind:
            return evidence.evidence_id
    return None


def _evidence_is_valid(snapshot: RunSnapshot, evidence_id: str) -> bool:
    return any(
        evidence.evidence_id == evidence_id and evidence.is_valid for evidence in snapshot.evidence
    )


def _checkpoint_digest(snapshot: Any) -> str:
    return _digest_json(snapshot.model_dump(mode="json", exclude={"captured_at"}))


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _workspace_relative(path: str, workspace_prefix: str) -> str:
    prefix = workspace_prefix.strip("/")
    if not prefix or prefix == ".":
        return path
    if path == prefix:
        return "."
    marker = f"{prefix}/"
    return path[len(marker) :] if path.startswith(marker) else path


def _observation_error_code(observation: Any) -> ErrorCode:
    try:
        return ErrorCode(observation.code)
    except (ValueError, TypeError):
        return ErrorCode.COMPLETION_TEST_RESULT_UNKNOWN


def _inspect_file_definition(sandbox: Sandbox) -> ToolDefinition:
    def handler(
        arguments: dict[str, Any],
        _context: ToolExecutionContext,
    ) -> HandlerResult:
        try:
            file = sandbox.read_file(str(arguments["path"]))
            text = file.data.decode("utf-8")
        except SandboxViolation as exc:
            return HandlerResult(
                status=HandlerResultStatus.FAILED,
                output={"path": str(arguments["path"]), "status": "rejected"},
                summary=exc.message,
                error_code=exc.code.value,
            )
        except UnicodeDecodeError:
            return HandlerResult(
                status=HandlerResultStatus.FAILED,
                output={"path": str(arguments["path"]), "status": "non_text"},
                summary="file is not valid UTF-8",
                error_code=ErrorCode.TOOL_TEXT_ENCODING_INVALID.value,
            )
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={
                "path": file.path.workspace_relative,
                "content_digest": file.content_digest,
                "byte_count": file.byte_count,
                "line_count": len(text.splitlines()),
            },
            summary="inspected verifier file version",
            evidence=(
                _evidence_draft(
                    EvidenceKind.READ,
                    path=file.path.workspace_relative,
                    content_digest=file.content_digest,
                ),
            ),
        )

    return ToolDefinition(
        spec=ToolSpec(
            name=_INSPECT_TOOL,
            description="Inspect one file version for the completion verifier.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1024,
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            output_kind=ToolOutputKind.JSON,
            risk=ToolRisk.READ,
            required_permissions=frozenset({ToolPermission.READ}),
            timeout_seconds=5,
            retry_policy=RetryPolicy.SAFE_READ_ONLY,
            idempotent=True,
            path_arguments=("path",),
            output_limit_bytes=4096,
            approval_requirement=ApprovalRequirement.NEVER,
            concurrency_class=ConcurrencyClass.READ_ONLY_PARALLEL,
        ),
        handler=handler,
    )


def _is_agent_read(evidence: Evidence, snapshot: RunSnapshot) -> bool:
    invocation = next(
        (
            invocation
            for invocation in snapshot.invocations
            if invocation.tool_call_id == evidence.tool_call_id
        ),
        None,
    )
    return invocation is not None and invocation.tool_name == "read_file"


def _ranges_cover(required: tuple[int, int], ranges: tuple[tuple[int, int], ...]) -> bool:
    start, end = required
    ordered = sorted(ranges)
    current = start
    for range_start, range_end in ordered:
        if range_end < current:
            continue
        if range_start > current:
            return False
        current = max(current, range_end + 1)
        if current > end:
            return True
    return False


def _acceptance_status(checks: list[_Check]) -> AcceptanceStatus:
    failures = [check for check in checks if not check.passed]
    if not failures:
        return AcceptanceStatus.ACCEPTED
    if any(check.unverifiable for check in failures):
        return AcceptanceStatus.UNVERIFIABLE
    return AcceptanceStatus.REJECTED


def _collapse_checks(checks: list[_Check]) -> list[_Check]:
    """Merge repeated per-path checks into one stable item per item_id."""

    merged: dict[str, _Check] = {}
    ordering: list[str] = []
    for check in checks:
        current = merged.get(check.item_id)
        if current is None:
            merged[check.item_id] = check
            ordering.append(check.item_id)
            continue
        passed = current.passed and check.passed
        code = current.code if current.passed else current.code
        messages = [current.message]
        if check.message != current.message:
            messages.append(check.message)
        merged[check.item_id] = _Check(
            item_id=check.item_id,
            passed=passed,
            code=code if not passed else "OK",
            message="; ".join(dict.fromkeys(messages))[:2000],
            evidence_ids=tuple(sorted(set(current.evidence_ids).union(check.evidence_ids))),
            unverifiable=current.unverifiable or check.unverifiable,
        )
    return [merged[item_id] for item_id in ordering]


def _replace_check(checks: list[_Check], replacement: _Check) -> list[_Check]:
    found = False
    updated: list[_Check] = []
    for check in checks:
        if check.item_id == replacement.item_id:
            updated.append(replacement)
            found = True
        else:
            updated.append(check)
    if not found:
        updated.append(replacement)
    return updated


def _failure_summary(items: tuple[CompletionItem, ...]) -> str | None:
    failures = [f"{item.item_id}:{item.code}" for item in items if not item.passed]
    return ", ".join(sorted(set(failures)))[:4000] or None


def _code_value(code: ErrorCode | str) -> str:
    return code.value if isinstance(code, ErrorCode) else code


def _evidence_draft(kind: EvidenceKind, *, path: str, content_digest: str):
    from .domain import EvidenceDraft

    return EvidenceDraft(kind=kind, path=path, content_digest=content_digest)


def _digest_json(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _safe_error(exc: Exception) -> str:
    message = str(exc)
    return message[:1000] if message else exc.__class__.__name__


__all__ = [
    "CompletionChecker",
    "CompletionCheckerError",
    "detect_completion_claim",
    "parse_code_understanding_document",
    "workspace_baseline_from_json",
    "workspace_baseline_to_json",
]
