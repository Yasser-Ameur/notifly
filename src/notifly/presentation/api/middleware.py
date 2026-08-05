"""HTTP middleware for correlation ID propagation and request metrics."""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from notifly.domain.ports.metrics import Metrics, NoopMetrics
from notifly.logging import CORRELATION_ID_VAR


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagates ``X-Correlation-ID`` through the request.

    Accepts a client-supplied ID, or generates one, stores it in the logging
    context var, and echoes it back on the response so callers can trace logs.
    """

    HEADER = "x-correlation-id"

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        correlation_id = request.headers.get(self.HEADER) or uuid4().hex
        request.state.correlation_id = correlation_id
        token = CORRELATION_ID_VAR.set(correlation_id)
        try:
            response: Response = await call_next(request)
        finally:
            CORRELATION_ID_VAR.reset(token)
        response.headers[self.HEADER] = correlation_id
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records HTTP request volume and latency into the metrics reporter.

    The ``/health/metrics`` endpoint itself is skipped so a scrape never feeds
    back into the request counters. Unmatched paths are labelled with the literal
    path; matched routes use the route template so label cardinality stays low.
    """

    METRICS_PATHS = ("/health/metrics",)

    def __init__(self, app: Any, *, metrics: Metrics | None = None) -> None:
        super().__init__(app)
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        if request.url.path in self.METRICS_PATHS:
            response: Response = await call_next(request)
            return response
        metrics: Metrics
        if self._metrics is not None:
            metrics = self._metrics
        else:
            app_metrics = getattr(request.app.state, "metrics", None)
            metrics = app_metrics if app_metrics is not None else NoopMetrics()
        method = request.method
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            self._record(metrics, method, path, 500, start)
            raise
        self._record(metrics, method, path, response.status_code, start)
        return response

    @staticmethod
    def _record(metrics: Metrics, method: str, path: str, status: int, start: float) -> None:
        metrics.http_request(
            method=method,
            path=path,
            status=status,
            duration_ms=(time.perf_counter() - start) * 1000,
        )
