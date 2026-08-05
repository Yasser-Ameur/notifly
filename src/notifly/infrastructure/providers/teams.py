"""Microsoft Teams incoming-webhook provider (Adaptive Cards)."""

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

_ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


class _TeamsSettings(BaseModel):
    title: str | None = None
    theme_color: str | None = None


class TeamsProvider(Provider):
    """Deliver Adaptive Cards to a Teams incoming webhook."""

    channel_type = ChannelType.TEAMS
    capabilities = ProviderCapabilities(
        supports_html=False,
        supports_attachments=False,
        supports_templates=True,
        supports_scheduling=False,
        supports_delivery_callbacks=False,
    )

    def __init__(self, settings: _TeamsSettings, transport: HttpTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport or HttpTransport(_extract_retry_settings({}))

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        try:
            validated = _TeamsSettings.model_validate(settings)
        except Exception as exc:
            raise ProviderConfigurationError(f"Invalid teams provider settings: {exc}") from exc
        return cls(validated, transport=HttpTransport(_extract_retry_settings(settings)))

    async def send(self, message: ProviderMessage) -> ProviderResult:
        custom_card = message.settings.get("adaptive_card")
        card = custom_card if isinstance(custom_card, dict) else self._default_card(message)
        payload: dict[str, Any] = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": card,
                }
            ],
        }
        return await deliver(
            self._transport,
            message.recipient,
            json=payload,
            correlation_id=message.correlation_id,
            provider_name="Teams",
        )

    def _default_card(self, message: ProviderMessage) -> dict[str, Any]:
        body: list[dict[str, Any]] = []
        if message.subject:
            body.append(
                {
                    "type": "TextBlock",
                    "text": message.subject,
                    "weight": "Bolder",
                    "wrap": True,
                }
            )
        body.append({"type": "TextBlock", "text": message.body, "wrap": True})
        card: dict[str, Any] = {
            "type": "AdaptiveCard",
            "$schema": _ADAPTIVE_CARD_SCHEMA,
            "version": "1.4",
            "body": body,
        }
        if self._settings.title is not None:
            card["title"] = self._settings.title
        if self._settings.theme_color is not None:
            card["themeColor"] = self._settings.theme_color
        return card
