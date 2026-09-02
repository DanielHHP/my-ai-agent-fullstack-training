from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.errors import GatewayError, error_payload
from app.schemas import UnifiedMessage, UnifiedRequest

router = APIRouter()


def _gateway_error_response(exc: GatewayError) -> JSONResponse:
    return JSONResponse(error_payload(exc), status_code=exc.status_code)


def _unified_messages_from_openai(payload: dict) -> list[UnifiedMessage]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise GatewayError(
            "messages must be a non-empty list",
            status_code=422,
            error_type="invalid_request_error",
            code="missing_required_parameter",
            param="messages",
        )
    return [UnifiedMessage.model_validate(item) for item in messages if isinstance(item, dict)]


def _chat_request(payload: dict) -> UnifiedRequest:
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise GatewayError(
            "model is required",
            status_code=422,
            error_type="invalid_request_error",
            code="missing_required_parameter",
            param="model",
        )
    messages = _unified_messages_from_openai(payload)
    return UnifiedRequest(
        model=model,
        protocol="chat_completions",
        messages=messages,
        stream=bool(payload.get("stream", False)),
        temperature=payload.get("temperature"),
        top_p=payload.get("top_p"),
        max_tokens=payload.get("max_tokens"),
        metadata=payload.get("metadata") or {},
    )


def _responses_request(payload: dict) -> UnifiedRequest:
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise GatewayError(
            "model is required",
            status_code=422,
            error_type="invalid_request_error",
            code="missing_required_parameter",
            param="model",
        )

    input_value = payload.get("input")
    messages: list[UnifiedMessage] = []
    if isinstance(input_value, str):
        messages.append(UnifiedMessage(role="user", content=input_value))
    elif isinstance(input_value, list):
        for item in input_value:
            if not isinstance(item, dict):
                raise GatewayError(
                    "input list items must be objects",
                    status_code=422,
                    error_type="invalid_request_error",
                    code="invalid_request",
                    param="input",
                )
            role = item.get("role", "user")
            content = item.get("content", "")
            messages.append(UnifiedMessage(role=role, content=content))
    else:
        raise GatewayError(
            "input must be a string or message list",
            status_code=422,
            error_type="invalid_request_error",
            code="invalid_request",
            param="input",
        )

    if isinstance(payload.get("instructions"), str):
        messages.insert(0, UnifiedMessage(role="system", content=payload["instructions"]))

    return UnifiedRequest(
        model=model,
        protocol="openai_responses",
        messages=messages,
        stream=bool(payload.get("stream", False)),
        temperature=payload.get("temperature"),
        top_p=payload.get("top_p"),
        max_tokens=payload.get("max_output_tokens", payload.get("max_tokens")),
        metadata=payload.get("metadata") or {},
    )


def _messages_request(payload: dict) -> UnifiedRequest:
    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise GatewayError(
            "model is required",
            status_code=422,
            error_type="invalid_request_error",
            code="missing_required_parameter",
            param="model",
        )

    messages: list[UnifiedMessage] = []
    system = payload.get("system")
    if isinstance(system, (str, list)):
        messages.append(UnifiedMessage(role="system", content=system))

    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise GatewayError(
            "messages must be a non-empty list",
            status_code=422,
            error_type="invalid_request_error",
            code="missing_required_parameter",
            param="messages",
        )
    for item in raw_messages:
        if not isinstance(item, dict):
            raise GatewayError(
                "messages items must be objects",
                status_code=422,
                error_type="invalid_request_error",
                code="invalid_request",
                param="messages",
            )
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"}:
            raise GatewayError(
                f"Anthropic Messages API does not support role {role!r}",
                status_code=422,
                error_type="invalid_request_error",
                code="invalid_message_role",
            )
        messages.append(UnifiedMessage(role=role, content=content))

    return UnifiedRequest(
        model=model,
        protocol="anthropic_messages",
        messages=messages,
        stream=bool(payload.get("stream", False)),
        temperature=payload.get("temperature"),
        top_p=payload.get("top_p"),
        max_tokens=payload.get("max_tokens"),
        metadata=payload.get("metadata") or {},
    )


async def _model_call(request: Request, unified: UnifiedRequest):
    gateway = request.app.state.gateway
    if unified.stream:
        result = await gateway.stream(
            unified,
            is_disconnected=request.is_disconnected,
        )
        return StreamingResponse(
            result.stream,
            media_type="text/event-stream",
            headers={
                "X-Request-ID": result.request_id,
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    result = await gateway.complete(unified)
    return JSONResponse(result.raw, headers={"X-Request-ID": result.request_id})


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
        unified = _chat_request(payload)
        return await _model_call(request, unified)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.post("/v1/responses")
async def responses(request: Request):
    try:
        payload = await request.json()
        unified = _responses_request(payload)
        return await _model_call(request, unified)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.post("/v1/messages")
async def messages(request: Request):
    try:
        payload = await request.json()
        unified = _messages_request(payload)
        return await _model_call(request, unified)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    config_loaded = getattr(request.app.state, "config", None) is not None
    return {"status": "ok" if config_loaded else "starting"}
