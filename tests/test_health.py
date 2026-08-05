"""Health endpoint tests."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from notifly.main import create_app


async def test_liveness(client) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness(client) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["service"] == "notifly"
    assert body["version"]


async def test_readiness_fails_when_database_unreachable(test_settings) -> None:
    settings = test_settings.model_copy(
        update={"database_url": "sqlite+aiosqlite:///./nonexistent_dir_zzz/notifly.db"}
    )
    application = create_app(settings)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as ac,
    ):
        response = await ac.get("/health/ready")
    assert response.status_code == 503
