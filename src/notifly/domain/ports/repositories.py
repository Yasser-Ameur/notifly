"""Repository ports and the Unit of Work contract.

The application layer depends on these protocols only. SQLAlchemy (and, in
tests, in-memory) implementations live in the infrastructure layer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from notifly.domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from notifly.domain.models.application import ApiKey, Application
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.idempotency import IdempotencyRecord
from notifly.domain.models.notification import Delivery, DeliveryAttempt, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.models.template import Template


class ApplicationRepository(Protocol):
    async def add(self, application: Application) -> None: ...

    async def get(self, application_id: UUID) -> Application | None: ...

    async def get_by_name(self, name: str) -> Application | None: ...

    async def list_apps(self, *, limit: int, offset: int) -> list[Application]: ...

    async def count(self) -> int: ...


class ApiKeyRepository(Protocol):
    async def add(self, api_key: ApiKey) -> None: ...

    async def get_by_id(self, key_id: UUID) -> ApiKey | None: ...

    async def get_by_hash(self, key_hash: str) -> ApiKey | None: ...

    async def get_by_prefix(self, key_prefix: str) -> list[ApiKey]: ...

    async def list_by_app(self, application_id: UUID) -> list[ApiKey]: ...

    async def update(self, api_key: ApiKey) -> None: ...


class ChannelRepository(Protocol):
    async def add(self, channel: ChannelConfig) -> None: ...

    async def get(self, channel_id: UUID) -> ChannelConfig | None: ...

    async def get_by_app_and_type(
        self, application_id: UUID, channel_type: ChannelType
    ) -> ChannelConfig | None: ...

    async def list_by_app(self, application_id: UUID) -> list[ChannelConfig]: ...

    async def update(self, channel: ChannelConfig) -> None: ...


class TemplateRepository(Protocol):
    async def add(self, template: Template) -> None: ...

    async def get(self, template_id: UUID) -> Template | None: ...

    async def get_by_app_and_event(self, application_id: UUID, event: str) -> Template | None: ...

    async def list_by_app(self, application_id: UUID) -> list[Template]: ...

    async def update(self, template: Template) -> None: ...

    async def delete(self, template: Template) -> None: ...


class NotificationRepository(Protocol):
    async def add(self, notification: Notification) -> None: ...

    async def get(self, notification_id: UUID) -> Notification | None: ...

    async def search(
        self,
        *,
        application_id: UUID,
        status: NotificationStatus | None = None,
        event: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Notification]: ...

    async def count(
        self,
        *,
        application_id: UUID,
        status: NotificationStatus | None = None,
        event: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int: ...

    async def list_due_scheduled(self, *, now: datetime, limit: int) -> list[Notification]: ...

    async def list_deadletters(
        self, *, application_id: UUID, limit: int, offset: int
    ) -> list[Notification]: ...

    async def update(self, notification: Notification) -> None: ...


class DeliveryRepository(Protocol):
    async def add_many(self, deliveries: list[Delivery]) -> None: ...

    async def add(self, delivery: Delivery) -> None: ...

    async def get(self, delivery_id: UUID) -> Delivery | None: ...

    async def get_for_update(self, delivery_id: UUID) -> Delivery | None: ...

    async def list_by_notification(self, notification_id: UUID) -> list[Delivery]: ...

    async def list_due_retry(self, *, now: datetime, limit: int) -> list[Delivery]: ...

    async def search(
        self,
        *,
        application_id: UUID,
        notification_id: UUID | None = None,
        channel_type: ChannelType | None = None,
        status: DeliveryStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Delivery]: ...

    async def update(self, delivery: Delivery) -> None: ...


class DeliveryAttemptRepository(Protocol):
    async def add(self, attempt: DeliveryAttempt) -> None: ...


class AuditRepository(Protocol):
    async def add(self, entry: AuditLogEntry) -> None: ...

    async def search(
        self, *, application_id: UUID, limit: int, offset: int
    ) -> list[AuditLogEntry]: ...


class OutboxRepository(Protocol):
    async def add(self, event: OutboxEvent) -> None: ...

    async def get_pending(self, *, limit: int) -> list[OutboxEvent]: ...

    async def get_pending_for_update(self, *, limit: int) -> list[OutboxEvent]: ...

    async def mark_published(self, event_id: UUID, now: datetime) -> None: ...

    async def mark_failed(self, event_id: UUID, error: str) -> None: ...


class IdempotencyRepository(Protocol):
    async def get(self, application_id: UUID, key: str) -> IdempotencyRecord | None: ...

    async def add(self, record: IdempotencyRecord) -> None: ...


class UnitOfWork(Protocol):
    @property
    def applications(self) -> ApplicationRepository: ...

    @property
    def api_keys(self) -> ApiKeyRepository: ...

    @property
    def channels(self) -> ChannelRepository: ...

    @property
    def templates(self) -> TemplateRepository: ...

    @property
    def notifications(self) -> NotificationRepository: ...

    @property
    def deliveries(self) -> DeliveryRepository: ...

    @property
    def delivery_attempts(self) -> DeliveryAttemptRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    @property
    def idempotency(self) -> IdempotencyRepository: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
