"""M8 application-layer tests: the Operations (query + dead-letter recovery) service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from tests.helpers import FakeClock, make_application, make_template

from notifly.application.dto import NotificationCreated
from notifly.application.services.notifications import NotificationService
from notifly.application.services.operations import OperationsService
from notifly.domain.enums import (
    AuditAction,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    OutboxEventType,
)
from notifly.domain.errors import InvalidStateError, NotFoundError
from notifly.domain.models.application import Application
from notifly.domain.models.notification import Delivery
from notifly.infrastructure.db.orm import AuditLogRow, OutboxEventRow
from notifly.infrastructure.db.uow import create_uow_factory


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture()
def ops(db) -> OperationsService:
    fake_clock = FakeClock(datetime(2026, 1, 1, tzinfo=UTC))
    return OperationsService(create_uow_factory(db), clock=fake_clock)


async def _prepare(db, clock: FakeClock) -> Application:
    application = await make_application(db)
    await make_template(db, application.id)
    return application


async def _create_notification(
    db,
    clock: FakeClock,
    application: Application,
    *,
    recipients: dict[ChannelType, str] | None = None,
) -> NotificationCreated:
    engine = NotificationService(create_uow_factory(db), clock=clock)
    return await engine.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients=recipients or {ChannelType.EMAIL: "alice@example.com"},
    )


async def _set_delivery_states(
    db, notification_id, *, delivery_statuses: list[DeliveryStatus]
) -> list[Delivery]:
    now = datetime.now(UTC)
    async with create_uow_factory(db)() as uow:
        deliveries = await uow.deliveries.list_by_notification(notification_id)
        assert len(deliveries) == len(delivery_statuses)
        for delivery, status in zip(deliveries, delivery_statuses, strict=True):
            delivery.status = status
            delivery.updated_at = now
            if status is DeliveryStatus.FAILED:
                delivery.attempts = 3
                delivery.last_error = "boom"
                delivery.completed_at = now
            await uow.deliveries.update(delivery)
        return deliveries


async def _set_notification_status(db, notification_id, status: NotificationStatus) -> None:
    async with create_uow_factory(db)() as uow:
        notification = await uow.notifications.get(notification_id)
        assert notification is not None
        notification.status = status
        notification.updated_at = datetime.now(UTC)
        await uow.notifications.update(notification)


async def _load_outbox_events(db) -> list[OutboxEventRow]:
    async with db() as session:
        rows = await session.scalars(select(OutboxEventRow).order_by(OutboxEventRow.created_at))
        return list(rows)


async def _count_audit(db, action: AuditAction) -> int:
    async with db() as session:
        count = await session.scalar(
            select(func.count(AuditLogRow.id)).where(AuditLogRow.action == action.value)
        )
        return int(count or 0)


async def test_list_notifications_filters_and_paginates(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)
    await _set_notification_status(db, created.notification.id, NotificationStatus.SENT)

    page = await ops.list_notifications(application.id, status=NotificationStatus.SENT, limit=10)
    assert page.total == 1
    assert [item.id for item in page.items] == [created.notification.id]

    page = await ops.list_notifications(application.id, status=NotificationStatus.PENDING, limit=10)
    assert page.total == 0
    assert page.items == []


async def test_list_notifications_filters_by_event_and_window(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    await _create_notification(db, clock, application)

    page = await ops.list_notifications(
        application.id, event="user_welcome", created_before=clock.now() + timedelta(seconds=1)
    )
    assert page.total == 1
    page = await ops.list_notifications(application.id, event="nope")
    assert page.total == 0
    page = await ops.list_notifications(
        application.id, created_after=clock.now() + timedelta(seconds=1)
    )
    assert page.total == 0


async def test_list_notifications_scoped_to_application(db, ops, clock) -> None:
    application_a = await _prepare(db, clock)
    application_b = await make_application(db, "other")
    await make_template(db, application_b.id)
    created = await _create_notification(db, clock, application_a)
    await _create_notification(db, clock, application_b)

    page = await ops.list_notifications(application_a.id)
    assert page.total == 1
    assert page.items[0].id == created.notification.id


async def test_list_deliveries_filters(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)
    await _set_delivery_states(db, created.notification.id, delivery_statuses=[DeliveryStatus.SENT])

    page = await ops.list_deliveries(
        application.id, channel_type=ChannelType.EMAIL, status=DeliveryStatus.SENT
    )
    assert page.total == 1
    assert page.items[0].notification_id == created.notification.id
    page = await ops.list_deliveries(application.id, status=DeliveryStatus.FAILED)
    assert page.total == 0


async def test_get_application(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    loaded = await ops.get_application(application.id)
    assert loaded.id == application.id
    assert loaded.name == application.name


async def test_get_application_missing_raises_not_found(db, ops) -> None:
    with pytest.raises(NotFoundError):
        await ops.get_application(uuid4())


async def test_list_deadletters_returns_only_failed(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    failed = await _create_notification(db, clock, application)
    sent = await _create_notification(db, clock, application)
    await _set_notification_status(db, sent.notification.id, NotificationStatus.SENT)
    await _set_notification_status(db, failed.notification.id, NotificationStatus.FAILED)
    await _set_delivery_states(
        db, failed.notification.id, delivery_statuses=[DeliveryStatus.FAILED]
    )

    page = await ops.list_deadletters(application.id)
    assert page.total == 1
    assert page.items[0].id == failed.notification.id


async def test_list_audit_paginates(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    await _create_notification(db, clock, application)
    page = await ops.list_audit(application.id, limit=1)
    assert page.total >= 1
    assert len(page.items) == 1


async def test_retry_resets_failed_delivery_and_emits_event(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)
    await _set_notification_status(db, created.notification.id, NotificationStatus.FAILED)
    await _set_delivery_states(
        db, created.notification.id, delivery_statuses=[DeliveryStatus.FAILED]
    )

    result = await ops.retry_notification(
        application.id, created.notification.id, actor="ops-user", correlation_id="c1"
    )

    assert result.notification.status is NotificationStatus.PENDING
    assert result.notification.processed_at is None
    assert len(result.deliveries) == 1
    assert result.deliveries[0].status is DeliveryStatus.PENDING
    assert result.deliveries[0].attempts == 0
    assert result.deliveries[0].last_error is None
    assert result.deliveries[0].completed_at is None
    assert result.deliveries[0].next_attempt_at is not None

    events = await _load_outbox_events(db)
    retried = [e for e in events if e.event_type == OutboxEventType.NOTIFICATION_RETRIED.value]
    assert len(retried) == 1
    assert retried[0].payload == {
        "notification_id": str(created.notification.id),
        "event": "user_welcome",
    }
    assert await _count_audit(db, AuditAction.NOTIFICATION_RETRIED) == 1


async def test_retry_partial_only_resets_failed_delivery(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(
        db,
        clock,
        application,
        recipients={
            ChannelType.EMAIL: "a@example.com",
            ChannelType.SLACK: "https://hooks.example/slack",
        },
    )
    await _set_notification_status(db, created.notification.id, NotificationStatus.PARTIAL)
    await _set_delivery_states(
        db, created.notification.id, delivery_statuses=[DeliveryStatus.FAILED, DeliveryStatus.SENT]
    )

    result = await ops.retry_notification(application.id, created.notification.id, actor="ops-user")

    statuses = {d.channel_type: d.status for d in result.deliveries}
    assert statuses[ChannelType.EMAIL] is DeliveryStatus.PENDING
    assert statuses[ChannelType.SLACK] is DeliveryStatus.SENT
    sent = next(d for d in result.deliveries if d.channel_type is ChannelType.SLACK)
    assert sent.attempts == 0
    assert result.notification.status is NotificationStatus.PENDING


async def test_retry_rejects_non_failed_notification(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)
    await _set_notification_status(db, created.notification.id, NotificationStatus.SENT)
    await _set_delivery_states(db, created.notification.id, delivery_statuses=[DeliveryStatus.SENT])

    with pytest.raises(InvalidStateError):
        await ops.retry_notification(application.id, created.notification.id, actor="ops-user")


async def test_retry_unknown_notification_raises_not_found(db, ops) -> None:
    application = await make_application(db)
    with pytest.raises(NotFoundError):
        await ops.retry_notification(application.id, uuid4(), actor="ops-user")


async def test_retry_other_application_raises_not_found(db, ops, clock) -> None:
    application_a = await _prepare(db, clock)
    application_b = await make_application(db, "other")
    created = await _create_notification(db, clock, application_a)
    await _set_notification_status(db, created.notification.id, NotificationStatus.FAILED)
    await _set_delivery_states(
        db, created.notification.id, delivery_statuses=[DeliveryStatus.FAILED]
    )

    with pytest.raises(NotFoundError):
        await ops.retry_notification(application_b.id, created.notification.id, actor="ops-user")


async def test_retry_changes_are_durable(db, ops, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)
    await _set_notification_status(db, created.notification.id, NotificationStatus.FAILED)
    await _set_delivery_states(
        db, created.notification.id, delivery_statuses=[DeliveryStatus.FAILED]
    )

    await ops.retry_notification(application.id, created.notification.id, actor="ops-user")

    async with create_uow_factory(db)() as uow:
        notification = await uow.notifications.get(created.notification.id)
        delivery = (await uow.deliveries.list_by_notification(created.notification.id))[0]
    assert notification is not None
    assert notification.status is NotificationStatus.PENDING
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.attempts == 0
