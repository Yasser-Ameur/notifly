"""Global exception handlers mapping errors to RFC 7807-shaped responses.

Responses follow the documented shape:

    {
      "type": "https://notifly.dev/errors/<code>",
      "title": "...",
      "status": <http status>,
      "detail": "...",
      "correlation_id": "..."
    }

Handlers set the ``X-Correlation-ID`` response header themselves so it is present
even on responses produced outside the correlation middleware (e.g. the
``ServerErrorMiddleware`` 500 path, which runs outermost).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from notifly.domain.errors import NotiFlyError
from notifly.logging import get_correlation_id

logger = logging.getLogger(__name__)


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", None) or get_correlation_id() or "-"


def _json_response(
    request: Request, *, status_code: int, content: dict[str, object]
) -> JSONResponse:
    response = JSONResponse(status_code=status_code, content=content)
    response.headers["x-correlation-id"] = str(content.get("correlation_id", ""))
    return response


def _error_body(
    request: Request,
    *,
    code: str,
    title: str,
    status: int,
    detail: str,
) -> dict[str, object]:
    return {
        "type": f"https://notifly.dev/errors/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": _correlation_id(request),
    }


def notifly_error_handler(request: Request, exc: NotiFlyError) -> JSONResponse:
    return _json_response(
        request,
        status_code=exc.status_code,
        content=_error_body(
            request,
            code=exc.code,
            title=exc.code.replace("_", " ").title(),
            status=exc.status_code,
            detail=exc.detail or exc.code,
        ),
    )


def _validation_error_items(errors: Sequence[Any]) -> list[dict[str, str]]:
    items = []
    for err in errors:
        if not isinstance(err, dict):
            continue
        location = ".".join(str(part) for part in err.get("loc", ()))
        items.append({"field": location, "message": str(err.get("msg", ""))})
    return items


def _validation_error_response(request: Request, errors: Sequence[Any]) -> JSONResponse:
    return _json_response(
        request,
        status_code=400,
        content=_error_body(
            request,
            code="invalid_data",
            title="Invalid Data",
            status=400,
            detail="Request validation failed.",
        )
        | {"errors": _validation_error_items(errors)},
    )


def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _validation_error_response(request, exc.errors())


def pydantic_validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return _validation_error_response(request, exc.errors())


def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return _json_response(
        request,
        status_code=exc.status_code,
        content=_error_body(
            request,
            code="http_error",
            title="HTTP Error",
            status=exc.status_code,
            detail=detail,
        ),
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled error while processing %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return _json_response(
        request,
        status_code=500,
        content=_error_body(
            request,
            code="internal_error",
            title="Internal Server Error",
            status=500,
            detail="An unexpected error occurred.",
        ),
    )
