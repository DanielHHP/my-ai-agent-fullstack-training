"""Deterministic Agent Run orchestration across existing governance components."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from smolagents import Model
from smolagents.agents import ActionOutput, ToolOutput
from smolagents.memory import ActionStep, FinalAnswerStep, PlanningStep, ToolCall
from smolagents.monitoring import TokenUsage

from .agent_adapter import (
    AgentAdapterError,
    HarnessAgentAdapter,
    HarnessAgentStopSignal,
    assert_smolagents_compatibility,
    build_tool_calling_agent,
)
from .audit import AuditStoreError
from .checkpoint import CheckpointService, CheckpointServiceError
from .completion import CompletionChecker, CompletionCheckerError
from .config import ResolvedHarnessConfig, TestRepairAcceptance, load_config
from .domain import (
    AcceptanceStatus,
    BudgetCategory,
    BudgetDeltaCommand,
    BudgetSourceKind,
    CheckpointBoundary,
    CheckpointBoundaryKind,
    Clock,
    ErrorCode,
    ExecutionMode,
    FaultInjector,
    IdFactory,
    InvocationStatus,
    LeasePolicy,
    ModelCallRecord,
    ModelCallStatus,
    ObservationStatus,
    RecoveryAction,
    RecoveryPlan,
    Run,
    RunLease,
    RunMode,
    RunRequest,
    RunResult,
    RunSnapshot,
    RunStatus,
    Step,
    StepStatus,
    StopReason,
    TaskKind,
    ToolExecutionContext,
    ToolObservation,
    ToolPermission,
    Uuid4IdFactory,
)
from .gate import Gate
from .model_runtime import (
    ModelRuntimeError,
    TokenCounter,
    build_governed_model,
)
from .policy import PolicyEngine
from .registry import ToolRegistry
from .sandbox import CommandPolicy, Sandbox, SandboxViolation, WorkspaceBaseline
from .state_store import MAX_FINAL_ANSWER_CHARS, StateStoreError
from .storage import SQLiteStorage, StorageError
from .tools import register_readonly_tools, register_side_effect_tools

_LOGGER = logging.getLogger(__name__)
_AGENT_ACTOR = "agent"
_AGENT_ROLE = "developer"
_HARNESS_ACTOR = "harness"
_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.STOPPED,
        RunStatus.FAILED,
        RunStatus.BLOCKED,
    }
)
_UNKNOWN_RESULT_CODES = frozenset(
    {
        ErrorCode.GATE_RESULT_UNKNOWN.value,
        ErrorCode.GATE_HANDLER_TIMEOUT.value,
        ErrorCode.SANDBOX_RESULT_UNKNOWN.value,
        ErrorCode.SANDBOX_COMMAND_TIMEOUT.value,
        ErrorCode.SANDBOX_COMMAND_CANCELLED.value,
        ErrorCode.SANDBOX_PROCESS_TERMINATION_FAILED.value,
        ErrorCode.COMPLETION_TEST_RESULT_UNKNOWN.value,
    }
)
_FATAL_DENIAL_CODES = frozenset(
    {
        ErrorCode.AUDIT_WRITE_FAILED.value,
        ErrorCode.GATE_INTENT_WRITE_FAILED.value,
        ErrorCode.GATE_START_WRITE_FAILED.value,
        ErrorCode.GATE_RESULT_WRITE_FAILED.value,
        ErrorCode.GATE_RESULT_INVALID.value,
        ErrorCode.STATE_INTEGRITY_ERROR.value,
        ErrorCode.APPROVAL_CHANNEL_UNAVAILABLE.value,
    }
)


class HarnessError(RuntimeError):
    """Stable fail-closed Harness operation failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message[:1000]
        super().__init__(f"{code.value}: {self.message}")


@dataclass(frozen=True)
class TokenCostPolicy:
    """Trusted deterministic conversion from TokenUsage to resource cost."""

    per_token: Decimal = Decimal("0.000001")
    version: str = "p0-token-cost-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.per_token, Decimal) or not self.per_token.is_finite():
            raise ValueError("per_token must be a finite Decimal")
        if self.per_token < 0:
            raise ValueError("per_token must be non-negative")
        if not self.version or len(self.version) > 128:
            raise ValueError("token-cost policy version must be bounded and non-empty")

    def cost(self, usage: TokenUsage | None) -> Decimal:
        """Return Decimal cost for one public token-usage value."""

        if usage is None:
            return Decimal(0)
        if usage.total_tokens < 0:
            raise HarnessError(
                ErrorCode.HARNESS_BUDGET_ACCOUNTING_FAILED,
                "token usage must be non-negative",
            )
        return Decimal(usage.total_tokens) * self.per_token


ToolFactory = Callable[[ResolvedHarnessConfig, Sandbox], ToolRegistry]


@dataclass
class _Session:
    config: ResolvedHarnessConfig
    storage: SQLiteStorage
    sandbox: Sandbox
    registry: ToolRegistry
    policy: PolicyEngine
    gate: Gate
    completion: CompletionChecker
    checkpoints: CheckpointService
    adapter: HarnessAgentAdapter
    lease: RunLease
    run_id: str
    task_description: str
    startup_workspace: WorkspaceBaseline
    current_step_id: str | None = None


