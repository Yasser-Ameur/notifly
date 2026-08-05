"""Application and API key management endpoints."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from notifly.application.dto import AuthenticatedContext
from notifly.domain.errors import ForbiddenError
from notifly.presentation.api.deps import AppService, CurrentApp
from notifly.presentation.api.schemas import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ApplicationCreate,
    ApplicationCreatedResponse,
    ApplicationResponse,
)

router = APIRouter(prefix="/v1/apps", tags=["applications"])


def _require_ownership(context: AuthenticatedContext, application_id: UUID) -> None:
    if context.application.id != application_id:
        raise ForbiddenError("API key is scoped to a different application.")


@router.post("", response_model=ApplicationCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_application(
    body: ApplicationCreate,
    service: AppService,
) -> ApplicationCreatedResponse:
    """Create an application and its one-time bootstrap API key."""
    created = await service.create_application(body.name)
    return ApplicationCreatedResponse(
        id=created.application.id,
        name=created.application.name,
        api_key=created.issued_key.plaintext,
    )


@router.get("", response_model=list[ApplicationResponse])
async def list_applications(
    service: AppService,
    current: CurrentApp,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ApplicationResponse]:
    """List applications (paginated)."""
    applications = await service.list_applications(limit=limit, offset=offset)
    return [ApplicationResponse.model_validate(application) for application in applications]


@router.post(
    "/{application_id}/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_api_key(
    application_id: UUID,
    service: AppService,
    current: CurrentApp,
    body: ApiKeyCreate | None = None,
) -> ApiKeyCreatedResponse:
    """Issue a new API key. The plaintext is returned exactly once."""
    _require_ownership(current, application_id)
    issued = await service.issue_api_key(application_id, body.name if body else None)
    return ApiKeyCreatedResponse(
        id=issued.api_key.id,
        name=issued.api_key.name,
        key_prefix=issued.api_key.key_prefix,
        created_at=issued.api_key.created_at,
        api_key=issued.plaintext,
    )


@router.get(
    "/{application_id}/api-keys",
    response_model=list[ApiKeyResponse],
)
async def list_api_keys(
    application_id: UUID,
    service: AppService,
    current: CurrentApp,
) -> list[ApiKeyResponse]:
    """List issued keys (metadata only — never the secret)."""
    _require_ownership(current, application_id)
    keys = await service.list_api_keys(application_id)
    return [ApiKeyResponse.model_validate(key) for key in keys]


@router.delete(
    "/{application_id}/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_api_key(
    application_id: UUID,
    key_id: UUID,
    service: AppService,
    current: CurrentApp,
) -> None:
    """Revoke an API key."""
    _require_ownership(current, application_id)
    await service.revoke_api_key(application_id, key_id)
