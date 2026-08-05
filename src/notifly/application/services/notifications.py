"""Notification Engine: send flow orchestration.

The engine validates variables, renders templates into per-channel payloads,
and persists the notification, its delivery plan, the outbox event, the audit
entry, and the idempotency record in one atomic transaction. It never invokes
a provider — the dispatcher does that later (see ``dispatcher.py``).
"""

from __future__ import annotations

import hashlib
import json as jsonlib
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from notifly.application.dto import NotificationCreated
from notifly.application.services.audit import write_audit
from notifly.application.templating import render_template, validate_variables
from notifly.domain.enums import (
    AuditAction,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    OutboxEventType,
)
from notifly.domain.errors import (
    IdempotencyConflictError,
    InvalidDataError,
    NotFoundError,
)
from notifly.domain.models.idempotency import IdempotencyRecord
from notifly.domain.models.notification import Delivery, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.models.template import Template
from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory
from notifly.domain.providers import ProviderRegistry
from notifly.logging import get_correlation_id

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_RETRY_BACKOFF_SECONDS = 5.0


def _coerce_recipients(recipients: dict[ChannelType, str]) -> dict[ChannelType, str]:
    """Normalize string keys (e.g. from JSON) to ``ChannelType`` members."""
    return {
        channel_type
        if isinstance(channel_type, ChannelType)
        else ChannelType(channel_type): recipient
        for channel_type, recipient in recipients.items()
    }


