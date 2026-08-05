"""M2 persistence integration tests: SQLAlchemy repositories + Unit of Work."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from notifly.domain.enums import (
    AttemptStatus,
    AuditAction,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    OutboxEventType,
    VariableType,
)
from notifly.domain.models.application import ApiKey, Application
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.idempotency import IdempotencyRecord
from notifly.domain.models.notification import Delivery, DeliveryAttempt, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.models.template import Template, TemplateChannelContent, VariableDef
from notifly.domain.ports.repositories import UnitOfWork
from notifly.infrastructure.db.base import Base
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.infrastructure.db.uow import create_uow_factory


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture()
async def db(test_settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_engine(test_settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.fixture()
async def uow(db) -> AsyncIterator[UnitOfWork]:
    unit = create_uow_factory(db)()
    try:
        yield unit
    finally:
        await unit.close()


def make_app(name: str = "acme") -> Application:
    now = _now()
    return Application(id=uuid4(), name=name, created_at=now, updated_at=now)


def make_api_key(application_id) -> ApiKey:
    now = _now()
    return ApiKey(
        id=uuid4(),
        application_id=application_id,
        name="primary",
        key_hash="x" * 64,
        key_prefix="nf_live",
        created_at=now,
    )


def make_channel(
    application_id: object, channel_type: ChannelType = ChannelType.EMAIL
) -> ChannelConfig:
    now = _now()
    return ChannelConfig(
        id=uuid4(),
        application_id=application_id,
        channel_type=channel_type,
        name="email",
        config={"from": "no-reply@example.com"},
        max_attempts=4,
        retry_backoff_seconds=2.5,
        rate_limit_per_minute=10,
        created_at=now,
        updated_at=now,
    )


def make_template(application_id: object, event: str = "user.signup") -> Template:
    now = _now()
    return Template(
        id=uuid4(),
        application_id=application_id,
        name="signup",
        event=event,
        description="Signup welcome",
        variables=[VariableDef(name="user_name", type=VariableType.STRING, required=True)],
        channels={
            ChannelType.EMAIL: TemplateChannelContent(
                subject="Welcome {{ user_name }}", body="Hi {{ user_name }}!"
            )
        },
        created_at=now,
        updated_at=now,
    )


def make_notification(application_id: object, **kw) -> Notification:
    now = _now()
    fields: dict = {
        "id": uuid4(),
        "application_id": application_id,
        "event": "user.signup",
        "variables": {"user_name": "Ada"},
        "correlation_id": "corr-1",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(kw)
    return Notification(**fields)


def make_delivery(notification_id: object, **kw) -> Delivery:
    now = _now()
    fields: dict = {
        "id": uuid4(),
        "notification_id": notification_id,
        "channel_type": ChannelType.EMAIL,
        "provider": "smtp",
        "recipient": "ada@example.com",
        "subject": "Welcome",
        "body": "Hi Ada!",
        "created_at": now,
        "updated_at": now,
    }
    fields.update(kw)
    return Delivery(**fields)


async def test_application_roundtrip(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    await uow.commit()

    assert await uow.applications.get(app.id) == app
    assert await uow.applications.get(uuid4()) is None
    assert await uow.applications.get_by_name("acme") == app
    assert await uow.applications.get_by_name("nope") is None
    assert await uow.applications.count() == 1


async def test_application_list_pagination(uow) -> None:
    apps = [make_app(name=f"app-{i}") for i in range(5)]
    for app in apps:
        await uow.applications.add(app)
    await uow.commit()

    assert [a.id for a in await uow.applications.list_apps(limit=2, offset=0)] == [
        a.id for a in apps[:2]
    ]
    assert [a.id for a in await uow.applications.list_apps(limit=10, offset=3)] == [
        a.id for a in apps[3:]
    ]
    assert await uow.applications.count() == 5


async def test_application_unique_name_violation(uow, db) -> None:
    await uow.applications.add(make_app(name="dup"))
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        await other.applications.add(make_app(name="dup"))
        with pytest.raises(IntegrityError):
            await other.commit()
    finally:
        await other.close()


async def test_api_key_roundtrip_and_update(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    key = make_api_key(app.id)
    await uow.api_keys.add(key)
    await uow.commit()

    assert await uow.api_keys.get_by_id(key.id) == key
    assert await uow.api_keys.get_by_hash(key.key_hash) == key
    assert await uow.api_keys.get_by_hash("z" * 64) is None
    assert [k.id for k in await uow.api_keys.list_by_app(app.id)] == [key.id]

    key.revoked_at = _now()
    await uow.api_keys.update(key)
    await uow.commit()
    loaded = await uow.api_keys.get_by_id(key.id)
    assert loaded is not None
    assert loaded.revoked_at is not None
    assert loaded.active is False


async def test_api_key_unique_hash_violation(uow, db) -> None:
    app = make_app()
    await uow.applications.add(app)
    await uow.api_keys.add(make_api_key(app.id))
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        await other.api_keys.add(make_api_key(app.id))
        with pytest.raises(IntegrityError):
            await other.commit()
    finally:
        await other.close()


async def test_channel_roundtrip_and_lookups(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    channel = make_channel(app.id)
    await uow.channels.add(channel)
    await uow.commit()

    assert await uow.channels.get(channel.id) == channel
    assert await uow.channels.get_by_app_and_type(app.id, ChannelType.EMAIL) == channel
    assert await uow.channels.get_by_app_and_type(app.id, ChannelType.WEBHOOK) is None
    assert [c.id for c in await uow.channels.list_by_app(app.id)] == [channel.id]

    channel.name = "renamed"
    channel.enabled = False
    await uow.channels.update(channel)
    await uow.commit()
    loaded = await uow.channels.get(channel.id)
    assert loaded is not None
    assert loaded.name == "renamed"
    assert loaded.enabled is False


async def test_channel_unique_app_type_violation(uow, db) -> None:
    app = make_app()
    await uow.applications.add(app)
    await uow.channels.add(make_channel(app.id))
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        await other.channels.add(make_channel(app.id))
        with pytest.raises(IntegrityError):
            await other.commit()
    finally:
        await other.close()


async def test_template_roundtrip_json_columns(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    template = make_template(app.id)
    await uow.templates.add(template)
    await uow.commit()

    loaded = await uow.templates.get(template.id)
    assert loaded == template
    assert loaded is not None
    assert loaded.variables[0].name == "user_name"
    assert loaded.variables[0].type is VariableType.STRING
    assert loaded.channels[ChannelType.EMAIL].subject == "Welcome {{ user_name }}"
    assert await uow.templates.get_by_app_and_event(app.id, "user.signup") == template
    assert await uow.templates.get_by_app_and_event(app.id, "other.event") is None
    assert [t.id for t in await uow.templates.list_by_app(app.id)] == [template.id]


async def test_template_update_and_delete(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    template = make_template(app.id)
    await uow.templates.add(template)
    await uow.commit()

    template.event = "user.signup.v2"
    template.channels[ChannelType.EMAIL].subject = "New subject"
    await uow.templates.update(template)
    await uow.commit()
    loaded = await uow.templates.get(template.id)
    assert loaded is not None
    assert loaded.event == "user.signup.v2"
    assert loaded.channels[ChannelType.EMAIL].subject == "New subject"

    await uow.templates.delete(template)
    await uow.commit()
    assert await uow.templates.get(template.id) is None


async def test_template_unique_app_event_violation(uow, db) -> None:
    app = make_app()
    await uow.applications.add(app)
    await uow.templates.add(make_template(app.id, event="e1"))
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        await other.templates.add(make_template(app.id, event="e1"))
        with pytest.raises(IntegrityError):
            await other.commit()
    finally:
        await other.close()


async def test_notification_roundtrip(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    await uow.commit()

    loaded = await uow.notifications.get(notification.id)
    assert loaded == notification
    assert loaded is not None
    assert loaded.status is NotificationStatus.PENDING


async def test_notification_search_and_count(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    now = _now()
    base = {
        "created_at": now,
        "updated_at": now,
        "scheduled_at": now - timedelta(minutes=1),
    }
    n1 = make_notification(app.id, event="a", status=NotificationStatus.SENT, **base)
    n2 = make_notification(app.id, event="b", status=NotificationStatus.FAILED, **base)
    n3 = make_notification(
        app.id,
        event="b",
        status=NotificationStatus.PENDING,
        created_at=now + timedelta(hours=1),
        updated_at=now + timedelta(hours=1),
        scheduled_at=now + timedelta(minutes=5),
    )
    for n in (n1, n2, n3):
        await uow.notifications.add(n)
    await uow.commit()

    all_ = await uow.notifications.search(application_id=app.id)
    assert {n.id for n in all_} == {n1.id, n2.id, n3.id}
    assert await uow.notifications.count(application_id=app.id) == 3
    assert (
        await uow.notifications.count(application_id=app.id, status=NotificationStatus.FAILED) == 1
    )
    assert await uow.notifications.count(application_id=app.id, event="b") == 2
    assert (
        await uow.notifications.count(
            application_id=app.id,
            created_after=now - timedelta(seconds=30),
            created_before=now + timedelta(seconds=30),
        )
        == 2
    )

    by_status = await uow.notifications.search(
        application_id=app.id, status=NotificationStatus.FAILED
    )
    assert [n.id for n in by_status] == [n2.id]

    by_event = await uow.notifications.search(application_id=app.id, event="b")
    assert {n.id for n in by_event} == {n2.id, n3.id}

    by_window = await uow.notifications.search(
        application_id=app.id,
        created_after=now - timedelta(seconds=30),
        created_before=now + timedelta(seconds=30),
    )
    assert {n.id for n in by_window} == {n1.id, n2.id}

    page = await uow.notifications.search(application_id=app.id, limit=2, offset=0)
    assert len(page) == 2


async def test_notification_list_due_scheduled(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    now = _now()
    due = make_notification(app.id, scheduled_at=now - timedelta(minutes=2))
    future = make_notification(app.id, scheduled_at=now + timedelta(hours=1))
    not_scheduled = make_notification(app.id, scheduled_at=None)
    for n in (due, future, not_scheduled):
        await uow.notifications.add(n)
    await uow.commit()

    due_list = await uow.notifications.list_due_scheduled(now=now, limit=10)
    assert [n.id for n in due_list] == [due.id]


async def test_notification_list_deadletters(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    failed = make_notification(app.id, status=NotificationStatus.FAILED)
    pending = make_notification(app.id, status=NotificationStatus.PENDING)
    await uow.notifications.add(failed)
    await uow.notifications.add(pending)
    await uow.commit()

    dead = await uow.notifications.list_deadletters(application_id=app.id, limit=10, offset=0)
    assert [n.id for n in dead] == [failed.id]


async def test_notification_update(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    await uow.commit()

    notification.status = NotificationStatus.SENT
    notification.processed_at = _now()
    notification.retry_count = 1
    await uow.notifications.update(notification)
    await uow.commit()

    loaded = await uow.notifications.get(notification.id)
    assert loaded is not None
    assert loaded.status is NotificationStatus.SENT
    assert loaded.processed_at is not None
    assert loaded.retry_count == 1


async def test_delivery_roundtrip_and_search(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    delivery = make_delivery(notification.id)
    await uow.deliveries.add(delivery)
    await uow.commit()

    assert await uow.deliveries.get(delivery.id) == delivery
    assert [d.id for d in await uow.deliveries.list_by_notification(notification.id)] == [
        delivery.id
    ]

    found = await uow.deliveries.search(
        application_id=app.id,
        notification_id=notification.id,
        channel_type=ChannelType.EMAIL,
        status=DeliveryStatus.PENDING,
    )
    assert [d.id for d in found] == [delivery.id]
    assert await uow.deliveries.search(application_id=app.id, status=DeliveryStatus.SENT) == []
    assert await uow.deliveries.search(application_id=uuid4()) == []


async def test_delivery_add_many_and_get_for_update(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    d1 = make_delivery(notification.id)
    d2 = make_delivery(notification.id, channel_type=ChannelType.WEBHOOK, provider="generic")
    await uow.deliveries.add_many([d1, d2])
    await uow.commit()

    locked = await uow.deliveries.get_for_update(d1.id)
    assert locked == d1
    assert await uow.deliveries.get_for_update(uuid4()) is None


async def test_delivery_list_due_retry(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    now = _now()
    due = make_delivery(
        notification.id, status=DeliveryStatus.PENDING, next_attempt_at=now - timedelta(minutes=1)
    )
    future = make_delivery(
        notification.id, status=DeliveryStatus.PENDING, next_attempt_at=now + timedelta(hours=1)
    )
    sent = make_delivery(notification.id, status=DeliveryStatus.SENT, next_attempt_at=None)
    await uow.deliveries.add_many([due, future, sent])
    await uow.commit()

    due_list = await uow.deliveries.list_due_retry(now=now, limit=10)
    assert [d.id for d in due_list] == [due.id]


async def test_delivery_update(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    delivery = make_delivery(notification.id)
    await uow.deliveries.add(delivery)
    await uow.commit()

    delivery.status = DeliveryStatus.SENT
    delivery.provider_message_id = "msg-1"
    delivery.attempts = 1
    delivery.completed_at = _now()
    await uow.deliveries.update(delivery)
    await uow.commit()

    loaded = await uow.deliveries.get(delivery.id)
    assert loaded is not None
    assert loaded.status is DeliveryStatus.SENT
    assert loaded.provider_message_id == "msg-1"


async def test_delivery_attempt_add(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    delivery = make_delivery(notification.id)
    await uow.deliveries.add(delivery)
    attempt = DeliveryAttempt(
        id=uuid4(),
        delivery_id=delivery.id,
        attempt_number=1,
        status=AttemptStatus.FAILED,
        error="boom",
        duration_ms=120,
        created_at=_now(),
    )
    await uow.delivery_attempts.add(attempt)
    await uow.commit()


async def test_audit_add_and_search(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    entry = AuditLogEntry(
        id=uuid4(),
        application_id=app.id,
        actor="system",
        action=AuditAction.TEMPLATE_CREATED,
        resource_type="template",
        resource_id=uuid4(),
        correlation_id="corr-9",
        payload={"event": "user.signup"},
        created_at=_now(),
    )
    await uow.audit.add(entry)
    await uow.commit()

    assert [e.id for e in await uow.audit.search(application_id=app.id, limit=10, offset=0)] == [
        entry.id
    ]
    assert await uow.audit.search(application_id=uuid4(), limit=10, offset=0) == []


async def test_outbox_roundtrip_and_marking(uow) -> None:
    event = OutboxEvent(
        id=uuid4(),
        event_type=OutboxEventType.NOTIFICATION_CREATED,
        payload={"notification_id": str(uuid4())},
        correlation_id="corr-7",
        created_at=_now(),
    )
    await uow.outbox.add(event)
    await uow.commit()

    pending = await uow.outbox.get_pending(limit=10)
    assert [e.id for e in pending] == [event.id]

    locked = await uow.outbox.get_pending_for_update(limit=10)
    assert [e.id for e in locked] == [event.id]

    now = _now()
    await uow.outbox.mark_published(event.id, now)
    await uow.commit()
    assert await uow.outbox.get_pending(limit=10) == []
    assert await uow.outbox.get_pending_for_update(limit=10) == []


async def test_outbox_mark_failed(uow) -> None:
    event = OutboxEvent(
        id=uuid4(),
        event_type=OutboxEventType.NOTIFICATION_CREATED,
        created_at=_now(),
    )
    await uow.outbox.add(event)
    await uow.commit()

    await uow.outbox.mark_failed(event.id, "provider unreachable")
    await uow.commit()
    assert await uow.outbox.get_pending(limit=10) == []


async def test_outbox_status_persisted(uow, db) -> None:
    event = OutboxEvent(
        id=uuid4(),
        event_type=OutboxEventType.NOTIFICATION_CREATED,
        created_at=_now(),
    )
    await uow.outbox.add(event)
    await uow.commit()

    now = _now()
    await uow.outbox.mark_published(event.id, now)
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        assert await other.outbox.get_pending_for_update(limit=10) == []
    finally:
        await other.close()


async def test_idempotency_roundtrip(uow) -> None:
    app = make_app()
    await uow.applications.add(app)
    record = IdempotencyRecord(
        id=uuid4(),
        application_id=app.id,
        key="key-123",
        request_hash="h" * 64,
        notification_id=uuid4(),
        created_at=_now(),
    )
    await uow.idempotency.add(record)
    await uow.commit()

    assert await uow.idempotency.get(app.id, "key-123") == record
    assert await uow.idempotency.get(app.id, "missing") is None


async def test_idempotency_unique_app_key(uow, db) -> None:
    app = make_app()
    await uow.applications.add(app)
    base = {
        "id": uuid4(),
        "application_id": app.id,
        "key": "dup",
        "request_hash": "h" * 64,
        "created_at": _now(),
    }
    await uow.idempotency.add(IdempotencyRecord(**base, notification_id=uuid4()))
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        await other.idempotency.add(IdempotencyRecord(**base, notification_id=uuid4()))
        with pytest.raises(IntegrityError):
            await other.commit()
    finally:
        await other.close()


async def test_update_missing_rows_are_noops(uow) -> None:
    missing = uuid4()
    await uow.api_keys.update(make_api_key(missing))
    await uow.channels.update(make_channel(missing))
    await uow.templates.update(make_template(missing))
    await uow.templates.delete(make_template(missing))
    await uow.notifications.update(make_notification(missing))
    await uow.deliveries.update(make_delivery(missing))
    await uow.outbox.mark_published(missing, _now())
    await uow.outbox.mark_failed(missing, "boom")
    await uow.commit()


async def test_coerce_preserves_tz_aware_datetime(uow) -> None:
    from notifly.infrastructure.db.repositories import _as_utc

    aware = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert _as_utc(aware) == aware
    assert _as_utc(aware).utcoffset() == timedelta(0)


async def test_attempt_row_mapping_roundtrip(uow, db) -> None:
    from notifly.infrastructure.db import orm
    from notifly.infrastructure.db.repositories import _attempt_from_row

    attempt = DeliveryAttempt(
        id=uuid4(),
        delivery_id=uuid4(),
        attempt_number=1,
        status=AttemptStatus.SUCCESS,
        error=None,
        duration_ms=12,
        created_at=_now(),
    )
    await uow.delivery_attempts.add(attempt)
    await uow.commit()

    async with db() as session:
        row = await session.get(orm.DeliveryAttemptRow, attempt.id)
    assert _attempt_from_row(row) == attempt


async def test_uow_rollback_discards_changes(uow, db) -> None:
    await uow.applications.add(make_app(name="rollback-app"))
    await uow.rollback()
    await uow.close()

    other = create_uow_factory(db)()
    try:
        assert await other.applications.count() == 0
        assert await other.applications.get_by_name("rollback-app") is None
    finally:
        await other.close()


async def test_uow_atomic_commit_multiple_repos(uow, db) -> None:
    app = make_app()
    await uow.applications.add(app)
    notification = make_notification(app.id)
    await uow.notifications.add(notification)
    event = OutboxEvent(
        id=uuid4(),
        event_type=OutboxEventType.NOTIFICATION_CREATED,
        payload={"notification_id": str(notification.id)},
        created_at=_now(),
    )
    await uow.outbox.add(event)
    await uow.commit()

    other = create_uow_factory(db)()
    try:
        assert await other.applications.count() == 1
        assert await other.notifications.get(notification.id) == notification
        assert len(await other.outbox.get_pending(limit=10)) == 1
    finally:
        await other.close()
