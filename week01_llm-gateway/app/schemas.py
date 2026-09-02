from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProtocolName = Literal["chat_completions", "openai_responses", "anthropic_messages"]
RouteProtocolName = Literal["chat", "responses", "messages"]


class UnifiedMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]]
    name: str | None = None
    tool_call_id: str | None = None


class StructuredOutputSpec(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    schema: dict[str, Any]
    name: str | None = None
    strict: bool = False


class PromptReference(BaseModel):
    id: str
    version: int | None = Field(default=None, ge=1)
    variables: dict[str, Any] = Field(default_factory=dict)
    position: Literal["prepend", "append"] = "prepend"


class UnifiedRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    protocol: ProtocolName
    messages: list[UnifiedMessage]
    instructions: str | None = None
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    response_format: StructuredOutputSpec | None = None
    prompt_ref: PromptReference | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NormalizedUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class UnifiedResponse(BaseModel):
    id: str
    protocol: ProtocolName
    model: str
    content_text: str
    usage: NormalizedUsage
    raw: dict[str, Any]


class UnifiedStreamEvent(BaseModel):
    type: Literal["text_delta", "usage", "done", "error"]
    delta: str | None = None
    usage: NormalizedUsage | None = None
    error: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


class UpstreamRequest(BaseModel):
    provider: str
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    stream: bool
    timeout: Any
