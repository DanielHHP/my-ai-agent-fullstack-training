"""Governed model-provider adaptation and model-call lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from smolagents import Model
from smolagents.models import ChatMessage
from smolagents.monitoring import TokenUsage

from .config import ModelConfig, ResolvedHarnessConfig
from .domain import (
    Clock,
    ErrorCode,
    ModelCallRecord,
    ModelCallStatus,
)
from .state_store import StateStoreError
from .storage import SQLiteStorage, StorageError

_PROVIDER = "openai_chat_completions_v1"
_CREDENTIAL_ALLOWLIST = {
    "MODEL_API_KEY",
    "OPENAI_API_KEY",
    "GUARDED_HARNESS_MODEL_API_KEY",
}
_TOKENIZER_ALLOWLIST = {
    "tiktoken:o200k_base",
    "tiktoken:cl100k_base",
    "tiktoken:p50k_base",
}
_MAX_ERROR_TEXT = 1000


class ModelRuntimeError(RuntimeError):
    """Stable fail-closed model provider or call failure."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        unknown: bool = False,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message[:_MAX_ERROR_TEXT]
        self.unknown = unknown
        self.retryable = retryable
        super().__init__(f"{code.value}: {self.message}")


class SecretResolver(Protocol):
    """Resolve a non-secret credential reference to a bounded secret."""

    def __call__(self, credential_ref: str) -> str:
        """Return the secret value or raise ModelRuntimeError."""


class TokenCounter(Protocol):
    """Count or reject model input before a network request."""

    def count_messages(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None,
    ) -> int:
        """Return the input token count."""


class EnvironmentSecretResolver:
    """Read only explicitly allowlisted model credential environment variables."""

    def __call__(self, credential_ref: str) -> str:
        if credential_ref not in _CREDENTIAL_ALLOWLIST:
            raise ModelRuntimeError(
                ErrorCode.MODEL_CREDENTIAL_UNAVAILABLE,
                "credential reference is not allowlisted",
            )
        value = os.environ.get(credential_ref)
        if value is None or not value.strip():
            raise ModelRuntimeError(
                ErrorCode.MODEL_CREDENTIAL_UNAVAILABLE,
                f"credential {credential_ref} is unavailable",
            )
        if len(value) > 4096 or "\x00" in value:
            raise ModelRuntimeError(
                ErrorCode.MODEL_CREDENTIAL_UNAVAILABLE,
                "credential value is invalid",
            )
        return value


class TiktokenTokenCounter:
    """Fixed tiktoken-backed input counter."""

    def __init__(self, tokenizer_id: str) -> None:
        if tokenizer_id not in _TOKENIZER_ALLOWLIST:
            raise ModelRuntimeError(
                ErrorCode.MODEL_USAGE_INVALID,
                "tokenizer is not in the fixed registry",
            )
        self.tokenizer_id = tokenizer_id
        try:
            import tiktoken
        except ModuleNotFoundError as exc:
            raise ModelRuntimeError(
                ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
                "tiktoken is not installed",
            ) from exc
        encoding_name = tokenizer_id.split(":", 1)[1]
        try:
            self._encoding = tiktoken.get_encoding(encoding_name)
        except Exception as exc:
            raise ModelRuntimeError(
                ErrorCode.MODEL_USAGE_INVALID,
                "configured tokenizer is unavailable",
            ) from exc

    def count_messages(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None,
    ) -> int:
        serialized_messages = json.dumps(
            messages,
            ensure_ascii=False,
            default=_json_default,
            sort_keys=True,
            separators=(",", ":"),
        )
        serialized_tools = (
            json.dumps(
                tools,
                ensure_ascii=False,
                default=_json_default,
                sort_keys=True,
                separators=(",", ":"),
            )
            if tools
            else ""
        )
        try:
            return len(self._encoding.encode(serialized_messages + serialized_tools))
        except Exception as exc:
            raise ModelRuntimeError(
                ErrorCode.MODEL_USAGE_INVALID,
                "input token counting failed",
            ) from exc


