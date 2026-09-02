from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app import __version__
from app.api.routes import router
from app.config import GatewayConfig, load_config
from app.services.gateway import GatewayService
from app.services.router import ModelRouter
from app.services.upstream import UpstreamClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    gateway_config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = gateway_config
        router_service = ModelRouter(gateway_config)
        client = httpx.AsyncClient()
        upstream = UpstreamClient(
            client=client,
            retry_statuses=gateway_config.retry.retry_statuses,
        )
        app.state.router = router_service
        app.state.upstream = upstream
        app.state.gateway = GatewayService(
            gateway_config,
            router_service,
            upstream,
        )
        try:
            yield
        finally:
            await upstream.close()

    app = FastAPI(
        title="Unified LLM Gateway",
        version=__version__,
        description="Multi-protocol LLM gateway for OpenAI Chat Completions, Responses, and Anthropic Messages.",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
