"""Offline tests for governed real-model invocation semantics."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from fake_model import FakeScriptedModel, ScriptedResponse, final_answer, tool_call
from smolagents import Model
from smolagents.models import (
    ChatMessage,
    ChatMessageToolCall,
    ChatMessageToolCallFunction,
    MessageRole,
)
from smolagents.monitoring import TokenUsage

from guarded_harness import (
    AcceptanceStatus,
    ErrorCode,
    Harness,
    ModelCallService,
    ModelCallStatus,
    Run,
    RunBudget,
    RunMode,
    RunRequest,
    RunStatus,
    Sandbox,
    SQLiteStorage,
    Step,
    StepStatus,
    SystemClock,
    load_config,
)


class FixedTokenCounter:
    def __init__(self, count: int = 10) -> None:
        self.count = count

    def count_messages(self, messages, tools) -> int:
        del messages, tools
        return self.count


class RateLimitError(RuntimeError):
    status_code = 429


class MissingUsageModel(Model):
    def __init__(self) -> None:
        super().__init__(model_id="missing-usage")
        self.calls = 0

    def generate(self, messages, stop_sequences=None, response_format=None, **kwargs):
        del messages, stop_sequences, response_format, kwargs
        self.calls += 1
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=[
                ChatMessageToolCall(
                    id="call-final",
                    type="function",
                    function=ChatMessageToolCallFunction(
                        name="final_answer",
                        arguments={"answer": "candidate"},
                    ),
                )
            ],
            token_usage=None,
        )


class TextOnlyModel(Model):
    def __init__(self) -> None:
        super().__init__(model_id="text-only")

    def generate(self, messages, stop_sequences=None, response_format=None, **kwargs):
        del messages, stop_sequences, response_format, kwargs
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content="final answer without a tool call",
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class MissingCallIdModel(Model):
    def __init__(self) -> None:
        super().__init__(model_id="missing-call-id")

    def generate(self, messages, stop_sequences=None, response_format=None, **kwargs):
        del messages, stop_sequences, response_format, kwargs
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=[
                ChatMessageToolCall(
                    id="",
                    type="function",
                    function=ChatMessageToolCallFunction(
                        name="final_answer",
                        arguments={"answer": "candidate"},
                    ),
                )
            ],
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class ToolChoiceRecordingModel(Model):
    def __init__(self) -> None:
        super().__init__(model_id="tool-choice-recorder")
        self.tool_choice: object = ...

    def generate(self, messages, stop_sequences=None, response_format=None, **kwargs):
        del messages, stop_sequences, response_format
        self.tool_choice = kwargs.get("tool_choice")
        return ChatMessage(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=[
                ChatMessageToolCall(
                    id="call-final",
                    type="function",
                    function=ChatMessageToolCallFunction(
                        name="final_answer",
                        arguments={"answer": "candidate"},
                    ),
                )
            ],
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


def _workspace(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "tests@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.name", "Harness Tests"],
        check=True,
    )
    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(workspace), "add", "."], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "initial"], check=True)
    return workspace


def _config(
    tmp_path: Path,
    *,
    max_token_cost: float = 1,
    max_response_bytes: int = 65536,
    tool_choice: str | None = "required",
) -> tuple[Path, Path]:
    workspace = _workspace(tmp_path)
    payload: dict[str, Any] = {
        "version": 2,
        "state_dir": str(tmp_path / "state"),
        "workspace": {"root": str(workspace)},
        "budget": {
            "max_steps": 4,
            "max_tool_calls": 4,
            "max_token_cost": max_token_cost,
            "max_wall_time_seconds": 120,
        },
        "model": {
            "provider": "openai_chat_completions_v1",
            "model_id": "fake-model-2026-01-01",
            "endpoint": "https://api.example.invalid/v1",
            "credential_ref": "MODEL_API_KEY",
            "tokenizer_id": "tiktoken:o200k_base",
            "max_input_tokens": 1000,
            "max_output_tokens": 100,
            "max_response_bytes": max_response_bytes,
            "max_tool_calls_per_response": 4,
            "max_retry_attempts": 1,
            "tool_choice": tool_choice,
            "pricing": {
                "version": "price-v1",
                "input_per_million": "1",
                "output_per_million": "2",
            },
        },
        "task": {
            "id": "model_runtime",
            "kind": "code_understanding",
            "description": "Explain the code.",
            "allowed_tools": ["read_file"],
            "paths": {"allow_write": ["artifacts/report.md"]},
            "acceptance": {
                "strategy": "code_understanding",
                "deliverables": ["artifacts/report.md"],
            },
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path, workspace


def _run(config: Path, model: Model, counter: FixedTokenCounter | None = None):
    return Harness(
        config_path=config,
        model=model,
        token_counter=counter or FixedTokenCounter(),
    ).run(RunRequest(mode=RunMode.START))


def _model_calls(config: Path, run_id: str, workspace: Path):
    storage = SQLiteStorage(
        config.parent / "state" / "harness.sqlite3",
        workspace,
        SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="test-reader") as unit:
        return unit.state.get_model_calls(run_id), unit.audit


def test_successful_model_call_is_persisted_with_usage_and_step(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path)
    result = _run(config, FakeScriptedModel((final_answer("candidate"),)))

    calls, audit = _model_calls(config, result.run_id, workspace)
    assert result.status == RunStatus.COMPLETED
    assert result.acceptance_status == AcceptanceStatus.REJECTED
    assert len(calls) == 1
    call = calls[0]
    assert call.status == ModelCallStatus.SUCCEEDED
    assert call.step_id
    assert call.input_tokens == 1
    assert call.output_tokens == 1
    assert call.actual_cost is not None and call.actual_cost > 0


def test_real_model_tool_candidate_still_passes_through_gate(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path)
    model = FakeScriptedModel(
        (
            tool_call("read-1", "read_file", {"path": "src/app.py"}),
            final_answer("candidate"),
        )
    )
    result = _run(config, model)

    assert result.status == RunStatus.COMPLETED
    assert len(model.calls) == 2
    storage = SQLiteStorage(
        config.parent / "state" / "harness.sqlite3",
        workspace,
        SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="test-reader") as unit:
        snapshot = unit.state.get_run_snapshot(result.run_id)
        assert snapshot.invocations[0].tool_call_id == "read-1"
        assert snapshot.invocations[0].status.value == "SUCCEEDED"
        assert len(snapshot.model_calls) == 2


def test_configured_tool_choice_is_forwarded_to_provider(tmp_path: Path) -> None:
    config, _workspace_path = _config(tmp_path, tool_choice="auto")
    model = ToolChoiceRecordingModel()

    _run(config, model)

    assert model.tool_choice == "auto"


def test_budget_rejection_happens_before_model_call(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path, max_token_cost=0.000001)
    model = FakeScriptedModel((final_answer("unused"),))
    result = _run(config, model)

    assert result.status == RunStatus.STOPPED
    assert result.error_code == ErrorCode.GATE_BUDGET_EXHAUSTED.value
    assert model.calls == []
    calls, _audit = _model_calls(config, result.run_id, workspace)
    assert calls == ()


def test_missing_usage_and_text_fallback_fail_closed(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path)
    model = MissingUsageModel()
    result = _run(config, model)

    assert result.status == RunStatus.FAILED
    assert result.error_code == ErrorCode.MODEL_USAGE_INVALID.value
    assert model.calls == 1
    calls, _audit = _model_calls(config, result.run_id, workspace)
    assert [call.status for call in calls] == [ModelCallStatus.FAILED]

    text_config, text_workspace = _config(tmp_path / "text")
    text_result = _run(text_config, TextOnlyModel())
    assert text_result.status == RunStatus.FAILED
    assert text_result.error_code == ErrorCode.MODEL_PROTOCOL_UNSUPPORTED.value
    text_calls, _audit = _model_calls(text_config, text_result.run_id, text_workspace)
    assert text_calls[0].status == ModelCallStatus.FAILED

    id_config, id_workspace = _config(tmp_path / "missing-id")
    id_result = _run(id_config, MissingCallIdModel())
    assert id_result.status == RunStatus.FAILED
    assert id_result.error_code == ErrorCode.MODEL_TOOL_CALLS_INVALID.value
    id_calls, _audit = _model_calls(id_config, id_result.run_id, id_workspace)
    assert id_calls[0].status == ModelCallStatus.FAILED


def test_rate_limit_retry_is_explicit_and_grouped(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path)
    model = FakeScriptedModel(
        (
            ScriptedResponse(error=RateLimitError("rate limited")),
            final_answer("candidate"),
        )
    )
    result = _run(config, model)

    assert result.status == RunStatus.COMPLETED
    assert len(model.calls) == 2
    calls, _audit = _model_calls(config, result.run_id, workspace)
    assert [call.attempt_number for call in calls] == [1, 2]
    assert calls[0].logical_model_call_id == calls[1].logical_model_call_id
    assert calls[0].status == ModelCallStatus.FAILED
    assert calls[1].status == ModelCallStatus.SUCCEEDED


def test_timeout_is_unknown_and_not_retried(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path)
    model = FakeScriptedModel((ScriptedResponse(error=TimeoutError("timed out")),))
    result = _run(config, model)

    assert result.status == RunStatus.BLOCKED
    assert result.error_code == ErrorCode.MODEL_TIMEOUT.value
    assert len(model.calls) == 1
    calls, _audit = _model_calls(config, result.run_id, workspace)
    assert calls[0].status == ModelCallStatus.UNKNOWN_RESULT


def test_oversized_response_fails_before_agent_tool_execution(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path, max_response_bytes=8)
    result = _run(config, FakeScriptedModel((final_answer("too-large"),)))

    assert result.status == RunStatus.FAILED
    assert result.error_code == ErrorCode.MODEL_RESPONSE_TOO_LARGE.value
    calls, _audit = _model_calls(config, result.run_id, workspace)
    assert calls[0].status == ModelCallStatus.FAILED


def test_model_audit_chain_uses_schema_two(tmp_path: Path) -> None:
    config, workspace = _config(tmp_path)
    result = _run(config, FakeScriptedModel((final_answer("candidate"),)))
    calls, _audit = _model_calls(config, result.run_id, workspace)
    assert calls
    storage = SQLiteStorage(
        config.parent / "state" / "harness.sqlite3",
        workspace,
        SystemClock(),
    )
    storage.initialize()
    with storage.transaction(actor="test-reader") as unit:
        events = unit.audit.list_model_events(result.run_id, calls[0].model_call_id)
        assert events
        assert all(event.audit_schema == 2 for event in events)
        assert unit.audit.verify_chain(result.run_id).valid


def test_started_model_call_blocks_resume_without_provider_replay(tmp_path: Path) -> None:
    config_path, workspace = _config(tmp_path)
    config = load_config(config_path)
    clock = SystemClock()
    sandbox = Sandbox.from_resolved_config(config, clock=clock)
    baseline = sandbox.capture_baseline()
    storage = SQLiteStorage(
        config_path.parent / "state" / "harness.sqlite3",
        workspace,
        clock,
    )
    storage.initialize()
    now = clock.now()
    run = Run(
        run_id=f"run_{uuid4().hex}",
        trace_id=uuid4().hex,
        task_id=config.config.task.id,
        task_kind=config.config.task.kind,
        task_description=config.config.task.description,
        config_fingerprint=config.fingerprint,
        workspace=baseline.identity,
        budget=RunBudget(
            max_steps=config.config.budget.max_steps,
            max_tool_calls=config.config.budget.max_tool_calls,
            max_token_cost=config.config.budget.max_token_cost,
            max_wall_time_seconds=config.config.budget.max_wall_time_seconds,
        ),
        created_at=now,
        updated_at=now,
    )
    step = Step(
        step_id=f"step_{uuid4().hex}",
        run_id=run.run_id,
        ordinal=0,
        summary="interrupted model call",
        started_at=now,
    )
    with storage.transaction(actor="test-setup") as unit:
        lease = unit.state.create_run(run, owner_id="test-setup")
        unit.state.record_workspace_baseline(run.run_id, baseline, lease_token=lease.token)
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
        unit.state.create_step(
            step,
            expected_run_revision=unit.state.get_run_revision(run.run_id),
            lease_token=lease.token,
        )
        unit.state.transition_step(
            step.step_id,
            expected_status=StepStatus.PLANNED,
            expected_revision=0,
            target=StepStatus.RUNNING,
            ended_at=None,
            lease_token=lease.token,
        )
    service = ModelCallService(
        storage=storage,
        config=config,
        run_id=run.run_id,
        lease_token=lease.token,
        token_counter=FixedTokenCounter(),
        ensure_step=lambda: step.step_id,
        clock=clock,
    )
    service.prepare(
        messages=[],
        tools=None,
        logical_model_call_id=f"logicalcall_{uuid4().hex}",
        attempt_number=1,
    )
    with storage.transaction(actor="test-setup") as unit:
        unit.state.release_lease(lease)

    resume_model = FakeScriptedModel((final_answer("must not run"),))
    result = Harness(
        config_path=config_path,
        model=resume_model,
        token_counter=FixedTokenCounter(),
    ).run(RunRequest(mode=RunMode.RESUME, run_id=run.run_id))

    assert result.status == RunStatus.BLOCKED
    assert result.error_code == ErrorCode.MODEL_CALL_UNKNOWN.value
    assert resume_model.calls == []
    calls, _audit = _model_calls(config_path, run.run_id, workspace)
    assert calls[0].status == ModelCallStatus.UNKNOWN_RESULT
