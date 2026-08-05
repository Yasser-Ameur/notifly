"""Slack incoming-webhook provider."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from notifly.domain.enums import ChannelType
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


class _SlackSettings(BaseModel):
    username: str | None = None
    icon_emoji: str | None = None
    icon_url: str | None = None
    channel: str | None = None


class SlackProvider(Provider):
    """Deliver plain-text or block payloads to a Slack incoming webhook."""

    channel_type = ChannelType.SLACK
    capabilities = ProviderCapabilities(
        supports_html=False,
        supports_attachments=True,
        supports_templates=True,
        supports_scheduling=False,
        supports_delivery_callbacks=False,
    )

    def __init__(self, settings: _SlackSettings, transport: HttpTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport or HttpTransport(_extract_retry_settings({}))

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        try:
            validated = _SlackSettings.model_validate(settings)
        except Exception as exc:
            raise ProviderConfigurationError(f"Invalid slack provider settings: {exc}") from exc
        return cls(validated, transport=HttpTransport(_extract_retry_settings(settings)))

    async def send(self, message: ProviderMessage) -> ProviderResult:
        payload: dict[str, Any] = {"text": message.body}
        if self._settings.username is not None:
            payload["username"] = self._settings.username
        if self._settings.icon_emoji is not None:
            payload["icon_emoji"] = self._settings.icon_emoji
        if self._settings.icon_url is not None:
            payload["icon_url"] = self._settings.icon_url
        if self._settings.channel is not None:
            payload["channel"] = self._settings.channel
        blocks = message.settings.get("blocks")
        attachments = message.settings.get("attachments")
        if blocks is not None:
            payload["blocks"] = blocks
        if attachments is not None:
            payload["attachments"] = attachments
        return await deliver(
            self._transport,
            message.recipient,
            json=payload,
            correlation_id=message.correlation_id,
            provider_name="Slack",
        )