class NotificationService:
    """Use cases for the notification lifecycle and the delivery plan."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()
        self._registry = registry

    async def create_notification(
        self,
        application_id: UUID,
        *,
        actor: str,
        event: str,
        variables: dict[str, Any],
        recipients: dict[ChannelType, str],
        scheduled_at: datetime | None = None,
        idempotency_key: str | None = None,
        template_id: UUID | None = None,
        correlation_id: str = "",
    ) -> NotificationCreated:
        """Create a notification and its delivery plan in one transaction.

        With an ``idempotency_key``, an identical earlier request returns the
        existing notification (``replayed=True``); a different payload under
        the same key is rejected with a conflict.
        """
        correlation_id = correlation_id or (get_correlation_id() or "")
        recipients = _coerce_recipients(recipients)
        request_hash = self._request_hash(event, variables, recipients, scheduled_at, template_id)
        async with self._uow_factory() as uow:
            await self._require_application(uow, application_id)
            if idempotency_key:
                existing = await uow.idempotency.get(application_id, idempotency_key)
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise IdempotencyConflictError(
                            "Idempotency-Key was already used with a different payload."
                        )
                    notification = await self._require_notification(
                        uow, application_id, existing.notification_id
                    )
                    deliveries = await uow.deliveries.list_by_notification(notification.id)
                    return NotificationCreated(
                        notification=notification, deliveries=deliveries, replayed=True
                    )

            template = await self._resolve_template(uow, application_id, template_id, event)
            resolved = validate_variables(template.variables, variables)
            rendered = render_template(template, resolved)
            self._validate_recipients(recipients, rendered)

            now = self._clock.now()
            notification = Notification(
                id=uuid4(),
                application_id=application_id,
                template_id=template.id,
                event=template.event,
                variables=variables,
                status=NotificationStatus.PENDING,
                scheduled_at=scheduled_at,
                correlation_id=correlation_id,
                retry_count=0,
                created_at=now,
                updated_at=now,
            )
            await uow.notifications.add(notification)

            deliveries = [
                await self._build_delivery(
                    uow, application_id, notification, channel_type, recipient, rendered, now
                )
                for channel_type, recipient in recipients.items()
            ]
            await uow.deliveries.add_many(deliveries)

            if scheduled_at is None:
                await uow.outbox.add(
                    OutboxEvent(
                        id=uuid4(),
                        event_type=OutboxEventType.NOTIFICATION_CREATED,
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
                action=AuditAction.NOTIFICATION_CREATED,
                resource_type="notification",
                resource_id=notification.id,
                correlation_id=correlation_id,
                now=now,
                payload={
                    "event": notification.event,
                    "channels": [str(delivery.channel_type) for delivery in deliveries],
                },
            )

            if idempotency_key:
                await uow.idempotency.add(
                    IdempotencyRecord(
                        id=uuid4(),
                        application_id=application_id,
                        key=idempotency_key,
                        request_hash=request_hash,
                        notification_id=notification.id,
                        created_at=now,
                    )
                )
        return NotificationCreated(notification=notification, deliveries=deliveries)

    async def get_notification(
        self, application_id: UUID, notification_id: UUID
    ) -> NotificationCreated:
        async with self._uow_factory() as uow:
            notification = await self._require_notification(uow, application_id, notification_id)
            deliveries = await uow.deliveries.list_by_notification(notification.id)
            return NotificationCreated(notification=notification, deliveries=deliveries)

    async def list_deliveries(self, application_id: UUID, notification_id: UUID) -> list[Delivery]:
        async with self._uow_factory() as uow:
            await self._require_notification(uow, application_id, notification_id)
            return await uow.deliveries.list_by_notification(notification_id)

    async def cancel_notification(
        self,
        application_id: UUID,
        notification_id: UUID,
        *,
        actor: str,
        correlation_id: str = "",
    ) -> NotificationCreated:
        correlation_id = correlation_id or (get_correlation_id() or "")
        now = self._clock.now()
        async with self._uow_factory() as uow:
            notification = await self._require_notification(uow, application_id, notification_id)
            notification.cancel()
            notification.updated_at = now
            await uow.notifications.update(notification)
            await write_audit(
                uow,
                application_id=application_id,
                actor=actor,
                action=AuditAction.NOTIFICATION_CANCELLED,
                resource_type="notification",
                resource_id=notification.id,
                correlation_id=correlation_id,
                now=now,
                payload={"event": notification.event},
            )
            deliveries = await uow.deliveries.list_by_notification(notification.id)
        return NotificationCreated(notification=notification, deliveries=deliveries)

    # --- helpers ---

    async def _require_application(self, uow: UnitOfWork, application_id: UUID) -> None:
        if await uow.applications.get(application_id) is None:
            raise NotFoundError(f"Application {application_id} does not exist.")

    async def _require_notification(
        self, uow: UnitOfWork, application_id: UUID, notification_id: UUID
    ) -> Notification:
        notification = await uow.notifications.get(notification_id)
        if notification is None or notification.application_id != application_id:
            raise NotFoundError(f"Notification {notification_id} does not exist.")
        return notification

    async def _resolve_template(
        self,
        uow: UnitOfWork,
        application_id: UUID,
        template_id: UUID | None,
        event: str,
    ) -> Template:
        if template_id is not None:
            template = await uow.templates.get(template_id)
            if template is None or template.application_id != application_id:
                raise NotFoundError(f"Template {template_id} does not exist.")
            if template.event != event:
                raise InvalidDataError(
                    f"Event {event!r} does not match template {template_id} ({template.event!r})."
                )
            return template
        template = await uow.templates.get_by_app_and_event(application_id, event)
        if template is None:
            raise NotFoundError(f"No template is defined for event {event!r} in this application.")
        return template

    @staticmethod
    def _validate_recipients(
        recipients: dict[ChannelType, str], rendered: dict[ChannelType, Any]
    ) -> None:
        if not recipients:
            raise InvalidDataError("At least one recipient is required.")
        missing = sorted(set(recipients) - set(rendered))
        if missing:
            channels = ", ".join(str(channel_type) for channel_type in missing)
            raise InvalidDataError(f"No template content for channel(s): {channels}.")

    async def _build_delivery(
        self,
        uow: UnitOfWork,
        application_id: UUID,
        notification: Notification,
        channel_type: ChannelType,
        recipient: str,
        rendered: dict[ChannelType, Any],
        now: datetime,
    ) -> Delivery:
        channel = await uow.channels.get_by_app_and_type(application_id, channel_type)
        if channel is not None and not channel.enabled:
            raise InvalidDataError(f"Channel '{channel_type}' is disabled for this application.")
        content = rendered[channel_type]
        return Delivery(
            id=uuid4(),
            notification_id=notification.id,
            channel_type=channel_type,
            provider=self._provider_name(channel_type),
            recipient=recipient,
            subject=content.subject,
            body=content.body,
            status=DeliveryStatus.PENDING,
            attempts=0,
            max_attempts=channel.max_attempts if channel is not None else _DEFAULT_MAX_ATTEMPTS,
            retry_backoff_seconds=(
                channel.retry_backoff_seconds
                if channel is not None
                else _DEFAULT_RETRY_BACKOFF_SECONDS
            ),
            rate_limit_per_minute=(channel.rate_limit_per_minute if channel is not None else None),
            created_at=now,
            updated_at=now,
        )

    def _provider_name(self, channel_type: ChannelType) -> str:
        if self._registry is not None:
            return self._registry.get(channel_type).__name__
        return channel_type.value

    @staticmethod
    def _request_hash(
        event: str,
        variables: dict[str, Any],
        recipients: dict[ChannelType, str],
        scheduled_at: datetime | None,
        template_id: UUID | None,
    ) -> str:
        canonical = jsonlib.dumps(
            {
                "template_id": str(template_id) if template_id is not None else None,
                "event": event,
                "variables": variables,
                "recipients": {
                    channel_type.value: recipient for channel_type, recipient in recipients.items()
                },
                "scheduled_at": scheduled_at.isoformat() if scheduled_at is not None else None,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
