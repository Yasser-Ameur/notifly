"""Provider abstraction: capabilities, message/result contracts, and registry.

Providers are the only components that know how to reach a delivery channel.
The rest of the application interacts only with the ``Provider`` port and the
``ProviderCapabilities`` metadata — never with a concrete provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import metadata
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from notifly.domain.enums import ChannelType, ProviderErrorKind
from notifly.domain.errors import ProviderNotRegisteredError

_ENTRY_POINT_GROUP = "notifly.providers"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Static metadata describing what a provider can do.

    The Notification Engine and Dispatcher use this metadata to make decisions
    (e.g. only render an HTML body for a provider that supports it) without
    knowing which concrete provider is in use.
    """

    supports_html: bool = False
    supports_attachments: bool = False
    supports_templates: bool = True
    supports_scheduling: bool = False
    supports_delivery_callbacks: bool = False
    max_payload_bytes: int | None = None


class ProviderMessage(BaseModel):
    recipient: str
    subject: str | None = None
    body: str
    html_body: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str = ""


class ProviderResult(BaseModel):
    delivered: bool
    provider_message_id: str | None = None
    error: str | None = None
    error_kind: ProviderErrorKind | None = None


class Provider(ABC):
    """Port that a delivery channel adapter must implement."""

    channel_type: ClassVar[ChannelType]
    capabilities: ClassVar[ProviderCapabilities]

    @classmethod
    @abstractmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        """Build a configured provider instance from channel settings."""

    @abstractmethod
    async def send(self, message: ProviderMessage) -> ProviderResult: ...


class ProviderRegistry:
    """Registry of provider classes by channel type.

    Built-in providers are registered on startup; third-party providers register
    themselves through the ``notifly.providers`` entry-point group and are
    discovered automatically.
    """

    def __init__(self, providers: dict[ChannelType, type[Provider]] | None = None) -> None:
        self._providers: dict[ChannelType, type[Provider]] = providers or {}

    def register(self, provider_cls: type[Provider]) -> None:
        self._providers[provider_cls.channel_type] = provider_cls

    def get(self, channel_type: ChannelType) -> type[Provider]:
        try:
            return self._providers[channel_type]
        except KeyError as exc:
            raise ProviderNotRegisteredError(
                f"No provider is registered for channel type '{channel_type}'."
            ) from exc

    def capabilities(self, channel_type: ChannelType) -> ProviderCapabilities:
        return self.get(channel_type).capabilities

    def channels(self) -> list[ChannelType]:
        return list(self._providers)

    def discover(self) -> None:
        """Register any providers advertised via entry points."""
        for entry_point in metadata.entry_points(group=_ENTRY_POINT_GROUP):
            self.register(entry_point.load())
