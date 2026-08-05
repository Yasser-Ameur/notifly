"""Template endpoints: CRUD and preview rendering."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from notifly.domain.enums import ChannelType
from notifly.domain.models.template import Template, TemplateChannelContent, VariableDef
from notifly.presentation.api.deps import CurrentApp, TemplateServiceDep
from notifly.presentation.api.schemas import (
    TemplateChannelContentInput,
    TemplateChannelContentResponse,
    TemplatePreviewRequest,
    TemplatePreviewResponse,
    TemplateResponse,
    TemplateUpsert,
    VariableDefInput,
)

router = APIRouter(prefix="/v1/templates", tags=["templates"])


def _domain_variables(variables: list[VariableDefInput]) -> list[VariableDef]:
    return [VariableDef(**variable.model_dump()) for variable in variables]


def _domain_channels(
    channels: dict[ChannelType, TemplateChannelContentInput],
) -> dict[ChannelType, TemplateChannelContent]:
    return {
        channel_type: TemplateChannelContent(subject=content.subject, body=content.body)
        for channel_type, content in channels.items()
    }


def _to_response(template: Template) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        name=template.name,
        event=template.event,
        description=template.description,
        variables=[
            VariableDefInput(name=v.name, type=v.type, required=v.required, default=v.default)
            for v in template.variables
        ],
        channels={
            channel_type: TemplateChannelContentResponse(subject=content.subject, body=content.body)
            for channel_type, content in template.channels.items()
        },
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateUpsert,
    service: TemplateServiceDep,
    current: CurrentApp,
) -> TemplateResponse:
    """Create a template for the authenticated application."""
    template = await service.create_template(
        current.application.id,
        actor=current.actor,
        name=body.name,
        event=body.event,
        description=body.description,
        variables=_domain_variables(body.variables),
        channels=_domain_channels(body.channels),
    )
    return _to_response(template)


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    service: TemplateServiceDep,
    current: CurrentApp,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[TemplateResponse]:
    """List the authenticated application's templates (paginated)."""
    templates = await service.list_templates(current.application.id)
    return [_to_response(template) for template in templates[offset : offset + limit]]


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    service: TemplateServiceDep,
    current: CurrentApp,
) -> TemplateResponse:
    """Get a template by id."""
    template = await service.get_template(current.application.id, template_id)
    return _to_response(template)


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    template_id: UUID,
    body: TemplateUpsert,
    service: TemplateServiceDep,
    current: CurrentApp,
) -> TemplateResponse:
    """Replace a template."""
    template = await service.update_template(
        current.application.id,
        template_id,
        actor=current.actor,
        name=body.name,
        event=body.event,
        description=body.description,
        variables=_domain_variables(body.variables),
        channels=_domain_channels(body.channels),
    )
    return _to_response(template)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    service: TemplateServiceDep,
    current: CurrentApp,
) -> None:
    """Delete a template."""
    await service.delete_template(current.application.id, template_id, actor=current.actor)


@router.post("/{template_id}/preview", response_model=TemplatePreviewResponse)
async def preview_template(
    template_id: UUID,
    body: TemplatePreviewRequest,
    service: TemplateServiceDep,
    current: CurrentApp,
) -> TemplatePreviewResponse:
    """Render a template with the supplied (sample) variables."""
    rendered = await service.preview_template(current.application.id, template_id, body.variables)
    return TemplatePreviewResponse(
        channels={
            channel_type: TemplateChannelContentResponse(subject=content.subject, body=content.body)
            for channel_type, content in rendered.items()
        }
    )
