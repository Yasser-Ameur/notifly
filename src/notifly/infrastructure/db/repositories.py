"""SQLAlchemy repository implementations.

Each repository maps between ORM rows and domain entities explicitly so the
domain layer never sees ORM objects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from notifly.domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from notifly.domain.models.application import ApiKey, Application
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.models.base import DomainModel
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.idempotency import IdempotencyRecord
from notifly.domain.models.notification import Delivery, DeliveryAttempt, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.models.template import Template, TemplateChannelContent
from notifly.infrastructure.db import orm


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce[T: DomainModel](row: object, model: type[T]) -> T:
    """Map an ORM row onto a domain model, normalizing datetime columns.

    SQLite stores ``DateTime(timezone=True)`` values without tzinfo while
    Postgres returns them tz-aware; normalizing to UTC keeps behaviour
    identical across both backends.
    """
    state = sa_inspect(row)
    assert state is not None
    mapper = state.mapper
    assert mapper is not None
    values: dict[str, object] = {}
    for column in mapper.columns:
        value = getattr(row, column.key)
        if isinstance(value, datetime):
            value = _as_utc(value)
        values[column.key] = value
    return model(**values)


def _serialize_channels(
    channels: dict[ChannelType, TemplateChannelContent],
) -> dict[str, dict[str, str | None]]:
    return {channel.value: content.model_dump(mode="json") for channel, content in channels.items()}


def _app_from_row(row: orm.ApplicationRow) -> Application:
    return _coerce(row, Application)


def _app_to_row(application: Application) -> orm.ApplicationRow:
    return orm.ApplicationRow(
        id=application.id,
        name=application.name,
        created_at=application.created_at,
        updated_at=application.updated_at,
    )


def _key_from_row(row: orm.ApiKeyRow) -> ApiKey:
    return _coerce(row, ApiKey)


def _key_to_row(api_key: ApiKey) -> orm.ApiKeyRow:
    return orm.ApiKeyRow(
        id=api_key.id,
        application_id=api_key.application_id,
        name=api_key.name,
        key_hash=api_key.key_hash,
        key_prefix=api_key.key_prefix,
        created_at=api_key.created_at,
        revoked_at=api_key.revoked_at,
        last_used_at=api_key.last_used_at,
    )


def _channel_from_row(row: orm.ChannelRow) -> ChannelConfig:
    return _coerce(row, ChannelConfig)


def _channel_to_row(channel: ChannelConfig) -> orm.ChannelRow:
    return orm.ChannelRow(
        id=channel.id,
        application_id=channel.application_id,
        channel_type=channel.channel_type.value,
        name=channel.name,
        enabled=channel.enabled,
        config=channel.config,
        max_attempts=channel.max_attempts,
        retry_backoff_seconds=channel.retry_backoff_seconds,
        rate_limit_per_minute=channel.rate_limit_per_minute,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _template_from_row(row: orm.TemplateRow) -> Template:
    return _coerce(row, Template)


def _template_to_row(template: Template) -> orm.TemplateRow:
    return orm.TemplateRow(
        id=template.id,
        application_id=template.application_id,
        name=template.name,
        event=template.event,
        description=template.description,
        variables=[v.model_dump(mode="json") for v in template.variables],
        channels=_serialize_channels(template.channels),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _notification_from_row(row: orm.NotificationRow) -> Notification:
    return _coerce(row, Notification)


def _notification_to_row(notification: Notification) -> orm.NotificationRow:
    return orm.NotificationRow(
        id=notification.id,
        application_id=notification.application_id,
        template_id=notification.template_id,
        event=notification.event,
        variables=notification.variables,
        status=notification.status.value,
        scheduled_at=notification.scheduled_at,
        correlation_id=notification.correlation_id,
        retry_count=notification.retry_count,
        created_at=notification.created_at,
        updated_at=notification.updated_at,
        processed_at=notification.processed_at,
    )


def _delivery_from_row(row: orm.DeliveryRow) -> Delivery:
    return _coerce(row, Delivery)


def _delivery_to_row(delivery: Delivery) -> orm.DeliveryRow:
    return orm.DeliveryRow(
        id=delivery.id,
        notification_id=delivery.notification_id,
        channel_type=delivery.channel_type.value,
        provider=delivery.provider,
        recipient=delivery.recipient,
        subject=delivery.subject,
        body=delivery.body,
        html_body=delivery.html_body,
        provider_settings=delivery.provider_settings,
        status=delivery.status.value,
        attempts=delivery.attempts,
        max_attempts=delivery.max_attempts,
        retry_backoff_seconds=delivery.retry_backoff_seconds,
        rate_limit_per_minute=delivery.rate_limit_per_minute,
        next_attempt_at=delivery.next_attempt_at,
        last_error=delivery.last_error,
        provider_message_id=delivery.provider_message_id,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
        completed_at=delivery.completed_at,
    )


def _attempt_from_row(row: orm.DeliveryAttemptRow) -> DeliveryAttempt:
    return _coerce(row, DeliveryAttempt)


def _attempt_to_row(attempt: DeliveryAttempt) -> orm.DeliveryAttemptRow:
    return orm.DeliveryAttemptRow(
        id=attempt.id,
        delivery_id=attempt.delivery_id,
        attempt_number=attempt.attempt_number,
        status=attempt.status.value,
        error=attempt.error,
        duration_ms=attempt.duration_ms,
        created_at=attempt.created_at,
    )


def _audit_from_row(row: orm.AuditLogRow) -> AuditLogEntry:
    return _coerce(row, AuditLogEntry)


def _audit_to_row(entry: AuditLogEntry) -> orm.AuditLogRow:
    return orm.AuditLogRow(
        id=entry.id,
        application_id=entry.application_id,
        actor=entry.actor,
        action=entry.action.value,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        correlation_id=entry.correlation_id,
        payload=entry.payload,
        created_at=entry.created_at,
    )


def _outbox_from_row(row: orm.OutboxEventRow) -> OutboxEvent:
    return _coerce(row, OutboxEvent)


def _outbox_to_row(event: OutboxEvent) -> orm.OutboxEventRow:
    return orm.OutboxEventRow(
        id=event.id,
        event_type=event.event_type.value,
        payload=event.payload,
        status=event.status.value,
        attempts=event.attempts,
        last_error=event.last_error,
        correlation_id=event.correlation_id,
        created_at=event.created_at,
        published_at=event.published_at,
    )


def _idempotency_from_row(row: orm.IdempotencyRecordRow) -> IdempotencyRecord:
    return _coerce(row, IdempotencyRecord)


def _idempotency_to_row(record: IdempotencyRecord) -> orm.IdempotencyRecordRow:
    return orm.IdempotencyRecordRow(
        id=record.id,
        application_id=record.application_id,
        key=record.key,
        request_hash=record.request_hash,
        notification_id=record.notification_id,
        created_at=record.created_at,
    )


class SqlAlchemyApplicationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, application: Application) -> None:
        self._session.add(_app_to_row(application))

    async def get(self, application_id: UUID) -> Application | None:
        row = await self._session.get(orm.ApplicationRow, application_id)
        return _app_from_row(row) if row else None

    async def get_by_name(self, name: str) -> Application | None:
        result = await self._session.scalar(
            select(orm.ApplicationRow).where(orm.ApplicationRow.name == name)
        )
        return _app_from_row(result) if result else None

    async def list_apps(self, *, limit: int, offset: int) -> list[Application]:
        result = await self._session.scalars(
            select(orm.ApplicationRow)
            .order_by(orm.ApplicationRow.created_at)
            .limit(limit)
            .offset(offset)
        )
        return [_app_from_row(row) for row in result]

    async def count(self) -> int:
        return int(await self._session.scalar(select(func.count(orm.ApplicationRow.id))) or 0)


class SqlAlchemyApiKeyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, api_key: ApiKey) -> None:
        self._session.add(_key_to_row(api_key))

    async def get_by_id(self, key_id: UUID) -> ApiKey | None:
        row = await self._session.get(orm.ApiKeyRow, key_id)
        return _key_from_row(row) if row else None

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self._session.scalar(
            select(orm.ApiKeyRow).where(orm.ApiKeyRow.key_hash == key_hash)
        )
        return _key_from_row(result) if result else None

    async def get_by_prefix(self, key_prefix: str) -> list[ApiKey]:
        result = await self._session.scalars(
            select(orm.ApiKeyRow)
            .where(orm.ApiKeyRow.key_prefix == key_prefix)
            .order_by(orm.ApiKeyRow.created_at)
        )
        return [_key_from_row(row) for row in result]

    async def list_by_app(self, application_id: UUID) -> list[ApiKey]:
        result = await self._session.scalars(
            select(orm.ApiKeyRow)
            .where(orm.ApiKeyRow.application_id == application_id)
            .order_by(orm.ApiKeyRow.created_at)
        )
        return [_key_from_row(row) for row in result]

    async def update(self, api_key: ApiKey) -> None:
        row = await self._session.get(orm.ApiKeyRow, api_key.id)
        if row is None:
            return
        row.revoked_at = api_key.revoked_at
        row.last_used_at = api_key.last_used_at
        row.name = api_key.name


class SqlAlchemyChannelRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, channel: ChannelConfig) -> None:
        self._session.add(_channel_to_row(channel))

    async def get(self, channel_id: UUID) -> ChannelConfig | None:
        row = await self._session.get(orm.ChannelRow, channel_id)
        return _channel_from_row(row) if row else None

    async def get_by_app_and_type(
        self, application_id: UUID, channel_type: ChannelType
    ) -> ChannelConfig | None:
        result = await self._session.scalar(
            select(orm.ChannelRow).where(
                orm.ChannelRow.application_id == application_id,
                orm.ChannelRow.channel_type == channel_type.value,
            )
        )
        return _channel_from_row(result) if result else None

    async def list_by_app(self, application_id: UUID) -> list[ChannelConfig]:
        result = await self._session.scalars(
            select(orm.ChannelRow)
            .where(orm.ChannelRow.application_id == application_id)
            .order_by(orm.ChannelRow.created_at)
        )
        return [_channel_from_row(row) for row in result]

    async def update(self, channel: ChannelConfig) -> None:
        row = await self._session.get(orm.ChannelRow, channel.id)
        if row is None:
            return
        row.name = channel.name
        row.enabled = channel.enabled
        row.config = channel.config
        row.max_attempts = channel.max_attempts
        row.retry_backoff_seconds = channel.retry_backoff_seconds
        row.rate_limit_per_minute = channel.rate_limit_per_minute
        row.updated_at = channel.updated_at


class SqlAlchemyTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, template: Template) -> None:
        self._session.add(_template_to_row(template))

    async def get(self, template_id: UUID) -> Template | None:
        row = await self._session.get(orm.TemplateRow, template_id)
        return _template_from_row(row) if row else None

    async def get_by_app_and_event(self, application_id: UUID, event: str) -> Template | None:
        result = await self._session.scalar(
            select(orm.TemplateRow).where(
                orm.TemplateRow.application_id == application_id,
                orm.TemplateRow.event == event,
            )
        )
        return _template_from_row(result) if result else None

    async def list_by_app(self, application_id: UUID) -> list[Template]:
        result = await self._session.scalars(
            select(orm.TemplateRow)
            .where(orm.TemplateRow.application_id == application_id)
            .order_by(orm.TemplateRow.created_at)
        )
        return [_template_from_row(row) for row in result]

    async def update(self, template: Template) -> None:
        row = await self._session.get(orm.TemplateRow, template.id)
        if row is None:
            return
        row.name = template.name
        row.event = template.event
        row.description = template.description
        row.variables = [v.model_dump(mode="json") for v in template.variables]
        row.channels = _serialize_channels(template.channels)
        row.updated_at = template.updated_at

    async def delete(self, template: Template) -> None:
        row = await self._session.get(orm.TemplateRow, template.id)
        if row is not None:
            await self._session.delete(row)


class SqlAlchemyNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, notification: Notification) -> None:
        self._session.add(_notification_to_row(notification))

    async def get(self, notification_id: UUID) -> Notification | None:
        row = await self._session.get(orm.NotificationRow, notification_id)
        return _notification_from_row(row) if row else None

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
    ) -> list[Notification]:
        stmt = select(orm.NotificationRow).where(
            orm.NotificationRow.application_id == application_id
        )
        if status is not None:
            stmt = stmt.where(orm.NotificationRow.status == status.value)
        if event is not None:
            stmt = stmt.where(orm.NotificationRow.event == event)
        if created_after is not None:
            stmt = stmt.where(orm.NotificationRow.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(orm.NotificationRow.created_at <= created_before)
        result = await self._session.scalars(
            stmt.order_by(orm.NotificationRow.created_at.desc()).limit(limit).offset(offset)
        )
        return [_notification_from_row(row) for row in result]

    async def count(
        self,
        *,
        application_id: UUID,
        status: NotificationStatus | None = None,
        event: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> int:
        stmt = select(func.count(orm.NotificationRow.id)).where(
            orm.NotificationRow.application_id == application_id
        )
        if status is not None:
            stmt = stmt.where(orm.NotificationRow.status == status.value)
        if event is not None:
            stmt = stmt.where(orm.NotificationRow.event == event)
        if created_after is not None:
            stmt = stmt.where(orm.NotificationRow.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(orm.NotificationRow.created_at <= created_before)
        return int(await self._session.scalar(stmt) or 0)

    async def list_due_scheduled(self, *, now: datetime, limit: int) -> list[Notification]:
        result = await self._session.scalars(
            select(orm.NotificationRow)
            .where(
                orm.NotificationRow.status == NotificationStatus.PENDING.value,
                orm.NotificationRow.scheduled_at.is_not(None),
                orm.NotificationRow.scheduled_at <= now,
            )
            .order_by(orm.NotificationRow.scheduled_at)
            .limit(limit)
        )
        return [_notification_from_row(row) for row in result]

    async def list_deadletters(
        self, *, application_id: UUID, limit: int, offset: int
    ) -> list[Notification]:
        result = await self._session.scalars(
            select(orm.NotificationRow)
            .where(
                orm.NotificationRow.application_id == application_id,
                orm.NotificationRow.status == NotificationStatus.FAILED.value,
            )
            .order_by(orm.NotificationRow.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_notification_from_row(row) for row in result]

    async def update(self, notification: Notification) -> None:
        row = await self._session.get(orm.NotificationRow, notification.id)
        if row is None:
            return
        row.template_id = notification.template_id
        row.event = notification.event
        row.variables = notification.variables
        row.status = notification.status.value
        row.scheduled_at = notification.scheduled_at
        row.correlation_id = notification.correlation_id
        row.retry_count = notification.retry_count
        row.updated_at = notification.updated_at
        row.processed_at = notification.processed_at


class SqlAlchemyDeliveryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_many(self, deliveries: list[Delivery]) -> None:
        self._session.add_all([_delivery_to_row(d) for d in deliveries])

    async def add(self, delivery: Delivery) -> None:
        self._session.add(_delivery_to_row(delivery))

    async def get(self, delivery_id: UUID) -> Delivery | None:
        row = await self._session.get(orm.DeliveryRow, delivery_id)
        return _delivery_from_row(row) if row else None

    async def get_for_update(self, delivery_id: UUID) -> Delivery | None:
        stmt = select(orm.DeliveryRow).where(orm.DeliveryRow.id == delivery_id).with_for_update()
        row = await self._session.scalar(stmt)
        return _delivery_from_row(row) if row else None

    async def list_by_notification(self, notification_id: UUID) -> list[Delivery]:
        result = await self._session.scalars(
            select(orm.DeliveryRow)
            .where(orm.DeliveryRow.notification_id == notification_id)
            .order_by(orm.DeliveryRow.created_at)
        )
        return [_delivery_from_row(row) for row in result]

    async def list_due_retry(self, *, now: datetime, limit: int) -> list[Delivery]:
        result = await self._session.scalars(
            select(orm.DeliveryRow)
            .where(
                orm.DeliveryRow.status == DeliveryStatus.PENDING.value,
                orm.DeliveryRow.next_attempt_at.is_not(None),
                orm.DeliveryRow.next_attempt_at <= now,
            )
            .order_by(orm.DeliveryRow.next_attempt_at)
            .limit(limit)
        )
        return [_delivery_from_row(row) for row in result]

    async def search(
        self,
        *,
        application_id: UUID,
        notification_id: UUID | None = None,
        channel_type: ChannelType | None = None,
        status: DeliveryStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Delivery]:
        stmt = (
            select(orm.DeliveryRow)
            .join(
                orm.NotificationRow,
                orm.DeliveryRow.notification_id == orm.NotificationRow.id,
            )
            .where(orm.NotificationRow.application_id == application_id)
        )
        if notification_id is not None:
            stmt = stmt.where(orm.DeliveryRow.notification_id == notification_id)
        if channel_type is not None:
            stmt = stmt.where(orm.DeliveryRow.channel_type == channel_type.value)
        if status is not None:
            stmt = stmt.where(orm.DeliveryRow.status == status.value)
        result = await self._session.scalars(
            stmt.order_by(orm.DeliveryRow.created_at.desc()).limit(limit).offset(offset)
        )
        return [_delivery_from_row(row) for row in result]

    async def update(self, delivery: Delivery) -> None:
        row = await self._session.get(orm.DeliveryRow, delivery.id)
        if row is None:
            return
        row.status = delivery.status.value
        row.attempts = delivery.attempts
        row.next_attempt_at = delivery.next_attempt_at
        row.last_error = delivery.last_error
        row.provider_message_id = delivery.provider_message_id
        row.updated_at = delivery.updated_at
        row.completed_at = delivery.completed_at


class SqlAlchemyDeliveryAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, attempt: DeliveryAttempt) -> None:
        self._session.add(_attempt_to_row(attempt))


class SqlAlchemyAuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, entry: AuditLogEntry) -> None:
        self._session.add(_audit_to_row(entry))

    async def search(self, *, application_id: UUID, limit: int, offset: int) -> list[AuditLogEntry]:
        result = await self._session.scalars(
            select(orm.AuditLogRow)
            .where(orm.AuditLogRow.application_id == application_id)
            .order_by(orm.AuditLogRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [_audit_from_row(row) for row in result]


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: OutboxEvent) -> None:
        self._session.add(_outbox_to_row(event))

    async def get_pending(self, *, limit: int) -> list[OutboxEvent]:
        result = await self._session.scalars(
            select(orm.OutboxEventRow)
            .where(orm.OutboxEventRow.status == "pending")
            .order_by(orm.OutboxEventRow.created_at)
            .limit(limit)
        )
        return [_outbox_from_row(row) for row in result]

    async def get_pending_for_update(self, *, limit: int) -> list[OutboxEvent]:
        stmt = (
            select(orm.OutboxEventRow)
            .where(orm.OutboxEventRow.status == "pending")
            .order_by(orm.OutboxEventRow.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.scalars(stmt)
        return [_outbox_from_row(row) for row in result]

    async def mark_published(self, event_id: UUID, now: datetime) -> None:
        row = await self._session.get(orm.OutboxEventRow, event_id)
        if row is None:
            return
        row.status = "published"
        row.published_at = now

    async def mark_failed(self, event_id: UUID, error: str) -> None:
        row = await self._session.get(orm.OutboxEventRow, event_id)
        if row is None:
            return
        row.status = "failed"
        row.attempts += 1
        row.last_error = error


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, application_id: UUID, key: str) -> IdempotencyRecord | None:
        result = await self._session.scalar(
            select(orm.IdempotencyRecordRow).where(
                orm.IdempotencyRecordRow.application_id == application_id,
                orm.IdempotencyRecordRow.key == key,
            )
        )
        return _idempotency_from_row(result) if result else None

    async def add(self, record: IdempotencyRecord) -> None:
        self._session.add(_idempotency_to_row(record))
