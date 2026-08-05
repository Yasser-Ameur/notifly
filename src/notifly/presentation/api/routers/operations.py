"""Operations endpoints: query and recover, scoped to the authenticated app."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from notifly.application.dto import NotificationCreated, OpsPage
from notifly.domain.enums import ChannelType, DeliveryStatus, NotificationStatus
from notifly.presentation.api.deps import CurrentApp, OperationsServiceDep
from notifly.presentation.api.schemas import (
    ApplicationResponse,
    AuditEntryResponse,
    DeliveryResponse,
    NotificationDetailResponse,
    NotificationResponse,
    OpsPageResponse,
)

router = APIRouter(prefix="/v1/operations", tags=["operations"])


def _page[R: BaseModel](response_model: type[R], page: OpsPage[Any]) -> OpsPageResponse[R]:
    return OpsPageResponse[R](
        items=[response_model.model_validate(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


def _to_detail(created: NotificationCreated) -> NotificationDetailResponse:
    return NotificationDetailResponse(
        notification=NotificationResponse.model_validate(created.notification),
        deliveries=[DeliveryResponse.model_validate(delivery) for delivery in created.deliveries],
    )


@router.get("/notifications", response_model=OpsPageResponse[NotificationResponse])
async def list_notifications(
    service: OperationsServiceDep,
    current: CurrentApp,
    status: NotificationStatus | None = None,
    event: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpsPageResponse[NotificationResponse]:
    """Query notifications by status, event, or creation window."""
    page = await service.list_notifications(
        current.application.id,
        status=status,
        event=event,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        offset=offset,
    )
    return _page(NotificationResponse, page)


@router.get("/deliveries", response_model=OpsPageResponse[DeliveryResponse])
async def list_deliveries(
    service: OperationsServiceDep,
    current: CurrentApp,
    notification_id: UUID | None = None,
    channel_type: ChannelType | None = None,
    status: DeliveryStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpsPageResponse[DeliveryResponse]:
    """Query deliveries by channel or status."""
    page = await service.list_deliveries(
        current.application.id,
        notification_id=notification_id,
        channel_type=channel_type,
        status=status,
        limit=limit,
        offset=offset,
    )
    return _page(DeliveryResponse, page)


@router.get("/applications", response_model=ApplicationResponse)
async def get_application(
    service: OperationsServiceDep,
    current: CurrentApp,
) -> ApplicationResponse:
    """Get the current (authenticated) application."""
    application = await service.get_application(current.application.id)
    return ApplicationResponse.model_validate(application)


@router.get("/deadletters", response_model=OpsPageResponse[NotificationResponse])
async def list_deadletters(
    service: OperationsServiceDep,
    current: CurrentApp,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpsPageResponse[NotificationResponse]:
    """List notifications whose deliveries permanently failed."""
    page = await service.list_deadletters(current.application.id, limit=limit, offset=offset)
    return _page(NotificationResponse, page)


@router.post("/notifications/{notification_id}/retry", response_model=NotificationDetailResponse)
async def retry_notification(
    notification_id: UUID,
    service: OperationsServiceDep,
    current: CurrentApp,
) -> NotificationDetailResponse:
    """Requeue a dead-lettered or partially delivered notification."""
    created = await service.retry_notification(
        current.application.id, notification_id, actor=current.actor
    )
    return _to_detail(created)


@router.get("/audit", response_model=OpsPageResponse[AuditEntryResponse])
async def list_audit(
    service: OperationsServiceDep,
    current: CurrentApp,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OpsPageResponse[AuditEntryResponse]:
    """Query the audit log for this application."""
    page = await service.list_audit(current.application.id, limit=limit, offset=offset)
    return _page(AuditEntryResponse, page)
