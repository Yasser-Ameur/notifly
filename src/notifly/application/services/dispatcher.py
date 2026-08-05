"""Dispatcher: the worker-side counterpart to the Notification Engine.

The engine persists the delivery plan; the dispatcher executes it. For every
due delivery of a notification it applies the channel rate limit, builds a
configured provider, invokes it, records the attempt, and advances the
delivery (and then the notification) to its correct state. Everything happens
inside one Unit of Work per notification, so a worker crash never leaves the
record half-updated.

The dispatcher is idempotent: re-dispatching a notification whose deliveries
are already terminal is a no-op, which makes duplicate outbox events harmless.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from notifly.application.dto import DispatchSummary
from notifly.application.services.audit import write_audit
from notifly.domain.enums import (
    AttemptStatus,
    AuditAction,
    DeliveryStatus,
    NotificationStatus,
    ProviderErrorKind,
)
from notifly.domain.errors import ProviderConfigurationError
from notifly.domain.models.notification import Delivery, DeliveryAttempt, Notification
from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.rate_limit import RateLimiter
from notifly.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory
from notifly.domain.providers import ProviderMessage, ProviderRegistry

logger = logging.getLogger(__name__)


class DispatcherService:
    """Executes the delivery plan for a notification, channel by channel."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        registry: ProviderRegistry,
        rate_limiter: RateLimiter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._registry = registry
        self._rate_limiter = rate_limiter
        self._clock = clock or SystemClock()

    async def dispatch_notification(self, notification_id: UUID) -> DispatchSummary:
        """Dispatch every pending, due delivery of ``notification_id``."""
        now = self._clock.now()
        dispatched = 0
        async with self._uow_factory() as uow:
            notification = await uow.notifications.get(notification_id)
            if notification is None or notification.status is NotificationStatus.CANCELLED:
                return DispatchSummary(notification_id=notification_id, dispatched=0, skipped=True)
            notification.mark_processing()
            notification.updated_at = now
            await uow.notifications.update(notification)

            deliveries = await uow.deliveries.list_by_notification(notification_id)
            for delivery in deliveries:
                if self._is_due(delivery, now) and await self._dispatch_delivery(
                    uow, notification, delivery, now
                ):
                    dispatched += 1
            notification.mark_completed(deliveries)
            notification.updated_at = now
            await uow.notifications.update(notification)
        return DispatchSummary(notification_id=notification_id, dispatched=dispatched)

    @staticmethod
    def _is_due(delivery: Delivery, now: datetime) -> bool:
        if delivery.status is not DeliveryStatus.PENDING:
            return False
        return delivery.next_attempt_at is None or delivery.next_attempt_at <= now

    async def _dispatch_delivery(
        self,
        uow: UnitOfWork,
        notification: Notification,
        delivery: Delivery,
        now: datetime,
    ) -> bool:
        """Return True when an attempt was actually made (not deferred)."""
        if not await self._rate_limit_allowed(uow, notification, delivery, now):
            return False
        delivery.mark_processing()
        delivery.updated_at = now
        await uow.deliveries.update(delivery)

        channel = await uow.channels.get_by_app_and_type(
            notification.application_id, delivery.channel_type
        )
        if channel is None or not channel.enabled:
            error = f"Channel '{delivery.channel_type}' is not configured or is disabled."
            await self._fail(uow, notification, delivery, error, transient=False, now=now)
            return True

        attempt_number = delivery.attempts + 1
        message = ProviderMessage(
            recipient=delivery.recipient,
            subject=delivery.subject,
            body=delivery.body,
            html_body=delivery.html_body,
            settings=delivery.provider_settings,
            correlation_id=notification.correlation_id,
        )
        try:
            provider = self._registry.get(delivery.channel_type).from_settings(channel.config)
            start = time.monotonic()
            result = await provider.send(message)
        except ProviderConfigurationError as exc:
            await self._fail(
                uow,
                notification,
                delivery,
                f"Invalid provider configuration: {exc}",
                transient=False,
                now=now,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Provider raised while sending notification %s delivery %s: %s",
                notification.id,
                delivery.id,
                exc,
            )
            await self._fail(uow, notification, delivery, str(exc), transient=True, now=now)
            return True
        duration_ms = int((time.monotonic() - start) * 1000)

        if result.delivered:
            delivery.mark_sent(result.provider_message_id)
            delivery.updated_at = now
            await uow.deliveries.update(delivery)
            await self._record_attempt(
                uow, delivery, attempt_number, AttemptStatus.SUCCESS, None, duration_ms, now
            )
            await write_audit(
                uow,
                application_id=notification.application_id,
                actor="dispatcher",
                action=AuditAction.DELIVERY_SENT,
                resource_type="delivery",
                resource_id=delivery.id,
                correlation_id=notification.correlation_id,
                now=now,
                payload={
                    "channel": delivery.channel_type.value,
                    "attempt": attempt_number,
                    "provider_message_id": result.provider_message_id,
                    "duration_ms": duration_ms,
                },
            )
            return True

        transient = result.error_kind is ProviderErrorKind.TRANSIENT
        error = result.error or f"Provider '{delivery.provider}' failed to deliver the message."
        await self._fail(
            uow,
            notification,
            delivery,
            error,
            transient=transient,
            now=now,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
        )
        return True

    async def _fail(
        self,
        uow: UnitOfWork,
        notification: Notification,
        delivery: Delivery,
        error: str,
        *,
        transient: bool,
        now: datetime,
        attempt_number: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        attempt_number = delivery.attempts + 1 if attempt_number is None else attempt_number
        delivery.mark_failed(error, transient=transient, now=now)
        delivery.updated_at = now
        await uow.deliveries.update(delivery)
        await self._record_attempt(
            uow, delivery, attempt_number, AttemptStatus.FAILED, error, duration_ms, now
        )
        await write_audit(
            uow,
            application_id=notification.application_id,
            actor="dispatcher",
            action=AuditAction.DELIVERY_FAILED,
            resource_type="delivery",
            resource_id=delivery.id,
            correlation_id=notification.correlation_id,
            now=now,
            payload={
                "channel": delivery.channel_type.value,
                "attempt": attempt_number,
                "transient": transient,
                "error": error,
                "duration_ms": duration_ms,
            },
        )

    async def _rate_limit_allowed(
        self,
        uow: UnitOfWork,
        notification: Notification,
        delivery: Delivery,
        now: datetime,
    ) -> bool:
        if delivery.rate_limit_per_minute is None or self._rate_limiter is None:
            return True
        key = f"{notification.application_id}:{delivery.channel_type.value}"
        allowed = await self._rate_limiter.acquire(
            key, limit=delivery.rate_limit_per_minute, window_seconds=60.0
        )
        if allowed:
            return True
        delivery.next_attempt_at = now + timedelta(seconds=delivery.retry_backoff_seconds)
        delivery.updated_at = now
        await uow.deliveries.update(delivery)
        return False

    @staticmethod
    async def _record_attempt(
        uow: UnitOfWork,
        delivery: Delivery,
        attempt_number: int,
        status: AttemptStatus,
        error: str | None,
        duration_ms: int | None,
        now: datetime,
    ) -> None:
        await uow.delivery_attempts.add(
            DeliveryAttempt(
                id=uuid4(),
                delivery_id=delivery.id,
                attempt_number=attempt_number,
                status=status,
                error=error,
                duration_ms=duration_ms,
                created_at=now,
            )
        )
