import json as json_lib
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import parse_protocols
from app.core.errors import GatewayError, error_payload
from app.core.security import authenticate
from app.schemas import (
    PromptCreate,
    PromptReference,
    PromptRender,
    StructuredOutputSpec,
    UnifiedMessage,
    UnifiedRequest,
)

router = APIRouter()


async def limited_identity(
    request: Request,
    identity: Annotated[str, Depends(authenticate)],
) -> str:
    await request.app.state.rate_limiter.check(identity)
    return identity


Identity = Annotated[str, Depends(authenticate)]
LimitedIdentity = Annotated[str, Depends(limited_identity)]


def _gateway_error_response(exc: GatewayError) -> JSONResponse:
    return JSONResponse(error_payload(exc), status_code=exc.status_code)


async def _json_payload(request: Request) -> dict:
    try:
        payload = await request.json()
    except (json_lib.JSONDecodeError, UnicodeDecodeError) as exc:
        raise GatewayError(
            "Request body must be valid JSON",
            status_code=400,
            error_type="invalid_request_error",
            code="invalid_json",
            param="body",
        ) from exc

    if not isinstance(payload, dict):
        raise GatewayError(
            "Request body must be a JSON object",
            status_code=400,
            error_type="invalid_request_error",
            code="invalid_json",
            param="body",
        )
    return payload


def _prompt_ref_from_payload(payload: dict) -> PromptReference | None:
    value = payload.get("prompt_ref")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GatewayError(
            "prompt_ref must be an object",
            status_code=422,
            error_type="invalid_request_error",
            code="invalid_request",
            param="prompt_ref",
        )
    return PromptReference.model_validate(value)


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
    return [
        UnifiedMessage.model_validate(item)
        for item in messages
        if isinstance(item, dict)
    ]


def _structured_spec_from_payload(payload: dict) -> StructuredOutputSpec | None:
    value = payload.get("response_format")
    declared = False

    if value is not None:
        declared = True
        if not isinstance(value, dict):
            raise GatewayError(
                "response_format must be an object",
                status_code=422,
                error_type="invalid_request_error",
                code="invalid_request",
                param="response_format",
            )
        if value.get("type") != "json_schema":
            raise GatewayError(
                "Only response_format type 'json_schema' is supported",
                status_code=422,
                error_type="invalid_request_error",
                code="invalid_request",
                param="response_format",
            )
        value = value.get("json_schema") or {}
    else:
        text_value = payload.get("text")
        if isinstance(text_value, dict):
            text_format = text_value.get("format")
            if (
                isinstance(text_format, dict)
                and text_format.get("type") == "json_schema"
            ):
                declared = True
                value = text_format

    if not declared:
        return None

    if not isinstance(value, dict):
        raise GatewayError(
            "Structured output configuration must be an object",
            status_code=422,
            error_type="invalid_request_error",
            code="invalid_request",
            param="response_format",
        )

    schema = value.get("schema")
    if not isinstance(schema, dict):
        raise GatewayError(
            "Structured output requires a JSON Schema",
            status_code=422,
            error_type="invalid_request_error",
            code="invalid_request",
            param="response_format",
        )
    return StructuredOutputSpec(
        schema=schema,
        name=value.get("name"),
        strict=bool(value.get("strict", False)),
    )


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
        response_format=_structured_spec_from_payload(payload),
        prompt_ref=_prompt_ref_from_payload(payload),
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

    return UnifiedRequest(
        model=model,
        protocol="openai_responses",
        messages=messages,
        instructions=payload.get("instructions"),
        stream=bool(payload.get("stream", False)),
        temperature=payload.get("temperature"),
        top_p=payload.get("top_p"),
        max_tokens=payload.get("max_output_tokens", payload.get("max_tokens")),
        response_format=_structured_spec_from_payload(payload),
        prompt_ref=_prompt_ref_from_payload(payload),
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
        response_format=_structured_spec_from_payload(payload),
        prompt_ref=_prompt_ref_from_payload(payload),
        metadata=payload.get("metadata") or {},
    )


