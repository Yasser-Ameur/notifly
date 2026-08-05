"""Infrastructure and observability unit tests (M0 coverage)."""

from __future__ import annotations

import logging

from notifly.config import Settings
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.logging import (
    configure_logging,
    get_correlation_id,
    set_correlation_id,
)
from notifly.presentation.api.deps import get_session_factory


def test_create_engine_sqlite(test_settings) -> None:
    engine = create_engine(test_settings)
    assert engine.dialect.name == "sqlite"
    assert engine.url.drivername == "sqlite+aiosqlite"


def test_create_engine_postgres(test_settings) -> None:
    settings = test_settings.model_copy(
        update={"database_url": "postgresql+asyncpg://user:pass@localhost:5432/db"}
    )
    engine = create_engine(settings)
    assert engine.dialect.name == "postgresql"


def test_create_session_factory_returns_sessions(test_settings) -> None:
    engine = create_engine(test_settings)
    factory = create_session_factory(engine)
    assert callable(factory)


def test_get_session_factory_returns_state_value(app) -> None:
    from fastapi import Request

    request = Request(
        scope={
            "type": "http",
            "app": app,
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [],
            "client": None,
            "server": None,
            "scheme": "http",
            "root_path": "",
        }
    )
    assert get_session_factory(request) is app.state.session_factory


def test_correlation_id_contextvar_roundtrip() -> None:
    assert get_correlation_id() is None
    set_correlation_id("corr-123")
    assert get_correlation_id() == "corr-123"
    set_correlation_id(None)


def test_configure_logging_text() -> None:
    configure_logging(level="INFO", json_logs=False)
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert len(root.handlers) == 1


def test_configure_logging_json() -> None:
    configure_logging(level="DEBUG", json_logs=True)
    root = logging.getLogger()
    assert root.level == logging.DEBUG


def test_settings_production_flag() -> None:
    dev = Settings()
    prod = Settings(environment="production")
    assert dev.is_production is False
    assert prod.is_production is True
