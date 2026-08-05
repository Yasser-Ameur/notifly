"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from notifly.config import Environment, Settings
from notifly.main import create_app


@pytest.fixture()
def test_settings(tmp_path) -> Settings:
    return Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        redis_url="redis://localhost:6379/15",
    )


@pytest.fixture()
async def app(test_settings) -> AsyncIterator:
    from notifly.infrastructure.db.base import Base

    application = create_app(test_settings)
    async with application.router.lifespan_context(application):
        async with application.state.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield application


@pytest.fixture()
async def client(app) -> AsyncIterator:
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
