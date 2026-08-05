"""Built-in provider adapters and registry wiring.

Providers implement the ``Provider`` port from the domain layer. This module
exposes the built-in set, registers them into a ``ProviderRegistry``, and
instantiates a configured provider for a channel type.
"""

from __future__ import annotations

from typing import Any

from notifly.domain.enums import ChannelType
from notifly.domain.providers import Provider, ProviderRegistry
from notifly.infrastructure.providers.discord import DiscordProvider
from notifly.infrastructure.providers.email import EmailProvider
from notifly.infrastructure.providers.slack import SlackProvider
from notifly.infrastructure.providers.teams import TeamsProvider
from notifly.infrastructure.providers.webhook import WebhookProvider

BUILTIN_PROVIDERS: tuple[type[Provider], ...] = (
    EmailProvider,
    SlackProvider,
    DiscordProvider,
    TeamsProvider,
    WebhookProvider,
)

__all__ = [
    "BUILTIN_PROVIDERS",
    "build_provider",
    "create_provider_registry",
    "register_builtins",
]


def register_builtins(registry: ProviderRegistry) -> ProviderRegistry:
    """Register every built-in provider class into ``registry``."""
    for provider_cls in BUILTIN_PROVIDERS:
        registry.register(provider_cls)
    return registry


def create_provider_registry() -> ProviderRegistry:
    """Build a registry seeded with built-ins plus entry-point providers."""
    registry = ProviderRegistry()
    register_builtins(registry)
    registry.discover()
    return registry


def build_provider(
    registry: ProviderRegistry, channel_type: ChannelType, config: dict[str, Any]
) -> Provider:
    """Instantiate a configured provider for ``channel_type``."""
    provider_cls = registry.get(channel_type)
    return provider_cls.from_settings(config)
