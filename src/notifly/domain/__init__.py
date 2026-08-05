"""Domain layer: business entities, value objects, enums, errors, and ports.

The domain layer depends on nothing but the standard library and Pydantic.
It never imports from application, infrastructure, or presentation.
"""

from notifly.domain import enums, errors, models, providers
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderRegistry,
    ProviderResult,
)

__all__ = [
    "Provider",
    "ProviderCapabilities",
    "ProviderMessage",
    "ProviderRegistry",
    "ProviderResult",
    "enums",
    "errors",
    "models",
    "providers",
]
