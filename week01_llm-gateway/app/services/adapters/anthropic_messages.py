from __future__ import annotations

import json
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
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    cached_tokens = int(usage.get("cache_read_input_tokens", 0) or 0) + int(
        usage.get("cache_creation_input_tokens", 0) or 0
    )
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )


def _extract_text_blocks(content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content


class AnthropicMessagesAdapter(ProtocolAdapter):
    name = "messages"
    protocol = "anthropic_messages"

    def build_request(
        self,
        *,
        target: RouteTarget,
        request: UnifiedRequest,
        request_id: str,
        provider: ProviderConfig,
    ) -> UpstreamRequest:
        if request.max_tokens is None:
            raise GatewayError(
                "max_tokens is required for Anthropic Messages API",
                status_code=422,
                error_type="invalid_request_error",
                code="missing_required_parameter",
                param="max_tokens",
            )

        system: str | list[dict[str, Any]] | None = None
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role == "system":
                system = _extract_text_blocks(message.content)
                continue
            if message.role not in {"user", "assistant"}:
                raise GatewayError(
                    f"Anthropic Messages API does not support role {message.role!r}",
                    status_code=422,
                    error_type="invalid_request_error",
                    code="invalid_message_role",
                )
            messages.append(
                {"role": message.role, "content": _extract_text_blocks(message.content)}
            )

        body: dict[str, Any] = {
            "model": target.model,
            "messages": messages,
            "stream": request.stream,
            "max_tokens": request.max_tokens,
        }
        if system is not None:
            body["system"] = system
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.response_format is not None:
            instruction = (
                "Return only valid JSON that conforms to the following JSON Schema. "
                "Do not wrap the JSON in Markdown fences or add commentary.\n"
                f"Schema: {json.dumps(request.response_format.schema, ensure_ascii=False)}"
            )
            if isinstance(system, list):
                system = [{"type": "text", "text": instruction}, *system]
            elif isinstance(system, str):
                system = f"{instruction}\n\n{system}"
            else:
                system = instruction
            body["system"] = system
        if request.metadata:
            body["metadata"] = request.metadata

        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            "anthropic-version": "2023-06-01",
            **provider.extra_headers,
        }
        api_key = provider.api_key.get_secret_value()
        if api_key:
            headers["x-api-key"] = api_key
        return self._json_request(
            provider=target.provider,
            url=f"{provider.base_url}/v1/messages",
            headers=headers,
            body=body,
            stream=request.stream,
            timeout=self._timeout(provider),
        )

    def parse_response(self, raw: dict[str, Any]) -> UnifiedResponse:
        content = ""
        blocks = raw.get("content") or []
        if isinstance(blocks, str):
            content = blocks
        else:
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    content = str(block.get("text", ""))
                    break
        return UnifiedResponse(
            id=str(raw.get("id", "")),
            protocol=self.protocol,
            model=str(raw.get("model", "")),
            content_text=content,
            usage=self.normalize_usage(raw),
            raw=raw,
        )

    def parse_stream_event(self, payload: dict[str, Any]) -> UnifiedStreamEvent | None:
        event_type = payload.get("type")
        if event_type == "content_block_delta":
            delta = payload.get("delta") or {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                return UnifiedStreamEvent(
                    type="text_delta",
                    delta=delta["text"],
                    raw=payload,
                )
            return None
        if event_type == "message_start":
            message = payload.get("message") or {}
            return UnifiedStreamEvent(
                type="usage",
                usage=self.normalize_usage({"usage": message.get("usage", {})}),
                raw=payload,
            )
        if event_type == "message_delta":
            return UnifiedStreamEvent(
                type="usage",
                usage=self.normalize_usage({"usage": payload.get("usage", {})}),
                raw=payload,
            )
        if event_type == "message_stop":
            return UnifiedStreamEvent(type="done", raw=payload)
        return None

    def normalize_usage(self, raw: dict[str, Any]) -> NormalizedUsage:
        return _normalize_usage(raw)

    def map_error(
        self,
        *,
        status_code: int,
        raw: dict[str, Any] | str,
    ) -> GatewayError:
        message = "Upstream Anthropic Messages request failed"
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