@dataclass(frozen=True)
class _AgentOutcome:
    kind: str
    approval_id: str | None = None
    error_code: str | None = None
    message: str | None = None

    @classmethod
    def final(cls) -> _AgentOutcome:
        return cls("FINAL_ANSWER")

    @classmethod
    def budget(cls) -> _AgentOutcome:
        return cls("BUDGET_EXHAUSTED", error_code=ErrorCode.GATE_BUDGET_EXHAUSTED.value)

    @classmethod
    def wait(cls, approval_id: str | None) -> _AgentOutcome:
        return cls(
            "WAITING_APPROVAL",
            approval_id=approval_id,
            error_code=ErrorCode.APPROVAL_REQUIRED.value,
        )

    @classmethod
    def unknown(cls, code: str | None) -> _AgentOutcome:
        return cls(
            "UNKNOWN_RESULT",
            error_code=code or ErrorCode.GATE_RESULT_UNKNOWN.value,
        )

    @classmethod
    def failed(cls, code: str | None) -> _AgentOutcome:
        return cls("FAILED", error_code=code or ErrorCode.HARNESS_INTERNAL_ERROR.value)

    @classmethod
    def terminal(cls, code: str | None) -> _AgentOutcome:
        return cls("TERMINAL", error_code=code)


class Harness:
    """The single public Agent Run orchestrator."""

    def __init__(
        self,
        *,
        config_path: Path,
        model: Model,
        tool_factory: ToolFactory | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        command_policy: CommandPolicy | None = None,
        fault_injector: FaultInjector | None = None,
        token_cost_policy: TokenCostPolicy | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._config_path = Path(config_path)
        self._model = model
        self._tool_factory = tool_factory or _default_tool_factory
        self._clock = clock
        self._ids = id_factory or Uuid4IdFactory()
        self._command_policy = command_policy
        self._fault_injector = fault_injector
        self._token_cost_policy = token_cost_policy or TokenCostPolicy()
        self._token_counter = token_counter
        self._lock = threading.RLock()

    def run(self, request: RunRequest) -> RunResult:
        """Start or resume one run through the complete governed lifecycle."""

        if not isinstance(request, RunRequest):
            raise TypeError("request must be a RunRequest")
        with self._lock:
            if request.mode == RunMode.START:
                return self._start(instruction=request.instruction)
            return self._resume(request.run_id or "")

    def _start(self, *, instruction: str | None) -> RunResult:
        try:
            assert_smolagents_compatibility()
            config = self._load_config()
            sandbox = self._build_sandbox(config)
            startup_workspace = sandbox.capture_baseline()
            storage = self._build_storage(config)
            storage.initialize()
            registry = self._tool_factory(config, sandbox)
            visible_names = self._validate_tool_assembly(config, registry)
            policy = self._build_policy(config, registry, visible_names)
            gate = Gate(
                storage=storage,
                registry=registry,
                policy=policy,
                sandbox=sandbox,
                clock=self._clock,
                id_factory=self._ids,
            )
            completion = CompletionChecker(
                storage,
                config,
                gate,
                sandbox,
                clock=self._clock,
                id_factory=self._ids,
            )
            checkpoints = CheckpointService(
                storage=storage,
                config=config,
                sandbox=sandbox,
                clock=self._clock,
                id_factory=self._ids,
            )
            visible_specs = registry.snapshot(
                _agent_context(config, "pending-step"),
                policy,
            )
            adapter = HarnessAgentAdapter(
                gate=gate,
                registry=registry,
                visible_specs=visible_specs,
            )
            run = self._new_run(
                config,
                startup_workspace,
                instruction=instruction,
            )
            with storage.transaction(actor=_HARNESS_ACTOR) as unit:
                lease = unit.state.create_run(
                    run,
                    owner_id=_HARNESS_ACTOR,
                    lease_policy=LeasePolicy(),
                )
                unit.state.record_workspace_baseline(
                    run.run_id,
                    startup_workspace,
                    lease_token=lease.token,
                )
                unit.state.transition_run(
                    run.run_id,
                    expected_status=RunStatus.NEW,
                    expected_revision=unit.state.get_run_revision(run.run_id),
                    target=RunStatus.RUNNING,
                    stop_reason=None,
                    ended_at=None,
                    acceptance_status=None,
                    lease_token=lease.token,
                )
            session = _Session(
                config=config,
                storage=storage,
                sandbox=sandbox,
                registry=registry,
                policy=policy,
                gate=gate,
                completion=completion,
                checkpoints=checkpoints,
                adapter=adapter,
                lease=lease,
                run_id=run.run_id,
                task_description=run.task_description,
                startup_workspace=startup_workspace,
            )
            if isinstance(config.config.task.acceptance, TestRepairAcceptance):
                try:
                    completion.capture_test_baseline(
                        run.run_id,
                        lease.token,
                        startup_workspace=startup_workspace,
                    )
                except CompletionCheckerError as exc:
                    return self._finish_failure(session, RunStatus.FAILED, exc.code.value)
            self._create_checkpoint(
                session,
                kind=CheckpointBoundaryKind.RUN_STARTED,
                boundary_ref="run-start",
                summary="Run started with persisted workspace baseline",
            )
            return self._drive(session, task=run.task_description)
        except (HarnessError, AgentAdapterError) as exc:
            raise HarnessError(_error_code(exc), _safe_message(exc)) from exc
        except (
            AuditStoreError,
            StorageError,
            StateStoreError,
            CheckpointServiceError,
            SandboxViolation,
            CompletionCheckerError,
        ) as exc:
            raise HarnessError(_error_code(exc), _safe_message(exc)) from exc
        except Exception as exc:
            raise HarnessError(
                ErrorCode.HARNESS_INTERNAL_ERROR,
                _safe_message(exc),
            ) from exc

    def _resume(self, run_id: str) -> RunResult:
        if not run_id:
            raise HarnessError(
                ErrorCode.HARNESS_RUN_REQUEST_INVALID,
                "RESUME requires a run_id",
            )
        try:
            assert_smolagents_compatibility()
            config = self._load_config()
            sandbox = self._build_sandbox(config)
            storage = self._build_storage(config)
            storage.initialize()
            registry = self._tool_factory(config, sandbox)
            visible_names = self._validate_tool_assembly(config, registry)
            policy = self._build_policy(config, registry, visible_names)
            gate = Gate(
                storage=storage,
                registry=registry,
                policy=policy,
                sandbox=sandbox,
                clock=self._clock,
                id_factory=self._ids,
            )
            completion = CompletionChecker(
                storage,
                config,
                gate,
                sandbox,
                clock=self._clock,
                id_factory=self._ids,
            )
            checkpoints = CheckpointService(
                storage=storage,
                config=config,
                sandbox=sandbox,
                clock=self._clock,
                id_factory=self._ids,
            )
            visible_specs = registry.snapshot(
                _agent_context(config, "pending-step"),
                policy,
            )
            adapter = HarnessAgentAdapter(
                gate=gate,
                registry=registry,
                visible_specs=visible_specs,
            )
            with storage.transaction(actor=_HARNESS_ACTOR) as unit:
                run = unit.state.get_run(run_id)
                if run.config_fingerprint != config.fingerprint:
                    raise HarnessError(
                        ErrorCode.HARNESS_CONFIG_RELOAD_FAILED,
                        "resume configuration fingerprint does not match the run",
                    )
                lease = unit.state.acquire_lease(run_id, owner_id=_HARNESS_ACTOR)
                startup_workspace = unit.state.get_workspace_baseline(run_id)
            if startup_workspace is None:
                return self._finish_missing_baseline(
                    storage=storage,
                    lease=lease,
                    run_id=run_id,
                )
            session = _Session(
                config=config,
                storage=storage,
                sandbox=sandbox,
                registry=registry,
                policy=policy,
                gate=gate,
                completion=completion,
                checkpoints=checkpoints,
                adapter=adapter,
                lease=lease,
                run_id=run_id,
                task_description=run.task_description,
                startup_workspace=startup_workspace,
            )
            interrupted = tuple(
                call
                for call in self._snapshot(session).model_calls
                if call.status.value == "STARTED"
            )
            if interrupted:
                return self._finish_interrupted_model_calls(session, interrupted)
            plan = checkpoints.prepare_recovery(run_id, lease.token)
            return self._handle_recovery(session, plan)
        except (HarnessError, AgentAdapterError) as exc:
            raise HarnessError(_error_code(exc), _safe_message(exc)) from exc
        except (
            AuditStoreError,
            StorageError,
            StateStoreError,
            CheckpointServiceError,
            SandboxViolation,
            CompletionCheckerError,
        ) as exc:
            raise HarnessError(_error_code(exc), _safe_message(exc)) from exc
        except Exception as exc:
            raise HarnessError(
                ErrorCode.HARNESS_INTERNAL_ERROR,
                _safe_message(exc),
            ) from exc

    def _finish_missing_baseline(
        self,
        *,
        storage: SQLiteStorage,
        lease: RunLease,
        run_id: str,
    ) -> RunResult:
        resume_blocked = False
        with storage.transaction(actor=_HARNESS_ACTOR) as unit:
            run = unit.state.get_run(run_id)
            if run.status in {RunStatus.RUNNING, RunStatus.WAITING_APPROVAL}:
                unit.state.transition_run(
                    run_id,
                    expected_status=run.status,
                    expected_revision=unit.state.get_run_revision(run_id),
                    target=RunStatus.BLOCKED,
                    stop_reason=StopReason.WORKSPACE_CHANGED,
                    ended_at=self._now(),
                    acceptance_status=AcceptanceStatus.NOT_EVALUATED,
                    lease_token=lease.token,
                )
            elif run.status not in _TERMINAL_RUN_STATUSES:
                resume_blocked = True
            if not resume_blocked:
                current = unit.state.get_run(run_id)
                unit.state.release_lease(lease)
                snapshot = unit.state.get_run_snapshot(run_id)
                head = unit.audit.head(run_id)
                return RunResult(
                    run_id=current.run_id,
                    trace_id=current.trace_id,
                    status=current.status,
                    stop_reason=current.stop_reason,
                    acceptance_status=current.acceptance_status,
                    completion=snapshot.completion,
                    candidate_final_answer=current.candidate_final_answer,
                    last_checkpoint_id=current.last_checkpoint_id,
                    last_audit_sequence=head.sequence if head is not None else 0,
                    error_code=ErrorCode.HARNESS_WORKSPACE_BASELINE_MISSING.value,
                )
        with storage.transaction(actor=_HARNESS_ACTOR) as unit:
            unit.state.release_lease(lease)
        raise HarnessError(
            ErrorCode.HARNESS_RESUME_NOT_ALLOWED,
            "run has no persisted baseline and is not resumable",
        )

    def _handle_recovery(self, session: _Session, plan: RecoveryPlan) -> RunResult:
        if plan.action in {
            RecoveryAction.WAIT_FOR_APPROVAL,
            RecoveryAction.MANUAL_REVIEW,
            RecoveryAction.MARK_UNKNOWN_RESULT,
            RecoveryAction.ALREADY_TERMINAL,
        }:
            return self._finish_result(
                session,
                approval_id=plan.approval_id
                if plan.action == RecoveryAction.WAIT_FOR_APPROVAL
                else None,
                error_code=plan.reason_code,
            )
        if plan.action in {
            RecoveryAction.REENTER_GATE,
            RecoveryAction.RESUME_CLAIM_THEN_REENTER_GATE,
        }:
            snapshot = self._snapshot(session)
            invocation = next(
                (item for item in snapshot.invocations if item.tool_call_id == plan.invocation_id),
                None,
            )
            if invocation is None:
                return self._finish_failure(
                    session,
                    RunStatus.BLOCKED,
                    ErrorCode.RECOVERY_STATE_AMBIGUOUS.value,
                )
            context = ToolExecutionContext(
                run_id=session.run_id,
                step_id=invocation.step_id,
                task_id=snapshot.run.task_id,
                task_kind=snapshot.run.task_kind,
                mode=ExecutionMode.EXECUTE,
                actor=_AGENT_ACTOR,
                roles=(_AGENT_ROLE,),
            )
            observation = session.gate.reenter_prepared(
                session.run_id,
                invocation.tool_call_id,
                context,
                session.lease.token,
            )
            handled = self._handle_observation(session, observation)
            if handled is not None:
                return self._conclude_outcome(session, handled)
            self._checkpoint_tool_observation(session, observation)
            if plan.approval_id is not None:
                self._create_checkpoint(
                    session,
                    kind=CheckpointBoundaryKind.APPROVAL_SETTLED,
                    boundary_ref=f"approval-settled:{invocation.tool_call_id}",
                    summary="Approved invocation re-entered Gate",
                    tool_call_id=invocation.tool_call_id,
                    approval_id=plan.approval_id,
                )
            plan = session.checkpoints.prepare_recovery(
                session.run_id,
                session.lease.token,
            )
            if plan.action != RecoveryAction.CONTINUE_AGENT:
                return self._handle_recovery(session, plan)

        context = session.checkpoints.build_continuation_context(plan)
        continuation = session.checkpoints.render_continuation(context)
        task = (
            f"{session.task_description}\n\n"
            f"{continuation}\n\n"
            "Continue from the persisted safe boundary. Do not repeat completed operations "
            "unless the tools explicitly return replay-suppressed evidence."
        )
        return self._drive(session, task=task)

    def _drive(self, session: _Session, *, task: str) -> RunResult:
        if self._budget_exhausted(session):
            return self._finish_budget_stop(session)
        remaining_steps = self._remaining_steps(session)
        observer = _StepObserver()
        model = self._model
        if session.config.config.version == 2:
            model = build_governed_model(
                delegate=self._model,
                storage=session.storage,
                config=session.config,
                run_id=session.run_id,
                lease_token=session.lease.token,
                ensure_step=lambda: self._ensure_step(session, "Agent decision step"),
                token_counter=self._token_counter,
                clock=self._now_clock(),
            )
        agent = build_tool_calling_agent(
            model=model,
            adapter=session.adapter,
            step_callbacks=[observer],
            max_steps=remaining_steps,
        )
        generator = agent.run(task, stream=True, reset=True)
        outcome: _AgentOutcome | None = None
        final_seen = False
        try:
            for event in generator:
                if isinstance(event, ToolCall):
                    self._ensure_step(session, f"Agent tool call: {event.name}")
                    session.adapter.activate(
                        context=self._execution_context(session),
                        lease_token=session.lease.token,
                    )
                elif isinstance(event, ToolOutput):
                    if event.is_final_answer:
                        try:
                            self._record_final_answer(session, event.output)
                            final_seen = True
                        except HarnessError as exc:
                            outcome = _AgentOutcome.failed(exc.code.value)
                            session.adapter.request_stop(outcome.kind)
                            agent.interrupt()
                    else:
                        handled = self._handle_observation(
                            session,
                            event.output if isinstance(event.output, ToolObservation) else None,
                        )
                        if handled is not None:
                            outcome = handled
                            session.adapter.request_stop(handled.kind)
                            agent.interrupt()
                        elif self._budget_exhausted(session):
                            outcome = _AgentOutcome.budget()
                            session.adapter.request_stop(outcome.kind)
                            agent.interrupt()
                elif isinstance(event, ActionStep):
                    if session.current_step_id is None:
                        self._ensure_step(session, "Agent decision step")
                    self._complete_step(
                        session,
                        event,
                        force_complete=outcome is not None,
                    )
                    session.adapter.deactivate()
                    if outcome is None and self._budget_exhausted(session):
                        outcome = _AgentOutcome.budget()
                        agent.interrupt()
                elif isinstance(event, FinalAnswerStep):
                    if not final_seen:
                        final_seen = True
                elif isinstance(event, ActionOutput):
                    if event.is_final_answer:
                        final_seen = True
                elif isinstance(event, PlanningStep):
                    raise HarnessError(
                        ErrorCode.SMOLAGENTS_STREAM_CONTRACT_INVALID,
                        "planning steps are not enabled in P0",
                    )
                else:
                    raise HarnessError(
                        ErrorCode.SMOLAGENTS_STREAM_CONTRACT_INVALID,
                        f"unexpected Agent stream event: {type(event).__name__}",
                    )
        except HarnessAgentStopSignal as exc:
            if outcome is None:
                outcome = _AgentOutcome.failed(ErrorCode.HARNESS_INTERNAL_ERROR.value)
                outcome = _AgentOutcome(
                    outcome.kind, error_code=outcome.error_code, message=exc.reason
                )
        except HarnessError as exc:
            _LOGGER.exception("Harness control error during Agent stream")
            outcome = _AgentOutcome.failed(exc.code.value)
        except AgentAdapterError as exc:
            _LOGGER.exception("Agent adapter failed")
            outcome = _AgentOutcome.failed(exc.code.value)
        except Exception as exc:
            model_error = _find_model_runtime_error(exc)
            if outcome is None and model_error is not None:
                self._fail_current_step(session, model_error.code)
                if model_error.code == ErrorCode.GATE_BUDGET_EXHAUSTED:
                    outcome = _AgentOutcome.budget()
                elif model_error.unknown:
                    outcome = _AgentOutcome.unknown(model_error.code.value)
                else:
                    outcome = _AgentOutcome.failed(model_error.code.value)
            elif outcome is None:
                _LOGGER.exception("Agent stream failed")
                outcome = _AgentOutcome.failed(ErrorCode.HARNESS_INTERNAL_ERROR.value)
                outcome = _AgentOutcome(
                    outcome.kind,
                    error_code=outcome.error_code,
                    message=_safe_message(exc),
                )
        finally:
            session.adapter.deactivate()
        if outcome is None:
            if final_seen:
                outcome = _AgentOutcome.final()
            elif self._budget_exhausted(session):
                outcome = _AgentOutcome.budget()
            else:
                outcome = _AgentOutcome.failed(ErrorCode.SMOLAGENTS_STREAM_CONTRACT_INVALID.value)
        return self._conclude_outcome(session, outcome)

    def _fail_current_step(self, session: _Session, code: ErrorCode) -> None:
        step_id = session.current_step_id
        if step_id is None:
            return
        try:
            with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
                snapshot = unit.state.get_run_snapshot(session.run_id)
                step = next((item for item in snapshot.steps if item.step_id == step_id), None)
                if step is not None and step.status == StepStatus.RUNNING:
                    unit.state.transition_step(
                        step_id,
                        expected_status=StepStatus.RUNNING,
                        expected_revision=unit.state.get_step_revision(step_id),
                        target=StepStatus.FAILED,
                        ended_at=self._now(),
                        lease_token=session.lease.token,
                        error_code=code.value,
                    )
        except Exception:
            _LOGGER.exception("failed to close model step after runtime error")
        session.current_step_id = None

    def _finish_interrupted_model_calls(
        self,
        session: _Session,
        calls: tuple[ModelCallRecord, ...],
    ) -> RunResult:
        for call in calls:
            with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
                unit.state.fail_model_call(
                    call.model_call_id,
                    status=ModelCallStatus.UNKNOWN_RESULT,
                    error_code=ErrorCode.MODEL_CALL_UNKNOWN,
                    provider_request_id=call.provider_request_id,
                    lease_token=session.lease.token,
                )
        return self._finish_failure(
            session,
            RunStatus.BLOCKED,
            ErrorCode.MODEL_CALL_UNKNOWN.value,
            stop_reason=StopReason.UNKNOWN_RESULT,
        )

    def _conclude_outcome(self, session: _Session, outcome: _AgentOutcome) -> RunResult:
        if outcome.kind == "WAITING_APPROVAL":
            return self._finish_result(
                session,
                approval_id=outcome.approval_id,
                error_code=outcome.error_code,
            )
        if outcome.kind == "BUDGET_EXHAUSTED":
            return self._finish_budget_stop(session)
        if outcome.kind == "UNKNOWN_RESULT":
            return self._finish_failure(
                session,
                RunStatus.BLOCKED,
                outcome.error_code or ErrorCode.GATE_RESULT_UNKNOWN.value,
                stop_reason=StopReason.UNKNOWN_RESULT,
            )
        if outcome.kind == "FAILED":
            return self._finish_failure(
                session,
                RunStatus.FAILED,
                outcome.error_code or ErrorCode.HARNESS_INTERNAL_ERROR.value,
            )
        if outcome.kind == "TERMINAL":
            return self._finish_result(session, error_code=outcome.error_code)
        return self._verify(session)

    def _verify(self, session: _Session) -> RunResult:
        with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
            run = unit.state.get_run(session.run_id)
            revision = unit.state.get_run_revision(session.run_id)
            unit.state.transition_run(
                session.run_id,
                expected_status=RunStatus.RUNNING,
                expected_revision=revision,
                target=RunStatus.VERIFYING,
                stop_reason=None,
                ended_at=None,
                acceptance_status=None,
                lease_token=session.lease.token,
            )
        try:
            if run.task_kind == TaskKind.CODE_UNDERSTANDING:
                completion = session.completion.evaluate_code_understanding(
                    session.run_id,
                    session.lease.token,
                    startup_workspace=session.startup_workspace,
                )
            else:
                completion = session.completion.evaluate_test_repair(
                    session.run_id,
                    session.lease.token,
                    startup_workspace=session.startup_workspace,
                )
            self._create_checkpoint(
                session,
                kind=CheckpointBoundaryKind.COMPLETION_RECORDED,
                boundary_ref="completion",
                summary="Completion result recorded",
            )
            self._transition_terminal(
                session,
                RunStatus.COMPLETED,
                acceptance_status=completion.acceptance_status,
            )
        except (CompletionCheckerError, CheckpointServiceError) as exc:
            return self._finish_failure(
                session,
                RunStatus.FAILED,
                _error_code(exc).value,
            )
        return self._finish_result(session)

    def _handle_observation(
        self,
        session: _Session,
        observation: ToolObservation | None,
    ) -> _AgentOutcome | None:
        if observation is None:
            return _AgentOutcome.failed(ErrorCode.GATE_OUTPUT_INVALID.value)
        if observation.code == ErrorCode.GATE_BUDGET_EXHAUSTED.value:
            return _AgentOutcome.budget()
        if observation.code in {
            ErrorCode.APPROVAL_REJECTED.value,
            ErrorCode.APPROVAL_EXPIRED.value,
        }:
            return _AgentOutcome.terminal(observation.code)
        if observation.code in {
            ErrorCode.GATE_INTERNAL_ERROR.value,
            ErrorCode.STATE_INVALID_TRANSITION.value,
        }:
            return _AgentOutcome.failed(observation.code)
        if observation.status == ObservationStatus.WAITING_APPROVAL:
            self._checkpoint_tool_observation(session, observation)
            return _AgentOutcome.wait(observation.approval_id)
        if observation.status in {
            ObservationStatus.UNKNOWN_RESULT,
            ObservationStatus.FAILED,
        } and (
            observation.code in _UNKNOWN_RESULT_CODES
            or observation.status == ObservationStatus.UNKNOWN_RESULT
        ):
            self._checkpoint_tool_observation(session, observation)
            return _AgentOutcome.unknown(observation.code)
        if (
            observation.status == ObservationStatus.DENIED
            and observation.code in _FATAL_DENIAL_CODES
        ):
            return _AgentOutcome.failed(observation.code)
        return None

    def _checkpoint_tool_observation(
        self,
        session: _Session,
        observation: ToolObservation,
    ) -> None:
        snapshot = self._snapshot(session)
        invocation = next(
            (
                item
                for item in snapshot.invocations
                if item.tool_call_id == observation.tool_call_id
            ),
            None,
        )
        if observation.status == ObservationStatus.WAITING_APPROVAL:
            self._create_checkpoint(
                session,
                kind=CheckpointBoundaryKind.APPROVAL_REQUESTED,
                boundary_ref=f"approval-request:{observation.tool_call_id}",
                summary="Approval requested before a governed tool call",
                tool_call_id=observation.tool_call_id,
                approval_id=observation.approval_id,
            )
            return
        if invocation is None:
            return
        kind = {
            InvocationStatus.SUCCEEDED: CheckpointBoundaryKind.TOOL_SUCCEEDED,
            InvocationStatus.FAILED: CheckpointBoundaryKind.TOOL_FAILED,
            InvocationStatus.TIMED_OUT: CheckpointBoundaryKind.TOOL_TIMED_OUT,
            InvocationStatus.UNKNOWN_RESULT: CheckpointBoundaryKind.TOOL_UNKNOWN_RESULT,
        }.get(invocation.status)
        if kind is None:
            return
        self._create_checkpoint(
            session,
            kind=kind,
            boundary_ref=observation.tool_call_id,
            summary=invocation.result_summary or f"{invocation.tool_name} result",
            tool_call_id=invocation.tool_call_id,
            approval_id=invocation.approval_id,
        )

    def _record_final_answer(self, session: _Session, output: object) -> None:
        if isinstance(output, str):
            text = output
        else:
            text = str(output)
        if not text.strip() or len(text) > MAX_FINAL_ANSWER_CHARS or "\x00" in text:
            raise HarnessError(
                ErrorCode.HARNESS_FINAL_ANSWER_INVALID,
                "final answer is empty, contains NUL, or exceeds 16000 characters",
            )
        try:
            with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
                unit.state.set_candidate_final_answer(
                    session.run_id,
                    text,
                    expected_run_revision=unit.state.get_run_revision(session.run_id),
                    lease_token=session.lease.token,
                )
        except (StateStoreError, StorageError, AuditStoreError) as exc:
            raise HarnessError(_error_code(exc), _safe_message(exc)) from exc

    def _ensure_step(self, session: _Session, summary: str) -> str:
        if session.current_step_id is not None:
            return session.current_step_id
        now = self._now()
        with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
            snapshot = unit.state.get_run_snapshot(session.run_id)
            step = Step(
                step_id=self._ids.new_step_id(),
                run_id=session.run_id,
                ordinal=len(snapshot.steps),
                summary=_bounded(summary, 4000),
                started_at=now,
            )
            unit.state.create_step(
                step,
                expected_run_revision=unit.state.get_run_revision(session.run_id),
                lease_token=session.lease.token,
            )
            unit.state.transition_step(
                step.step_id,
                expected_status=StepStatus.PLANNED,
                expected_revision=0,
                target=StepStatus.RUNNING,
                ended_at=None,
                lease_token=session.lease.token,
            )
        session.current_step_id = step.step_id
        return step.step_id

    def _complete_step(
        self,
        session: _Session,
        event: ActionStep,
        *,
        force_complete: bool,
    ) -> None:
        step_id = session.current_step_id
        if step_id is None:
            raise HarnessError(
                ErrorCode.HARNESS_STEP_PERSIST_FAILED,
                "completed action step has no persisted step",
            )
        token_usage = event.token_usage
        if token_usage is None:
            token_usage = TokenUsage(input_tokens=0, output_tokens=0)
        with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
            snapshot = unit.state.get_run_snapshot(session.run_id)
            step = next((item for item in snapshot.steps if item.step_id == step_id), None)
            if step is None:
                raise HarnessError(
                    ErrorCode.HARNESS_STEP_PERSIST_FAILED,
                    "persisted action step is missing",
                )
            target = (
                StepStatus.FAILED
                if event.error is not None and not force_complete
                else StepStatus.COMPLETED
            )
            unit.state.transition_step(
                step_id,
                expected_status=StepStatus.RUNNING,
                expected_revision=unit.state.get_step_revision(step_id),
                target=target,
                ended_at=self._now(),
                lease_token=session.lease.token,
                error_code=(
                    ErrorCode.HARNESS_INTERNAL_ERROR.value if target == StepStatus.FAILED else None
                ),
            )
        self._record_step_budget(
            session,
            step_id,
            token_usage,
            include_token_cost=session.config.config.version != 2,
        )
        if target == StepStatus.COMPLETED:
            try:
                self._create_checkpoint(
                    session,
                    kind=CheckpointBoundaryKind.STEP_COMPLETED,
                    boundary_ref=f"step:{step_id}",
                    summary="Agent action step completed",
                    step_id=step_id,
                )
            except CheckpointServiceError:
                if not self._run_blocks_step_checkpoint(session):
                    raise
        session.current_step_id = None

    def _record_step_budget(
        self,
        session: _Session,
        step_id: str,
        token_usage: TokenUsage,
        *,
        include_token_cost: bool = True,
    ) -> None:
        """Persist step, token, and wall-time budget deltas idempotently."""

        commands: list[tuple[BudgetSourceKind, str, BudgetCategory, Decimal]] = [
            (BudgetSourceKind.STEP, step_id, BudgetCategory.STEPS, Decimal(1)),
        ]
        if include_token_cost:
            commands.append(
                (
                    BudgetSourceKind.HARNESS,
                    f"tokens:{self._token_cost_policy.version}:{step_id}",
                    BudgetCategory.TOKEN_COST,
                    self._token_cost_policy.cost(token_usage),
                )
            )
        now = self._now()
        with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
            run = unit.state.get_run(session.run_id)
        if run.started_at is not None:
            desired_wall = max(0, int((now - run.started_at).total_seconds()))
            wall_delta = max(0, desired_wall - run.usage.wall_time_seconds)
            commands.append(
                (
                    BudgetSourceKind.HARNESS,
                    f"wall:{step_id}",
                    BudgetCategory.WALL_TIME,
                    Decimal(wall_delta),
                )
            )
        for source_kind, source_id, category, requested_delta in commands:
            try:
                with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
                    current = unit.state.get_run(session.run_id)
                    remaining = {
                        BudgetCategory.STEPS: Decimal(
                            max(0, current.budget.max_steps - current.usage.steps)
                        ),
                        BudgetCategory.TOKEN_COST: max(
                            Decimal(0),
                            current.budget.max_token_cost - current.usage.token_cost,
                        ),
                        BudgetCategory.WALL_TIME: Decimal(
                            max(
                                0,
                                current.budget.max_wall_time_seconds
                                - current.usage.wall_time_seconds,
                            )
                        ),
                        BudgetCategory.TOOL_CALLS: Decimal(0),
                    }[category]
                    delta = min(requested_delta, remaining)
                    if delta <= 0:
                        continue
                    unit.state.record_budget_delta(
                        BudgetDeltaCommand(
                            run_id=session.run_id,
                            source_kind=source_kind,
                            source_id=source_id,
                            category=category,
                            expected_run_revision=unit.state.get_run_revision(session.run_id),
                            delta=delta,
                        ),
                        lease_token=session.lease.token,
                    )
            except (StateStoreError, StorageError) as exc:
                raise HarnessError(
                    ErrorCode.HARNESS_BUDGET_ACCOUNTING_FAILED,
                    _safe_message(exc),
                ) from exc

    def _remaining_steps(self, session: _Session) -> int:
        run = self._snapshot(session).run
        return max(0, run.budget.max_steps - run.usage.steps)

    def _budget_exhausted(self, session: _Session) -> bool:
        run = self._snapshot(session).run
        return (
            run.usage.steps >= run.budget.max_steps
            or run.usage.tool_calls >= run.budget.max_tool_calls
            or run.usage.token_cost >= run.budget.max_token_cost
            or run.usage.wall_time_seconds >= run.budget.max_wall_time_seconds
        )

    def _finish_budget_stop(self, session: _Session) -> RunResult:
        self._transition_terminal(
            session,
            RunStatus.STOPPED,
            stop_reason=StopReason.BUDGET_EXHAUSTED,
            acceptance_status=AcceptanceStatus.NOT_EVALUATED,
        )
        return self._finish_result(
            session,
            error_code=ErrorCode.GATE_BUDGET_EXHAUSTED.value,
        )

    def _finish_failure(
        self,
        session: _Session,
        status: RunStatus,
        error_code: str,
        *,
        stop_reason: StopReason = StopReason.INTERNAL_ERROR,
    ) -> RunResult:
        try:
            self._transition_terminal(
                session,
                status,
                stop_reason=stop_reason,
                acceptance_status=AcceptanceStatus.NOT_EVALUATED,
            )
        except Exception as transition_error:
            raise HarnessError(
                ErrorCode.HARNESS_INTERNAL_ERROR,
                _safe_message(transition_error),
            ) from transition_error
        return self._finish_result(session, error_code=error_code)

    def _transition_terminal(
        self,
        session: _Session,
        status: RunStatus,
        *,
        stop_reason: StopReason | None = None,
        acceptance_status: AcceptanceStatus | None = None,
    ) -> None:
        with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
            current = unit.state.get_run(session.run_id)
            if current.status == status:
                return
            unit.state.transition_run(
                session.run_id,
                expected_status=current.status,
                expected_revision=unit.state.get_run_revision(session.run_id),
                target=status,
                stop_reason=stop_reason,
                ended_at=self._now(),
                acceptance_status=acceptance_status,
                lease_token=session.lease.token,
            )

    def _finish_result(
        self,
        session: _Session,
        *,
        approval_id: str | None = None,
        error_code: str | None = None,
    ) -> RunResult:
        try:
            with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
                snapshot = unit.state.get_run_snapshot(session.run_id)
                head = unit.audit.head(session.run_id)
                run = snapshot.run
                result = RunResult(
                    run_id=run.run_id,
                    trace_id=run.trace_id,
                    status=run.status,
                    stop_reason=run.stop_reason,
                    acceptance_status=run.acceptance_status,
                    completion=snapshot.completion,
                    candidate_final_answer=run.candidate_final_answer,
                    approval_id=approval_id or _pending_approval_id(snapshot),
                    last_checkpoint_id=run.last_checkpoint_id,
                    last_audit_sequence=head.sequence if head is not None else 0,
                    error_code=error_code,
                )
                unit.state.release_lease(session.lease)
                head = unit.audit.head(session.run_id)
                result = result.model_copy(
                    update={"last_audit_sequence": head.sequence if head is not None else 0}
                )
                return result
        except Exception as exc:
            raise HarnessError(
                ErrorCode.HARNESS_INTERNAL_ERROR,
                _safe_message(exc),
            ) from exc

    def _create_checkpoint(
        self,
        session: _Session,
        *,
        kind: CheckpointBoundaryKind,
        boundary_ref: str,
        summary: str,
        step_id: str | None = None,
        tool_call_id: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        session.checkpoints.create_checkpoint(
            session.run_id,
            session.lease.token,
            CheckpointBoundary(
                kind=kind,
                boundary_ref=boundary_ref,
                step_id=step_id,
                tool_call_id=tool_call_id,
                approval_id=approval_id,
                public_summary=_bounded(summary, 4000),
            ),
        )

    def _snapshot(self, session: _Session) -> RunSnapshot:
        with session.storage.transaction(actor=_HARNESS_ACTOR) as unit:
            return unit.state.get_run_snapshot(session.run_id)

    def _execution_context(self, session: _Session) -> ToolExecutionContext:
        step_id = session.current_step_id
        if step_id is None:
            step_id = self._ensure_step(session, "Agent decision step")
        snapshot = self._snapshot(session)
        return ToolExecutionContext(
            run_id=session.run_id,
            step_id=step_id,
            task_id=snapshot.run.task_id,
            task_kind=snapshot.run.task_kind,
            mode=ExecutionMode.EXECUTE,
            actor=_AGENT_ACTOR,
            roles=(_AGENT_ROLE,),
        )

    def _run_blocks_step_checkpoint(self, session: _Session) -> bool:
        status = self._snapshot(session).run.status
        return status in {
            RunStatus.WAITING_APPROVAL,
            RunStatus.STOPPED,
            RunStatus.FAILED,
            RunStatus.BLOCKED,
        }

    def _new_run(
        self,
        config: ResolvedHarnessConfig,
        startup_workspace: WorkspaceBaseline,
        *,
        instruction: str | None,
    ) -> Run:
        now = self._now()
        task_description = (
            instruction if instruction is not None else config.config.task.description
        )
        return Run(
            run_id=self._ids.new_run_id(),
            trace_id=self._ids.new_trace_id(),
            task_id=config.config.task.id,
            task_kind=config.config.task.kind,
            task_description=task_description,
            config_fingerprint=config.fingerprint,
            workspace=startup_workspace.identity,
            budget=config.config.budget.as_run_budget(),
            created_at=now,
            updated_at=now,
        )

    def _load_config(self) -> ResolvedHarnessConfig:
        try:
            return load_config(self._config_path)
        except Exception as exc:
            raise HarnessError(
                ErrorCode.HARNESS_CONFIG_RELOAD_FAILED,
                _safe_message(exc),
            ) from exc

    def _build_sandbox(self, config: ResolvedHarnessConfig) -> Sandbox:
        return Sandbox.from_resolved_config(
            config,
            command_policy=self._command_policy,
            clock=self._now_clock(),
            fault_injector=self._fault_injector,
        )

    def _build_storage(self, config: ResolvedHarnessConfig) -> SQLiteStorage:
        return SQLiteStorage(
            Path(config.state_dir) / "harness.sqlite3",
            Path(config.workspace_root),
            self._now_clock(),
            fault_injector=self._fault_injector,
        )

    def _validate_tool_assembly(
        self,
        config: ResolvedHarnessConfig,
        registry: ToolRegistry,
    ) -> tuple[str, ...]:
        configured = tuple(config.config.task.allowed_tools)
        missing = tuple(sorted(name for name in configured if not registry.contains(name)))
        if missing:
            raise HarnessError(
                ErrorCode.HARNESS_TOOL_ASSEMBLY_FAILED,
                f"configured tools are not registered: {','.join(missing)}",
            )
        return tuple(sorted(configured))

    def _build_policy(
        self,
        config: ResolvedHarnessConfig,
        registry: ToolRegistry,
        visible_names: tuple[str, ...],
    ) -> PolicyEngine:
        permissions: set[ToolPermission] = set()
        for name in visible_names:
            permissions.update(registry.get(name).spec.required_permissions)
        return PolicyEngine(
            allowed_tool_names=visible_names,
            role_permissions={_AGENT_ROLE: permissions},
            task_approval_tools=config.config.task.approval.required_tools,
            approval_roles=config.config.task.approval.approver_roles,
            approval_ttl_seconds=config.config.task.approval.ttl_seconds,
        )

    def _now(self) -> datetime:
        return self._now_clock().now()

    def _now_clock(self) -> Clock:
        if self._clock is not None:
            return self._clock
        return _DefaultClock()


class _DefaultClock:
    def now(self) -> datetime:
        from datetime import timezone

        return datetime.now(timezone.utc)


class _StepObserver:
    """Observation-only callback required by the public Agent composition."""

    def __call__(self, memory_step: object, **kwargs: object) -> None:
        del kwargs
        if not isinstance(memory_step, ActionStep):
            return


def _default_tool_factory(
    config: ResolvedHarnessConfig,
    sandbox: Sandbox,
) -> ToolRegistry:
    registry = ToolRegistry()
    register_readonly_tools(registry, sandbox)
    register_side_effect_tools(registry, sandbox, config)
    return registry


def _agent_context(
    config: ResolvedHarnessConfig,
    step_id: str,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        run_id=f"run_{'0' * 32}",
        step_id=f"step_{'0' * 32}" if not step_id.startswith("step_") else step_id,
        task_id=config.config.task.id,
        task_kind=config.config.task.kind,
        mode=ExecutionMode.EXECUTE,
        actor=_AGENT_ACTOR,
        roles=(_AGENT_ROLE,),
    )


def _pending_approval_id(snapshot: RunSnapshot) -> str | None:
    from .domain import ApprovalStatus

    return next(
        (
            approval.approval_id
            for approval in snapshot.approvals
            if approval.status
            in {
                ApprovalStatus.PENDING,
                ApprovalStatus.APPROVED,
                ApprovalStatus.CLAIMED,
            }
        ),
        None,
    )


def _bounded(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _error_code(exc: Exception) -> ErrorCode:
    value = getattr(exc, "code", ErrorCode.HARNESS_INTERNAL_ERROR)
    if isinstance(value, ErrorCode):
        return value
    try:
        return ErrorCode(str(value))
    except ValueError:
        return ErrorCode.HARNESS_INTERNAL_ERROR


def _safe_message(exc: Exception) -> str:
    value = getattr(exc, "message", None)
    if isinstance(value, str) and value:
        return value[:1000]
    return _bounded(exc, 1000)


def _find_model_runtime_error(exc: BaseException) -> ModelRuntimeError | None:
    current: BaseException | None = exc
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, ModelRuntimeError):
            return current
        current = current.__cause__ or current.__context__
    return None


__all__ = ["Harness", "HarnessError", "TokenCostPolicy"]
