"""Notification endpoints: send, inspect, and cancel."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, status
from fastapi.responses import JSONResponse

from notifly.application.dto import NotificationCreated
from notifly.domain.models.notification import Delivery
from notifly.presentation.api.deps import CurrentApp, NotificationServiceDep
from notifly.presentation.api.schemas import (
    DeliveryResponse,
    NotificationCreateRequest,
    NotificationDetailResponse,
    NotificationResponse,
)

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])


def _delivery_response(delivery: Delivery) -> DeliveryResponse:
    return DeliveryResponse(
        id=delivery.id,
        notification_id=delivery.notification_id,
        channel_type=delivery.channel_type,
        provider=delivery.provider,
        recipient=delivery.recipient,
        subject=delivery.subject,
        body=delivery.body,
        status=delivery.status,
        attempts=delivery.attempts,
        next_attempt_at=delivery.next_attempt_at,
        last_error=delivery.last_error,
        provider_message_id=delivery.provider_message_id,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
    )


def _to_response(created: NotificationCreated) -> NotificationDetailResponse:
    notification = created.notification
    return NotificationDetailResponse(
        notification=NotificationResponse(
            id=notification.id,
            template_id=notification.template_id,
            event=notification.event,
            variables=notification.variables,
            status=notification.status,
            scheduled_at=notification.scheduled_at,
            correlation_id=notification.correlation_id,
            created_at=notification.created_at,
            updated_at=notification.updated_at,
        ),
        deliveries=[_delivery_response(delivery) for delivery in created.deliveries],
    )


@router.post("", response_model=NotificationDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_notification(
    body: NotificationCreateRequest,
    service: NotificationServiceDep,
    current: CurrentApp,
    x_idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Send a notification now, or schedule it via ``scheduled_at``.

    Honors the ``Idempotency-Key`` header: a replay returns the existing
    notification with HTTP 200 instead of creating a duplicate.
    """
    created = await service.create_notification(
        current.application.id,
        actor=current.actor,
        event=body.event,
        variables=body.variables,
        recipients=body.recipients,
        scheduled_at=body.scheduled_at,
        idempotency_key=x_idempotency_key,
        template_id=body.template_id,
    )
    payload = _to_response(created).model_dump(mode="json")
    if created.replayed:
        return JSONResponse(status_code=status.HTTP_200_OK, content=payload)
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=payload)


@router.get("/{notification_id}", response_model=NotificationDetailResponse)
async def get_notification(
    notification_id: UUID,
    service: NotificationServiceDep,
    current: CurrentApp,
) -> NotificationDetailResponse:
    """Get a notification and the status of each delivery."""
    created = await service.get_notification(current.application.id, notification_id)
    return _to_response(created)


@router.get("/{notification_id}/deliveries", response_model=list[DeliveryResponse])
async def list_deliveries(
    notification_id: UUID,
    service: NotificationServiceDep,
    current: CurrentApp,
) -> list[DeliveryResponse]:
    """List the per-channel deliveries of a notification."""
    deliveries = await service.list_deliveries(current.application.id, notification_id)
    return [_delivery_response(delivery) for delivery in deliveries]


@router.post("/{notification_id}/cancel", response_model=NotificationDetailResponse)
async def cancel_notification(
    notification_id: UUID,
    service: NotificationServiceDep,
    current: CurrentApp,
) -> NotificationDetailResponse:
    """Cancel a pending or scheduled notification."""
    created = await service.cancel_notification(
        current.application.id, notification_id, actor=current.actor
    )
    return _to_response(created)
