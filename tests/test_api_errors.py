"""M3 tests for the RFC 7807 error responses."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from notifly.application.services.applications import ApplicationService
from notifly.domain.errors import AuthenticationError, NotFoundError
from notifly.logging import CORRELATION_ID_VAR
from notifly.presentation.api.errors import (
    _error_body,
    _validation_error_items,
    http_exception_handler,
    notifly_error_handler,
    pydantic_validation_error_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from notifly.presentation.api.schemas import ApplicationCreate


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/v1/apps", "headers": []})


def _body(response) -> dict:
    return json.loads(response.body)


def test_error_body_shape() -> None:
    request = _request()
    token = CORRELATION_ID_VAR.set("corr-123")
    try:
        body = _error_body(
            request, code="not_found", title="Not Found", status=404, detail="missing"
        )
    finally:
        CORRELATION_ID_VAR.reset(token)
    assert body == {
        "type": "https://notifly.dev/errors/not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "missing",
        "correlation_id": "corr-123",
    }


def test_notifly_error_handler() -> None:
    response = notifly_error_handler(_request(), NotFoundError("No such app"))
    assert response.status_code == 404
    body = _body(response)
    assert body["type"] == "https://notifly.dev/errors/not_found"
    assert body["title"] == "Not Found"
    assert body["detail"] == "No such app"


def test_notifly_error_handler_falls_back_to_code() -> None:
    response = notifly_error_handler(_request(), AuthenticationError())
    body = _body(response)
    assert body["detail"] == "unauthenticated"


def test_validation_error_handler() -> None:
    exc = RequestValidationError(
        [{"loc": ("body", "name"), "msg": "too short", "type": "string_too_short"}]
    )
    response = validation_error_handler(_request(), exc)
    assert response.status_code == 400
    body = _body(response)
    assert body["type"] == "https://notifly.dev/errors/invalid_data"
    assert body["errors"] == [{"field": "body.name", "message": "too short"}]


def test_pydantic_validation_error_handler() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ApplicationCreate(name="")
    response = pydantic_validation_error_handler(_request(), exc_info.value)
    assert response.status_code == 400
    body = _body(response)
    assert body["errors"] == [
        {"field": "name", "message": "String should have at least 1 character"}
    ]


def test_validation_error_items_skips_non_dicts() -> None:
    items = _validation_error_items([{"loc": ("body",), "msg": "bad"}, "not-a-dict"])
    assert items == [{"field": "body", "message": "bad"}]


def test_http_exception_handler() -> None:
    response = http_exception_handler(_request(), StarletteHTTPException(404, "nope"))
    assert response.status_code == 404
    body = _body(response)
    assert body["detail"] == "nope"
    assert body["type"] == "https://notifly.dev/errors/http_error"


def test_unhandled_exception_handler() -> None:
    response = unhandled_exception_handler(_request(), RuntimeError("boom"))
    assert response.status_code == 500
    body = _body(response)
    assert body["type"] == "https://notifly.dev/errors/internal_error"
    assert body["detail"] == "An unexpected error occurred."


async def test_unhandled_exception_returns_500_via_http(client, monkeypatch) -> None:
    async def boom(self, raw_key: str | None):
        raise RuntimeError("boom")

    monkeypatch.setattr(ApplicationService, "authenticate", boom)
    correlation_id = uuid4().hex
    response = await client.get(
        "/v1/apps",
        headers={"X-Notifly-Key": f"notifly_{uuid4().hex}", "X-Correlation-ID": correlation_id},
    )
    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "https://notifly.dev/errors/internal_error"
    assert body["correlation_id"] == correlation_id
    assert response.headers.get("x-correlation-id") == correlation_id
