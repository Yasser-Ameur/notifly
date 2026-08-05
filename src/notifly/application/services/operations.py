"""Operations use cases: cross-entity querying and dead-letter recovery.

The Operations service backs the read/ops endpoints of the API. Everything is
scoped to a single application. The only mutating use case is the dead-letter
retry, which resets permanently failed deliveries, re-emits an outbox event,
and audits the action in one transaction — the transactional outbox then
takes over from there.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from notifly.application.dto import NotificationCreated, OpsPage
from notifly.application.services.audit import write_audit
from notifly.domain.enums import (
    AuditAction,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    OutboxEventType,
)
from notifly.domain.errors import InvalidStateError, NotFoundError
from notifly.domain.models.application import Application
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.models.notification import Delivery, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory
from notifly.logging import get_correlation_id


class OperationsService:
    """Query and recovery use cases scoped to one application."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    async def list_notifications(
        self,
        application_id: UUID,
        *,
        status: NotificationStatus | None = None,
        event: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> OpsPage[Notification]:
        async with self._uow_factory() as uow:
            items = await uow.notifications.search(
                application_id=application_id,
                status=status,
                event=event,
                created_after=created_after,
                created_before=created_before,
                limit=limit,
                offset=offset,
            )
            total = await uow.notifications.count(
                application_id=application_id,
                status=status,
                event=event,
                created_after=created_after,
                created_before=created_before,
            )
        return OpsPage(items=items, total=total, limit=limit, offset=offset)

    async def list_deliveries(
        self,
        application_id: UUID,
        *,
        notification_id: UUID | None = None,
        channel_type: ChannelType | None = None,
        status: DeliveryStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> OpsPage[Delivery]:
        async with self._uow_factory() as uow:
            items = await uow.deliveries.search(
                application_id=application_id,
                notification_id=notification_id,
                channel_type=channel_type,
                status=status,
                limit=limit,
                offset=offset,
            )
            total = await uow.deliveries.count(
                application_id=application_id,
                notification_id=notification_id,
                channel_type=channel_type,
                status=status,
            )
        return OpsPage(items=items, total=total, limit=limit, offset=offset)

    async def get_application(self, application_id: UUID) -> Application:
        async with self._uow_factory() as uow:
            application = await uow.applications.get(application_id)
            if application is None:
                raise NotFoundError(f"Application {application_id} does not exist.")
            return application

    async def list_deadletters(
        self, application_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> OpsPage[Notification]:
        async with self._uow_factory() as uow:
            items = await uow.notifications.list_deadletters(
                application_id=application_id, limit=limit, offset=offset
            )
            total = await uow.notifications.count(
                application_id=application_id, status=NotificationStatus.FAILED
            )
        return OpsPage(items=items, total=total, limit=limit, offset=offset)

    async def list_audit(
        self, application_id: UUID, *, limit: int = 100, offset: int = 0
    ) -> OpsPage[AuditLogEntry]:
        async with self._uow_factory() as uow:
            items = await uow.audit.search(
                application_id=application_id, limit=limit, offset=offset
            )
            total = await uow.audit.count(application_id=application_id)
        return OpsPage(items=items, total=total, limit=limit, offset=offset)

    async def retry_notification(
        self,
        application_id: UUID,
        notification_id: UUID,
        *,
        actor: str,
        correlation_id: str = "",
    ) -> NotificationCreated:
        """Requeue a dead-lettered (or partially delivered) notification.

        Permanently failed deliveries are reset to PENDING with fresh attempt
        state, the notification returns to PENDING, and a ``NOTIFICATION_RETRIED``
        outbox event is committed so the normal dispatch path takes over.
        """
        correlation_id = correlation_id or (get_correlation_id() or "")
        now = self._clock.now()
        async with self._uow_factory() as uow:
            notification = await self._require_notification(uow, application_id, notification_id)
            if notification.status not in (
                NotificationStatus.FAILED,
                NotificationStatus.PARTIAL,
            ):
                raise InvalidStateError(
                    f"Notification {notification_id} has no failed deliveries to retry."
                )
            deliveries = await uow.deliveries.list_by_notification(notification_id)
            failed = [
                delivery for delivery in deliveries if delivery.status is DeliveryStatus.FAILED
            ]
            for delivery in failed:
                delivery.reset_for_retry(now)
                delivery.updated_at = now
                await uow.deliveries.update(delivery)

            notification.status = NotificationStatus.PENDING
            notification.updated_at = now
            notification.processed_at = None
            await uow.notifications.update(notification)

            await uow.outbox.add(
                OutboxEvent(
                    id=uuid4(),
                    event_type=OutboxEventType.NOTIFICATION_RETRIED,
                    payload={
                        "notification_id": str(notification.id),
                        "event": notification.event,
                    },
                    correlation_id=correlation_id,
                    created_at=now,
                )
            )
            await write_audit(
                uow,
                application_id=application_id,
                actor=actor,
                action=AuditAction.NOTIFICATION_RETRIED,
                resource_type="notification",
                resource_id=notification.id,
                correlation_id=correlation_id,
                now=now,
                payload={"retried_deliveries": len(failed)},
            )
        return NotificationCreated(notification=notification, deliveries=deliveries)

    @staticmethod
    async def _require_notification(
        uow: UnitOfWork, application_id: UUID, notification_id: UUID
    ) -> Notification:
        notification = await uow.notifications.get(notification_id)
        if notification is None or notification.application_id != application_id:
            raise NotFoundError(f"Notification {notification_id} does not exist.")
        return notification
