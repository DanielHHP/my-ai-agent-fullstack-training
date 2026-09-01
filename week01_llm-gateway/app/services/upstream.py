from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.errors import UpstreamError
from app.schemas import UpstreamRequest


@dataclass
class OpenStream:
    response: httpx.Response
    provider_name: str


class UpstreamClient:
    """Executes protocol-agnostic upstream requests produced by adapters."""

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def request_json(self, request: UpstreamRequest) -> dict[str, Any]:
        try:
            response = await self.client.post(
                request.url,
                headers=request.headers,
                json=request.body,
                timeout=request.timeout,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise UpstreamError(
                f"{request.provider}: {type(exc).__name__}",
                retryable=True,
            ) from exc
        if response.is_error:
            raise self._http_error(request.provider, response)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise UpstreamError(
                f"{request.provider}: upstream returned invalid JSON",
            ) from exc

    async def open_stream(self, request: UpstreamRequest) -> OpenStream:
        upstream_request = self.client.build_request(
            "POST",
            request.url,
            headers={**request.headers, "Accept": "text/event-stream"},
            json=request.body,
            timeout=request.timeout,
        )
        try:
            response = await self.client.send(upstream_request, stream=True)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise UpstreamError(
                f"{request.provider}: {type(exc).__name__}",
                retryable=True,
            ) from exc
        if response.is_error:
            await response.aread()
            error = self._http_error(request.provider, response)
            await response.aclose()
            raise error
        return OpenStream(response=response, provider_name=request.provider)

    def _http_error(self, provider: str, response: httpx.Response) -> UpstreamError:
        message = f"{provider}: HTTP {response.status_code}"
        details: Any = None
        try:
            details = response.json()
            if isinstance(details, dict):
                upstream_message = details.get("error", {}).get("message")
                if upstream_message:
                    message = f"{message}: {upstream_message}"
        except (json.JSONDecodeError, AttributeError):
            details = response.text[:1000]
        status_code = response.status_code if 400 <= response.status_code < 500 else 502
        return UpstreamError(
            message,
            status_code=status_code,
            retryable=response.status_code in {408, 409, 429, 500, 502, 503, 504},
            details=details,
        )
