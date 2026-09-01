from __future__ import annotations

from app.core.errors import GatewayError
from app.services.adapters.anthropic_messages import AnthropicMessagesAdapter
from app.services.adapters.base import ProtocolAdapter
from app.services.adapters.openai_chat import OpenAIChatAdapter
from app.services.adapters.openai_responses import OpenAIResponsesAdapter

_ADAPTERS: dict[str, ProtocolAdapter] = {
    OpenAIChatAdapter.name: OpenAIChatAdapter(),
    OpenAIResponsesAdapter.name: OpenAIResponsesAdapter(),
    AnthropicMessagesAdapter.name: AnthropicMessagesAdapter(),
}


def get_adapter(protocol: str) -> ProtocolAdapter:
    try:
        return _ADAPTERS[protocol]
    except KeyError as exc:
        raise GatewayError(
            f"Unsupported protocol: {protocol!r}",
            status_code=400,
            error_type="invalid_request_error",
            code="unsupported_protocol",
        ) from exc


__all__ = [
    "AnthropicMessagesAdapter",
    "OpenAIChatAdapter",
    "OpenAIResponsesAdapter",
    "ProtocolAdapter",
    "get_adapter",
]