def token_counter_from_config(config: ResolvedHarnessConfig) -> TokenCounter:
    """Build the fixed token counter declared by a version 2 model profile."""

    model = _require_model_config(config)
    return TiktokenTokenCounter(model.tokenizer_id)


def create_model_from_config(
    config: ResolvedHarnessConfig,
    *,
    secret_resolver: SecretResolver | None = None,
    http_client: object | None = None,
) -> Model:
    """Construct the allowlisted OpenAI Chat Completions model."""

    model_config = _require_model_config(config)
    if model_config.provider != _PROVIDER:
        raise ModelRuntimeError(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "configured provider is not supported",
        )
    secret = (secret_resolver or EnvironmentSecretResolver())(model_config.credential_ref)
    try:
        import httpx
        from smolagents import OpenAIServerModel
    except ModuleNotFoundError as exc:
        raise ModelRuntimeError(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "OpenAI Chat Completions SDK dependencies are unavailable",
        ) from exc
    endpoint = _validated_endpoint(model_config)
    timeout = httpx.Timeout(
        model_config.request_timeout_seconds,
        connect=model_config.connect_timeout_seconds,
    )
    try:
        client = http_client or httpx.Client(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        )
        return OpenAIServerModel(
            model_id=model_config.model_id,
            api_base=endpoint,
            api_key=secret,
            retry=False,
            requests_per_minute=model_config.requests_per_minute or None,
            client_kwargs={
                "max_retries": 0,
                "http_client": client,
            },
        )
    except ModelRuntimeError:
        raise
    except Exception as exc:
        raise ModelRuntimeError(
            ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
            "OpenAI Chat Completions client construction failed",
        ) from exc


@dataclass(frozen=True)
class ModelCallContext:
    """One prepared model attempt and its effective request limits."""

    call: ModelCallRecord
    timeout_seconds: float


