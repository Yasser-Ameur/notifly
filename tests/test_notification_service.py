"""M6 application-layer tests: the Notification Engine (send flow)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notifly.application.services.applications import ApplicationService
from notifly.application.services.notifications import NotificationService
from notifly.application.services.templates import TemplateService
from notifly.domain.enums import (
    AuditAction,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    OutboxEventType,
    VariableType,
)
from notifly.domain.errors import (
    IdempotencyConflictError,
    InvalidDataError,
    InvalidStateError,
    NotFoundError,
    VariableValidationError,
)
from notifly.domain.models.application import Application
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.template import TemplateChannelContent, VariableDef
from notifly.infrastructure.db.base import Base
from notifly.infrastructure.db.orm import (
    AuditLogRow,
    DeliveryRow,
    NotificationRow,
    OutboxEventRow,
)
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.infrastructure.db.uow import create_uow_factory
from notifly.infrastructure.providers import register_builtins


@pytest.fixture()
async def db(test_settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(test_settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.fixture()
def service(db) -> NotificationService:
    return NotificationService(create_uow_factory(db))


async def _make_app(db, name: str = "acme") -> Application:
    created = await ApplicationService(
        create_uow_factory(db), key_prefix_str="notifly_", key_hash_iterations=1000
    ).create_application(name)
    return created.application


async def _make_template(db, application_id) -> None:
    await TemplateService(create_uow_factory(db)).create_template(
        application_id,
        actor="a",
        name="Welcome",
        event="user_welcome",
        description=None,
        variables=[VariableDef(name="name", type=VariableType.STRING)],
        channels={
            ChannelType.EMAIL: TemplateChannelContent(
                subject="Hi {{ name }}", body="Welcome {{ name }}"
            ),
            ChannelType.SLACK: TemplateChannelContent(subject=None, body="Welcome {{ name }}"),
        },
    )


async def _add_channel(
    db,
    application_id: str,
    *,
    channel_type: ChannelType = ChannelType.EMAIL,
    enabled: bool = True,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 5.0,
    rate_limit_per_minute: int | None = None,
) -> None:
    now = datetime.now(UTC)
    async with create_uow_factory(db)() as uow:
        await uow.channels.add(
            ChannelConfig(
                id=uuid4(),
                application_id=application_id,
                channel_type=channel_type,
                name=channel_type.value,
                enabled=enabled,
                config={},
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                rate_limit_per_minute=rate_limit_per_minute,
                created_at=now,
                updated_at=now,
            )
        )


async def _count(db, row_cls, *, column: str = "id") -> int:
    async with db() as session:
        count = await session.scalar(select(func.count()).select_from(row_cls))
        return int(count or 0)


async def _count_outbox(db) -> int:
    async with db() as session:
        count = await session.scalar(
            select(func.count(OutboxEventRow.id)).where(
                OutboxEventRow.event_type == OutboxEventType.NOTIFICATION_CREATED.value
            )
        )
        return int(count or 0)


async def _count_audit(db, action: AuditAction) -> int:
    async with db() as session:
        count = await session.scalar(
            select(func.count(AuditLogRow.id)).where(AuditLogRow.action == action.value)
        )
        return int(count or 0)


def _payload(*, event: str = "user_welcome", variables: dict | None = None, **extra) -> dict:
    data = {
        "event": event,
        "variables": variables if variables is not None else {"name": "Alice"},
        "recipients": {"email": "alice@example.com"},
    }
    data.update(extra)
    return data


async def test_create_immediate_emits_outbox_and_delivery(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    created = await service.create_notification(
        application.id, actor="key", correlation_id="corr-1", **_payload()
    )
    assert not created.replayed
    assert created.notification.status is NotificationStatus.PENDING
    assert created.notification.correlation_id == "corr-1"
    assert len(created.deliveries) == 1
    delivery = created.deliveries[0]
    assert delivery.channel_type is ChannelType.EMAIL
    assert delivery.recipient == "alice@example.com"
    assert delivery.subject == "Hi Alice"
    assert delivery.body == "Welcome Alice"
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.provider == "email"
    assert await _count_outbox(db) == 1
    assert await _count(db, DeliveryRow) == 1
    assert await _count_audit(db, AuditAction.NOTIFICATION_CREATED) == 1


async def test_create_with_multiple_recipient_channels(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    payload = _payload(
        recipients={"email": "a@example.com", "slack": "https://hooks.example/slack"}
    )
    created = await service.create_notification(application.id, actor="key", **payload)
    assert {d.channel_type for d in created.deliveries} == {
        ChannelType.EMAIL,
        ChannelType.SLACK,
    }
    assert {d.recipient for d in created.deliveries} == {
        "a@example.com",
        "https://hooks.example/slack",
    }


async def test_create_scheduled_emits_no_outbox_event(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    scheduled = datetime.now(UTC) + timedelta(hours=1)
    created = await service.create_notification(
        application.id,
        actor="key",
        scheduled_at=scheduled,
        **_payload(),
    )
    assert created.notification.scheduled_at == scheduled
    assert await _count_outbox(db) == 0
    assert await _count_audit(db, AuditAction.NOTIFICATION_CREATED) == 1


async def test_idempotent_replay_returns_existing(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    first = await service.create_notification(
        application.id, actor="key", idempotency_key="send-1", **_payload()
    )
    second = await service.create_notification(
        application.id, actor="key", idempotency_key="send-1", **_payload()
    )
    assert second.replayed
    assert second.notification.id == first.notification.id
    assert len(second.deliveries) == 1
    assert await _count(db, NotificationRow) == 1
    assert await _count_outbox(db) == 1


async def test_idempotency_conflict_on_different_payload(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    await service.create_notification(
        application.id, actor="key", idempotency_key="send-1", **_payload()
    )
    with pytest.raises(IdempotencyConflictError):
        await service.create_notification(
            application.id,
            actor="key",
            idempotency_key="send-1",
            **_payload(variables={"name": "Bob"}),
        )


async def test_missing_template_not_found(db, service) -> None:
    application = await _make_app(db)
    with pytest.raises(NotFoundError):
        await service.create_notification(
            application.id, actor="key", **_payload(event="unknown_event")
        )


async def test_template_id_event_mismatch(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    templates = await TemplateService(create_uow_factory(db)).list_templates(application.id)
    with pytest.raises(InvalidDataError):
        await service.create_notification(
            application.id,
            actor="key",
            template_id=templates[0].id,
            **_payload(event="other_event"),
        )


async def test_missing_required_variable(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    with pytest.raises(VariableValidationError):
        await service.create_notification(application.id, actor="key", **_payload(variables={}))


async def test_recipient_channel_without_content(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    with pytest.raises(InvalidDataError):
        await service.create_notification(
            application.id,
            actor="key",
            **_payload(recipients={"discord": "https://hooks.example/discord"}),
        )


async def test_empty_recipients_rejected(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    with pytest.raises(InvalidDataError):
        await service.create_notification(application.id, actor="key", **_payload(recipients={}))


async def test_disabled_channel_rejected(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    await _add_channel(db, application.id, enabled=False)
    with pytest.raises(InvalidDataError):
        await service.create_notification(application.id, actor="key", **_payload())


async def test_channel_retry_policy_snapshotted_onto_delivery(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    await _add_channel(
        db,
        application.id,
        max_attempts=7,
        retry_backoff_seconds=30.0,
        rate_limit_per_minute=120,
    )
    created = await service.create_notification(application.id, actor="key", **_payload())
    delivery = created.deliveries[0]
    assert delivery.max_attempts == 7
    assert delivery.retry_backoff_seconds == 30.0
    assert delivery.rate_limit_per_minute == 120


async def test_provider_name_from_registry(db) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    registry = create_registry_with_builtins()
    registry_service = NotificationService(create_uow_factory(db), registry=registry)
    created = await registry_service.create_notification(application.id, actor="key", **_payload())
    assert created.deliveries[0].provider == "EmailProvider"


async def test_get_notification_returns_deliveries(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    created = await service.create_notification(application.id, actor="key", **_payload())
    fetched = await service.get_notification(application.id, created.notification.id)
    assert fetched.notification.id == created.notification.id
    assert len(fetched.deliveries) == 1


async def test_get_notification_scoped_to_application(db, service) -> None:
    app_a = await _make_app(db, "a")
    app_b = await _make_app(db, "b")
    await _make_template(db, app_a.id)
    created = await service.create_notification(app_a.id, actor="key", **_payload())
    with pytest.raises(NotFoundError):
        await service.get_notification(app_b.id, created.notification.id)


async def test_list_deliveries(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    created = await service.create_notification(
        application.id,
        actor="key",
        **_payload(recipients={"email": "a@example.com", "slack": "https://hooks.example/s"}),
    )
    deliveries = await service.list_deliveries(application.id, created.notification.id)
    assert len(deliveries) == 2


async def test_cancel_pending_notification(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    created = await service.create_notification(application.id, actor="key", **_payload())
    cancelled = await service.cancel_notification(
        application.id, created.notification.id, actor="key"
    )
    assert cancelled.notification.status is NotificationStatus.CANCELLED
    assert await _count_audit(db, AuditAction.NOTIFICATION_CANCELLED) == 1


async def test_cancel_terminal_notification_rejected(db, service) -> None:
    application = await _make_app(db)
    await _make_template(db, application.id)
    created = await service.create_notification(application.id, actor="key", **_payload())
    now = datetime.now(UTC)
    async with create_uow_factory(db)() as uow:
        notification = await uow.notifications.get(created.notification.id)
        assert notification is not None
        notification.status = NotificationStatus.SENT
        notification.processed_at = now
        await uow.notifications.update(notification)
    with pytest.raises(InvalidStateError):
        await service.cancel_notification(application.id, created.notification.id, actor="key")


def create_registry_with_builtins():
    from notifly.domain.providers import ProviderRegistry

    registry = ProviderRegistry()
    register_builtins(registry)
    return registry
