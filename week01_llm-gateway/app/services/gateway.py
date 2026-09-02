from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import GatewayConfig, RouteTarget
from app.core.errors import (
    GatewayError,
    StructuredOutputError,
    UpstreamError,
    error_payload,
)
from app.schemas import (
    NormalizedUsage,
    UnifiedMessage,
    UnifiedRequest,
    UnifiedStreamEvent,
)
from app.services.adapters import ProtocolAdapter, get_adapter
from app.services.prompts import PromptRepository
from app.services.router import ModelRouter
from app.services.structured import repair_instruction, validate_structured_content
from app.services.upstream import UpstreamClient
from app.services.usage import UsageEvent, UsageRepository

logger = logging.getLogger(__name__)

_ROUTE_PROTOCOLS = {
    "chat_completions": "chat",
    "openai_responses": "responses",
    "anthropic_messages": "messages",
}

_DEFAULT_ENDPOINTS = {
    "chat_completions": "/v1/chat/completions",
    "openai_responses": "/v1/responses",
    "anthropic_messages": "/v1/messages",
}


@dataclass
class CallMetrics:
    requested_model: str | None = None
    protocol: str | None = None
    endpoint: str | None = None
    api_key_hash: str = "anonymous"
    stream: bool = False
    retries: int = 0
    fallbacks: int = 0
    repair_retries: int = 0
    first_token_ms: float | None = None
    latency_ms: float | None = None
    status: str | None = None
    status_code: int | None = None
    provider: str | None = None
    upstream_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    error_type: str | None = None
    error_message: str | None = None
    prompt_id: str | None = None
    prompt_version: int | None = None
    retry_input_tokens: int = 0
    retry_output_tokens: int = 0
    retry_cached_tokens: int = 0
    retry_cost_usd: float = 0.0
    retry_upstream_model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


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
        prompts: PromptRepository | None = None,
        usage: UsageRepository | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.upstream = upstream
        self.prompts = prompts
        self.usage = usage

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
        api_key_hash: str | None = None,
        endpoint: str | None = None,
    ) -> CompletionResult:
        """Execute a non-streaming model call with retry and same-protocol fallback."""
        call_id = request_id or f"req_{uuid.uuid4().hex}"
        metrics = CallMetrics(
            requested_model=request.model,
            protocol=request.protocol,
            endpoint=endpoint or _DEFAULT_ENDPOINTS.get(request.protocol),
            api_key_hash=api_key_hash or "anonymous",
            stream=False,
        )
        started = time.perf_counter()
        last_error: UpstreamError | None = None
        structured_attempt = 0

        try:
            protocol = self._route_protocol(request)
            adapter = get_adapter(protocol)
            prepared_request, prompt_id, prompt_version = await self._prepare_request(
                request
            )
            metrics.prompt_id = prompt_id
            metrics.prompt_version = prompt_version
            working_request = prepared_request.model_copy(update={"stream": False})

            while True:
                candidates = self.router.candidates(working_request.model, protocol)
                raw: dict[str, Any] | None = None
                selected_target: RouteTarget | None = None

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
                            if (
                                exc.retryable
                                and attempts < self.config.retry.max_retries_per_route
                            ):
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
                        selected_target = target
                        break

                    if raw is not None:
                        break

                if raw is None or selected_target is None:
                    metrics.fallbacks = max(0, len(candidates) - 1)
                    error = self._final_error(adapter, last_error)
                    self._apply_error_metrics(metrics, error)
                    raise error

                if working_request.response_format is None:
                    return CompletionResult(
                        raw=raw,
                        request_id=call_id,
                        metrics=metrics,
                    )

                try:
                    unified = adapter.parse_response(raw)
                    validate_structured_content(
                        unified.content_text,
                        working_request.response_format.schema,
                    )
                except StructuredOutputError as exc:
                    self._capture_retry_usage(
                        adapter, raw, metrics, selected_target.model
                    )
                    if structured_attempt >= self.config.structured_output_retries:
                        self._apply_error_metrics(metrics, exc)
                        raise
                    structured_attempt += 1
                    metrics.repair_retries += 1
                    working_request = self._request_with_repair(
                        adapter,
                        working_request,
                        raw,
                        repair_instruction(
                            exc,
                            working_request.response_format.schema,
                        ),
                    )
                    last_error = None
                    continue

                return CompletionResult(
                    raw=raw,
                    request_id=call_id,
                    metrics=metrics,
                )
        except GatewayError as exc:
            self._apply_error_metrics(metrics, exc)
            raise
        finally:
            metrics.latency_ms = round((time.perf_counter() - started) * 1000, 3)
            await self._record_usage_safely(call_id, metrics)

    async def stream(
        self,
        request: UnifiedRequest,
        *,
        request_id: str | None = None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
        api_key_hash: str | None = None,
        endpoint: str | None = None,
    ) -> StreamResult:
        """Return an SSE byte iterator with retry/fallback before any bytes are emitted."""
        call_id = request_id or f"req_{uuid.uuid4().hex}"
        metrics = CallMetrics(
            requested_model=request.model,
            protocol=request.protocol,
            endpoint=endpoint or _DEFAULT_ENDPOINTS.get(request.protocol),
            api_key_hash=api_key_hash or "anonymous",
            stream=True,
        )
        started_sync = time.perf_counter()

        try:
            protocol = self._route_protocol(request)
            adapter = get_adapter(protocol)
            prepared_request, prompt_id, prompt_version = await self._prepare_request(
                request
            )
            metrics.prompt_id = prompt_id
            metrics.prompt_version = prompt_version
            candidates = self.router.candidates(prepared_request.model, protocol)
            working_request = prepared_request.model_copy(update={"stream": True})

            # Validate the first route synchronously so invalid request parameters surface
            # as normal JSON errors instead of a stream that starts and then emits an error.
            first_target = candidates[0]
            adapter.build_request(
                target=first_target,
                request=working_request,
                request_id=call_id,
                provider=self.config.providers[first_target.provider],
            )
        except GatewayError as exc:
            metrics.latency_ms = round((time.perf_counter() - started_sync) * 1000, 3)
            self._apply_error_metrics(metrics, exc)
            await self._record_usage_safely(call_id, metrics)
            raise

        async def generate() -> AsyncIterator[bytes]:
            started = time.perf_counter()
            emitted = False
            last_error: UpstreamError | None = None
            parse_buffer = b""

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
                                if (
                                    is_disconnected is not None
                                    and await is_disconnected()
                                ):
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

                            self._flush_stream_buffer(
                                adapter, parse_buffer, metrics, started
                            )
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
                self._apply_error_metrics(metrics, error)
                yield self._sse_error(error)
                yield b"data: [DONE]\n\n"
            except asyncio.CancelledError:
                metrics.status = "cancelled"
                metrics.status_code = 499
                raise
            except GatewayError as exc:
                self._apply_error_metrics(metrics, exc)
                yield self._sse_error(exc)
                yield b"data: [DONE]\n\n"
            finally:
                metrics.latency_ms = round((time.perf_counter() - started) * 1000, 3)
                await self._record_usage_safely(call_id, metrics)

        return StreamResult(stream=generate(), request_id=call_id, metrics=metrics)

    async def _prepare_request(
        self,
        request: UnifiedRequest,
    ) -> tuple[UnifiedRequest, str | None, int | None]:
        if request.prompt_ref is None:
            return request, None, None
        if self.prompts is None:
            raise GatewayError(
                "Prompt repository is not configured",
                status_code=503,
                error_type="gateway_error",
                code="prompt_repository_unavailable",
            )

        prompt_ref = request.prompt_ref
        prompt, rendered = await self.prompts.render(
            prompt_ref.id,
            prompt_ref.variables,
            prompt_ref.version,
        )
        return (
            self._apply_prompt(request, prompt.role, rendered, prompt_ref.position),
            prompt.id,
            prompt.version,
        )

    @staticmethod
    def _apply_prompt(
        request: UnifiedRequest,
        role: str,
        rendered: str,
        position: str,
    ) -> UnifiedRequest:
        if request.protocol == "chat_completions":
            message = UnifiedMessage(role=role, content=rendered)
            messages = list(request.messages)
            if position == "append":
                messages.append(message)
            else:
                messages.insert(0, message)
            return request.model_copy(update={"messages": messages})

        if request.protocol == "openai_responses":
            existing = request.instructions
            instructions = f"{rendered}\n\n{existing}" if existing else rendered
            return request.model_copy(update={"instructions": instructions})

        if request.protocol == "anthropic_messages":
            messages = list(request.messages)
            system_index = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.role == "system"
                ),
                None,
            )
            if system_index is None:
                messages.insert(0, UnifiedMessage(role="system", content=rendered))
            else:
                existing = messages[system_index].content
                if isinstance(existing, str):
                    merged = f"{rendered}\n\n{existing}"
                else:
                    merged = [{"type": "text", "text": rendered}, *existing]
                messages[system_index] = messages[system_index].model_copy(
                    update={"content": merged}
                )
            return request.model_copy(update={"messages": messages})

        return request

    @staticmethod
    def _request_with_repair(
        adapter: ProtocolAdapter,
        request: UnifiedRequest,
        raw: dict[str, Any],
        instruction: str,
    ) -> UnifiedRequest:
        """Append the failed assistant output and a repair instruction to the request."""
        unified = adapter.parse_response(raw)
        previous = unified.content_text
        messages = list(request.messages)
        messages.append(UnifiedMessage(role="assistant", content=previous))
        messages.append(UnifiedMessage(role="user", content=instruction))
        return request.model_copy(update={"messages": messages})

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
        usage = raw.get("usage")
        if usage:
            metrics.metadata["usage"] = usage

    def _capture_retry_usage(
        self,
        adapter: ProtocolAdapter,
        raw: dict[str, Any],
        metrics: CallMetrics,
        upstream_model: str,
    ) -> None:
        try:
            unified = adapter.parse_response(raw)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            return

        metrics.retry_input_tokens += unified.usage.input_tokens
        metrics.retry_output_tokens += unified.usage.output_tokens
        metrics.retry_cached_tokens += unified.usage.cached_tokens
        metrics.retry_upstream_model = upstream_model

        usage = raw.get("usage")
        if usage:
            metrics.metadata.setdefault("retry_usage", []).append(usage)

    @staticmethod
    def _apply_error_metrics(metrics: CallMetrics, error: GatewayError) -> None:
        metrics.status = "error"
        metrics.status_code = error.status_code
        metrics.error_type = error.error_type
        metrics.error_message = error.message

    async def _record_usage_safely(
        self,
        request_id: str,
        metrics: CallMetrics,
    ) -> None:
        if self.usage is None:
            return

        try:
            if metrics.upstream_model:
                metrics.cost_usd = self.usage.calculate_cost(
                    metrics.upstream_model,
                    metrics.input_tokens,
                    metrics.output_tokens,
                    metrics.cached_tokens,
                )

            retry_model = metrics.retry_upstream_model or metrics.upstream_model
            if retry_model and (
                metrics.retry_input_tokens
                or metrics.retry_output_tokens
                or metrics.retry_cached_tokens
            ):
                metrics.retry_cost_usd = self.usage.calculate_cost(
                    retry_model,
                    metrics.retry_input_tokens,
                    metrics.retry_output_tokens,
                    metrics.retry_cached_tokens,
                )

            event = UsageEvent(
                request_id=request_id,
                api_key_hash=metrics.api_key_hash,
                protocol=metrics.protocol or "",
                endpoint=metrics.endpoint or "",
                requested_model=metrics.requested_model or "",
                provider=metrics.provider,
                upstream_model=metrics.upstream_model,
                stream=metrics.stream,
                status=metrics.status or "error",
                status_code=metrics.status_code or 500,
                input_tokens=metrics.input_tokens,
                output_tokens=metrics.output_tokens,
                cached_tokens=metrics.cached_tokens,
                cost_usd=metrics.cost_usd,
                latency_ms=metrics.latency_ms or 0.0,
                first_token_ms=metrics.first_token_ms,
                retries=metrics.retries,
                fallbacks=metrics.fallbacks,
                repair_retries=metrics.repair_retries,
                retry_input_tokens=metrics.retry_input_tokens,
                retry_output_tokens=metrics.retry_output_tokens,
                retry_cached_tokens=metrics.retry_cached_tokens,
                retry_cost_usd=metrics.retry_cost_usd,
                error_type=metrics.error_type,
                error_message=metrics.error_message,
                prompt_id=metrics.prompt_id,
                prompt_version=metrics.prompt_version,
                metadata=metrics.metadata,
            )
            await self.usage.record(event)
        except Exception:
            logger.exception("Failed to persist usage event %s", request_id)

    def _consume_stream_chunk(
        self,
        adapter: ProtocolAdapter,
        buffer: bytes,
        chunk: bytes,
        metrics: CallMetrics,
        started: float,
    ) -> bytes:
        buffer += chunk
        lines = buffer.split(b"\n")
        buffer = lines.pop()
        for line in lines:
            event = adapter.parse_stream_line(line.decode("utf-8", errors="ignore"))
            self._apply_stream_event(metrics, event, started)
        return buffer

    def _flush_stream_buffer(
        self,
        adapter: ProtocolAdapter,
        buffer: bytes,
        metrics: CallMetrics,
        started: float,
    ) -> None:
        if buffer.strip():
            event = adapter.parse_stream_line(buffer.decode("utf-8", errors="ignore"))
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
            if isinstance(event.raw, dict) and event.raw.get("usage"):
                metrics.metadata["usage"] = event.raw["usage"]

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
        retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
        return UpstreamError(
            f"{target.provider}: stream interrupted: {type(exc).__name__}",
            retryable=retryable,
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
        return (
            f"data: {json.dumps(error_payload(error), ensure_ascii=False)}\n\n".encode()
        )
