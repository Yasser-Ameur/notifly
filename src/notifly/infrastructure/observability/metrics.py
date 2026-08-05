"""Prometheus metrics adapter.

Collectors are declared once at module level against the default registry, so
any number of ``PrometheusMetrics`` instances (one per app) share the same
series instead of tripping Prometheus' duplicate-collector guard. The exposition
endpoint simply dumps ``generate_latest()``.
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

from notifly.domain.enums import ChannelType, DeliveryStatus

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

_NOTIFICATIONS_CREATED = Counter(
    "notifly_notifications_created_total",
    "Notifications accepted into the system (excludes idempotent replays).",
    ["scheduled"],
)
_DELIVERY_ATTEMPTS = Counter(
    "notifly_delivery_attempts_total",
    "Provider delivery attempts by channel and terminal outcome.",
    ["channel", "outcome"],
)
_DELIVERY_DURATION = Histogram(
    "notifly_delivery_duration_seconds",
    "Duration of a provider invocation.",
    ["channel"],
    buckets=_LATENCY_BUCKETS,
)
_DELIVERY_DEFERRED = Counter(
    "notifly_delivery_deferred_total",
    "Deliveries deferred because the channel rate limit was reached.",
    ["channel"],
)
_OUTBOX_PUBLISHED = Counter(
    "notifly_outbox_published_total",
    "Outbox events successfully relayed to the task queue.",
    ["event_type"],
)
_OUTBOX_FAILED = Counter(
    "notifly_outbox_failed_total",
    "Outbox events that could not be relayed to the task queue.",
    ["event_type"],
)
_HTTP_REQUESTS = Counter(
    "notifly_http_requests_total",
    "HTTP requests handled, by route and response status.",
    ["method", "path", "status"],
)
_HTTP_REQUEST_DURATION = Histogram(
    "notifly_http_request_duration_seconds",
    "HTTP request handling duration.",
    ["method", "path"],
    buckets=_LATENCY_BUCKETS,
)


class PrometheusMetrics:
    """Prometheus-backed reporter writing to the default registry."""

    def notification_created(self, *, scheduled: bool) -> None:
        _NOTIFICATIONS_CREATED.labels(scheduled=str(scheduled).lower()).inc()

    def delivery_attempt(self, *, channel: ChannelType, outcome: DeliveryStatus) -> None:
        _DELIVERY_ATTEMPTS.labels(channel=channel.value, outcome=outcome.value).inc()

    def delivery_duration(self, *, channel: ChannelType, seconds: float) -> None:
        _DELIVERY_DURATION.labels(channel=channel.value).observe(seconds)

    def delivery_deferred(self, *, channel: ChannelType) -> None:
        _DELIVERY_DEFERRED.labels(channel=channel.value).inc()

    def outbox_published(self, *, event_type: str) -> None:
        _OUTBOX_PUBLISHED.labels(event_type=event_type).inc()

    def outbox_failed(self, *, event_type: str) -> None:
        _OUTBOX_FAILED.labels(event_type=event_type).inc()

    def http_request(self, *, method: str, path: str, status: int, duration_ms: float) -> None:
        _HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()
        _HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration_ms / 1000.0)
