"""Outbox relay: moves persisted outbox events onto the job queue.

The transactional outbox keeps the database the source of truth: an event is
committed atomically with the business mutation that produced it, and only
then does this relay push it to the queue and mark it published (or failed, if
the queue transport rejects it). The relay also seeds the outbox for
time-based triggers — due scheduled notifications and due delivery retries —
so the worker never polls the database directly.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

from notifly.domain.enums import NotificationStatus, OutboxEventType
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.metrics import Metrics, NoopMetrics
from notifly.domain.ports.repositories import UnitOfWorkFactory
from notifly.domain.ports.tasks import TaskDispatcher

logger = logging.getLogger(__name__)

DISPATCH_TASK = "dispatch_notification"


class OutboxPublisher:
    """Relays PENDING outbox events to the task dispatcher."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        task_dispatcher: TaskDispatcher,
        *,
        clock: Clock | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._task_dispatcher = task_dispatcher
        self._clock = clock or SystemClock()
        self._metrics = metrics or NoopMetrics()

    async def publish_pending(self, *, limit: int = 100) -> int:
        """Enqueue every pending outbox event and mark it published or failed."""
        now = self._clock.now()
        published = 0
        async with self._uow_factory() as uow:
            events = await uow.outbox.get_pending_for_update(limit=limit)
            for event in events:
                try:
                    await self._task_dispatcher.enqueue(DISPATCH_TASK, self._job_payload(event))
                except Exception as exc:
                    logger.warning(
                        "Failed to enqueue outbox event %s for task %s: %s",
                        event.id,
                        DISPATCH_TASK,
                        exc,
                    )
                    await uow.outbox.mark_failed(event.id, str(exc))
                    self._metrics.outbox_failed(event_type=event.event_type.value)
                else:
                    await uow.outbox.mark_published(event.id, now)
                    self._metrics.outbox_published(event_type=event.event_type.value)
                    published += 1
        return published

    async def requeue_due_retries(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Seed a ``DELIVERY_RETRY`` event for every notification with a due delivery.

        Only retryable deliveries (PENDING with an elapsed ``next_attempt_at``)
        are considered; cancelled notifications are never requeued.
        """
        now = now or self._clock.now()
        created = 0
        async with self._uow_factory() as uow:
            deliveries = await uow.deliveries.list_due_retry(now=now, limit=limit)
            for delivery in deliveries:
                notification = await uow.notifications.get(delivery.notification_id)
                if notification is None or notification.status is NotificationStatus.CANCELLED:
                    continue
                await uow.outbox.add(
                    OutboxEvent(
                        id=uuid4(),
                        event_type=OutboxEventType.DELIVERY_RETRY,
                        payload={"notification_id": str(delivery.notification_id)},
                        correlation_id=notification.correlation_id,
                        created_at=now,
                    )
                )
                created += 1
        return created

    async def publish_due_scheduled(self, *, now: datetime | None = None, limit: int = 100) -> int:
        """Release due scheduled notifications by seeding ``NOTIFICATION_CREATED`` events.

        Each released notification moves PENDING -> PROCESSING so it is never
        selected twice, even if the relay or the worker fails afterwards.
        """
        now = now or self._clock.now()
        created = 0
        async with self._uow_factory() as uow:
            notifications = await uow.notifications.list_due_scheduled(now=now, limit=limit)
            for notification in notifications:
                notification.status = NotificationStatus.PROCESSING
                notification.updated_at = now
                await uow.notifications.update(notification)
                await uow.outbox.add(
                    OutboxEvent(
                        id=uuid4(),
                        event_type=OutboxEventType.NOTIFICATION_CREATED,
                        payload={
                            "notification_id": str(notification.id),
                            "event": notification.event,
                        },
                        correlation_id=notification.correlation_id,
                        created_at=now,
                    )
                )
                created += 1
        return created

    @staticmethod
    def _job_payload(event: OutboxEvent) -> dict[str, Any]:
        return {"notification_id": event.payload.get("notification_id")}
