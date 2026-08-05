"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notifly.application.dto import AuthenticatedContext
from notifly.application.services.applications import ApplicationService
from notifly.application.services.notifications import NotificationService
from notifly.application.services.templates import TemplateService
from notifly.domain.ports.repositories import UnitOfWorkFactory
from notifly.infrastructure.db.uow import SqlAlchemyUnitOfWork


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


def get_unit_of_work_factory(request: Request) -> UnitOfWorkFactory:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return lambda: SqlAlchemyUnitOfWork(factory())


def get_application_service(request: Request) -> ApplicationService:
    settings = request.app.state.settings
    return ApplicationService(
        get_unit_of_work_factory(request),
        key_prefix_str=settings.api_key_prefix,
        key_hash_iterations=settings.api_key_hash_iterations,
    )


def get_template_service(request: Request) -> TemplateService:
    return TemplateService(get_unit_of_work_factory(request))


def get_notification_service(request: Request) -> NotificationService:
    return NotificationService(get_unit_of_work_factory(request))


async def get_current_application(
    request: Request,
    x_notifly_key: Annotated[str | None, Header(alias="X-Notifly-Key")] = None,
) -> AuthenticatedContext:
    """Authenticate the request against an application API key."""
    service = get_application_service(request)
    return await service.authenticate(x_notifly_key)


DbSession = Annotated[AsyncSession, Depends(get_session)]
AppService = Annotated[ApplicationService, Depends(get_application_service)]
TemplateServiceDep = Annotated[TemplateService, Depends(get_template_service)]
NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
CurrentApp = Annotated[AuthenticatedContext, Depends(get_current_application)]
