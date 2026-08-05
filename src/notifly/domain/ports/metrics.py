"""Metrics port: application services emit observable events without knowing Prometheus.

The application layer depends on the ``Metrics`` protocol; infrastructure ships a
Prometheus adapter (``infrastructure.observability.metrics``). Services default to
``NoopMetrics`` so observability is entirely optional and never affects behaviour.
"""

from __future__ import annotations

from typing import Protocol

from notifly.domain.enums import ChannelType, DeliveryStatus


class Metrics(Protocol):
    """Observability events emitted by the application and presentation layers."""

    def notification_created(self, *, scheduled: bool) -> None: ...

    def delivery_attempt(self, *, channel: ChannelType, outcome: DeliveryStatus) -> None: ...

    def delivery_duration(self, *, channel: ChannelType, seconds: float) -> None: ...

    def delivery_deferred(self, *, channel: ChannelType) -> None: ...

    def outbox_published(self, *, event_type: str) -> None: ...

    def outbox_failed(self, *, event_type: str) -> None: ...

    def http_request(self, *, method: str, path: str, status: int, duration_ms: float) -> None: ...


class NoopMetrics:
    """No-op adapter used by default when no metrics backend is configured."""

    def notification_created(self, *, scheduled: bool) -> None: ...

    def delivery_attempt(self, *, channel: ChannelType, outcome: DeliveryStatus) -> None: ...

    def delivery_duration(self, *, channel: ChannelType, seconds: float) -> None: ...

    def delivery_deferred(self, *, channel: ChannelType) -> None: ...

    def outbox_published(self, *, event_type: str) -> None: ...

    def outbox_failed(self, *, event_type: str) -> None: ...

    def http_request(self, *, method: str, path: str, status: int, duration_ms: float) -> None: ...