async def _model_call(
    request: Request, unified: UnifiedRequest, identity: str | None = None
):
    gateway = request.app.state.gateway
    if unified.stream:
        result = await gateway.stream(
            unified,
            is_disconnected=request.is_disconnected,
            api_key_hash=identity,
            endpoint=request.url.path,
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

    result = await gateway.complete(
        unified,
        api_key_hash=identity,
        endpoint=request.url.path,
    )
    return JSONResponse(result.raw, headers={"X-Request-ID": result.request_id})


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/v1/chat/completions")
async def chat_completions(request: Request, identity: LimitedIdentity):
    try:
        payload = await _json_payload(request)
        unified = _chat_request(payload)
        return await _model_call(request, unified, identity)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.post("/v1/responses")
async def responses(request: Request, identity: LimitedIdentity):
    try:
        payload = await _json_payload(request)
        unified = _responses_request(payload)
        return await _model_call(request, unified, identity)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.post("/v1/messages")
async def messages(request: Request, identity: LimitedIdentity):
    try:
        payload = await _json_payload(request)
        unified = _messages_request(payload)
        return await _model_call(request, unified, identity)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.post("/v1/prompts", status_code=201)
async def create_prompt(request: Request, _: Identity):
    try:
        payload = await _json_payload(request)
        prompt = PromptCreate.model_validate(payload)
        record = await request.app.state.prompts.create_version(prompt)
        return JSONResponse(record.model_dump(mode="json"), status_code=201)
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.get("/v1/prompts")
async def list_prompts(request: Request, _: Identity):
    try:
        records = await request.app.state.prompts.list()
        return JSONResponse([record.model_dump(mode="json") for record in records])
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.get("/v1/prompts/{prompt_id}")
async def get_prompt(
    prompt_id: str,
    request: Request,
    _: Identity,
    version: int | None = Query(default=None, ge=1),
):
    try:
        record = await request.app.state.prompts.get(prompt_id, version)
        return JSONResponse(record.model_dump(mode="json"))
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.post("/v1/prompts/{prompt_id}/render")
async def render_prompt(prompt_id: str, request: Request, _: Identity):
    try:
        payload = await _json_payload(request)
        render = PromptRender.model_validate(payload)
        prompt, content = await request.app.state.prompts.render(
            prompt_id,
            render.variables,
            render.version,
        )
        return JSONResponse(
            {
                "id": prompt.id,
                "version": prompt.version,
                "role": prompt.role,
                "content": content,
            }
        )
    except GatewayError as exc:
        return _gateway_error_response(exc)


@router.get("/v1/models")
async def list_models(request: Request, _: Identity):
    now = int(time.time())
    data = []
    for alias, model in sorted(request.app.state.config.models.items()):
        protocols = sorted(
            {protocol for route in model.routes for protocol in route.protocols}
        )
        data.append(
            {
                "id": alias,
                "object": "model",
                "created": now,
                "owned_by": "llm-gateway",
                "supported_protocols": protocols,
            }
        )
    return {"object": "list", "data": data}


@router.get("/admin/usage")
async def usage(
    request: Request,
    _: Identity,
    limit: int = Query(default=100, ge=1, le=1000),
):
    return {"data": await request.app.state.usage.recent(limit)}


@router.get("/admin/routes")
async def route_status(request: Request, _: Identity):
    models: dict[str, dict] = {}
    for alias, model in request.app.state.config.models.items():
        model_data = model.model_dump(mode="json")
        for route in model_data["routes"]:
            route["protocols"] = sorted(parse_protocols(route["api"]))
        models[alias] = model_data
    return {
        "models": models,
        "circuits": request.app.state.router.status(),
    }


@router.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    required = (
        "config",
        "prompts",
        "router",
        "upstream",
        "usage",
        "rate_limiter",
        "gateway",
    )
    ready = all(getattr(request.app.state, name, None) is not None for name in required)
    return {"status": "ok" if ready else "starting"}
