from __future__ import annotations

import json
from abc import ABC, abstractmethod
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


class ProtocolAdapter(ABC):
    name: str
    protocol: str

    @abstractmethod
    def build_request(
        self,
        *,
        target: RouteTarget,
        request: UnifiedRequest,
        request_id: str,
        provider: ProviderConfig,
    ) -> UpstreamRequest:
        raise NotImplementedError

    @abstractmethod
    def parse_response(self, raw: dict[str, Any]) -> UnifiedResponse:
        raise NotImplementedError

    @abstractmethod
    def parse_stream_event(self, payload: dict[str, Any]) -> UnifiedStreamEvent | None:
        raise NotImplementedError

    def parse_stream_line(self, line: str) -> UnifiedStreamEvent | None:
        stripped = line.strip()
        if not stripped:
            return None
        if stripped.startswith("data:"):
            data = stripped[5:].strip()
            if data == "[DONE]":
                return UnifiedStreamEvent(type="done", raw={"line": stripped})
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as exc:
                return UnifiedStreamEvent(
                    type="error",
                    error={"message": f"Invalid SSE JSON: {exc}"},
                    raw={"line": stripped},
                )
            return self.parse_stream_event(payload)
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return self.parse_stream_event(payload)

    @abstractmethod
    def normalize_usage(self, raw: dict[str, Any]) -> NormalizedUsage:
        raise NotImplementedError

    @abstractmethod
    def map_error(
        self,
        *,
        status_code: int,
        raw: dict[str, Any] | str,
    ) -> GatewayError:
        raise NotImplementedError

    @abstractmethod
    def apply_structured_output(
        self,
        request: UnifiedRequest,
        spec: StructuredOutputSpec,
    ) -> UnifiedRequest:
        raise NotImplementedError

    @staticmethod
    def _timeout(provider: ProviderConfig) -> Any:
        import httpx

        return httpx.Timeout(
            timeout=provider.timeout_seconds,
            connect=provider.connect_timeout_seconds,
        )

    @staticmethod
    def _json_request(
        *,
        provider: str,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        stream: bool,
        timeout: Any,
    ) -> UpstreamRequest:
        return UpstreamRequest(
            provider=provider,
            url=url,
            headers=headers,
            body=body,
            stream=stream,
            timeout=timeout,
        )

    def _auth_headers(self, provider: ProviderConfig, request_id: str) -> dict[str, str]:
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Request-ID": request_id,
            **provider.extra_headers,
        }
        api_key = provider.api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
