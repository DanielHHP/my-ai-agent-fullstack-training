"""Governed execution tests for WP-04."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import guarded_harness as gh
from guarded_harness import (
    AcceptanceStatus,
    ApprovalConfig,
    ApprovalRequirement,
    ApprovalStatus,
    ApprovalStore,
    Approver,
    AuditDecision,
    AuditStatus,
    BudgetConfig,
    CodeUnderstandingAcceptance,
    ConcurrencyClass,
    ErrorCode,
    EvidenceDraft,
    EvidenceKind,
    ExecutionMode,
    Gate,
    GatePhase,
    HandlerResult,
    HandlerResultStatus,
    HarnessConfig,
    InvocationStatus,
    LeasePolicy,
    ObservationStatus,
    PathPolicyConfig,
    PolicyEngine,
    ResolvedHarnessConfig,
    RetryPolicy,
    Run,
    RunBudget,
    RunStatus,
    Sandbox,
    SQLiteStorage,
    Step,
    StepStatus,
    StopReason,
    TaskConfig,
    TaskKind,
    ToolCallRequest,
    ToolDefinition,
    ToolExecutionContext,
    ToolOutputKind,
    ToolPermission,
    ToolRegistry,
    ToolRisk,
    ToolSpec,
    WorkspaceConfig,
    WorkspaceIdentity,
    register_side_effect_tools,
)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
RUN_ID = "run_00000000000000000000000000000001"
TRACE_ID = "00000000000000000000000000000002"
STEP_ID = "step_00000000000000000000000000000003"
DIGEST = f"sha256:{'a' * 64}"
PATHS_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


class MutableClock:
    """Small deterministic clock with explicit advancement."""

    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


@dataclass
class GateFixture:
    storage: SQLiteStorage
    sandbox: Sandbox
    gate: Gate
    fault_injector: object | None
    lease_token: str
    context: ToolExecutionContext
    write_handler_calls: list[dict]
    read_handler_calls: list[dict]
    deadline_state: dict
    output: dict


def _resolved_config(tmp_path: Path, repo_root: Path, workspace: Path) -> ResolvedHarnessConfig:
    state_dir = tmp_path / "state"
    config = HarnessConfig(
        version=1,
        state_dir=str(state_dir),
        workspace=WorkspaceConfig(
            root=str(workspace),
            require_clean_workspace=True,
            protect_other_git_changes=True,
        ),
        budget=BudgetConfig(
            max_steps=10,
            max_tool_calls=20,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=3600,
        ),
        task=TaskConfig(
            id="understand_login",
            kind=TaskKind.CODE_UNDERSTANDING,
            description="Explain login",
            allowed_tools=(
                "read_file",
                "write_file",
                "command_tool",
                "bounded_output",
                "secret_output",
                "slow_tool",
                "failing_tool",
                "none_output",
                "started_observer",
                "result_fault_tool",
                "timed_out_tool",
                "normalizer_tool",
            ),
            paths=PathPolicyConfig(
                deny_read=("**/.env",),
                deny_write=("src/**", "tests/**"),
                allow_write=("artifacts/**",),
            ),
            acceptance=CodeUnderstandingAcceptance(
                strategy="code_understanding",
                deliverables=("artifacts/out.md",),
            ),
            approval=ApprovalConfig(
                required_tools=("write_file",),
                approver_roles=("operator",),
                ttl_seconds=900,
                channel="local_cli",
            ),
        ),
    )
    return ResolvedHarnessConfig(
        config=config,
        workspace_root=str(workspace),
        state_dir=str(state_dir),
        deliverables=("artifacts/out.md",),
        fingerprint=DIGEST,
    )


def _tool(
    name: str,
    handler,
    *,
    risk: ToolRisk = ToolRisk.READ,
    permissions: frozenset[ToolPermission] = frozenset({ToolPermission.READ}),
    approval: ApprovalRequirement = ApprovalRequirement.NEVER,
    idempotent: bool = True,
    path_arguments: tuple[str, ...] = ("path",),
    output_kind: ToolOutputKind = ToolOutputKind.JSON,
    output_limit: int = 4096,
    schema: dict | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        spec=ToolSpec(
            name=name,
            description="Test tool.",
            input_schema=schema or PATHS_SCHEMA,
            output_kind=output_kind,
            risk=risk,
            required_permissions=permissions,
            timeout_seconds=10,
            retry_policy=RetryPolicy.SAFE_READ_ONLY if idempotent else RetryPolicy.NEVER,
            idempotent=idempotent,
            path_arguments=path_arguments,
            output_limit_bytes=output_limit,
            approval_requirement=approval,
            concurrency_class=(
                ConcurrencyClass.READ_ONLY_PARALLEL
                if risk == ToolRisk.READ
                else ConcurrencyClass.SERIAL
            ),
        ),
        handler=handler,
    )


def _gate_fixture(
    tmp_path: Path,
    *,
    max_tool_calls: int = 20,
    fault_injector=None,
) -> GateFixture:
    root = tmp_path / "repo"
    workspace = root / "task"
    (workspace / "src").mkdir(parents=True)
    (workspace / "artifacts").mkdir()
    (workspace / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (workspace / "src" / "other.py").write_text("print('other')\n", encoding="utf-8")
    import subprocess

    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "tests@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Harness Tests"],
        check=True,
        capture_output=True,
    )

    config = _resolved_config(tmp_path, root, workspace)
    clock = MutableClock()
    storage = SQLiteStorage(
        tmp_path / "state" / "harness.sqlite3",
        workspace,
        clock,
        fault_injector=fault_injector,
    )
    storage.initialize()
    sandbox = Sandbox.from_resolved_config(config, clock=clock)

    write_calls: list[dict] = []
    read_calls: list[dict] = []
    deadline_state: dict = {}
    output: dict = {}

    def read_handler(args, _context):
        read_calls.append(args)
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"path": args["path"]},
            summary="read succeeded",
        )

    def write_handler(args, _context):
        write_calls.append(args)
        if args.get("content") == "fail":
            return HandlerResult(
                status=HandlerResultStatus.FAILED,
                summary="write failed",
                error_code="WRITE_FAILED",
            )
        if args.get("content") == "unknown":
            return HandlerResult(
                status=HandlerResultStatus.UNKNOWN_RESULT,
                summary="write result unknown",
                error_code=ErrorCode.GATE_RESULT_UNKNOWN.value,
            )
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"written": args["path"]},
            summary="write succeeded",
            evidence=(
                EvidenceDraft(
                    kind=EvidenceKind.DOCUMENT,
                    path=args["path"],
                    content_digest=DIGEST,
                ),
            ),
        )

    def command_handler(_args, _context):
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"exit_code": 0},
            summary="command succeeded",
        )

    def bounded_handler(_args, _context):
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output="x" * 500,
            summary="bounded",
        )

    def secret_handler(_args, _context):
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"value": "ok", "access_token": "super-secret-token"},
            summary="token=super-secret-token",
        )

    def slow_handler(_args, _context):
        deadline_state["called"] = True
        clock.advance(11)
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"late": True},
            summary="late",
        )

    def failing_handler(_args, _context):
        return HandlerResult(
            status=HandlerResultStatus.FAILED,
            summary="upstream failed",
            error_code="UPSTREAM_UNAVAILABLE",
        )

    def none_output_handler(_args, _context):
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"unexpected": True},
            summary="invalid none output",
        )

    def started_observer(_args, _context):
        with storage.transaction(actor="agent") as unit:
            snapshot = unit.state.get_run_snapshot(RUN_ID)
            deadline_state["started_status"] = next(
                invocation.status
                for invocation in snapshot.invocations
                if invocation.tool_call_id == "call-started-observer"
            )
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"observed": True},
            summary="observed",
        )

    def result_fault_handler(_args, _context):
        deadline_state["result_fault_handler_calls"] = (
            deadline_state.get("result_fault_handler_calls", 0) + 1
        )
        if fault_injector is not None:
            fault_injector.arm("audit:before-insert")
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            output={"done": True},
            summary="result audit will fail",
        )

    def timed_out_handler(_args, _context):
        return HandlerResult(
            status=HandlerResultStatus.TIMED_OUT,
            summary="command timed out after the process group stopped",
            error_code=ErrorCode.SANDBOX_COMMAND_TIMEOUT.value,
        )

    def normalizer_handler(_args, _context):
        deadline_state["normalizer_handler_called"] = True
        return HandlerResult(
            status=HandlerResultStatus.SUCCEEDED,
            summary="unreachable",
        )

    registry = ToolRegistry()
    registry.register(_tool("read_file", read_handler))
    registry.register(
        _tool(
            "write_file",
            write_handler,
            risk=ToolRisk.WRITE,
            permissions=frozenset({ToolPermission.WRITE}),
            approval=ApprovalRequirement.REQUIRED,
            idempotent=False,
        )
    )
    registry.register(
        _tool(
            "command_tool",
            command_handler,
            risk=ToolRisk.EXECUTE,
            permissions=frozenset({ToolPermission.EXECUTE}),
            idempotent=False,
            path_arguments=(),
            schema={
                "type": "object",
                "properties": {"command_id": {"type": "string"}},
                "required": ["command_id"],
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        _tool(
            "bounded_output",
            bounded_handler,
            path_arguments=(),
            output_kind=ToolOutputKind.TEXT,
            output_limit=64,
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        _tool(
            "secret_output",
            secret_handler,
            path_arguments=(),
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
    )
    registry.register(
        _tool(
            "slow_tool",
            slow_handler,
            idempotent=False,
            path_arguments=(),
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )
    )
    empty_schema = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    registry.register(
        _tool(
            "failing_tool",
            failing_handler,
            path_arguments=(),
            schema=empty_schema,
        )
    )
    registry.register(
        _tool(
            "none_output",
            none_output_handler,
            path_arguments=(),
            output_kind=ToolOutputKind.NONE,
            schema=empty_schema,
        )
    )
    registry.register(
        _tool(
            "started_observer",
            started_observer,
            path_arguments=(),
            schema=empty_schema,
        )
    )
    registry.register(
        _tool(
            "result_fault_tool",
            result_fault_handler,
            path_arguments=(),
            schema=empty_schema,
        )
    )
    registry.register(
        _tool(
            "timed_out_tool",
            timed_out_handler,
            path_arguments=(),
            idempotent=True,
            schema=empty_schema,
        )
    )
    normalizer_definition = _tool(
        "normalizer_tool",
        normalizer_handler,
        path_arguments=(),
        schema=empty_schema,
    )
    registry.register(
        ToolDefinition(
            spec=normalizer_definition.spec,
            handler=normalizer_definition.handler,
            normalize_args=_raise_normalizer,
        )
    )
    policy = PolicyEngine(
        allowed_tool_names=registry.names(),
        role_permissions={
            "developer": {
                ToolPermission.READ,
                ToolPermission.WRITE,
                ToolPermission.EXECUTE,
            },
            "observer": {ToolPermission.READ},
        },
        task_approval_tools=("write_file",),
        approval_roles=("operator",),
    )

    run = Run(
        run_id=RUN_ID,
        trace_id=TRACE_ID,
        task_id="understand_login",
        task_kind=TaskKind.CODE_UNDERSTANDING,
        task_description="Explain login",
        config_fingerprint=DIGEST,
        workspace=WorkspaceIdentity(
            git_root=str(sandbox.git_root),
            workspace_root=str(sandbox.workspace_root),
            workspace_prefix=sandbox.workspace_prefix,
            baseline_fingerprint=DIGEST,
            external_dirty_baseline_fingerprint=DIGEST,
        ),
        budget=RunBudget(
            max_steps=10,
            max_tool_calls=max_tool_calls,
            max_token_cost=Decimal("10"),
            max_wall_time_seconds=3600,
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    with storage.transaction(actor="agent") as unit:
        lease = unit.state.create_run(run, owner_id="agent", lease_policy=LeasePolicy())
        unit.state.create_step(
            Step(
                step_id=STEP_ID,
                run_id=RUN_ID,
                ordinal=0,
                summary="test",
                started_at=NOW,
            ),
            expected_run_revision=0,
            lease_token=lease.token,
        )
        unit.state.transition_step(
            STEP_ID,
            expected_status=StepStatus.PLANNED,
            expected_revision=0,
            target=StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
        unit.state.transition_run(
            RUN_ID,
            expected_status=RunStatus.NEW,
            expected_revision=2,
            target=RunStatus.RUNNING,
            stop_reason=None,
            ended_at=None,
            acceptance_status=None,
            lease_token=lease.token,
        )

    context = ToolExecutionContext(
        run_id=RUN_ID,
        step_id=STEP_ID,
        task_id="understand_login",
        task_kind=TaskKind.CODE_UNDERSTANDING,
        mode=ExecutionMode.EXECUTE,
        actor="agent",
        roles=("developer",),
    )
    gate = Gate(storage=storage, registry=registry, policy=policy, sandbox=sandbox, clock=clock)
    fixture = GateFixture(
        storage=storage,
        sandbox=sandbox,
        gate=gate,
        fault_injector=fault_injector,
        lease_token=lease.token,
        context=context,
        write_handler_calls=write_calls,
        read_handler_calls=read_calls,
        deadline_state=deadline_state,
        output=output,
    )
    fixture.output["clock"] = clock
    return fixture


def _request(
    fixture: GateFixture,
    *,
    call_id: str,
    tool: str,
    args: dict,
    context: ToolExecutionContext | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call_id=call_id,
        tool_name=tool,
        args=args,
        context=context or fixture.context,
        lease_token=fixture.lease_token,
    )


def _raise_normalizer(_args, _context):
    raise RuntimeError("normalizer bug")


def _approve(fixture: GateFixture, approval_id: str) -> None:
    with fixture.storage.transaction(actor="operator") as unit:
        current = ApprovalStore(unit).load(approval_id, lease_token=fixture.lease_token)
        ApprovalStore(unit).approve(
            approval_id,
            expected_revision=current.revision,
            approver=Approver(actor="operator", roles=("operator",)),
            lease_token=fixture.lease_token,
        )


def _reject(fixture: GateFixture, approval_id: str) -> None:
    with fixture.storage.transaction(actor="operator") as unit:
        current = ApprovalStore(unit).load(approval_id, lease_token=fixture.lease_token)
        ApprovalStore(unit).reject(
            approval_id,
            expected_revision=current.revision,
            approver=Approver(actor="operator", roles=("operator",)),
            reason_code="HUMAN_REJECTED",
            lease_token=fixture.lease_token,
        )


def test_schema_rejections_have_zero_handler_calls(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)

    for call_id, args in (
        ("call-missing", {}),
        ("call-type", {"path": 7}),
        ("call-extra", {"path": "src/app.py", "injected": True}),
    ):
        observation = fixture.gate.invoke(
            _request(fixture, call_id=call_id, tool="read_file", args=args)
        )
        assert observation.status == ObservationStatus.DENIED
        assert observation.code == ErrorCode.GATE_INVALID_ARGUMENT.value
        assert observation.phase == GatePhase.SCHEMA

    assert fixture.read_handler_calls == []


def test_trusted_normalizer_failure_is_semantic_denial(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-normalizer",
            tool="normalizer_tool",
            args={},
        )
    )
    assert observation.status == ObservationStatus.DENIED
    assert observation.code == ErrorCode.GATE_SEMANTIC_INVALID.value
    assert "normalizer_handler_called" not in fixture.deadline_state


def test_visibility_permission_and_plan_mode_are_enforced(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)

    unknown = fixture.gate.invoke(
        _request(fixture, call_id="call-unknown", tool="git_diff", args={"path": "src/app.py"})
    )
    assert unknown.code == ErrorCode.GATE_TOOL_NOT_FOUND.value

    observer_context = fixture.context.model_copy(update={"roles": ("observer",)})
    write = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-write-permission",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "x"},
            context=observer_context,
        )
    )
    assert write.code == ErrorCode.GATE_PERMISSION_DENIED.value

    plan_context = fixture.context.model_copy(update={"mode": ExecutionMode.PLAN})
    plan = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-plan",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "x"},
            context=plan_context,
        )
    )
    assert plan.code == ErrorCode.GATE_MODE_DENIED.value
    assert fixture.write_handler_calls == []


def test_sandbox_path_escape_and_command_injection_are_denied(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)

    path = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-path",
            tool="read_file",
            args={"path": "artifacts/../../.env"},
        )
    )
    assert path.status == ObservationStatus.DENIED
    assert path.phase == GatePhase.SEMANTIC
    assert fixture.read_handler_calls == []

    command = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-command",
            tool="command_tool",
            args={"command_id": "acceptance_test", "argv": ["sh"]},
        )
    )
    assert command.code == ErrorCode.GATE_INVALID_ARGUMENT.value


def test_redaction_changed_write_arguments_are_denied_before_invocation(
    tmp_path: Path,
) -> None:
    fixture = _gate_fixture(tmp_path)

    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-unrecoverable-write",
            tool="write_file",
            args={
                "path": "artifacts/out.md",
                "content": "token = super-secret-token",
            },
        )
    )

    assert observation.status == ObservationStatus.DENIED
    assert observation.code == ErrorCode.TOOL_ARGUMENTS_NOT_RECOVERABLE.value
    assert observation.phase == GatePhase.SEMANTIC
    assert observation.retryable is True
    assert fixture.write_handler_calls == []
    with fixture.storage.transaction(actor="auditor") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
        assert snapshot.invocations == ()
        assert snapshot.approvals == ()
        events = unit.audit.list_tool_events(RUN_ID, "call-unrecoverable-write")
    assert [event.code for event in events] == [ErrorCode.TOOL_ARGUMENTS_NOT_RECOVERABLE.value]
    assert "super-secret-token" not in str(events[0].input_summary)
    assert "super-secret-token" not in str(events[0].output_summary)


def test_existing_redaction_placeholders_remain_recoverable(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)

    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-recoverable-write",
            tool="write_file",
            args={
                "path": "artifacts/out.md",
                "content": "token = [REDACTED]",
            },
        )
    )

    assert observation.status == ObservationStatus.WAITING_APPROVAL
    with fixture.storage.transaction(actor="auditor") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
    assert len(snapshot.invocations) == 1
    assert snapshot.invocations[0].status == InvocationStatus.PREPARED
    assert len(snapshot.approvals) == 1


def test_g3_wait_and_g4_one_time_execution(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    request = _request(
        fixture,
        call_id="call-approved-write",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "hello"},
    )

    waiting = fixture.gate.invoke(request)
    assert waiting.status == ObservationStatus.WAITING_APPROVAL
    assert waiting.approval_id is not None
    assert fixture.write_handler_calls == []
    with fixture.storage.transaction(actor="agent") as unit:
        assert unit.state.get_run(RUN_ID).status == RunStatus.WAITING_APPROVAL
        approval = ApprovalStore(unit).load(waiting.approval_id, lease_token=fixture.lease_token)
        assert approval.status == ApprovalStatus.PENDING

    _approve(fixture, waiting.approval_id)
    first = fixture.gate.invoke(request)
    assert first.status == ObservationStatus.ALLOWED
    assert first.evidence_ids
    assert len(fixture.write_handler_calls) == 1

    second = fixture.gate.invoke(request)
    assert second.status == ObservationStatus.DENIED
    assert second.code == ErrorCode.GATE_REPLAY_SUPPRESSED.value
    assert second.evidence_ids == first.evidence_ids
    assert len(fixture.write_handler_calls) == 1

    equivalent_new_call = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-approved-write-new-id",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "hello"},
        )
    )
    assert equivalent_new_call.code == ErrorCode.GATE_REPLAY_SUPPRESSED.value
    assert equivalent_new_call.evidence_ids == first.evidence_ids
    assert len(fixture.write_handler_calls) == 1

    with fixture.storage.transaction(actor="agent") as unit:
        approval = ApprovalStore(unit).load(waiting.approval_id, lease_token=fixture.lease_token)
        assert approval.status == ApprovalStatus.CONSUMED
        invocations = unit.state.get_run_snapshot(RUN_ID).invocations
        assert len(invocations) == 1
        assert invocations[0].status == InvocationStatus.SUCCEEDED


def test_real_write_tool_is_approval_gated_once(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    registry = ToolRegistry()
    resolved = _resolved_config(
        tmp_path,
        tmp_path / "repo",
        fixture.sandbox.workspace_root,
    )
    assert register_side_effect_tools(registry, fixture.sandbox, resolved) == (
        "apply_patch",
        "write_file",
    )
    policy = PolicyEngine(
        allowed_tool_names=registry.names(),
        role_permissions={
            "developer": {
                ToolPermission.READ,
                ToolPermission.WRITE,
            }
        },
        task_approval_tools=("write_file",),
        approval_roles=("operator",),
    )
    gate = Gate(
        storage=fixture.storage,
        registry=registry,
        policy=policy,
        sandbox=fixture.sandbox,
        clock=fixture.output["clock"],
    )
    request = ToolCallRequest(
        tool_call_id="real-write",
        tool_name="write_file",
        args={"path": "artifacts/out.md", "content": "# real write\n"},
        context=fixture.context,
        lease_token=fixture.lease_token,
    )
    target = fixture.sandbox.workspace_root / "artifacts" / "out.md"
    waiting = gate.invoke(request)
    assert waiting.status == ObservationStatus.WAITING_APPROVAL
    assert not target.exists()

    _approve(fixture, waiting.approval_id)
    first = gate.invoke(request)
    assert first.status == ObservationStatus.ALLOWED
    assert target.read_text(encoding="utf-8") == "# real write\n"
    assert first.evidence_ids

    target.write_text("external\n", encoding="utf-8")
    replay = gate.invoke(request)
    assert replay.code == ErrorCode.GATE_REPLAY_SUPPRESSED.value
    assert target.read_text(encoding="utf-8") == "external\n"


def test_g2_third_equivalent_read_is_denied(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    args = {"path": "src/app.py"}

    for index in range(2):
        observation = fixture.gate.invoke(
            _request(
                fixture,
                call_id=f"call-read-{index}",
                tool="read_file",
                args=args,
            )
        )
        assert observation.status == ObservationStatus.ALLOWED

    denied = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-read-2",
            tool="read_file",
            args=args,
        )
    )
    assert denied.status == ObservationStatus.DENIED
    assert denied.code == ErrorCode.GATE_REPEATED_CALL.value
    assert denied.phase == GatePhase.REPEAT
    assert len(fixture.read_handler_calls) == 2


def test_approval_is_consumed_on_deterministic_failure(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    request = _request(
        fixture,
        call_id="call-write-failed",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "fail"},
    )
    waiting = fixture.gate.invoke(request)
    _approve(fixture, waiting.approval_id)
    failed = fixture.gate.invoke(request)
    assert failed.status == ObservationStatus.FAILED
    assert len(fixture.write_handler_calls) == 1
    with fixture.storage.transaction(actor="agent") as unit:
        approval = ApprovalStore(unit).load(waiting.approval_id, lease_token=fixture.lease_token)
        assert approval.status == ApprovalStatus.CONSUMED


def test_unknown_write_keeps_approval_claimed_and_never_retries(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    request = _request(
        fixture,
        call_id="call-write-unknown",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "unknown"},
    )
    waiting = fixture.gate.invoke(request)
    _approve(fixture, waiting.approval_id)
    unknown = fixture.gate.invoke(request)
    assert unknown.status == ObservationStatus.UNKNOWN_RESULT
    assert len(fixture.write_handler_calls) == 1
    with fixture.storage.transaction(actor="agent") as unit:
        approval = ApprovalStore(unit).load(waiting.approval_id, lease_token=fixture.lease_token)
        assert approval.status == ApprovalStatus.CLAIMED

    repeated = fixture.gate.invoke(request)
    assert repeated.status == ObservationStatus.DENIED
    assert len(fixture.write_handler_calls) == 1


def test_budget_and_started_before_handler(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path, max_tool_calls=1)
    written = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-read-first",
            tool="read_file",
            args={"path": "src/app.py"},
        )
    )
    assert written.status == ObservationStatus.ALLOWED

    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.SUCCEEDED

    exhausted = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-read-second",
            tool="read_file",
            args={"path": "src/other.py"},
        )
    )
    assert exhausted.code == ErrorCode.GATE_BUDGET_EXHAUSTED.value
    assert len(fixture.read_handler_calls) == 1


def test_handler_timeout_is_unknown_and_not_retried(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-slow",
            tool="slow_tool",
            args={},
        )
    )
    assert observation.status == ObservationStatus.UNKNOWN_RESULT
    assert observation.code == ErrorCode.GATE_HANDLER_TIMEOUT.value
    assert fixture.deadline_state["called"] is True
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.UNKNOWN_RESULT

    repeated = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-slow",
            tool="slow_tool",
            args={},
        )
    )
    assert repeated.status == ObservationStatus.DENIED
    assert fixture.deadline_state["called"] is True


def test_started_is_visible_inside_handler(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-started-observer",
            tool="started_observer",
            args={},
        )
    )
    assert observation.status == ObservationStatus.ALLOWED
    assert fixture.deadline_state["started_status"] == InvocationStatus.STARTED


def test_same_call_id_with_changed_arguments_is_rejected(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    first = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-identity",
            tool="read_file",
            args={"path": "src/app.py"},
        )
    )
    assert first.status == ObservationStatus.ALLOWED
    second = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-identity",
            tool="read_file",
            args={"path": "src/other.py"},
        )
    )
    assert second.code == ErrorCode.GATE_TOOL_CALL_ID_CONFLICT.value
    assert len(fixture.read_handler_calls) == 1


def test_changed_arguments_invalidate_pending_approval_binding(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    waiting = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-binding",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "original"},
        )
    )
    assert waiting.status == ObservationStatus.WAITING_APPROVAL
    _approve(fixture, waiting.approval_id)

    changed = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-binding",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "changed"},
        )
    )
    assert changed.code == ErrorCode.GATE_TOOL_CALL_ID_CONFLICT.value
    assert fixture.write_handler_calls == []


def test_expired_approval_is_denied_without_handler(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    request = _request(
        fixture,
        call_id="call-expired",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "x"},
    )
    waiting = fixture.gate.invoke(request)
    assert waiting.status == ObservationStatus.WAITING_APPROVAL
    fixture.output["clock"].advance(901)
    with fixture.storage.transaction(actor="agent") as unit:
        replacement = unit.state.acquire_lease(RUN_ID, owner_id="agent-resume")
    fixture.lease_token = replacement.token
    request = _request(
        fixture,
        call_id="call-expired",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "x"},
    )

    expired = fixture.gate.invoke(request)
    assert expired.status == ObservationStatus.DENIED
    assert expired.code == ErrorCode.APPROVAL_EXPIRED.value
    assert fixture.write_handler_calls == []
    with fixture.storage.transaction(actor="agent") as unit:
        run = unit.state.get_run(RUN_ID)
        assert run.status == RunStatus.STOPPED
        assert run.stop_reason == StopReason.APPROVAL_EXPIRED
        assert run.acceptance_status == AcceptanceStatus.NOT_EVALUATED


def test_rejected_approval_returns_stable_gate_denial(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    request = _request(
        fixture,
        call_id="call-rejected",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "x"},
    )
    waiting = fixture.gate.invoke(request)
    _reject(fixture, waiting.approval_id)

    rejected = fixture.gate.invoke(request)
    assert rejected.status == ObservationStatus.DENIED
    assert rejected.code == ErrorCode.APPROVAL_REJECTED.value
    assert fixture.write_handler_calls == []
    with fixture.storage.transaction(actor="agent") as unit:
        run = unit.state.get_run(RUN_ID)
        assert run.status == RunStatus.STOPPED
        assert run.stop_reason == StopReason.APPROVAL_REJECTED


def test_handler_failure_is_deterministic(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-failed",
            tool="failing_tool",
            args={},
        )
    )
    assert observation.status == ObservationStatus.FAILED
    assert observation.code == "UPSTREAM_UNAVAILABLE"
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.FAILED
        assert invocation.error_code == "UPSTREAM_UNAVAILABLE"


def test_confirmed_idempotent_timeout_persists_timed_out(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-timed-out",
            tool="timed_out_tool",
            args={},
        )
    )
    assert observation.status == ObservationStatus.FAILED
    assert observation.code == ErrorCode.SANDBOX_COMMAND_TIMEOUT.value
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.TIMED_OUT


def test_invalid_handler_output_becomes_unknown(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-invalid-output",
            tool="none_output",
            args={},
        )
    )
    assert observation.status == ObservationStatus.UNKNOWN_RESULT
    assert observation.code == ErrorCode.GATE_RESULT_INVALID.value
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.UNKNOWN_RESULT


def test_output_is_bounded_redacted_and_results_are_audited(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    bounded = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-bounded",
            tool="bounded_output",
            args={},
        )
    )
    assert bounded.truncated is True
    assert isinstance(bounded.output, dict)
    assert bounded.output["output_kind"] == "TEXT"

    secret = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-secret",
            tool="secret_output",
            args={},
        )
    )
    assert secret.output == {"value": "ok", "access_token": "[REDACTED]"}
    assert "super-secret-token" not in secret.message
    with fixture.storage.transaction(actor="agent") as unit:
        events = unit.audit.list_tool_events(RUN_ID, "call-secret")
        assert all("super-secret-token" not in str(event.model_dump()) for event in events)


def test_denial_audit_contains_required_a1_fields(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-a1",
            tool="read_file",
            args={},
        )
    )
    with fixture.storage.transaction(actor="agent") as unit:
        events = unit.audit.list_tool_events(RUN_ID, "call-a1")
    denied = [event for event in events if event.decision == AuditDecision.DENY]
    assert denied
    assert denied[-1].status == AuditStatus.DENIED
    assert denied[-1].rule_id == "GATE_SCHEMA_V1"
    assert denied[-1].actor == "agent"
    assert denied[-1].tool_call_id == "call-a1"


def test_allow_audit_failure_never_reaches_handler(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    fault_injector.arm("audit:before-insert")
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-audit-allow-failure",
            tool="read_file",
            args={"path": "src/app.py"},
        )
    )
    assert observation.status == ObservationStatus.DENIED
    assert observation.code == ErrorCode.AUDIT_WRITE_FAILED.value
    assert fixture.read_handler_calls == []
    with fixture.storage.transaction(actor="agent") as unit:
        assert unit.state.get_run_snapshot(RUN_ID).invocations == ()


def test_deny_audit_failure_still_blocks_handler(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    fault_injector.arm("audit:before-insert")
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-audit-deny-failure",
            tool="read_file",
            args={},
        )
    )
    assert observation.status == ObservationStatus.DENIED
    assert observation.code == ErrorCode.AUDIT_WRITE_FAILED.value
    assert fixture.read_handler_calls == []


def test_result_audit_failure_leaves_invocation_started(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-result-audit-failure",
            tool="result_fault_tool",
            args={},
        )
    )
    assert observation.code == ErrorCode.AUDIT_WRITE_FAILED.value
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.STARTED


def test_fault_before_start_leaves_prepared_without_handler(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    fault_injector.arm("gate:before-start")
    observation = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-before-start-failure",
            tool="read_file",
            args={"path": "src/app.py"},
        )
    )
    assert observation.code == ErrorCode.GATE_START_WRITE_FAILED.value
    assert fixture.read_handler_calls == []
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        assert invocation.status == InvocationStatus.PREPARED


def test_concurrent_equivalent_calls_are_serialized(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    calls = [
        _request(
            fixture,
            call_id=f"call-concurrent-{index}",
            tool="read_file",
            args={"path": "src/app.py"},
        )
        for index in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        observations = list(executor.map(fixture.gate.invoke, calls))
    assert all(item.status == ObservationStatus.ALLOWED for item in observations)
    assert len(fixture.read_handler_calls) == 2
    with fixture.storage.transaction(actor="agent") as unit:
        events = unit.audit.list_run_events(RUN_ID)
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))


def test_concurrent_approved_call_executes_at_most_once(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    request = _request(
        fixture,
        call_id="call-concurrent-write",
        tool="write_file",
        args={"path": "artifacts/out.md", "content": "x"},
    )
    waiting = fixture.gate.invoke(request)
    _approve(fixture, waiting.approval_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        observations = list(executor.map(fixture.gate.invoke, (request, request)))
    assert sum(item.status == ObservationStatus.ALLOWED for item in observations) == 1
    assert sum(item.status == ObservationStatus.DENIED for item in observations) == 1
    assert len(fixture.write_handler_calls) == 1


def test_revision_reads_are_public_and_lease_bound(tmp_path: Path) -> None:
    fixture = _gate_fixture(tmp_path)
    with fixture.storage.transaction(actor="agent") as unit:
        run_revision = unit.state.get_run_revision(RUN_ID)
        assert run_revision >= 3
        unit.state.verify_lease(RUN_ID, fixture.lease_token)
        try:
            unit.state.verify_lease(RUN_ID, "wrong-token")
        except Exception as exc:
            assert getattr(exc, "code", None) == ErrorCode.STATE_LEASE_INVALID
        else:
            raise AssertionError("wrong lease token was accepted")


def test_reenter_prepared_revalidates_and_executes_once(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    fault_injector.arm("gate:before-start")
    first = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-prepared-read",
            tool="read_file",
            args={"path": "src/app.py"},
        )
    )
    assert first.status == ObservationStatus.DENIED
    assert fixture.read_handler_calls == []
    fault_injector.disable("gate:before-start")
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        usage_before = unit.state.get_run(RUN_ID).usage.tool_calls
    assert invocation.status == InvocationStatus.PREPARED

    recovered = fixture.gate.reenter_prepared(
        RUN_ID,
        invocation.tool_call_id,
        fixture.context,
        fixture.lease_token,
    )

    assert recovered.status == ObservationStatus.ALLOWED
    assert len(fixture.read_handler_calls) == 1
    with fixture.storage.transaction(actor="agent") as unit:
        result = unit.state.get_run_snapshot(RUN_ID).invocations[0]
        usage_after = unit.state.get_run(RUN_ID).usage.tool_calls
    assert result.status == InvocationStatus.SUCCEEDED
    assert usage_after == usage_before


def test_reenter_claimed_prepared_resumes_without_double_budget(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    waiting = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-claimed-write",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "payload"},
        )
    )
    assert waiting.status == ObservationStatus.WAITING_APPROVAL
    _approve(fixture, waiting.approval_id)
    fault_injector.arm("gate:before-start")
    interrupted = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-claimed-write",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "payload"},
        )
    )
    assert interrupted.status == ObservationStatus.DENIED
    assert fixture.write_handler_calls == []
    fault_injector.disable("gate:before-start")
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
        invocation = snapshot.invocations[0]
        approval = next(
            item for item in snapshot.approvals if item.approval_id == waiting.approval_id
        )
        usage_before = snapshot.run.usage.tool_calls
    assert invocation.status == InvocationStatus.PREPARED
    assert approval.status == ApprovalStatus.CLAIMED

    recovered = fixture.gate.reenter_prepared(
        RUN_ID,
        invocation.tool_call_id,
        fixture.context,
        fixture.lease_token,
    )

    assert recovered.status == ObservationStatus.ALLOWED
    assert len(fixture.write_handler_calls) == 1
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
        usage_after = snapshot.run.usage.tool_calls
        approval = next(
            item for item in snapshot.approvals if item.approval_id == waiting.approval_id
        )
        events = unit.audit.list_approval_events(RUN_ID, waiting.approval_id)
    assert snapshot.invocations[0].status == InvocationStatus.SUCCEEDED
    assert approval.status == ApprovalStatus.CONSUMED
    assert usage_after == usage_before
    assert any(event.event_type == gh.AuditEventType.APPROVAL_CLAIM_RECOVERED for event in events)


def test_reenter_approved_prepared_claims_and_records_budget_once(
    tmp_path: Path,
) -> None:
    fixture = _gate_fixture(tmp_path)
    waiting = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-approved-write",
            tool="write_file",
            args={"path": "artifacts/out.md", "content": "approved payload"},
        )
    )
    _approve(fixture, waiting.approval_id)
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
        invocation = snapshot.invocations[0]
        usage_before = snapshot.run.usage.tool_calls
    assert invocation.status == InvocationStatus.PREPARED
    assert usage_before == 0

    recovered = fixture.gate.reenter_prepared(
        RUN_ID,
        invocation.tool_call_id,
        fixture.context,
        fixture.lease_token,
    )

    assert recovered.status == ObservationStatus.ALLOWED
    assert len(fixture.write_handler_calls) == 1
    with fixture.storage.transaction(actor="agent") as unit:
        snapshot = unit.state.get_run_snapshot(RUN_ID)
        approval = next(
            item for item in snapshot.approvals if item.approval_id == waiting.approval_id
        )
    assert snapshot.invocations[0].status == InvocationStatus.SUCCEEDED
    assert snapshot.run.usage.tool_calls == 1
    assert approval.status == ApprovalStatus.CONSUMED


def test_reenter_started_is_rejected_without_replay(
    tmp_path: Path,
    fault_injector,
) -> None:
    fixture = _gate_fixture(tmp_path, fault_injector=fault_injector)
    first = fixture.gate.invoke(
        _request(
            fixture,
            call_id="call-started-read",
            tool="result_fault_tool",
            args={},
        )
    )
    assert first.status == ObservationStatus.DENIED
    assert fixture.deadline_state["result_fault_handler_calls"] == 1
    fault_injector.disable("audit:before-insert")
    with fixture.storage.transaction(actor="agent") as unit:
        invocation = unit.state.get_run_snapshot(RUN_ID).invocations[0]
    assert invocation.status == InvocationStatus.STARTED

    recovered = fixture.gate.reenter_prepared(
        RUN_ID,
        invocation.tool_call_id,
        fixture.context,
        fixture.lease_token,
    )

    assert recovered.status == ObservationStatus.DENIED
    assert fixture.deadline_state["result_fault_handler_calls"] == 1
