"""M7 application-layer tests: the Outbox relay and time-based triggers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from tests.helpers import FakeClock, add_channel, make_application, make_template

from notifly.application.dto import NotificationCreated
from notifly.application.services.dispatcher import DispatcherService
from notifly.application.services.notifications import NotificationService
from notifly.application.services.outbox import DISPATCH_TASK, OutboxPublisher
from notifly.domain.enums import (
    ChannelType,
    NotificationStatus,
    OutboxEventType,
    OutboxStatus,
    ProviderErrorKind,
)
from notifly.domain.models.application import Application
from notifly.domain.ports.tasks import InMemoryTaskDispatcher
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderRegistry,
    ProviderResult,
)
from notifly.infrastructure.db.orm import NotificationRow, OutboxEventRow
from notifly.infrastructure.db.uow import create_uow_factory


class _Outcome:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered


class _FakeProvider(Provider):
    channel_type = ChannelType.EMAIL
    capabilities = ProviderCapabilities()

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        return cls()

    async def send(self, message: ProviderMessage) -> ProviderResult:
        outcome = _OUTCOMES.pop(0) if _OUTCOMES else _Outcome()
        if outcome.delivered:
            return ProviderResult(delivered=True, provider_message_id="fake-1")
        return ProviderResult(delivered=False, error="boom", error_kind=ProviderErrorKind.TRANSIENT)


_OUTCOMES: list[_Outcome] = []


@pytest.fixture(autouse=True)
def _clear_outcomes() -> None:
    _OUTCOMES.clear()


class _FailingDispatcher:
    async def enqueue(self, task: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("redis is down")


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


async def _prepare(db, clock: FakeClock) -> Application:
    application = await make_application(db)
    await make_template(db, application.id)
    return application


async def _make_notification(
    db,
    clock: FakeClock,
    application: Application,
    *,
    scheduled_at: datetime | None = None,
) -> NotificationCreated:
    engine = NotificationService(create_uow_factory(db), clock=clock)
    return await engine.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients={ChannelType.EMAIL: "alice@example.com"},
        scheduled_at=scheduled_at,
    )


async def _count_outbox_events(db, status: OutboxStatus | None = None) -> int:
    async with db() as session:
        stmt = select(func.count(OutboxEventRow.id))
        if status is not None:
            stmt = stmt.where(OutboxEventRow.status == status.value)
        return int(await session.scalar(stmt) or 0)


async def _make_due_retry(db, clock: FakeClock) -> NotificationCreated:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, max_attempts=3, retry_backoff_seconds=5.0)
    created = await _make_notification(db, clock, application)

    _OUTCOMES.append(_Outcome(delivered=False))
    registry = ProviderRegistry()
    registry.register(_FakeProvider)
    await DispatcherService(
        create_uow_factory(db), registry=registry, clock=clock
    ).dispatch_notification(created.notification.id)
    clock.advance(seconds=10.0)
    return created


async def test_publish_pending_relays_and_marks_published(db, clock) -> None:
    application = await _prepare(db, clock)
    created = await _make_notification(db, clock, application)
    dispatcher = InMemoryTaskDispatcher()
    publisher = OutboxPublisher(create_uow_factory(db), dispatcher, clock=clock)

    published = await publisher.publish_pending()

    assert published == 1
    assert dispatcher.jobs == [(DISPATCH_TASK, {"notification_id": str(created.notification.id)})]
    assert await _count_outbox_events(db, OutboxStatus.PUBLISHED) == 1
    assert await _count_outbox_events(db, OutboxStatus.PENDING) == 0


async def test_publish_pending_marks_failed_on_transport_error(db, clock) -> None:
    application = await _prepare(db, clock)
    await _make_notification(db, clock, application)
    publisher = OutboxPublisher(create_uow_factory(db), _FailingDispatcher(), clock=clock)

    published = await publisher.publish_pending()

    assert published == 0
    assert await _count_outbox_events(db, OutboxStatus.FAILED) == 1
    async with db() as session:
        row = await session.scalar(select(OutboxEventRow).where(OutboxEventRow.status == "failed"))
    assert row is not None
    assert row.attempts == 1
    assert "redis is down" in (row.last_error or "")


async def test_requeue_due_retries_seeds_and_relays(db, clock) -> None:
    created = await _make_due_retry(db, clock)
    relay = OutboxPublisher(create_uow_factory(db), InMemoryTaskDispatcher(), clock=clock)

    seeded = await relay.requeue_due_retries(now=clock.now())

    assert seeded == 1
    async with db() as session:
        events = await session.scalars(
            select(OutboxEventRow).where(OutboxEventRow.event_type == "delivery.retry")
        )
        rows = list(events)
    assert len(rows) == 1
    assert rows[0].payload == {"notification_id": str(created.notification.id)}

    published = await relay.publish_pending()
    assert published == 2
    assert await _count_outbox_events(db, OutboxStatus.PUBLISHED) == 2


async def test_requeue_due_retries_skips_cancelled(db, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, max_attempts=3, retry_backoff_seconds=5.0)
    created = await _make_notification(db, clock, application)

    _OUTCOMES.append(_Outcome(delivered=False))
    registry = ProviderRegistry()
    registry.register(_FakeProvider)
    await DispatcherService(
        create_uow_factory(db), registry=registry, clock=clock
    ).dispatch_notification(created.notification.id)

    clock.advance(seconds=10.0)
    await NotificationService(create_uow_factory(db), clock=clock).cancel_notification(
        application.id, created.notification.id, actor="key"
    )
    relay = OutboxPublisher(create_uow_factory(db), InMemoryTaskDispatcher(), clock=clock)
    seeded = await relay.requeue_due_retries(now=clock.now())

    assert seeded == 0
    async with db() as session:
        count = await session.scalar(
            select(func.count(OutboxEventRow.id)).where(
                OutboxEventRow.event_type == OutboxEventType.DELIVERY_RETRY.value
            )
        )
    assert int(count or 0) == 0


async def test_publish_due_scheduled_releases_and_marks_processing(db, clock) -> None:
    application = await _prepare(db, clock)
    scheduled = clock.now() - timedelta(hours=1)
    created = await _make_notification(db, clock, application, scheduled_at=scheduled)
    assert await _count_outbox_events(db) == 0

    relay = OutboxPublisher(create_uow_factory(db), InMemoryTaskDispatcher(), clock=clock)
    released = await relay.publish_due_scheduled(now=clock.now())

    assert released == 1
    async with db() as session:
        notification_row = await session.get(NotificationRow, created.notification.id)
        event_row = await session.scalar(select(OutboxEventRow))
    assert notification_row is not None
    assert notification_row.status == NotificationStatus.PROCESSING.value
    assert event_row is not None
    assert event_row.event_type == OutboxEventType.NOTIFICATION_CREATED.value
    assert event_row.payload["notification_id"] == str(created.notification.id)

    published = await relay.publish_pending()
    assert published == 1


async def test_publish_due_scheduled_ignores_future_and_immediate(db, clock) -> None:
    application = await _prepare(db, clock)
    await _make_notification(db, clock, application, scheduled_at=clock.now() + timedelta(hours=1))
    await _make_notification(db, clock, application)

    relay = OutboxPublisher(create_uow_factory(db), InMemoryTaskDispatcher(), clock=clock)
    released = await relay.publish_due_scheduled(now=clock.now())

    assert released == 0
    assert await _count_outbox_events(db) == 1
