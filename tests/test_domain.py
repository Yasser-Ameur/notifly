"""Domain layer unit tests: enums, errors, entities, state transitions, providers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from notifly.domain.enums import (
    AttemptStatus,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    OutboxStatus,
)
from notifly.domain.errors import (
    AlreadyExistsError,
    AuthenticationError,
    ConflictError,
    IdempotencyConflictError,
    InvalidDataError,
    InvalidStateError,
    NotFoundError,
    NotiFlyError,
    ProviderError,
    ProviderNotRegisteredError,
    RateLimitExceededError,
)
from notifly.domain.models.application import ApiKey, Application
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.idempotency import IdempotencyRecord
from notifly.domain.models.notification import Delivery, DeliveryAttempt, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.models.template import Template, TemplateChannelContent, VariableDef
from notifly.domain.ports.clock import SystemClock
from notifly.domain.ports.rate_limit import InMemoryRateLimiter
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderRegistry,
    ProviderResult,
)

NOW = datetime.now(UTC)
APP_ID = uuid4()


def make_notification(**overrides: object) -> Notification:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "application_id": APP_ID,
        "event": "user_welcome",
        "correlation_id": "corr-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return Notification(**defaults)


def make_delivery(**overrides: object) -> Delivery:
    defaults: dict[str, object] = {
        "id": uuid4(),
        "notification_id": uuid4(),
        "channel_type": ChannelType.EMAIL,
        "provider": "email_smtp",
        "recipient": "a@example.com",
        "body": "hello",
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return Delivery(**defaults)


class TestEnums:
    def test_channel_values(self) -> None:
        assert ChannelType.EMAIL == "email"
        assert ChannelType.WEBHOOK == "webhook"
        assert list(ChannelType) == [
            ChannelType.EMAIL,
            ChannelType.SLACK,
            ChannelType.DISCORD,
            ChannelType.TEAMS,
            ChannelType.WEBHOOK,
        ]


class TestErrors:
    def test_hierarchy_codes(self) -> None:
        assert NotiFlyError().code == "internal_error"
        assert NotFoundError().status_code == 404
        assert InvalidDataError().status_code == 400
        assert AuthenticationError().status_code == 401
        assert ConflictError().status_code == 409
        assert AlreadyExistsError().code == "already_exists"
        assert IdempotencyConflictError().code == "idempotency_conflict"
        assert InvalidStateError().status_code == 409
        assert RateLimitExceededError().status_code == 429
        assert ProviderError().status_code == 502
        assert ProviderNotRegisteredError().status_code == 400

    def test_error_carries_detail_and_payload(self) -> None:
        error = InvalidDataError("bad input", payload={"field": "name"})
        assert error.detail == "bad input"
        assert error.payload == {"field": "name"}
        assert str(error) == "bad input"


class TestModels:
    def test_application(self) -> None:
        app = Application(id=uuid4(), name="acme", created_at=NOW, updated_at=NOW)
        assert app.name == "acme"

    def test_api_key_active(self) -> None:
        key = ApiKey(
            id=uuid4(),
            application_id=APP_ID,
            name="default",
            key_hash="h",
            key_prefix="notifly_",
            created_at=NOW,
        )
        assert key.active is True
        revoked = key.model_copy(update={"revoked_at": NOW})
        assert revoked.active is False

    def test_channel_config_defaults(self) -> None:
        channel = ChannelConfig(
            id=uuid4(),
            application_id=APP_ID,
            channel_type=ChannelType.SLACK,
            name="ops",
            created_at=NOW,
            updated_at=NOW,
        )
        assert channel.max_attempts == 3
        assert channel.rate_limit_per_minute is None
        assert channel.enabled is True

    def test_template_requires_channel_content(self) -> None:
        with pytest.raises(InvalidDataError):
            Template(
                id=uuid4(),
                application_id=APP_ID,
                name="welcome",
                event="user_welcome",
                channels={},
                created_at=NOW,
                updated_at=NOW,
            )

    def test_template_variable_name_pattern(self) -> None:
        with pytest.raises(ValueError):
            VariableDef(name="not valid!", type="string")

    def test_template_variable_names(self) -> None:
        template = Template(
            id=uuid4(),
            application_id=APP_ID,
            name="welcome",
            event="user_welcome",
            variables=[
                VariableDef(name="name", type="string", required=True),
                VariableDef(name="count", type="number", required=False, default=0),
            ],
            channels={
                ChannelType.EMAIL: TemplateChannelContent(
                    subject="Hi {{ name }}", body="Hello {{ name }}"
                )
            },
            created_at=NOW,
            updated_at=NOW,
        )
        assert template.variable_names() == {"name", "count"}

    def test_audit_entry(self) -> None:
        entry = AuditLogEntry(
            id=uuid4(),
            application_id=APP_ID,
            actor="notifly_abc",
            action="application.created",
            resource_type="application",
            correlation_id="corr-1",
            created_at=NOW,
        )
        assert entry.actor == "notifly_abc"

    def test_idempotency_record(self) -> None:
        record = IdempotencyRecord(
            id=uuid4(),
            application_id=APP_ID,
            key="k1",
            request_hash="h",
            notification_id=uuid4(),
            created_at=NOW,
        )
        assert record.key == "k1"

    def test_delivery_attempt(self) -> None:
        attempt = DeliveryAttempt(
            id=uuid4(),
            delivery_id=uuid4(),
            attempt_number=1,
            status=AttemptStatus.SUCCESS,
            duration_ms=12,
            created_at=NOW,
        )
        assert attempt.duration_ms == 12


class TestOutboxEvent:
    def test_mark_published(self) -> None:
        event = OutboxEvent(
            id=uuid4(),
            event_type="notification.created",
            payload={"notification_id": str(uuid4())},
            correlation_id="corr-1",
            created_at=NOW,
        )
        event.mark_published(NOW)
        assert event.status is OutboxStatus.PUBLISHED
        assert event.published_at == NOW

    def test_mark_failed(self) -> None:
        event = OutboxEvent(
            id=uuid4(),
            event_type="notification.created",
            created_at=NOW,
        )
        event.mark_failed("redis down")
        assert event.status is OutboxStatus.FAILED
        assert event.attempts == 1
        assert event.last_error == "redis down"


class TestNotificationTransitions:
    def test_mark_processing(self) -> None:
        notification = make_notification()
        notification.mark_processing()
        assert notification.status is NotificationStatus.PROCESSING

    def test_mark_processing_cancelled_raises(self) -> None:
        notification = make_notification(status=NotificationStatus.CANCELLED)
        with pytest.raises(InvalidStateError):
            notification.mark_processing()

    def test_cancel(self) -> None:
        notification = make_notification()
        notification.cancel()
        assert notification.status is NotificationStatus.CANCELLED

    def test_cancel_terminal_raises(self) -> None:
        notification = make_notification(status=NotificationStatus.SENT)
        with pytest.raises(InvalidStateError):
            notification.cancel()

    def test_mark_completed_all_sent(self) -> None:
        notification = make_notification()
        delivered = [make_delivery(status=DeliveryStatus.SENT)]
        notification.mark_completed(delivered)
        assert notification.status is NotificationStatus.SENT
        assert notification.processed_at is not None

    def test_mark_completed_partial(self) -> None:
        notification = make_notification()
        deliveries = [
            make_delivery(status=DeliveryStatus.SENT),
            make_delivery(status=DeliveryStatus.FAILED),
        ]
        notification.mark_completed(deliveries)
        assert notification.status is NotificationStatus.PARTIAL

    def test_mark_completed_all_failed(self) -> None:
        notification = make_notification()
        deliveries = [make_delivery(status=DeliveryStatus.FAILED)]
        notification.mark_completed(deliveries)
        assert notification.status is NotificationStatus.FAILED

    def test_mark_completed_in_flight(self) -> None:
        notification = make_notification(status=NotificationStatus.PROCESSING)
        notification.mark_completed([])
        assert notification.status is NotificationStatus.PROCESSING

    def test_scheduled_at_must_be_aware(self) -> None:
        with pytest.raises(InvalidStateError):
            make_notification(scheduled_at=datetime(2026, 1, 1, 12, 0, 0))


class TestDeliveryTransitions:
    def test_mark_processing(self) -> None:
        delivery = make_delivery()
        delivery.mark_processing()
        assert delivery.status is DeliveryStatus.PROCESSING

    def test_mark_processing_terminal_raises(self) -> None:
        delivery = make_delivery(status=DeliveryStatus.SENT)
        with pytest.raises(InvalidStateError):
            delivery.mark_processing()

    def test_mark_sent(self) -> None:
        delivery = make_delivery()
        delivery.mark_sent("msg-123")
        assert delivery.status is DeliveryStatus.SENT
        assert delivery.provider_message_id == "msg-123"
        assert delivery.completed_at is not None
        assert delivery.next_attempt_at is None

    def test_mark_sent_after_permanent_failure_raises(self) -> None:
        delivery = make_delivery(
            status=DeliveryStatus.FAILED,
            attempts=3,
            max_attempts=3,
        )
        with pytest.raises(InvalidStateError):
            delivery.mark_sent("msg")

    def test_mark_failed_transient_schedules_retry(self) -> None:
        delivery = make_delivery(retry_backoff_seconds=5.0)
        delivery.mark_failed("timeout", transient=True, now=NOW)
        assert delivery.status is DeliveryStatus.PENDING
        assert delivery.attempts == 1
        assert delivery.next_attempt_at == NOW + timedelta(seconds=5)

    def test_mark_failed_transient_backoff_grows(self) -> None:
        delivery = make_delivery(retry_backoff_seconds=5.0)
        delivery.mark_failed("t1", transient=True, now=NOW)
        delivery.mark_failed("t2", transient=True, now=NOW + timedelta(seconds=6))
        assert delivery.attempts == 2
        assert delivery.next_attempt_at == NOW + timedelta(seconds=6) + timedelta(seconds=10)

    def test_mark_failed_permanent(self) -> None:
        delivery = make_delivery(max_attempts=1)
        delivery.mark_failed("boom", transient=True, now=NOW)
        assert delivery.status is DeliveryStatus.FAILED
        assert delivery.attempts == 1
        assert delivery.next_attempt_at is None
        assert delivery.completed_at is not None

    def test_mark_failed_exhausts_attempts(self) -> None:
        delivery = make_delivery(max_attempts=2)
        delivery.mark_failed("t", transient=True, now=NOW)
        delivery.mark_failed("t2", transient=True, now=NOW + timedelta(seconds=1))
        assert delivery.status is DeliveryStatus.FAILED

    def test_reset_for_retry(self) -> None:
        delivery = make_delivery(status=DeliveryStatus.FAILED, attempts=3, last_error="boom")
        delivery.reset_for_retry(NOW)
        assert delivery.status is DeliveryStatus.PENDING
        assert delivery.attempts == 0
        assert delivery.next_attempt_at == NOW
        assert delivery.last_error is None

    def test_is_terminal(self) -> None:
        assert make_delivery(status=DeliveryStatus.SENT).is_terminal is True
        assert make_delivery(status=DeliveryStatus.FAILED).is_terminal is True
        assert make_delivery().is_terminal is False


class FakeProvider(Provider):
    channel_type = ChannelType.EMAIL
    capabilities = ProviderCapabilities(supports_html=True)

    def __init__(self, settings: dict) -> None:
        self.settings = settings

    @classmethod
    def from_settings(cls, settings: dict) -> FakeProvider:
        return cls(settings)

    async def send(self, message: ProviderMessage) -> ProviderResult:
        return ProviderResult(delivered=True)


class TestProviderContracts:
    def test_provider_capabilities_defaults(self) -> None:
        capabilities = ProviderCapabilities()
        assert capabilities.supports_html is False
        assert capabilities.supports_attachments is False
        assert capabilities.supports_templates is True
        assert capabilities.max_payload_bytes is None

    def test_provider_message_defaults(self) -> None:
        message = ProviderMessage(recipient="a@example.com", body="hi")
        assert message.subject is None
        assert message.settings == {}
        assert message.correlation_id == ""

    def test_provider_result(self) -> None:
        result = ProviderResult(delivered=False, error="nope")
        assert result.error == "nope"


class TestProviderRegistry:
    def test_register_and_get(self) -> None:
        registry = ProviderRegistry()
        registry.register(FakeProvider)
        assert registry.get(ChannelType.EMAIL) is FakeProvider

    def test_get_missing_raises(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(ProviderNotRegisteredError):
            registry.get(ChannelType.TEAMS)

    def test_capabilities_lookup(self) -> None:
        registry = ProviderRegistry(providers={ChannelType.EMAIL: FakeProvider})
        assert registry.capabilities(ChannelType.EMAIL).supports_html is True

    def test_channels(self) -> None:
        registry = ProviderRegistry(providers={ChannelType.EMAIL: FakeProvider})
        assert registry.channels() == [ChannelType.EMAIL]

    def test_discover_without_entry_points(self) -> None:
        registry = ProviderRegistry()
        registry.discover()
        assert registry.channels() == []

    def test_discover_with_entry_points(self, monkeypatch) -> None:
        class FakeEntryPoint:
            def load(self) -> type[FakeProvider]:
                return FakeProvider

        monkeypatch.setattr(
            "notifly.domain.providers.metadata.entry_points",
            lambda group: [FakeEntryPoint()],
        )
        registry = ProviderRegistry()
        registry.discover()
        assert registry.get(ChannelType.EMAIL) is FakeProvider

    def test_registry_overwrites_on_duplicate_channel(self) -> None:
        class OtherEmailProvider(FakeProvider):
            pass

        registry = ProviderRegistry()
        registry.register(FakeProvider)
        registry.register(OtherEmailProvider)
        assert registry.get(ChannelType.EMAIL) is OtherEmailProvider


class TestPortImplementations:
    def test_system_clock_returns_aware_utc(self) -> None:
        now = SystemClock().now()
        assert now.tzinfo == UTC

    async def test_in_memory_rate_limiter_blocks_overflow(self) -> None:
        limiter = InMemoryRateLimiter()
        assert await limiter.acquire("key", limit=2, window_seconds=1.0) is True
        assert await limiter.acquire("key", limit=2, window_seconds=1.0) is True
        assert await limiter.acquire("key", limit=2, window_seconds=1.0) is False

    async def test_in_memory_rate_limiter_expires_window(self) -> None:
        limiter = InMemoryRateLimiter()
        assert await limiter.acquire("key", limit=1, window_seconds=0.05) is True
        assert await limiter.acquire("key", limit=1, window_seconds=0.05) is False
        await asyncio.sleep(0.06)
        assert await limiter.acquire("key", limit=1, window_seconds=0.05) is True

    async def test_in_memory_rate_limiter_keys_are_independent(self) -> None:
        limiter = InMemoryRateLimiter()
        assert await limiter.acquire("a", limit=1, window_seconds=1.0) is True
        assert await limiter.acquire("b", limit=1, window_seconds=1.0) is True