class ModelCallService:
    """Apply Run, budget, audit, and response policy around one model delegate."""

    def __init__(
        self,
        *,
        storage: SQLiteStorage,
        config: ResolvedHarnessConfig,
        run_id: str,
        lease_token: str,
        token_counter: TokenCounter,
        ensure_step: Callable[[], str],
        clock: Clock | None = None,
    ) -> None:
        self._storage = storage
        self._config = config
        self._model_config = _require_model_config(config)
        self._run_id = run_id
        self._lease_token = lease_token
        self._token_counter = token_counter
        self._ensure_step = ensure_step
        self._clock = clock or storage.clock

    def prepare(
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[object] | None,
        logical_model_call_id: str,
        attempt_number: int,
    ) -> ModelCallContext:
        """Persist STARTED and reservation before any provider request."""

        step_id = self._ensure_step()
        input_tokens = self._token_counter.count_messages(messages, tools)
        if input_tokens > self._model_config.max_input_tokens:
            raise ModelRuntimeError(
                ErrorCode.MODEL_INPUT_TOO_LARGE,
                "model input exceeds max_input_tokens",
            )
        reservation = self._reservation_cost()
        now = self._clock.now()
        call = ModelCallRecord(
            model_call_id=f"modelcall_{uuid4().hex}",
            logical_model_call_id=logical_model_call_id,
            run_id=self._run_id,
            step_id=step_id,
            provider=self._model_config.provider,
            model_id=self._model_config.model_id,
            config_fingerprint=self._config.fingerprint,
            attempt_number=attempt_number,
            status=ModelCallStatus.STARTED,
            started_at=now,
            reservation_cost=reservation,
            request_fingerprint=_request_fingerprint(
                self._model_config,
                messages,
                tools,
            ),
        )
        try:
            with self._storage.transaction(actor="harness") as unit:
                unit.state.create_model_call(call, lease_token=self._lease_token)
                run = unit.state.get_run(self._run_id)
                timeout = self._effective_timeout(run.started_at, now)
        except StateStoreError as exc:
            if exc.code == ErrorCode.STATE_BUDGET_EXCEEDED:
                raise ModelRuntimeError(
                    ErrorCode.GATE_BUDGET_EXHAUSTED,
                    "model call cannot reserve budget",
                ) from exc
            raise ModelRuntimeError(exc.code, exc.message, unknown=True) from exc
        except StorageError as exc:
            raise ModelRuntimeError(exc.code, exc.message, unknown=True) from exc
        return ModelCallContext(call=call, timeout_seconds=timeout)

    def complete(self, context: ModelCallContext, message: ChatMessage) -> Decimal:
        """Validate usage and settle one successful model response."""

        usage = _validated_usage(
            message.token_usage,
            max_input=self._model_config.max_input_tokens,
            max_output=self._model_config.max_output_tokens,
        )
        actual_cost = self._actual_cost(usage)
        request_id = _provider_request_id(message)
        try:
            with self._storage.transaction(actor="harness") as unit:
                unit.state.complete_model_call(
                    context.call.model_call_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    actual_cost=actual_cost,
                    provider_request_id=request_id,
                    lease_token=self._lease_token,
                )
        except (StateStoreError, StorageError) as exc:
            raise ModelRuntimeError(
                ErrorCode.MODEL_CALL_UNKNOWN,
                "model response could not be committed",
                unknown=True,
            ) from exc
        return actual_cost

    def fail(
        self,
        context: ModelCallContext,
        *,
        status: ModelCallStatus,
        code: ErrorCode,
        provider_request_id: str | None = None,
    ) -> None:
        """Persist a terminal failure or unknown outcome."""

        try:
            with self._storage.transaction(actor="harness") as unit:
                unit.state.fail_model_call(
                    context.call.model_call_id,
                    status=status,
                    error_code=code,
                    provider_request_id=provider_request_id,
                    lease_token=self._lease_token,
                )
        except (StateStoreError, StorageError) as exc:
            raise ModelRuntimeError(
                ErrorCode.MODEL_CALL_UNKNOWN,
                "model failure could not be committed",
                unknown=True,
            ) from exc

    def _reservation_cost(self) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(self._model_config.max_input_tokens)
            * self._model_config.pricing.input_per_million
            / million
            + Decimal(self._model_config.max_output_tokens)
            * self._model_config.pricing.output_per_million
            / million
        )

    def _actual_cost(self, usage: TokenUsage) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(usage.input_tokens) * self._model_config.pricing.input_per_million / million
            + Decimal(usage.output_tokens) * self._model_config.pricing.output_per_million / million
        )

    def _effective_timeout(self, started_at: datetime | None, now: datetime) -> float:
        if started_at is None:
            raise ModelRuntimeError(
                ErrorCode.STATE_INVALID_TRANSITION,
                "run has no start time",
            )
        elapsed = max(0.0, (now - started_at).total_seconds())
        remaining = self._config.config.budget.max_wall_time_seconds - elapsed
        if remaining <= 0:
            raise ModelRuntimeError(
                ErrorCode.GATE_BUDGET_EXHAUSTED,
                "model wall-time budget is exhausted",
            )
        return float(min(self._model_config.request_timeout_seconds, remaining))


