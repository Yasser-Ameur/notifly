"""Health and metrics endpoints: liveness, readiness, Prometheus exposition."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from notifly.presentation.api.deps import DbSession

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Process liveness: always returns 200 while the process is alive."""
    return {"status": "ok"}


@router.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics in the text exposition format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


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
