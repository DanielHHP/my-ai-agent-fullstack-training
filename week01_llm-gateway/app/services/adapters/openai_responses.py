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
    input_tokens = int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
    output_tokens = int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details", {})) or {}
    cached_tokens = int(details.get("cached_tokens", 0) or 0)
    return NormalizedUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )


def _content_from_payload(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for output in payload.get("output", []) or []:
        for item in output.get("content", []) or []:
            if item.get("type") in {"output_text", "text"} and isinstance(
                item.get("text"), str
            ):
                return item["text"]
    return ""


class OpenAIResponsesAdapter(ProtocolAdapter):
    name = "responses"
    protocol = "openai_responses"

    def build_request(
        self,
        *,
        target: RouteTarget,
        request: UnifiedRequest,
        request_id: str,
        provider: ProviderConfig,
    ) -> UpstreamRequest:
        input_value: Any
        if len(request.messages) == 1 and isinstance(
            request.messages[0].content, str
        ) and request.messages[0].role == "user":
            input_value = request.messages[0].content
        else:
            input_value = [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ]

        body: dict[str, Any] = {
            "model": target.model,
            "input": input_value,
            "stream": request.stream,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.top_p is not None:
            body["top_p"] = request.top_p
        if request.max_tokens is not None:
            body["max_output_tokens"] = request.max_tokens
        if request.response_format is not None:
            spec = request.response_format
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": spec.name or "structured_output",
                    "strict": spec.strict,
                    "schema": spec.schema,
                }
            }
        if request.metadata:
            body["metadata"] = request.metadata
        return self._json_request(
            provider=target.provider,
            url=f"{provider.base_url}/v1/responses",
            headers=self._auth_headers(provider, request_id),
            body=body,
            stream=request.stream,
            timeout=self._timeout(provider),
        )

    def parse_response(self, raw: dict[str, Any]) -> UnifiedResponse:
        return UnifiedResponse(
            id=str(raw.get("id", "")),
            protocol=self.protocol,
            model=str(raw.get("model", "")),
            content_text=_content_from_payload(raw),
            usage=self.normalize_usage(raw),
            raw=raw,
        )

    def parse_stream_event(self, payload: dict[str, Any]) -> UnifiedStreamEvent | None:
        event_type = payload.get("type")
        if event_type == "response.output_text.delta":
            delta = payload.get("delta")
            return UnifiedStreamEvent(
                type="text_delta",
                delta=delta if isinstance(delta, str) else None,
                raw=payload,
            )
        if event_type in {"response.completed", "response.output_text.done"}:
            usage_payload = payload.get("response", payload)
            return UnifiedStreamEvent(
                type="usage",
                usage=self.normalize_usage(usage_payload),
                raw=payload,
            )
        return None

    def normalize_usage(self, raw: dict[str, Any]) -> NormalizedUsage:
        return _normalize_usage(raw)

    def map_error(
        self,
        *,
        status_code: int,
        raw: dict[str, Any] | str,
    ) -> GatewayError:
        message = "Upstream OpenAI Responses request failed"
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
