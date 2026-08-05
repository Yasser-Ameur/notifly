"""Health check endpoints: liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import text

from notifly.presentation.api.deps import DbSession

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Process liveness: always returns 200 while the process is alive."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request, session: DbSession) -> dict[str, str]:
    """Readiness: verifies the database is reachable before routing traffic."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unreachable",
        ) from exc
    return {
        "status": "ready",
        "service": "notifly",
        "version": request.app.state.version,
    }
