"""Cross-layer data transfer objects for use cases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from notifly.domain.models.application import ApiKey, Application


@dataclass(frozen=True)
class IssuedApiKey:
    """An API key record plus its one-time plaintext."""

    api_key: ApiKey
    plaintext: str


@dataclass(frozen=True)
class ApplicationCreated:
    """Result of creating an application: the app and its bootstrap key."""

    application: Application
    issued_key: IssuedApiKey


@dataclass(frozen=True)
class AuthenticatedContext:
    """The identity established by a valid API key."""

    application: Application
    api_key: ApiKey

    @property
    def app_id(self) -> UUID:
        return self.application.id

    @property
    def actor(self) -> str:
        return self.api_key.key_prefix
