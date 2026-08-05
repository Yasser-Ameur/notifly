"""Generic HTTP webhook provider.

Sends the rendered payload to an arbitrary URL with configurable headers and
auth, supporting both JSON and raw (e.g. form-encoded or plain-text) bodies.
"""

from __future__ import annotations

import base64
import json as jsonlib
from typing import Any, Literal

from pydantic import BaseModel, Field

from notifly.domain.enums import ChannelType, ProviderErrorKind
from notifly.domain.errors import ProviderConfigurationError
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderResult,
)
from notifly.infrastructure.providers.http import (
    HttpTransport,
    _extract_retry_settings,
    deliver,
)


class _WebhookSettings(BaseModel):
    method: Literal["POST", "PUT"] = "POST"
    as_json: bool = True
    content_type: str | None = Field(default=None, min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    token: str | None = None
    username: str | None = None
    password: str | None = None


class WebhookProvider(Provider):
    """Deliver a rendered payload to any HTTP endpoint."""

    channel_type = ChannelType.WEBHOOK
    capabilities = ProviderCapabilities(
        supports_html=False,
        supports_attachments=True,
        supports_templates=True,
        supports_scheduling=False,
        supports_delivery_callbacks=True,
    )

    def __init__(self, settings: _WebhookSettings, transport: HttpTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport or HttpTransport(_extract_retry_settings({}))

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        try:
            validated = _WebhookSettings.model_validate(settings)
        except Exception as exc:
            raise ProviderConfigurationError(f"Invalid webhook provider settings: {exc}") from exc
        return cls(validated, transport=HttpTransport(_extract_retry_settings(settings)))

    async def send(self, message: ProviderMessage) -> ProviderResult:
        headers = (
            self._auth_headers()
            | self._settings.headers
            | dict(message.settings.get("headers", {}))
        )
        if self._settings.as_json:
            try:
                payload: Any = jsonlib.loads(message.body)
            except jsonlib.JSONDecodeError as exc:
                return ProviderResult(
                    delivered=False,
                    error=f"Webhook body is not valid JSON: {exc}",
                    error_kind=ProviderErrorKind.PERMANENT,
                )
            return await deliver(
                self._transport,
                message.recipient,
                json=payload,
                correlation_id=message.correlation_id,
                provider_name="Webhook",
                headers=headers,
                method=self._settings.method,
            )
        if self._settings.content_type is not None:
            headers = {**headers, "Content-Type": self._settings.content_type}
        return await deliver(
            self._transport,
            message.recipient,
            content=message.body,
            correlation_id=message.correlation_id,
            provider_name="Webhook",
            headers=headers,
            method=self._settings.method,
        )

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._settings.token is not None:
            headers["Authorization"] = f"Bearer {self._settings.token}"
        elif self._settings.username is not None:
            credentials = f"{self._settings.username}:{self._settings.password or ''}"
            encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        return headers
