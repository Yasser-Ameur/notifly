"""M9 tests: metrics wiring across services, middleware, and the /metrics endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from prometheus_client import REGISTRY
from starlette.requests import Request
from starlette.responses import Response
from tests.helpers import FakeClock, add_channel, make_application, make_template

from notifly.application.services.dispatcher import DispatcherService
from notifly.application.services.notifications import NotificationService
from notifly.application.services.outbox import OutboxPublisher
from notifly.domain.enums import (
    ChannelType,
    DeliveryStatus,
    OutboxEventType,
    ProviderErrorKind,
)
from notifly.domain.ports.rate_limit import InMemoryRateLimiter
from notifly.domain.ports.tasks import InMemoryTaskDispatcher
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderRegistry,
    ProviderResult,
)
from notifly.infrastructure.db.uow import create_uow_factory
from notifly.presentation.api.middleware import MetricsMiddleware


class RecordingMetrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, **kwargs: Any) -> None:
        self.calls.append((name, kwargs))

    def notification_created(self, *, scheduled: bool) -> None:
        self._record("notification_created", scheduled=scheduled)

    def delivery_attempt(self, *, channel: ChannelType, outcome: DeliveryStatus) -> None:
        self._record("delivery_attempt", channel=channel, outcome=outcome)

    def delivery_duration(self, *, channel: ChannelType, seconds: float) -> None:
        self._record("delivery_duration", channel=channel, seconds=seconds)

    def delivery_deferred(self, *, channel: ChannelType) -> None:
        self._record("delivery_deferred", channel=channel)

    def outbox_published(self, *, event_type: str) -> None:
        self._record("outbox_published", event_type=event_type)

    def outbox_failed(self, *, event_type: str) -> None:
        self._record("outbox_failed", event_type=event_type)

    def http_request(self, *, method: str, path: str, status: int, duration_ms: float) -> None:
        self._record(
            "http_request", method=method, path=path, status=status, duration_ms=duration_ms
        )

    def names(self, name: str) -> list[dict[str, Any]]:
        return [call[1] for call in self.calls if call[0] == name]


class _ProviderHarness:
    def __init__(self) -> None:
        self.results: list[ProviderResult] = []

    def provider_cls(self, channel_type: ChannelType) -> type[Provider]:
        harness = self

        class _Fake(Provider):
            capabilities = ProviderCapabilities()

            @classmethod
            def from_settings(cls, settings: dict[str, Any]) -> Provider:
                return cls()

            async def send(self, message: ProviderMessage) -> ProviderResult:
                if harness.results:
                    return harness.results.pop(0)
                return ProviderResult(delivered=True, provider_message_id="m-1")

        _Fake.channel_type = channel_type
        return _Fake


class _FailingDispatcher:
    async def enqueue(self, task: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("redis is down")


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 1, 1, tzinfo=UTC))


async def _prepare(db, clock: FakeClock, **channel_kwargs):
    application = await make_application(db)
    await make_template(db, application.id)
    await add_channel(db, application.id, **channel_kwargs)
    return application


async def _create(db, clock: FakeClock, application, **kwargs):
    return await NotificationService(create_uow_factory(db), clock=clock).create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients={ChannelType.EMAIL: "alice@example.com"},
        **kwargs,
    )


def _dispatcher(db, harness: _ProviderHarness, clock: FakeClock, metrics, *, rate_limiter=None):
    registry = ProviderRegistry()
    registry.register(harness.provider_cls(ChannelType.EMAIL))
    return DispatcherService(
        create_uow_factory(db),
        registry=registry,
        rate_limiter=rate_limiter,
        clock=clock,
        metrics=metrics,
    )


async def test_dispatcher_records_success_attempt(db, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create(db, clock, application)
    metrics = RecordingMetrics()

    await _dispatcher(db, _ProviderHarness(), clock, metrics).dispatch_notification(
        created.notification.id
    )

    assert metrics.names("delivery_attempt") == [
        {"channel": ChannelType.EMAIL, "outcome": DeliveryStatus.SENT}
    ]
    durations = metrics.names("delivery_duration")
    assert len(durations) == 1
    assert durations[0]["channel"] == ChannelType.EMAIL
    assert durations[0]["seconds"] >= 0
    assert not metrics.names("delivery_deferred")


async def test_dispatcher_records_failed_attempt_with_duration(db, clock) -> None:
    application = await _prepare(db, clock)
    created = await _create(db, clock, application)
    harness = _ProviderHarness()
    harness.results = [
        ProviderResult(delivered=False, error="rejected", error_kind=ProviderErrorKind.PERMANENT)
    ]
    metrics = RecordingMetrics()

    await _dispatcher(db, harness, clock, metrics).dispatch_notification(created.notification.id)

    assert metrics.names("delivery_attempt") == [
        {"channel": ChannelType.EMAIL, "outcome": DeliveryStatus.FAILED}
    ]
    assert len(metrics.names("delivery_duration")) == 1


async def test_dispatcher_records_deferred_when_rate_limited(db, clock) -> None:
    application = await _prepare(db, clock, rate_limit_per_minute=1, retry_backoff_seconds=5.0)
    first = await _create(db, clock, application)
    second = await _create(db, clock, application)
    metrics = RecordingMetrics()

    dispatcher = _dispatcher(
        db, _ProviderHarness(), clock, metrics, rate_limiter=InMemoryRateLimiter()
    )
    await dispatcher.dispatch_notification(first.notification.id)
    await dispatcher.dispatch_notification(second.notification.id)

    assert len(metrics.names("delivery_deferred")) == 1


async def test_notification_service_records_created(db, clock) -> None:
    application = await _prepare(db, clock)
    metrics = RecordingMetrics()
    service = NotificationService(create_uow_factory(db), clock=clock, metrics=metrics)

    await service.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients={ChannelType.EMAIL: "a@example.com"},
    )

    assert metrics.names("notification_created") == [{"scheduled": False}]


async def test_notification_service_records_scheduled(db, clock) -> None:
    application = await _prepare(db, clock)
    metrics = RecordingMetrics()
    service = NotificationService(create_uow_factory(db), clock=clock, metrics=metrics)

    await service.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients={ChannelType.EMAIL: "a@example.com"},
        scheduled_at=clock.now() + timedelta(hours=1),
    )

    assert metrics.names("notification_created") == [{"scheduled": True}]


async def test_notification_service_replay_does_not_record(db, clock) -> None:
    application = await _prepare(db, clock)
    metrics = RecordingMetrics()
    service = NotificationService(create_uow_factory(db), clock=clock, metrics=metrics)

    await service.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients={ChannelType.EMAIL: "a@example.com"},
        idempotency_key="send-1",
    )
    await service.create_notification(
        application.id,
        actor="key",
        event="user_welcome",
        variables={"name": "Alice"},
        recipients={ChannelType.EMAIL: "a@example.com"},
        idempotency_key="send-1",
    )

    assert len(metrics.names("notification_created")) == 1


async def test_outbox_records_published(db, clock) -> None:
    application = await _prepare(db, clock)
    await _create(db, clock, application)
    metrics = RecordingMetrics()

    publisher = OutboxPublisher(
        create_uow_factory(db),
        InMemoryTaskDispatcher(),
        clock=clock,
        metrics=metrics,
    )
    await publisher.publish_pending()

    assert metrics.names("outbox_published") == [
        {"event_type": OutboxEventType.NOTIFICATION_CREATED.value}
    ]
    assert not metrics.names("outbox_failed")


async def test_outbox_records_failed(db, clock) -> None:
    application = await _prepare(db, clock)
    await _create(db, clock, application)
    metrics = RecordingMetrics()

    publisher = OutboxPublisher(
        create_uow_factory(db),
        _FailingDispatcher(),
        clock=clock,
        metrics=metrics,
    )
    await publisher.publish_pending()

    assert metrics.names("outbox_failed") == [
        {"event_type": OutboxEventType.NOTIFICATION_CREATED.value}
    ]
    assert not metrics.names("outbox_published")


async def test_metrics_endpoint_exposes_text_format(app, client) -> None:
    response = await client.get("/health/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "notifly_notifications_created_total" in response.text


async def test_api_requests_feed_prometheus_http_counter(client) -> None:
    labels = {"method": "POST", "path": "/v1/apps", "status": "201"}
    before = float(REGISTRY.get_sample_value("notifly_http_requests_total", labels) or 0.0)

    response = await client.post("/v1/apps", json={"name": "metrics-app"})

    assert response.status_code == 201
    after = float(REGISTRY.get_sample_value("notifly_http_requests_total", labels) or 0.0)
    assert after == before + 1


def _make_request(path: str, *, app: Any = None) -> Request:
    state = type("State", (), {})()
    app = app or type("App", (), {"state": state})()
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
        "app": app,
    }
    return Request(scope)


async def test_metrics_middleware_records_success() -> None:
    metrics = RecordingMetrics()
    middleware = MetricsMiddleware(app=object(), metrics=metrics)

    async def call_next(request: Request) -> Response:
        return Response(status_code=200)

    result = await middleware.dispatch(_make_request("/v1/apps"), call_next)

    assert result.status_code == 200
    recorded = metrics.names("http_request")
    assert len(recorded) == 1
    assert recorded[0]["method"] == "GET"
    assert recorded[0]["path"] == "/v1/apps"
    assert recorded[0]["status"] == 200
    assert recorded[0]["duration_ms"] >= 0


async def test_metrics_middleware_records_exception_as_500() -> None:
    metrics = RecordingMetrics()
    middleware = MetricsMiddleware(app=object(), metrics=metrics)

    async def call_next(request: Request) -> Response:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await middleware.dispatch(_make_request("/boom"), call_next)

    recorded = metrics.names("http_request")
    assert len(recorded) == 1
    assert recorded[0]["method"] == "GET"
    assert recorded[0]["path"] == "/boom"
    assert recorded[0]["status"] == 500


async def test_metrics_middleware_skips_metrics_path() -> None:
    metrics = RecordingMetrics()
    middleware = MetricsMiddleware(app=object(), metrics=metrics)
    seen: list[str] = []

    async def call_next(request: Request) -> Response:
        seen.append(request.url.path)
        return Response(status_code=200)

    await middleware.dispatch(_make_request("/health/metrics"), call_next)

    assert seen == ["/health/metrics"]
    assert not metrics.names("http_request")


async def test_metrics_middleware_labels_unmatched_path() -> None:
    metrics = RecordingMetrics()
    middleware = MetricsMiddleware(app=object(), metrics=metrics)

    async def call_next(request: Request) -> Response:
        return Response(status_code=404)

    await middleware.dispatch(_make_request("/does/not/exist"), call_next)

    assert metrics.names("http_request")[0]["path"] == "/does/not/exist"
    assert metrics.names("http_request")[0]["status"] == 404


async def test_metrics_middleware_falls_back_to_app_state() -> None:
    metrics = RecordingMetrics()
    state = type("State", (), {"metrics": metrics})()
    app = type("App", (), {"state": state})()
    middleware = MetricsMiddleware(app=object())

    async def call_next(request: Request) -> Response:
        return Response(status_code=204)

    await middleware.dispatch(_make_request("/health/ready", app=app), call_next)

    assert len(metrics.names("http_request")) == 1
