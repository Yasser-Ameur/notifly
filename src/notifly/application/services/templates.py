"""Template use cases: CRUD, variable validation, and preview rendering."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from notifly.application.services.audit import write_audit
from notifly.application.templating import (
    RenderedContent,
    render_template,
    validate_variables,
)
from notifly.domain.enums import AuditAction, ChannelType
from notifly.domain.errors import (
    AlreadyExistsError,
    NotFoundError,
    VariableValidationError,
)
from notifly.domain.models.template import (
    Template,
    TemplateChannelContent,
    VariableDef,
)
from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory
from notifly.logging import get_correlation_id


class TemplateService:
    """Use cases for template lifecycle and rendering."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or SystemClock()

    async def create_template(
        self,
        application_id: UUID,
        *,
        actor: str,
        name: str,
        event: str,
        description: str | None,
        variables: list[VariableDef],
        channels: dict[ChannelType, TemplateChannelContent],
    ) -> Template:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            await self._require_application(uow, application_id)
            self._validate_declarations(variables)
            existing = await uow.templates.get_by_app_and_event(application_id, event)
            if existing is not None:
                raise AlreadyExistsError(
                    f"A template for event {event!r} already exists in this application."
                )
            template = Template(
                id=uuid4(),
                application_id=application_id,
                name=name,
                event=event,
                description=description,
                variables=variables,
                channels=channels,
                created_at=now,
                updated_at=now,
            )
            await uow.templates.add(template)
            await write_audit(
                uow,
                application_id=application_id,
                actor=actor,
                action=AuditAction.TEMPLATE_CREATED,
                resource_type="template",
                resource_id=template.id,
                correlation_id=get_correlation_id() or "",
                now=now,
                payload={"name": name, "event": event},
            )
        return template

    async def list_templates(self, application_id: UUID) -> list[Template]:
        async with self._uow_factory() as uow:
            await self._require_application(uow, application_id)
            return await uow.templates.list_by_app(application_id)

    async def get_template(self, application_id: UUID, template_id: UUID) -> Template:
        async with self._uow_factory() as uow:
            return await self._require_template(uow, application_id, template_id)

    async def update_template(
        self,
        application_id: UUID,
        template_id: UUID,
        *,
        actor: str,
        name: str,
        event: str,
        description: str | None,
        variables: list[VariableDef],
        channels: dict[ChannelType, TemplateChannelContent],
    ) -> Template:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            template = await self._require_template(uow, application_id, template_id)
            self._validate_declarations(variables)
            existing = await uow.templates.get_by_app_and_event(application_id, event)
            if existing is not None and existing.id != template_id:
                raise AlreadyExistsError(
                    f"A template for event {event!r} already exists in this application."
                )
            template.name = name
            template.event = event
            template.description = description
            template.variables = variables
            template.channels = channels
            template.updated_at = now
            await uow.templates.update(template)
            await write_audit(
                uow,
                application_id=application_id,
                actor=actor,
                action=AuditAction.TEMPLATE_UPDATED,
                resource_type="template",
                resource_id=template.id,
                correlation_id=get_correlation_id() or "",
                now=now,
                payload={"name": name, "event": event},
            )
        return template

    async def delete_template(self, application_id: UUID, template_id: UUID, *, actor: str) -> None:
        now = self._clock.now()
        async with self._uow_factory() as uow:
            template = await self._require_template(uow, application_id, template_id)
            await uow.templates.delete(template)
            await write_audit(
                uow,
                application_id=application_id,
                actor=actor,
                action=AuditAction.TEMPLATE_DELETED,
                resource_type="template",
                resource_id=template.id,
                correlation_id=get_correlation_id() or "",
                now=now,
                payload={"name": template.name, "event": template.event},
            )

    async def preview_template(
        self, application_id: UUID, template_id: UUID, variables: dict[str, Any]
    ) -> dict[ChannelType, RenderedContent]:
        async with self._uow_factory() as uow:
            template = await self._require_template(uow, application_id, template_id)
            resolved = validate_variables(template.variables, variables)
            return render_template(template, resolved)

    async def _require_application(self, uow: UnitOfWork, application_id: UUID) -> None:
        if await uow.applications.get(application_id) is None:
            raise NotFoundError(f"Application {application_id} does not exist.")

    @staticmethod
    def _validate_declarations(variables: list[VariableDef]) -> None:
        names = [variable.name for variable in variables]
        if len(names) != len(set(names)):
            raise VariableValidationError("duplicate variable declaration")

    async def _require_template(
        self, uow: UnitOfWork, application_id: UUID, template_id: UUID
    ) -> Template:
        template = await uow.templates.get(template_id)
        if template is None or template.application_id != application_id:
            raise NotFoundError(f"Template {template_id} does not exist.")
        return template
