"""Discord webhook provider."""

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


class _DiscordSettings(BaseModel):
    username: str | None = None
    avatar_url: str | None = None
    tts: bool = False


class DiscordProvider(Provider):
    """Deliver messages to a Discord webhook, optionally with embeds."""

    channel_type = ChannelType.DISCORD
    capabilities = ProviderCapabilities(
        supports_html=False,
        supports_attachments=True,
        supports_templates=True,
        supports_scheduling=False,
        supports_delivery_callbacks=False,
    )

    def __init__(self, settings: _DiscordSettings, transport: HttpTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport or HttpTransport(_extract_retry_settings({}))

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        try:
            validated = _DiscordSettings.model_validate(settings)
        except Exception as exc:
            raise ProviderConfigurationError(f"Invalid discord provider settings: {exc}") from exc
        return cls(validated, transport=HttpTransport(_extract_retry_settings(settings)))

    async def send(self, message: ProviderMessage) -> ProviderResult:
        payload: dict[str, Any] = {"content": message.body}
        if self._settings.username is not None:
            payload["username"] = self._settings.username
        if self._settings.avatar_url is not None:
            payload["avatar_url"] = self._settings.avatar_url
        if self._settings.tts:
            payload["tts"] = True
        embeds = message.settings.get("embeds")
        if embeds is not None:
            payload["embeds"] = embeds
        return await deliver(
            self._transport,
            message.recipient,
            json=payload,
            correlation_id=message.correlation_id,
            provider_name="Discord",
        )
