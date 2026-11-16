"""Human-readable rendering for the bounded inspection projection."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

_ANSI_ESCAPE_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_DEFAULT_DETAIL_LIMIT = 240


def render_inspection_text(data: Mapping[str, Any]) -> str:
    """Render one inspection projection as a stable hierarchical Turn view."""

    run = _mapping(data.get("run"))
    steps = sorted(
        _mappings(data.get("steps")),
        key=lambda item: (_integer(item.get("ordinal")), _text(item.get("step_id"))),
    )
    invocations = _mappings(data.get("invocations"))
    model_calls = _mappings(data.get("model_calls"))
    approvals = _mappings(data.get("approvals"))
    audit = _mapping(data.get("audit"))
    events = sorted(
        _mappings(audit.get("events")),
        key=lambda item: _integer(item.get("sequence")),
    )

    step_ids = {_text(step.get("step_id")) for step in steps}
    invocation_by_tool = {
        _text(item.get("tool_call_id")): item
        for item in invocations
        if _text(item.get("tool_call_id"))
    }
    approval_by_id = {
        _text(item.get("approval_id")): item for item in approvals if _text(item.get("approval_id"))
    }
    model_call_by_id = {
        _text(item.get("model_call_id")): item
        for item in model_calls
        if _text(item.get("model_call_id"))
    }
    invocation_by_step = _group_by(invocations, "step_id")
    model_calls_by_step = _group_by(model_calls, "step_id")
    approvals_by_step = _approvals_by_step(invocations, approvals)

    events_by_step: dict[str, list[Mapping[str, Any]]] = {step_id: [] for step_id in step_ids}
    run_events: list[Mapping[str, Any]] = []
    for event in events:
        step_id = _event_step_id(
            event,
            step_ids=step_ids,
            invocation_by_tool=invocation_by_tool,
            approval_by_id=approval_by_id,
            model_call_by_id=model_call_by_id,
        )
        if step_id is None:
            run_events.append(event)
        else:
            events_by_step.setdefault(step_id, []).append(event)

    root_children: list[list[str]] = [
        [f"trace {_text(run.get('trace_id'), '(missing)')}"],
        [
            f"task {_text(run.get('task_id'), '(missing)')} "
            f"({_text(run.get('task_kind'), 'unknown')})"
        ],
        [
            f"acceptance {_text(run.get('acceptance_status'), 'NOT_EVALUATED')}  "
            f"stop={_text(run.get('stop_reason'), '-')}"
        ],
        [f"workspace {_workspace_path(run)}"],
        [_budget_line(data)],
        [f"last checkpoint {_text(run.get('last_checkpoint_id'), '-')}"],
        [_approval_summary_line(approvals)],
    ]

    turn_blocks = [
        _turn_block(
            index=index,
            total=len(steps),
            step=step,
            model_calls=model_calls_by_step.get(_text(step.get("step_id")), ()),
            invocations=invocation_by_step.get(_text(step.get("step_id")), ()),
            approvals=approvals_by_step.get(_text(step.get("step_id")), ()),
            events=events_by_step.get(_text(step.get("step_id")), ()),
            audit_truncated=bool(audit.get("truncated")),
        )
        for index, step in enumerate(steps, start=1)
    ]
    root_children.append(_section("Turns", turn_blocks))

    operation_children = [_event_line(event) for event in run_events]
    if not operation_children:
        operation_children = ["(no visible run-level operations)"]
    root_children.append(_section("Run operations", operation_children, children_are_blocks=False))

    root_children.append(_completion_section(data.get("completion")))
    root_children.append(
        [
            "Audit chain "
            f"valid={'yes' if bool(audit.get('valid')) else 'no'}  "
            f"events={_integer(audit.get('event_count'))}  "
            f"shown={len(events)}  "
            f"truncated={'yes' if bool(audit.get('truncated')) else 'no'}  "
            f"head=#{_integer(audit.get('head_sequence'))}"
        ]
    )

    lines = [f"Run {_text(run.get('run_id'), '(missing)')}  {_text(run.get('status'), 'UNKNOWN')}"]
    for index, child_block in enumerate(root_children):
        lines.extend(
            _prefix_block(
                child_block,
                first_prefix="└─ " if index == len(root_children) - 1 else "├─ ",
                rest_prefix="   " if index == len(root_children) - 1 else "│  ",
            )
        )
    return "\n".join(lines) + "\n"


def _turn_block(
    *,
    index: int,
    total: int,
    step: Mapping[str, Any],
    model_calls: Sequence[Mapping[str, Any]],
    invocations: Sequence[Mapping[str, Any]],
    approvals: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    audit_truncated: bool,
) -> list[str]:
    step_id = _text(step.get("step_id"))
    header = (
        f"Turn {index}/{total}  [{_text(step.get('status'), 'UNKNOWN')}]  "
        f"{_one_line(step.get('summary'), fallback='(no summary)')}"
    )
    children: list[str] = []
    for item in model_calls:
        children.append(_current_model_call_line(item))
    for item in invocations:
        children.append(_current_invocation_line(item))
    for item in approvals:
        children.append(_current_approval_line(item))
    children.extend(_event_line(event) for event in events)

    if audit_truncated and not children:
        children.append("state details are outside the visible Audit window")
    if step_id and not children:
        children.append("(no model, tool, harness, or Audit operation recorded)")

    return [header, *children]


def _section(
    title: str,
    children: Sequence[list[str] | str],
    *,
    children_are_blocks: bool = True,
) -> list[str]:
    lines = [title]
    if not children:
        lines.append("└─ (none recorded)")
        return lines
    for index, child in enumerate(children):
        first_prefix = "└─ " if index == len(children) - 1 else "├─ "
        rest_prefix = "   " if index == len(children) - 1 else "│  "
        block = child if isinstance(child, list) and children_are_blocks else [str(child)]
        lines.extend(_prefix_block(block, first_prefix=first_prefix, rest_prefix=rest_prefix))
    return lines


def _prefix_block(block: Sequence[str], *, first_prefix: str, rest_prefix: str) -> list[str]:
    if not block:
        return []
    return [first_prefix + block[0], *(rest_prefix + line for line in block[1:])]


def _current_model_call_line(item: Mapping[str, Any]) -> str:
    usage = ""
    if item.get("input_tokens") is not None or item.get("output_tokens") is not None:
        input_tokens = _text(item.get("input_tokens"), "?")
        output_tokens = _text(item.get("output_tokens"), "?")
        usage = f"  tokens={input_tokens}/{output_tokens}"
    cost = item.get("actual_cost")
    if cost is None:
        cost = item.get("reservation_cost")
    return _one_line(
        "current  model  "
        f"{_text(item.get('provider'), '?')}/{_text(item.get('model_id'), '?')}  "
        f"{_text(item.get('status'), 'UNKNOWN')}  "
        f"call={_text(item.get('model_call_id'), '?')}  "
        f"attempt={_text(item.get('attempt_number'), '?')}"
        f"{usage}  cost={_text(cost, '-')}"
    )


def _current_invocation_line(item: Mapping[str, Any]) -> str:
    result = _one_line(item.get("result_summary"))
    error = _text(item.get("error_code"))
    approval = _text(item.get("approval_id"))
    if approval:
        approval = f"  approval={approval}"
    error_text = f"  error={error}" if error else ""
    result_text = f"  result={result}" if result else ""
    return _one_line(
        "current  tool  "
        f"{_text(item.get('tool_name'), '?')}  {_text(item.get('status'), 'UNKNOWN')}  "
        f"call={_text(item.get('tool_call_id'), '?')}  "
        f"risk={_text(item.get('risk'), '?')}"
        f"{approval}{error_text}{result_text}"
    )


def _current_approval_line(item: Mapping[str, Any]) -> str:
    roles = _list_text(item.get("allowed_approver_roles"), fallback="none")
    return _one_line(
        "current  approval  "
        f"{_text(item.get('approval_id'), '?')}  "
        f"{_text(item.get('effective_status'), _text(item.get('status'), 'UNKNOWN'))}  "
        f"tool={_text(item.get('tool_name'), '?')}  "
        f"call={_text(item.get('tool_call_id'), '?')}  "
        f"expires={_text(item.get('expires_at'), '-')}  roles={roles}"
    )


def _event_line(event: Mapping[str, Any]) -> str:
    event_type = _text(event.get("event_type"), "UNKNOWN_EVENT")
    parts = [
        _event_category(event_type),
        f"#{_integer(event.get('sequence'))}",
        event_type,
        _text(event.get("status"), "UNKNOWN"),
    ]
    decision = _text(event.get("decision"))
    if decision and decision != "NOT_APPLICABLE":
        parts.append(decision)

    known_labels: set[str] = set()

    def add(label: str, value: object) -> None:
        rendered = _value_text(value)
        if rendered and label not in known_labels:
            parts.append(f"{label}={rendered}")
            known_labels.add(label)

    add("actor", event.get("actor"))
    add("tool", event.get("tool_name"))
    add("call", event.get("tool_call_id"))
    add("approval", event.get("approval_id"))
    add("model_call", event.get("model_call_id"))
    add("rule", event.get("rule_id"))
    add("code", event.get("code"))

    input_summary = _mapping(event.get("input_summary"))
    output_summary = _mapping(event.get("output_summary"))
    if event_type == "TOOL_PREPARED":
        add("args", input_summary.get("argument_keys", input_summary.get("args")))
    elif event_type == "APPROVAL_REQUESTED":
        add("roles", input_summary.get("allowed_roles"))
        add("ttl", input_summary.get("ttl_seconds"))
    elif event_type == "CHECKPOINT_WRITTEN":
        add("boundary", input_summary.get("boundary_kind"))
        add("ref", input_summary.get("boundary_ref"))
        add("checkpoint", input_summary.get("checkpoint_id"))
    elif event_type == "RECOVERY_RECORDED":
        add("recovery", input_summary.get("recovery_id"))
        add("checkpoint", input_summary.get("checkpoint_id"))
        add("action", input_summary.get("action"))
    elif event_type == "MODEL_CALL_STARTED":
        add("provider", input_summary.get("provider"))
        add("model", input_summary.get("model_id"))
        add("attempt", input_summary.get("attempt"))
        add("reserved", input_summary.get("reservation_cost"))
    elif event_type == "MODEL_CALL_SUCCEEDED":
        add("input_tokens", input_summary.get("input_tokens"))
        add("output_tokens", input_summary.get("output_tokens"))
        add("cost", input_summary.get("actual_cost"))
    elif event_type == "BUDGET_UPDATED":
        add("category", input_summary.get("category"))
        add("delta", input_summary.get("delta"))
        add("source", input_summary.get("source_id"))
    elif event_type in {"TOOL_SUCCEEDED", "TOOL_FAILED", "TOOL_TIMED_OUT", "TOOL_UNKNOWN_RESULT"}:
        add("result", output_summary.get("summary"))
        add("evidence", output_summary.get("evidence_ids"))
    elif event_type == "EVIDENCE_RECORDED":
        add("kind", input_summary.get("kind"))
        add("path", input_summary.get("path"))
    elif event_type == "STEP_PLANNED":
        add("summary", input_summary.get("summary"))
    elif event_type.endswith("_FAILED") or event_type.endswith("_UNKNOWN"):
        add("reason", output_summary.get("reason_code"))
        add("provider_request", output_summary.get("provider_request_id"))

    return _one_line("  ".join(parts))


def _event_category(event_type: str) -> str:
    if event_type == "TOOL_PREPARED":
        return "response"
    if event_type.startswith("MODEL_"):
        return "model"
    if event_type.startswith("APPROVAL_"):
        return "approval"
    if event_type == "GATE_DECISION":
        return "gate"
    if event_type.startswith(("TOOL_", "EVIDENCE_")):
        return "tool"
    if event_type.startswith("CHECKPOINT_"):
        return "checkpoint"
    if event_type.startswith("RECOVERY_"):
        return "recovery"
    if event_type.startswith("BUDGET_"):
        return "budget"
    if event_type.startswith("COMPLETION_"):
        return "completion"
    return "harness"


def _event_step_id(
    event: Mapping[str, Any],
    *,
    step_ids: set[str],
    invocation_by_tool: Mapping[str, Mapping[str, Any]],
    approval_by_id: Mapping[str, Mapping[str, Any]],
    model_call_by_id: Mapping[str, Mapping[str, Any]],
) -> str | None:
    direct = _text(event.get("step_id"))
    if direct in step_ids:
        return direct

    tool_call_id = _text(event.get("tool_call_id"))
    invocation = invocation_by_tool.get(tool_call_id)
    if invocation is not None:
        step_id = _text(invocation.get("step_id"))
        if step_id in step_ids:
            return step_id

    approval = approval_by_id.get(_text(event.get("approval_id")))
    if approval is not None:
        invocation = invocation_by_tool.get(_text(approval.get("tool_call_id")))
        if invocation is not None:
            step_id = _text(invocation.get("step_id"))
            if step_id in step_ids:
                return step_id

    model_call = model_call_by_id.get(_text(event.get("model_call_id")))
    if model_call is not None:
        step_id = _text(model_call.get("step_id"))
        if step_id in step_ids:
            return step_id

    input_summary = _mapping(event.get("input_summary"))
    boundary_ref = _text(input_summary.get("boundary_ref"))
    if boundary_ref.startswith("step:") and boundary_ref[5:] in step_ids:
        return boundary_ref[5:]
    if boundary_ref.startswith("approval-request:"):
        invocation = invocation_by_tool.get(boundary_ref.removeprefix("approval-request:"))
        if invocation is not None:
            step_id = _text(invocation.get("step_id"))
            if step_id in step_ids:
                return step_id
    if boundary_ref in invocation_by_tool:
        step_id = _text(invocation_by_tool[boundary_ref].get("step_id"))
        if step_id in step_ids:
            return step_id

    source_id = _text(input_summary.get("source_id"))
    invocation = invocation_by_tool.get(source_id)
    if invocation is not None:
        step_id = _text(invocation.get("step_id"))
        if step_id in step_ids:
            return step_id
    for step_id in step_ids:
        if source_id == step_id or source_id.endswith(f":{step_id}"):
            return step_id
    return None


def _approvals_by_step(
    invocations: Sequence[Mapping[str, Any]],
    approvals: Sequence[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    step_by_tool = {
        _text(item.get("tool_call_id")): _text(item.get("step_id")) for item in invocations
    }
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for approval in approvals:
        step_id = step_by_tool.get(_text(approval.get("tool_call_id")))
        if step_id:
            grouped.setdefault(step_id, []).append(approval)
    return grouped


def _group_by(
    items: Sequence[Mapping[str, Any]],
    key: str,
) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in items:
        value = _text(item.get(key))
        if value:
            grouped.setdefault(value, []).append(item)
    return grouped


def _budget_line(data: Mapping[str, Any]) -> str:
    budget = _mapping(data.get("budget"))
    usage = _mapping(data.get("usage"))
    return (
        "budget "
        f"steps={_text(usage.get('steps'), '0')}/{_text(budget.get('max_steps'), '?')}  "
        f"tool_calls={_text(usage.get('tool_calls'), '0')}/"
        f"{_text(budget.get('max_tool_calls'), '?')}  "
        f"token_cost={_text(usage.get('token_cost'), '0')}/"
        f"{_text(budget.get('max_token_cost'), '?')}  "
        f"wall={_text(usage.get('wall_time_seconds'), '0')}/"
        f"{_text(budget.get('max_wall_time_seconds'), '?')}s"
    )


def _workspace_path(run: Mapping[str, Any]) -> str:
    workspace = _mapping(run.get("workspace"))
    return _text(workspace.get("workspace_root"), "(unknown)")


def _approval_summary_line(approvals: Sequence[Mapping[str, Any]]) -> str:
    if not approvals:
        return "approvals 0"
    counts: dict[str, int] = {}
    for approval in approvals:
        status = _text(
            approval.get("effective_status"),
            _text(approval.get("status"), "UNKNOWN"),
        )
        counts[status] = counts.get(status, 0) + 1
    rendered = ", ".join(f"{status}:{counts[status]}" for status in sorted(counts))
    return f"approvals {len(approvals)}  {rendered}"


def _completion_section(value: object) -> list[str]:
    completion = _mapping(value)
    if not completion:
        return ["Completion", "└─ (not recorded)"]
    details = [
        "  "
        f"acceptance={_text(completion.get('acceptance_status'), 'UNKNOWN')}  "
        f"checked_at={_text(completion.get('checked_at'), '-')}"
    ]
    items = _mappings(completion.get("items"))
    for item in items:
        details.append(
            f"{_text(item.get('item_id'), '?')}  "
            f"{'PASS' if bool(item.get('passed')) else 'FAIL'}  "
            f"{_text(item.get('code'), '?')}  "
            f"{_one_line(item.get('message'), fallback='(no message)')}"
        )
    failure = _one_line(completion.get("failure_summary"))
    if failure:
        details.append(f"failure {failure}")
    lines = ["Completion"]
    for index, detail in enumerate(details):
        prefix = "└─ " if index == len(details) - 1 else "├─ "
        lines.append(prefix + detail.lstrip())
    return lines


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _text(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    rendered = _one_line(value)
    return rendered or fallback


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _value_text(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(_one_line(item) for item in value if _one_line(item))
    if isinstance(value, Mapping):
        return ""
    return _one_line(value)


def _list_text(value: object, *, fallback: str) -> str:
    rendered = _value_text(value)
    return rendered or fallback


def _one_line(value: object, *, fallback: str = "", limit: int = _DEFAULT_DETAIL_LIMIT) -> str:
    if value is None:
        return fallback
    raw = str(value).replace("\r\n", "\n").replace("\r", "\n")
    without_ansi = _ANSI_ESCAPE_PATTERN.sub("", raw)
    cleaned = "".join(
        character if not unicodedata.category(character).startswith("C") else " "
        for character in without_ansi
    )
    normalized = " ".join(cleaned.split())
    if len(normalized) <= limit:
        return normalized or fallback
    return f"{normalized[: limit - 3]}..."


__all__ = ["render_inspection_text"]
