"""Shared HTTP delivery transport for webhook-based providers.

Provides a single httpx-based transport with timeouts, bounded retries with
exponential backoff, and correlation-ID propagation so no HTTP provider
reimplements connection handling.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from notifly.domain.enums import ProviderErrorKind
from notifly.domain.providers import ProviderResult

TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})


def is_transient_status(status_code: int) -> bool:
    """Return whether an HTTP status represents a retryable delivery failure."""
    return status_code in TRANSIENT_STATUS_CODES


def with_correlation(headers: dict[str, str] | None, correlation_id: str) -> dict[str, str]:
    """Return ``headers`` plus the correlation ID so one send can be traced."""
    merged = dict(headers or {})
    if correlation_id:
        merged["X-Correlation-ID"] = correlation_id
    return merged


@dataclass(frozen=True)
class HttpDeliverySettings:
    """Timeout/retry knobs shared by every HTTP provider."""

    timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    retry_backoff_factor: float = 2.0
    retry_max_backoff_seconds: float = 30.0


def _extract_retry_settings(settings: dict[str, Any]) -> HttpDeliverySettings:
    timeout = settings.get("timeout_seconds")
    max_retries = settings.get("max_retries")
    backoff = settings.get("retry_backoff_seconds")
    factor = settings.get("retry_backoff_factor")
    max_backoff = settings.get("retry_max_backoff_seconds")
    return HttpDeliverySettings(
        timeout_seconds=float(timeout) if timeout is not None else 10.0,
        max_retries=int(max_retries) if max_retries is not None else 3,
        retry_backoff_seconds=float(backoff) if backoff is not None else 1.0,
        retry_backoff_factor=float(factor) if factor is not None else 2.0,
        retry_max_backoff_seconds=float(max_backoff) if max_backoff is not None else 30.0,
    )


class HttpTransport:
    """A small async HTTP client with bounded retries.

    Transient failures (network errors and retryable HTTP statuses) are retried
    with exponential backoff up to ``max_retries``; permanent HTTP responses are
    returned untouched. Network failures that exhaust retries are re-raised so
    the caller can classify them as transient.
    """

    def __init__(
        self,
        settings: HttpDeliverySettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or HttpDeliverySettings()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self._settings.timeout_seconds),
            transport=transport,
            follow_redirects=True,
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        content: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self._request_with_retries(
            method, url, json=json, content=content, headers=headers
        )

    async def post(
        self,
        url: str,
        *,
        json: Any | None = None,
        content: str | bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self._request_with_retries(
            "POST", url, json=json, content=content, headers=headers
        )

    async def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        json: Any | None,
        content: str | bytes | None,
        headers: dict[str, str] | None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method, url, json=json, content=content, headers=headers
                )
            except httpx.TransportError:
                if attempt >= self._settings.max_retries:
                    raise
                attempt += 1
                await asyncio.sleep(self._backoff(attempt))
                continue
            if response.status_code < 400:
                return response
            if (
                response.status_code in TRANSIENT_STATUS_CODES
                and attempt < self._settings.max_retries
            ):
                attempt += 1
                await asyncio.sleep(self._backoff(attempt))
                continue
            return response

    def _backoff(self, attempt: int) -> float:
        exponent = attempt - 1
        delay = self._settings.retry_backoff_seconds * (
            self._settings.retry_backoff_factor**exponent
        )
        return min(self._settings.retry_max_backoff_seconds, delay)

    async def aclose(self) -> None:
        await self._client.aclose()


async def deliver(
    transport: HttpTransport,
    url: str,
    *,
    correlation_id: str,
    provider_name: str,
    json: Any | None = None,
    content: str | bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str = "POST",
) -> ProviderResult:
    """Send a payload and translate the outcome into a ``ProviderResult``.

    Exactly one of ``json`` or ``content`` should be provided.
    """
    request_headers = with_correlation(headers, correlation_id)
    try:
        if method == "POST":
            response = await transport.post(
                url, json=json, content=content, headers=request_headers
            )
        else:
            response = await transport.request(
                method, url, json=json, content=content, headers=request_headers
            )
    except httpx.TransportError as exc:
        return ProviderResult(
            delivered=False,
            error=f"{provider_name} unreachable after retries: {exc}",
            error_kind=ProviderErrorKind.TRANSIENT,
        )
    if response.status_code < 400:
        return ProviderResult(delivered=True)
    error_kind = (
        ProviderErrorKind.TRANSIENT
        if is_transient_status(response.status_code)
        else ProviderErrorKind.PERMANENT
    )
    return ProviderResult(
        delivered=False,
        error=(
            f"{provider_name} rejected request with HTTP "
            f"{response.status_code}: {response.text[:200]}"
        ),
        error_kind=error_kind,
    )
