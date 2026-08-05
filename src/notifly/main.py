"""NotiFly FastAPI application factory (composition root)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from notifly import __version__
from notifly.config import Settings, get_settings
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.logging import configure_logging
from notifly.presentation.api.routers import health


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.json_logs)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.settings = settings
        app.state.version = __version__
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="NotiFly",
        summary="Channel-agnostic notification orchestration platform.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.include_router(health.router)
    return app


app = create_app()
