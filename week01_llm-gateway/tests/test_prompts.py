from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.config import GatewayConfig
from app.core.errors import GatewayError
from app.schemas import PromptCreate, PromptReference, UnifiedMessage, UnifiedRequest
from app.services.gateway import GatewayService
from app.services.prompts import PromptRepository
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient


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


@pytest.mark.asyncio
async def test_prompt_repository_reports_unknown_prompt_id(tmp_path: Path) -> None:
    repo = PromptRepository(str(tmp_path / "prompts.db"))
    await repo.initialize()

    with pytest.raises(GatewayError) as exc_info:
        await repo.get("missing")

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "prompt_not_found"


def _gateway_config(database_url: str) -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "database_url": database_url,
            "retry": {
                "max_retries_per_route": 0,
                "base_delay_seconds": 0,
                "max_delay_seconds": 0,
            },
            "providers": {
                "openai": {
                    "base_url": "https://openai.test",
                    "api_key": "openai-key",
                },
                "anthropic": {
                    "base_url": "https://anthropic.test",
                    "api_key": "anthropic-key",
                },
            },
            "models": {
                "smart": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "openai",
                            "model": "gpt-acceptance",
                            "api": "chat,responses",
                        }
                    ],
                },
                "claude-fast": {
                    "strategy": "priority",
                    "routes": [
                        {
                            "provider": "anthropic",
                            "model": "claude-acceptance",
                            "api": "messages",
                        }
                    ],
                },
            },
        }
    )


@pytest.mark.asyncio
async def test_prompt_ref_injects_into_all_three_protocols(tmp_path: Path) -> None:
    database_path = str(tmp_path / "gateway.db")
    config = _gateway_config(database_path)
    prompts = PromptRepository(database_path)
    await prompts.initialize()
    await prompts.create_version(
        PromptCreate(
            id="reviewer",
            name="Reviewer",
            content="Review {{language}} code.",
            role="system",
        )
    )

    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "path": request.url.path,
                "body": json.loads(request.content),
            }
        )
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl_1",
                    "object": "chat.completion",
                    "model": "gpt-acceptance",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )
        if request.url.path == "/v1/responses":
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "object": "response",
                    "status": "completed",
                    "model": "gpt-acceptance",
                    "output_text": "ok",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-acceptance",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        service = GatewayService(
            config,
            ModelRouter(config),
            UpstreamClient(client=client, retry_statuses=config.retry.retry_statuses),
            prompts=prompts,
        )
        prompt_ref = PromptReference(
            id="reviewer",
            variables={"language": "Python"},
        )

        await service.complete(
            UnifiedRequest(
                model="smart",
                protocol="chat_completions",
                messages=[UnifiedMessage(role="user", content="hello")],
                prompt_ref=prompt_ref,
            )
        )
        await service.complete(
            UnifiedRequest(
                model="smart",
                protocol="openai_responses",
                messages=[UnifiedMessage(role="user", content="hello")],
                instructions="existing instructions",
                prompt_ref=prompt_ref,
            )
        )
        await service.complete(
            UnifiedRequest(
                model="claude-fast",
                protocol="anthropic_messages",
                messages=[UnifiedMessage(role="user", content="hello")],
                max_tokens=32,
                prompt_ref=prompt_ref,
            )
        )

        chat_body = captured[0]["body"]
        responses_body = captured[1]["body"]
        messages_body = captured[2]["body"]

        assert chat_body["messages"][0]["content"] == "Review Python code."
        assert responses_body["instructions"].startswith("Review Python code.")
        assert "existing instructions" in responses_body["instructions"]
        assert messages_body["system"][0]["text"] == "Review Python code."
    finally:
        await client.aclose()
