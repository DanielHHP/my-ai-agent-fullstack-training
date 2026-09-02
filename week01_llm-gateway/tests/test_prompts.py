from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import GatewayError
from app.schemas import PromptCreate, UnifiedMessage, UnifiedRequest
from app.services.gateway import GatewayService
from app.services.prompts import PromptRepository


@pytest.mark.asyncio
async def test_prompt_repository_creates_and_resolves_versions(tmp_path: Path) -> None:
    repo = PromptRepository(str(tmp_path / "prompts.db"))
    await repo.initialize()

    first = await repo.create_version(
        PromptCreate(
            id="reviewer",
            name="Reviewer",
            content="Review {{language}} code.",
            description="review prompt",
            role="system",
        )
    )
    second = await repo.create_version(
        PromptCreate(
            id="reviewer",
            name="Reviewer v2",
            content="Strictly review {{language}} code.",
            role="system",
        )
    )

    assert first.version == 1
    assert second.version == 2

    active = await repo.get("reviewer")
    assert active.version == 2
    assert active.is_active is True
    assert (await repo.get("reviewer", 1)).version == 1

    records = await repo.list()
    assert [record.version for record in records] == [2, 1]

    _, rendered = await repo.render("reviewer", {"language": "Python"})
    assert rendered == "Strictly review Python code."


@pytest.mark.asyncio
async def test_prompt_repository_reports_missing_variable(tmp_path: Path) -> None:
    repo = PromptRepository(str(tmp_path / "prompts.db"))
    await repo.initialize()
    await repo.create_version(
        PromptCreate(
            id="reviewer",
            name="Reviewer",
            content="Review {{language}} code.",
            role="system",
        )
    )

    with pytest.raises(GatewayError) as exc_info:
        await repo.render("reviewer", {})

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "prompt_render_error"


@pytest.mark.asyncio
async def test_prompt_repository_reports_invalid_template(tmp_path: Path) -> None:
    repo = PromptRepository(str(tmp_path / "prompts.db"))
    await repo.initialize()
    await repo.create_version(
        PromptCreate(
            id="broken",
            name="Broken",
            content="{% if %}broken",
            role="system",
        )
    )

    with pytest.raises(GatewayError) as exc_info:
        await repo.render("broken", {})

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "prompt_render_error"


def _request(protocol: str, messages: list[UnifiedMessage], **overrides: object) -> UnifiedRequest:
    data: dict[str, object] = {
        "model": "demo",
        "protocol": protocol,
        "messages": messages,
        "stream": False,
    }
    data.update(overrides)
    return UnifiedRequest.model_validate(data)


def test_apply_prompt_prepends_chat_message() -> None:
    request = _request(
        "chat_completions",
        [UnifiedMessage(role="user", content="hello")],
    )

    updated = GatewayService._apply_prompt(request, "system", "be concise", "prepend")

    assert [message.role for message in updated.messages] == ["system", "user"]
    assert updated.messages[0].content == "be concise"


def test_apply_prompt_writes_responses_instructions_before_existing() -> None:
    request = _request(
        "openai_responses",
        [UnifiedMessage(role="user", content="hello")],
        instructions="existing instructions",
    )

    updated = GatewayService._apply_prompt(request, "system", "rendered prompt", "prepend")

    assert updated.instructions == "rendered prompt\n\nexisting instructions"


def test_apply_prompt_prepends_anthropic_system_text_block() -> None:
    request = _request(
        "anthropic_messages",
        [
            UnifiedMessage(role="system", content=[{"type": "text", "text": "old"}]),
            UnifiedMessage(role="user", content="hello"),
        ],
        max_tokens=32,
    )

    updated = GatewayService._apply_prompt(request, "system", "rendered", "prepend")

    system = updated.messages[0].content
    assert isinstance(system, list)
    assert system[0] == {"type": "text", "text": "rendered"}
    assert system[1] == {"type": "text", "text": "old"}
