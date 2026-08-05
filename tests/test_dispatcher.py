"""M7 application-layer tests: the Dispatcher (worker-side delivery execution)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from tests.helpers import FakeClock, add_channel, make_application, make_template

from notifly.application.dto import NotificationCreated
from notifly.application.services.dispatcher import DispatcherService
from notifly.application.services.notifications import NotificationService
from notifly.domain.enums import (
    AttemptStatus,
    AuditAction,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    ProviderErrorKind,
)
from notifly.domain.errors import ProviderConfigurationError
from notifly.domain.models.application import Application
from notifly.domain.models.notification import DeliveryAttempt
from notifly.domain.ports.rate_limit import InMemoryRateLimiter
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderRegistry,
    ProviderResult,
)
from notifly.infrastructure.db.orm import AuditLogRow, DeliveryAttemptRow
from notifly.infrastructure.db.uow import create_uow_factory


class _ProviderHarness:
    def __init__(self) -> None:
        self.messages: list[ProviderMessage] = []
        self.outcomes_by_channel: dict[ChannelType, list[ProviderResult]] = {}
        self.send_count = 0
        self.raise_exception: Exception | None = None

    def provider_cls(self, channel_type: ChannelType) -> type[Provider]:
        harness = self

        class _Fake(Provider):
            capabilities = ProviderCapabilities()

            @classmethod
            def from_settings(cls, settings: dict[str, Any]) -> Provider:
                if settings.get("invalid"):
                    raise ProviderConfigurationError("bad settings")
                return cls()

            async def send(self, message: ProviderMessage) -> ProviderResult:
                harness.send_count += 1
                harness.messages.append(message)
                if harness.raise_exception is not None:
                    raise harness.raise_exception
                outcomes = harness.outcomes_by_channel.get(type(self).channel_type, [])
                if outcomes:
                    return outcomes.pop(0)
                return ProviderResult(delivered=True, provider_message_id="fake-1")

        _Fake.channel_type = channel_type
        return _Fake


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


@pytest.fixture()
def harness() -> _ProviderHarness:
    return _ProviderHarness()


@pytest.fixture()
def dispatcher(db, harness, clock) -> DispatcherService:
    registry = ProviderRegistry()
    registry.register(harness.provider_cls(ChannelType.EMAIL))
    registry.register(harness.provider_cls(ChannelType.SLACK))
    return DispatcherService(
        create_uow_factory(db), registry=registry, rate_limiter=InMemoryRateLimiter(), clock=clock
    )


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
    scheduled_at: datetime | None = None,
) -> NotificationCreated:
    engine = NotificationService(create_uow_factory(db), clock=clock)
    return await engine.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients=recipients or {ChannelType.EMAIL: "alice@example.com"},
        scheduled_at=scheduled_at,
    )


async def _count(db, row_cls) -> int:
    async with db() as session:
        return int(await session.scalar(select(func.count()).select_from(row_cls)) or 0)


async def _count_audit(db, action: AuditAction) -> int:
    async with db() as session:
        count = await session.scalar(
            select(func.count(AuditLogRow.id)).where(AuditLogRow.action == action.value)
        )
        return int(count or 0)


async def _load(db, notification_id) -> tuple[Any, Any, list[DeliveryAttempt]]:
    async with create_uow_factory(db)() as uow:
        notification = await uow.notifications.get(notification_id)
        delivery = (await uow.deliveries.list_by_notification(notification_id))[0]
        delivery_id = delivery.id
    async with db() as session:
        attempt_rows = await session.scalars(
            select(DeliveryAttemptRow)
            .where(DeliveryAttemptRow.delivery_id == delivery_id)
            .order_by(DeliveryAttemptRow.attempt_number)
        )
        attempts = [_attempt_from_row(row) for row in attempt_rows]
    return notification, delivery, attempts


def _attempt_from_row(row: Any) -> DeliveryAttempt:
    return DeliveryAttempt(
        id=row.id,
        delivery_id=row.delivery_id,
        attempt_number=row.attempt_number,
        status=AttemptStatus(row.status),
        error=row.error,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
    )


async def test_successful_dispatch_sends_via_provider(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id)
    created = await _create_notification(db, clock, application)

    summary = await dispatcher.dispatch_notification(created.notification.id)

    assert summary.notification_id == created.notification.id
    assert summary.dispatched == 1
    assert not summary.skipped
    assert harness.send_count == 1
    message = harness.messages[0]
    assert message.recipient == "alice@example.com"
    assert message.subject == "Hi Alice"
    assert message.body == "Welcome Alice"
    assert message.correlation_id == created.notification.correlation_id

    notification, delivery, attempts = await _load(db, created.notification.id)
    assert notification is not None
    assert notification.status is NotificationStatus.SENT
    assert notification.processed_at is not None
    assert delivery.status is DeliveryStatus.SENT
    assert delivery.provider_message_id == "fake-1"
    assert delivery.attempts == 0
    assert len(attempts) == 1
    assert attempts[0].status is AttemptStatus.SUCCESS
    assert attempts[0].attempt_number == 1
    assert await _count_audit(db, AuditAction.DELIVERY_SENT) == 1
    assert await _count_audit(db, AuditAction.DELIVERY_FAILED) == 0


async def test_transient_failure_schedules_retry(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, max_attempts=3, retry_backoff_seconds=10.0)
    created = await _create_notification(db, clock, application)
    harness.outcomes_by_channel[ChannelType.EMAIL] = [
        ProviderResult(
            delivered=False, error="smtp timeout", error_kind=ProviderErrorKind.TRANSIENT
        )
    ]

    summary = await dispatcher.dispatch_notification(created.notification.id)

    assert summary.dispatched == 1
    assert harness.send_count == 1
    notification, delivery, attempts = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.attempts == 1
    assert delivery.last_error == "smtp timeout"
    assert delivery.next_attempt_at == clock.now() + timedelta(seconds=10.0)
    assert notification is not None
    assert notification.status is NotificationStatus.PROCESSING
    assert len(attempts) == 1
    assert attempts[0].status is AttemptStatus.FAILED
    assert await _count_audit(db, AuditAction.DELIVERY_FAILED) == 1

    await dispatcher.dispatch_notification(created.notification.id)
    assert harness.send_count == 1


async def test_transient_failure_exhausts_and_deadletters(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, max_attempts=1, retry_backoff_seconds=5.0)
    created = await _create_notification(db, clock, application)
    harness.outcomes_by_channel[ChannelType.EMAIL] = [
        ProviderResult(delivered=False, error="down", error_kind=ProviderErrorKind.TRANSIENT)
    ]

    await dispatcher.dispatch_notification(created.notification.id)

    notification, delivery, _ = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is None
    assert delivery.completed_at is not None
    assert notification is not None
    assert notification.status is NotificationStatus.FAILED


async def test_permanent_failure_deadletters(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, max_attempts=5)
    created = await _create_notification(db, clock, application)
    harness.outcomes_by_channel[ChannelType.EMAIL] = [
        ProviderResult(delivered=False, error="rejected", error_kind=ProviderErrorKind.PERMANENT)
    ]

    await dispatcher.dispatch_notification(created.notification.id)

    notification, delivery, attempts = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.attempts == 1
    assert notification is not None
    assert notification.status is NotificationStatus.FAILED
    assert attempts[0].status is AttemptStatus.FAILED


async def test_partial_when_one_channel_fails(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, channel_type=ChannelType.EMAIL)
    await add_channel(db, application.id, channel_type=ChannelType.SLACK)
    created = await _create_notification(
        db,
        clock,
        application,
        recipients={
            ChannelType.EMAIL: "a@example.com",
            ChannelType.SLACK: "https://hooks.example/slack",
        },
    )
    harness.outcomes_by_channel[ChannelType.EMAIL] = [
        ProviderResult(delivered=False, error="boom", error_kind=ProviderErrorKind.PERMANENT)
    ]

    await dispatcher.dispatch_notification(created.notification.id)

    assert harness.send_count == 2
    async with create_uow_factory(db)() as uow:
        deliveries = await uow.deliveries.list_by_notification(created.notification.id)
        notification = await uow.notifications.get(created.notification.id)
    statuses = {d.channel_type: d.status for d in deliveries}
    assert statuses[ChannelType.EMAIL] is DeliveryStatus.FAILED
    assert statuses[ChannelType.SLACK] is DeliveryStatus.SENT
    assert notification is not None
    assert notification.status is NotificationStatus.PARTIAL


async def test_rate_limited_delivery_is_deferred(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, rate_limit_per_minute=1, retry_backoff_seconds=7.0)
    first = await _create_notification(db, clock, application)
    second = await _create_notification(db, clock, application)

    await dispatcher.dispatch_notification(first.notification.id)
    summary = await dispatcher.dispatch_notification(second.notification.id)

    assert harness.send_count == 1
    assert summary.dispatched == 0
    async with create_uow_factory(db)() as uow:
        second_delivery = (await uow.deliveries.list_by_notification(second.notification.id))[0]
        second_notification = await uow.notifications.get(second.notification.id)
    assert second_delivery.status is DeliveryStatus.PENDING
    assert second_delivery.attempts == 0
    assert second_delivery.next_attempt_at == clock.now() + timedelta(seconds=7.0)
    assert second_notification is not None
    assert second_notification.status is NotificationStatus.PROCESSING
    assert await _count(db, DeliveryAttemptRow) == 1


async def test_missing_channel_config_is_permanent_failure(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)

    await dispatcher.dispatch_notification(created.notification.id)

    assert harness.send_count == 0
    notification, delivery, attempts = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.FAILED
    assert "not configured" in (delivery.last_error or "")
    assert notification is not None
    assert notification.status is NotificationStatus.FAILED
    assert attempts[0].status is AttemptStatus.FAILED


async def test_disabled_channel_is_permanent_failure(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id)
    created = await _create_notification(db, clock, application)

    async with create_uow_factory(db)() as uow:
        channel = await uow.channels.get_by_app_and_type(application.id, ChannelType.EMAIL)
        assert channel is not None
        channel.enabled = False
        channel.updated_at = clock.now()
        await uow.channels.update(channel)

    await dispatcher.dispatch_notification(created.notification.id)

    assert harness.send_count == 0
    _, delivery, _ = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.FAILED


async def test_invalid_provider_settings_is_permanent_failure(
    db, dispatcher, harness, clock
) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, config={"invalid": True})
    created = await _create_notification(db, clock, application)

    await dispatcher.dispatch_notification(created.notification.id)

    assert harness.send_count == 0
    notification, delivery, _ = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.FAILED
    assert "configuration" in (delivery.last_error or "")
    assert notification is not None
    assert notification.status is NotificationStatus.FAILED


async def test_provider_exception_is_treated_as_transient(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id, max_attempts=3)
    created = await _create_notification(db, clock, application)
    harness.raise_exception = RuntimeError("connection reset")

    await dispatcher.dispatch_notification(created.notification.id)

    assert harness.send_count == 1
    notification, delivery, _ = await _load(db, created.notification.id)
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is not None
    assert notification is not None
    assert notification.status is NotificationStatus.PROCESSING


async def test_cancelled_notification_is_skipped(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create_notification(db, clock, application)
    engine = NotificationService(create_uow_factory(db), clock=clock)
    await engine.cancel_notification(application.id, created.notification.id, actor="key")

    summary = await dispatcher.dispatch_notification(created.notification.id)

    assert summary.skipped
    assert harness.send_count == 0
    notification, delivery, _ = await _load(db, created.notification.id)
    assert notification is not None
    assert notification.status is NotificationStatus.CANCELLED
    assert delivery.status is DeliveryStatus.PENDING


async def test_unknown_notification_is_skipped(db, dispatcher, harness) -> None:
    summary = await dispatcher.dispatch_notification(uuid4())
    assert summary.skipped
    assert harness.send_count == 0


async def test_re_dispatch_is_idempotent(db, dispatcher, harness, clock) -> None:
    application = await _prepare(db, clock)
    await add_channel(db, application.id)
    created = await _create_notification(db, clock, application)

    first = await dispatcher.dispatch_notification(created.notification.id)
    second = await dispatcher.dispatch_notification(created.notification.id)

    assert first.dispatched == 1
    assert second.dispatched == 0
    assert harness.send_count == 1
