from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import __version__
from app.api.routes import router
from app.config import GatewayConfig, load_config
from app.core.errors import GatewayError, error_payload, gateway_error_handler
from app.core.rate_limit import InMemoryRateLimiter
from app.services.gateway import GatewayService
from app.services.prompts import PromptRepository
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient
from app.services.usage import UsageRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(
    config: GatewayConfig | None = None,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    gateway_config = config or load_config()
    owns_client = http_client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = gateway_config
        db_path = Path(gateway_config.database_url)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        prompts = PromptRepository(gateway_config.database_url)
        await prompts.initialize()
        usage = UsageRepository(gateway_config.database_url, gateway_config)
        await usage.initialize()
        router_service = ModelRouter(gateway_config)
        client = http_client or httpx.AsyncClient()
        upstream = UpstreamClient(
            client=client,
            retry_statuses=gateway_config.retry.retry_statuses,
        )
        app.state.prompts = prompts
        app.state.router = router_service
        app.state.upstream = upstream
        app.state.usage = usage
        app.state.rate_limiter = InMemoryRateLimiter(gateway_config.rate_limit)
        app.state.gateway = GatewayService(
            gateway_config,
            router_service,
            upstream,
            prompts,
            usage,
        )
        try:
            yield
        finally:
            await upstream.close()
            if owns_client:
                await client.aclose()

    app = FastAPI(
        title="Unified LLM Gateway",
        version=__version__,
        description="Multi-protocol LLM gateway for OpenAI Chat Completions, Responses, and Anthropic Messages.",
        lifespan=lifespan,
    )
    app.add_exception_handler(GatewayError, gateway_error_handler)

    @app.exception_handler(ValidationError)
    async def validation_error_handler(
        request,
        exc: ValidationError,
    ) -> JSONResponse:
        del request
        first = exc.errors()[0] if exc.errors() else {}
        loc = first.get("loc", [])
        param = str(loc[-1]) if loc else None
        message = str(first.get("msg", "invalid request"))
        error = GatewayError(
            f"Request validation failed: {message}",
            status_code=422,
            error_type="invalid_request_error",
            code="invalid_request",
            param=param,
        )
        return JSONResponse(error_payload(error), status_code=error.status_code)

    app.include_router(router)
    return app


app = create_app()