class GovernedModel(Model):
    """Public `smolagents.Model` wrapper that governs every generate call."""

    def __init__(
        self,
        *,
        delegate: Model,
        service: ModelCallService,
        model_config: ModelConfig,
    ) -> None:
        if not isinstance(delegate, Model):
            raise TypeError("delegate must be a smolagents.Model")
        super().__init__(model_id=model_config.model_id)
        self._delegate = delegate
        self._service = service
        self._model_config = model_config

    def generate(
        self,
        messages: list[ChatMessage],
        stop_sequences: list[str] | None = None,
        response_format: dict[str, str] | None = None,
        tools_to_call_from: list[Any] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        logical_id = f"logicalcall_{uuid4().hex}"
        attempts = self._model_config.max_retry_attempts + 1
        for attempt_number in range(1, attempts + 1):
            context = self._service.prepare(
                messages=messages,
                tools=tools_to_call_from,
                logical_model_call_id=logical_id,
                attempt_number=attempt_number,
            )
            request_kwargs = dict(kwargs)
            request_kwargs.setdefault("max_tokens", self._model_config.max_output_tokens)
            request_kwargs.setdefault("timeout", context.timeout_seconds)
            try:
                response = self._delegate.generate(
                    messages,
                    stop_sequences=stop_sequences,
                    response_format=response_format,
                    tools_to_call_from=tools_to_call_from,
                    **request_kwargs,
                )
            except Exception as exc:
                error = _classify_provider_exception(exc)
                terminal_status = (
                    ModelCallStatus.UNKNOWN_RESULT if error.unknown else ModelCallStatus.FAILED
                )
                self._service.fail(
                    context,
                    status=terminal_status,
                    code=error.code,
                )
                if error.retryable and attempt_number < attempts:
                    continue
                raise error from exc
            try:
                _validate_chat_message(response, self._model_config)
                self._service.complete(context, response)
                return response
            except ModelRuntimeError as exc:
                self._service.fail(
                    context,
                    status=(
                        ModelCallStatus.UNKNOWN_RESULT if exc.unknown else ModelCallStatus.FAILED
                    ),
                    code=exc.code,
                )
                raise
        raise ModelRuntimeError(
            ErrorCode.MODEL_CALL_UNKNOWN,
            "model retry policy exhausted",
            unknown=True,
        )


def build_governed_model(
    *,
    delegate: Model,
    storage: SQLiteStorage,
    config: ResolvedHarnessConfig,
    run_id: str,
    lease_token: str,
    ensure_step: Callable[[], str],
    token_counter: TokenCounter | None = None,
    clock: Clock | None = None,
) -> GovernedModel:
    """Compose a governed model for the current Run."""

    model_config = _require_model_config(config)
    service = ModelCallService(
        storage=storage,
        config=config,
        run_id=run_id,
        lease_token=lease_token,
        token_counter=token_counter or token_counter_from_config(config),
        ensure_step=ensure_step,
        clock=clock,
    )
    return GovernedModel(
        delegate=delegate,
        service=service,
        model_config=model_config,
    )


def _require_model_config(config: ResolvedHarnessConfig) -> ModelConfig:
    if config.config.version != 2 or config.config.model is None:
        raise ModelRuntimeError(
            ErrorCode.MODEL_CONFIG_INVALID,
            "real model runtime requires a version 2 model profile",
        )
    return config.config.model


def _validated_endpoint(config: ModelConfig) -> str:
    parsed = urlsplit(config.endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ModelRuntimeError(ErrorCode.MODEL_ENDPOINT_DENIED, "model endpoint is invalid")
    if parsed.username is not None or parsed.password is not None:
        raise ModelRuntimeError(ErrorCode.MODEL_ENDPOINT_DENIED, "model endpoint has userinfo")
    return config.endpoint.rstrip("/")


def _request_fingerprint(
    config: ModelConfig,
    messages: Sequence[ChatMessage],
    tools: Sequence[object] | None,
) -> str:
    payload = json.dumps(
        {
            "provider": config.provider,
            "model_id": config.model_id,
            "messages": messages,
            "tools": tools,
        },
        ensure_ascii=False,
        default=_json_default,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _validate_chat_message(message: object, config: ModelConfig) -> None:
    if not isinstance(message, ChatMessage):
        raise ModelRuntimeError(
            ErrorCode.MODEL_RESPONSE_INVALID,
            "provider did not return ChatMessage",
        )
    tool_calls = message.tool_calls or []
    if not tool_calls:
        raise ModelRuntimeError(
            ErrorCode.MODEL_PROTOCOL_UNSUPPORTED,
            "provider did not return structured tool calls",
        )
    if len(tool_calls) > config.max_tool_calls_per_response:
        raise ModelRuntimeError(
            ErrorCode.MODEL_TOOL_CALLS_INVALID,
            "provider returned too many tool calls",
        )
    ids: set[str] = set()
    names: list[str] = []
    for call in tool_calls:
        call_id = getattr(call, "id", None)
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        if not isinstance(call_id, str) or not call_id or call_id in ids:
            raise ModelRuntimeError(
                ErrorCode.MODEL_TOOL_CALLS_INVALID,
                "tool call IDs must be non-empty and unique",
            )
        if not isinstance(name, str) or not name:
            raise ModelRuntimeError(
                ErrorCode.MODEL_TOOL_CALLS_INVALID,
                "tool call name is missing",
            )
        ids.add(call_id)
        names.append(name)
    if "final_answer" in names and len(names) > 1:
        raise ModelRuntimeError(
            ErrorCode.MODEL_TOOL_CALLS_INVALID,
            "final_answer cannot be combined with another tool call",
        )
    _validated_usage(
        message.token_usage,
        max_input=config.max_input_tokens,
        max_output=config.max_output_tokens,
    )
    try:
        encoded = json.dumps(
            {
                "content": message.content,
                "tool_calls": tool_calls,
            },
            ensure_ascii=False,
            default=_json_default,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModelRuntimeError(
            ErrorCode.MODEL_RESPONSE_INVALID,
            "model response is not serializable",
        ) from exc
    if len(encoded) > config.max_response_bytes:
        raise ModelRuntimeError(
            ErrorCode.MODEL_RESPONSE_TOO_LARGE,
            "model response exceeds max_response_bytes",
        )


def _validated_usage(
    usage: TokenUsage | None,
    *,
    max_input: int,
    max_output: int,
) -> TokenUsage:
    if usage is None:
        raise ModelRuntimeError(ErrorCode.MODEL_USAGE_INVALID, "token usage is missing")
    if (
        type(usage.input_tokens) is not int
        or type(usage.output_tokens) is not int
        or usage.input_tokens < 0
        or usage.output_tokens < 0
    ):
        raise ModelRuntimeError(ErrorCode.MODEL_USAGE_INVALID, "token usage is invalid")
    if usage.input_tokens > max_input or usage.output_tokens > max_output:
        raise ModelRuntimeError(
            ErrorCode.MODEL_USAGE_INVALID,
            "token usage exceeds configured request limits",
        )
    return usage


def _provider_request_id(message: ChatMessage) -> str | None:
    raw = message.raw
    value = getattr(raw, "id", None)
    if value is None and isinstance(raw, Mapping):
        value = raw.get("id")
    if value is None:
        return None
    text = str(value)
    return text[:256]


def _classify_provider_exception(exc: BaseException) -> ModelRuntimeError:
    text = f"{type(exc).__name__}: {exc}".casefold()
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return ModelRuntimeError(ErrorCode.MODEL_AUTH_FAILED, "model authentication failed")
    if status_code == 404:
        return ModelRuntimeError(ErrorCode.MODEL_NOT_FOUND, "model or endpoint was not found")
    if status_code == 429:
        return ModelRuntimeError(
            ErrorCode.MODEL_RATE_LIMITED,
            "model provider rate limit",
            retryable=True,
        )
    if "timeout" in text or isinstance(exc, TimeoutError):
        return ModelRuntimeError(
            ErrorCode.MODEL_TIMEOUT,
            "model request timed out",
            unknown=True,
        )
    if status_code is not None and int(status_code) >= 500:
        return ModelRuntimeError(
            ErrorCode.MODEL_CALL_UNKNOWN,
            "model provider returned an indeterminate server error",
            unknown=True,
        )
    if "connect" in text and ("connection" in text or "connecterror" in text):
        return ModelRuntimeError(
            ErrorCode.MODEL_RATE_LIMITED,
            "model connection could not be established",
            retryable=True,
        )
    return ModelRuntimeError(
        ErrorCode.MODEL_PROVIDER_UNAVAILABLE,
        "model provider request failed",
    )


def _json_default(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if all(hasattr(value, attribute) for attribute in ("name", "description", "inputs")):
        return {
            "name": value.name,
            "description": value.description,
            "inputs": value.inputs,
        }
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return str(value)


__all__ = [
    "EnvironmentSecretResolver",
    "GovernedModel",
    "ModelCallContext",
    "ModelCallService",
    "ModelRuntimeError",
    "SecretResolver",
    "TiktokenTokenCounter",
    "TokenCounter",
    "build_governed_model",
    "create_model_from_config",
    "token_counter_from_config",
]
