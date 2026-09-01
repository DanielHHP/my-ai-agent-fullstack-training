from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.routes import router
from app.config import GatewayConfig, load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    gateway_config = config or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.config = gateway_config
        yield

    app = FastAPI(
        title="Unified LLM Gateway",
        version=__version__,
        description="Multi-protocol LLM gateway for OpenAI Chat Completions, Responses, and Anthropic Messages.",
        lifespan=lifespan,
    )
    app.include_router(router)
    return app


app = create_app()
