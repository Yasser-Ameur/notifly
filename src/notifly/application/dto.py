"""Cross-layer data transfer objects for use cases."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from notifly.domain.models.application import ApiKey, Application
from notifly.domain.models.notification import Delivery, Notification


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


@dataclass(frozen=True)
class NotificationCreated:
    """Result of the Notification Engine: the notification plus its plan.

    ``replayed`` is True when an ``Idempotency-Key`` matched an earlier request
    and the engine returned the existing notification instead of a new one.
    """

    notification: Notification
    deliveries: list[Delivery]
    replayed: bool = False


@dataclass(frozen=True)
class DispatchSummary:
    """Result of a worker dispatch run for a single notification.

    ``skipped`` is True when the notification is missing or cancelled; the
    dispatcher leaves it untouched and performs no work.
    """

    notification_id: UUID
    dispatched: int = 0
    skipped: bool = False
