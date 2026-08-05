"""NotiFly FastAPI application factory (composition root)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from notifly import __version__
from notifly.config import Settings, get_settings
from notifly.domain.errors import NotiFlyError
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.logging import configure_logging
from notifly.presentation.api.errors import (
    http_exception_handler,
    notifly_error_handler,
    pydantic_validation_error_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from notifly.presentation.api.middleware import CorrelationIdMiddleware
from notifly.presentation.api.routers import apps, health, templates


def _register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotiFlyError, notifly_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValidationError, pydantic_validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)


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
    app.add_middleware(CorrelationIdMiddleware)
    _register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(apps.router)
    app.include_router(templates.router)
    return app


app = create_app()
