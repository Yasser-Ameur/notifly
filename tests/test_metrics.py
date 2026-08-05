"""M9 tests: the Prometheus metrics adapter."""

from __future__ import annotations

from prometheus_client import REGISTRY, generate_latest

from notifly.domain.enums import ChannelType, DeliveryStatus
from notifly.infrastructure.observability.metrics import PrometheusMetrics

metrics = PrometheusMetrics()


def _value(name: str, **labels: str) -> float:
    return float(REGISTRY.get_sample_value(name, labels) or 0.0)


def test_notification_created_counter() -> None:
    before = _value("notifly_notifications_created_total", scheduled="false")
    metrics.notification_created(scheduled=False)
    assert _value("notifly_notifications_created_total", scheduled="false") == before + 1


def test_notification_created_scheduled_label() -> None:
    before = _value("notifly_notifications_created_total", scheduled="true")
    metrics.notification_created(scheduled=True)
    assert _value("notifly_notifications_created_total", scheduled="true") == before + 1


def test_delivery_attempt_counter() -> None:
    before = _value("notifly_delivery_attempts_total", channel="email", outcome="sent")
    metrics.delivery_attempt(channel=ChannelType.EMAIL, outcome=DeliveryStatus.SENT)
    assert _value("notifly_delivery_attempts_total", channel="email", outcome="sent") == before + 1


def test_delivery_duration_histogram() -> None:
    before = _value("notifly_delivery_duration_seconds_count", channel="slack")
    metrics.delivery_duration(channel=ChannelType.SLACK, seconds=0.25)
    assert _value("notifly_delivery_duration_seconds_count", channel="slack") == before + 1


def test_delivery_deferred_counter() -> None:
    before = _value("notifly_delivery_deferred_total", channel="email")
    metrics.delivery_deferred(channel=ChannelType.EMAIL)
    assert _value("notifly_delivery_deferred_total", channel="email") == before + 1


def test_outbox_published_counter() -> None:
    before = _value("notifly_outbox_published_total", event_type="notification.created")
    metrics.outbox_published(event_type="notification.created")
    assert _value("notifly_outbox_published_total", event_type="notification.created") == before + 1


def test_outbox_failed_counter() -> None:
    before = _value("notifly_outbox_failed_total", event_type="delivery.retry")
    metrics.outbox_failed(event_type="delivery.retry")
    assert _value("notifly_outbox_failed_total", event_type="delivery.retry") == before + 1


def test_http_request_records_counter_and_duration() -> None:
    before = _value("notifly_http_requests_total", method="POST", path="/v1/apps", status="201")
    duration_before = _value(
        "notifly_http_request_duration_seconds_count", method="POST", path="/v1/apps"
    )
    metrics.http_request(method="POST", path="/v1/apps", status=201, duration_ms=12.5)
    assert (
        _value("notifly_http_requests_total", method="POST", path="/v1/apps", status="201")
        == before + 1
    )
    assert (
        _value("notifly_http_request_duration_seconds_count", method="POST", path="/v1/apps")
        == duration_before + 1
    )


def test_generate_latest_exposes_metric_families() -> None:
    body = generate_latest().decode()
    for name in (
        "notifly_notifications_created_total",
        "notifly_delivery_attempts_total",
        "notifly_delivery_duration_seconds",
        "notifly_delivery_deferred_total",
        "notifly_outbox_published_total",
        "notifly_outbox_failed_total",
        "notifly_http_requests_total",
        "notifly_http_request_duration_seconds",
    ):
        assert name in body
