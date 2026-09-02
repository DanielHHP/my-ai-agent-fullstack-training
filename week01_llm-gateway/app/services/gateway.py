from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import GatewayConfig, RouteTarget
from app.core.errors import GatewayError, UpstreamError, error_payload
from app.schemas import NormalizedUsage, UnifiedRequest, UnifiedStreamEvent
from app.services.adapters import ProtocolAdapter, get_adapter
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient

_ROUTE_PROTOCOLS = {
    "chat_completions": "chat",
    "openai_responses": "responses",
    "anthropic_messages": "messages",
}


@dataclass
class CallMetrics:
    retries: int = 0
    fallbacks: int = 0
    first_token_ms: float | None = None
    latency_ms: float | None = None
    status: str | None = None
    status_code: int | None = None
    provider: str | None = None
    upstream_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    error_type: str | None = None
    error_message: str | None = None


@dataclass
class CompletionResult:
    raw: dict[str, Any]
    request_id: str
    metrics: CallMetrics


@dataclass
class StreamResult:
    stream: AsyncIterator[bytes]
    request_id: str
    metrics: CallMetrics


class GatewayService:
    """Orchestrates non-streaming and streaming calls across protocol adapters."""

    def __init__(
        self,
        config: GatewayConfig,
        router: ModelRouter,
        upstream: UpstreamClient,
    ) -> None:
        self.config = config
        self.router = router
        self.upstream = upstream

    def _route_protocol(self, request: UnifiedRequest) -> str:
        try:
            return _ROUTE_PROTOCOLS[request.protocol]
        except KeyError as exc:
            raise GatewayError(
                f"Unsupported protocol: {request.protocol!r}",
                status_code=400,
                error_type="invalid_request_error",
                code="unsupported_protocol",
            ) from exc

    async def complete(
        self,
        request: UnifiedRequest,
        *,
        request_id: str | None = None,
    ) -> CompletionResult:
        """Execute a non-streaming model call with retry and same-protocol fallback."""
        call_id = request_id or f"req_{uuid.uuid4().hex}"
        protocol = self._route_protocol(request)
        adapter = get_adapter(protocol)
        candidates = self.router.candidates(request.model, protocol)
        working_request = request.model_copy(update={"stream": False})

        metrics = CallMetrics()
        started = time.perf_counter()
        last_error: UpstreamError | None = None

        try:
            for route_index, target in enumerate(candidates):
                upstream_request = adapter.build_request(
                    target=target,
                    request=working_request,
                    request_id=call_id,
                    provider=self.config.providers[target.provider],
                )
                attempts = 0
                while True:
                    try:
                        raw = await self.upstream.request_json(upstream_request)
                    except UpstreamError as exc:
                        last_error = exc
                        self.router.record_failure(target.provider)
                        if exc.retryable and attempts < self.config.retry.max_retries_per_route:
                            metrics.retries += 1
                            await self._backoff(attempts)
                            attempts += 1
                            continue
                        break

                    self.router.record_success(target.provider)
                    metrics.fallbacks = route_index
                    metrics.provider = target.provider
                    metrics.upstream_model = target.model
                    metrics.status = "success"
                    metrics.status_code = 200
                    self._capture_usage(adapter, raw, metrics)
                    return CompletionResult(
                        raw=raw,
                        request_id=call_id,
                        metrics=metrics,
                    )

            metrics.fallbacks = max(0, len(candidates) - 1)
            raise self._final_error(adapter, last_error)
        finally:
            metrics.latency_ms = round((time.perf_counter() - started) * 1000, 3)

    async def stream(
        self,
        request: UnifiedRequest,
        *,
        request_id: str | None = None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> StreamResult:
        """Return an SSE byte iterator with retry/fallback before any bytes are emitted."""
        call_id = request_id or f"req_{uuid.uuid4().hex}"
        protocol = self._route_protocol(request)
        adapter = get_adapter(protocol)
        candidates = self.router.candidates(request.model, protocol)
        working_request = request.model_copy(update={"stream": True})

        # Validate the first route synchronously so invalid request parameters surface
        # as normal JSON errors instead of a stream that starts and then emits an error.
        first_target = candidates[0]
        adapter.build_request(
            target=first_target,
            request=working_request,
            request_id=call_id,
            provider=self.config.providers[first_target.provider],
        )

        metrics = CallMetrics()

        async def generate() -> AsyncIterator[bytes]:
            started = time.perf_counter()
            emitted = False
            last_error: UpstreamError | None = None
            parse_buffer = ""

            try:
                for route_index, target in enumerate(candidates):
                    upstream_request = adapter.build_request(
                        target=target,
                        request=working_request,
                        request_id=call_id,
                        provider=self.config.providers[target.provider],
                    )
                    attempts = 0
                    while True:
                        opened = None
                        try:
                            opened = await self.upstream.open_stream(upstream_request)
                        except UpstreamError as exc:
                            last_error = exc
                            self.router.record_failure(target.provider)
                            if (
                                exc.retryable
                                and attempts < self.config.retry.max_retries_per_route
                            ):
                                metrics.retries += 1
                                await self._backoff(attempts)
                                attempts += 1
                                continue
                            break

                        metrics.provider = target.provider
                        metrics.upstream_model = target.model
                        metrics.fallbacks = route_index
                        try:
                            async for chunk in opened.response.aiter_bytes():
                                if is_disconnected is not None and await is_disconnected():
                                    metrics.status = "cancelled"
                                    metrics.status_code = 499
                                    return

                                if chunk:
                                    emitted = True
                                    parse_buffer = self._consume_stream_chunk(
                                        adapter,
                                        parse_buffer,
                                        chunk,
                                        metrics,
                                        started,
                                    )
                                    yield chunk

                            self._flush_stream_buffer(adapter, parse_buffer, metrics, started)
                            self.router.record_success(target.provider)
                            metrics.status = "success"
                            metrics.status_code = 200
                            return
                        except (UpstreamError, httpx.HTTPError) as exc:
                            upstream_error = self._stream_error(target, exc)
                            last_error = upstream_error
                            self.router.record_failure(target.provider)

                            if emitted:
                                metrics.status = "error"
                                metrics.status_code = 502
                                metrics.error_type = "stream_error"
                                metrics.error_message = upstream_error.message
                                yield self._sse_error(upstream_error)
                                yield b"data: [DONE]\n\n"
                                return

                            if (
                                upstream_error.retryable
                                and attempts < self.config.retry.max_retries_per_route
                            ):
                                metrics.retries += 1
                                await self._backoff(attempts)
                                attempts += 1
                                continue
                            break
                        finally:
                            if opened is not None:
                                await opened.response.aclose()

                metrics.fallbacks = max(0, len(candidates) - 1)
                error = self._final_error(adapter, last_error)
                metrics.status = "error"
                metrics.status_code = error.status_code
                metrics.error_type = error.error_type
                metrics.error_message = error.message
                yield self._sse_error(error)
                yield b"data: [DONE]\n\n"
            except asyncio.CancelledError:
                metrics.status = "cancelled"
                metrics.status_code = 499
                raise
            except GatewayError as exc:
                metrics.status = "error"
                metrics.status_code = exc.status_code
                metrics.error_type = exc.error_type
                metrics.error_message = exc.message
                yield self._sse_error(exc)
                yield b"data: [DONE]\n\n"
            finally:
                metrics.latency_ms = round((time.perf_counter() - started) * 1000, 3)

        return StreamResult(stream=generate(), request_id=call_id, metrics=metrics)

    async def _backoff(self, attempt: int) -> None:
        base = self.config.retry.base_delay_seconds * (2**attempt)
        delay = min(base, self.config.retry.max_delay_seconds)
        await asyncio.sleep(delay * random.uniform(0.75, 1.25))

    def _capture_usage(
        self,
        adapter: ProtocolAdapter,
        raw: dict[str, Any],
        metrics: CallMetrics,
    ) -> None:
        try:
            unified = adapter.parse_response(raw)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return
        self._merge_usage(metrics, unified.usage)

    def _consume_stream_chunk(
        self,
        adapter: ProtocolAdapter,
        buffer: str,
        chunk: bytes,
        metrics: CallMetrics,
        started: float,
    ) -> str:
        buffer += chunk.decode("utf-8", errors="ignore")
        lines = buffer.split("\n")
        buffer = lines.pop()
        for line in lines:
            event = adapter.parse_stream_line(line)
            self._apply_stream_event(metrics, event, started)
        return buffer

    def _flush_stream_buffer(
        self,
        adapter: ProtocolAdapter,
        buffer: str,
        metrics: CallMetrics,
        started: float,
    ) -> None:
        if buffer.strip():
            event = adapter.parse_stream_line(buffer)
            self._apply_stream_event(metrics, event, started)

    def _apply_stream_event(
        self,
        metrics: CallMetrics,
        event: UnifiedStreamEvent | None,
        started: float,
    ) -> None:
        if event is None:
            return
        if event.type == "text_delta" and metrics.first_token_ms is None:
            metrics.first_token_ms = round((time.perf_counter() - started) * 1000, 3)
        if event.usage is not None:
            self._merge_usage(metrics, event.usage)

    @staticmethod
    def _merge_usage(metrics: CallMetrics, usage: NormalizedUsage) -> None:
        if usage.input_tokens:
            metrics.input_tokens = usage.input_tokens
        if usage.output_tokens:
            metrics.output_tokens = usage.output_tokens
        if usage.cached_tokens:
            metrics.cached_tokens = usage.cached_tokens

    @staticmethod
    def _stream_error(target: RouteTarget, exc: Exception) -> UpstreamError:
        if isinstance(exc, UpstreamError):
            return exc
        return UpstreamError(
            f"{target.provider}: stream interrupted: {type(exc).__name__}",
            retryable=False,
        )

    @staticmethod
    def _final_error(
        adapter: ProtocolAdapter,
        last_error: UpstreamError | None,
    ) -> GatewayError:
        if last_error is None:
            return GatewayError("All upstream routes failed", status_code=502)
        raw: dict[str, Any] | str = last_error.details
        if raw is None:
            raw = last_error.message
        return adapter.map_error(status_code=last_error.status_code, raw=raw)

    @staticmethod
    def _sse_error(error: GatewayError) -> bytes:
        return f"data: {json.dumps(error_payload(error), ensure_ascii=False)}\n\n".encode()
