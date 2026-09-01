from __future__ import annotations

from typing import Any

from app.config import ProviderConfig, RouteTarget
from app.core.errors import GatewayError
from app.schemas import (
    NormalizedUsage,
    StructuredOutputSpec,
    UnifiedRequest,
    UnifiedResponse,
    UnifiedStreamEvent,
    UpstreamRequest,
)
from app.services.adapters.base import ProtocolAdapter


def _normalize_usage(payload: dict[str, Any]) -> NormalizedUsage:
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    output_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    details = usage.get("prompt_tokens_details", usage.get("input_tokens_details", {})) or {}
    cached_tokens = int(details.get("cached_tokens", 0) or 0)
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )


def _content_from_message_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


class OpenAIChatAdapter(ProtocolAdapter):
    name = "chat"
    protocol = "chat_completions"

    def build_request(
        self,
        *,
        target: RouteTarget,
        request: UnifiedRequest,
        request_id: str,
        provider: ProviderConfig,
    ) -> UpstreamRequest:
        body: dict[str, Any] = {
            "model": target.model,
            "messages": [
                message.model_dump(exclude_none=True) for message in request.messages
            ],
            "stream": request.stream,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            spec = request.response_format
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": spec.name or "structured_output",
                    "strict": spec.strict,
                    "schema": spec.schema,
                },
            }
        if request.metadata:
            body["metadata"] = request.metadata
        return self._json_request(
            provider=target.provider,
            url=f"{provider.base_url}/v1/chat/completions",
            headers=self._auth_headers(provider, request_id),
            body=body,
            stream=request.stream,
            timeout=self._timeout(provider),
        )

    def parse_response(self, raw: dict[str, Any]) -> UnifiedResponse:
        content = ""
        try:
            content = _content_from_message_content(
                raw["choices"][0]["message"]["content"]
            )
        except (KeyError, IndexError, TypeError):
            content = ""
        return UnifiedResponse(
            id=str(raw.get("id", "")),
            protocol=self.protocol,
            model=str(raw.get("model", "")),
            content_text=content,
            usage=self.normalize_usage(raw),
            raw=raw,
        )

    def parse_stream_event(self, payload: dict[str, Any]) -> UnifiedStreamEvent | None:
        delta = ""
        choices = payload.get("choices") or []
        if choices:
            candidate = (choices[0].get("delta") or {}).get("content")
            if isinstance(candidate, str):
                delta = candidate
        usage = payload.get("usage")
        if usage:
            return UnifiedStreamEvent(
                type="usage" if not delta else "text_delta",
                delta=delta or None,
                usage=self.normalize_usage(payload),
                raw=payload,
            )
        if delta:
            return UnifiedStreamEvent(type="text_delta", delta=delta, raw=payload)
        return None

    def normalize_usage(self, raw: dict[str, Any]) -> NormalizedUsage:
        return _normalize_usage(raw)

    def map_error(
        self,
        *,
        status_code: int,
        raw: dict[str, Any] | str,
    ) -> GatewayError:
        message = "Upstream OpenAI Chat Completions request failed"
        details: Any = raw
        if isinstance(raw, dict):
            message = str(raw.get("error", {}).get("message", message))
            details = raw.get("error", raw)
        if status_code == 401:
            return GatewayError(
                message,
                status_code=status_code,
                error_type="authentication_error",
                code="invalid_api_key",
                details=details,
            )
        if status_code == 429:
            return GatewayError(
                message,
                status_code=status_code,
                error_type="rate_limit_error",
                code="rate_limit_exceeded",
                details=details,
            )
        if status_code in {400, 404, 422}:
            return GatewayError(
                message,
                status_code=status_code,
                error_type="invalid_request_error",
                code="invalid_request",
                details=details,
            )
        return GatewayError(
            message,
            status_code=status_code,
            error_type="upstream_error",
            code="upstream_error",
            details=details,
        )

    def apply_structured_output(
        self,
        request: UnifiedRequest,
        spec: StructuredOutputSpec,
    ) -> UnifiedRequest:
        return request.model_copy(update={"response_format": spec})
