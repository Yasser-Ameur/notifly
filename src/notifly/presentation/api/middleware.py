"""HTTP middleware for correlation ID propagation."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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
