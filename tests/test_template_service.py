"""M4 application-layer tests for TemplateService."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notifly.application.services.templates import TemplateService
from notifly.domain.enums import AuditAction, ChannelType, VariableType
from notifly.domain.errors import (
    AlreadyExistsError,
    InvalidDataError,
    NotFoundError,
    TemplateRenderingError,
    VariableValidationError,
)
from notifly.domain.models.application import Application
from notifly.domain.models.template import TemplateChannelContent, VariableDef
from notifly.infrastructure.db.base import Base
from notifly.infrastructure.db.orm import AuditLogRow
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.infrastructure.db.uow import create_uow_factory


@pytest.fixture()
async def db(test_settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_engine(test_settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest.fixture()
def service(db) -> TemplateService:
    return TemplateService(create_uow_factory(db))


async def _make_app(db, name: str = "acme") -> Application:
    from notifly.application.services.applications import ApplicationService

    app_service = ApplicationService(
        create_uow_factory(db), key_prefix_str="notifly_", key_hash_iterations=1000
    )
    created = await app_service.create_application(name)
    return created.application


def _email_body() -> dict[ChannelType, TemplateChannelContent]:
    return {
        ChannelType.EMAIL: TemplateChannelContent(
            subject="Hi {{ name }}", body="Welcome {{ name }}"
        )
    }


def _vars() -> list[VariableDef]:
    return [VariableDef(name="name", type=VariableType.STRING)]


async def _count_audit(db, action: AuditAction) -> int:
    async with db() as session:
        count = await session.scalar(
            select(func.count(AuditLogRow.id)).where(AuditLogRow.action == action.value)
        )
        return int(count or 0)


async def test_create_template(db, service) -> None:
    application = await _make_app(db)
    template = await service.create_template(
        application.id,
        actor="key_prefix",
        name="Welcome",
        event="user_welcome",
        description="Greets a new user",
        variables=_vars(),
        channels=_email_body(),
    )
    assert template.application_id == application.id
    assert template.event == "user_welcome"
    assert template.channels[ChannelType.EMAIL].body == "Welcome {{ name }}"
    assert await _count_audit(db, AuditAction.TEMPLATE_CREATED) == 1


async def test_create_template_duplicate_event(db, service) -> None:
    application = await _make_app(db)
    await service.create_template(
        application.id,
        actor="a",
        name="One",
        event="evt",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    with pytest.raises(AlreadyExistsError):
        await service.create_template(
            application.id,
            actor="a",
            name="Two",
            event="evt",
            description=None,
            variables=[],
            channels=_email_body(),
        )
    assert await _count_audit(db, AuditAction.TEMPLATE_CREATED) == 1


async def test_create_template_duplicate_variable_declarations(db, service) -> None:
    application = await _make_app(db)
    with pytest.raises(VariableValidationError):
        await service.create_template(
            application.id,
            actor="a",
            name="Bad",
            event="bad",
            description=None,
            variables=[
                VariableDef(name="name", type=VariableType.STRING),
                VariableDef(name="name", type=VariableType.NUMBER),
            ],
            channels=_email_body(),
        )


async def test_create_template_empty_channels_rejected(db, service) -> None:
    application = await _make_app(db)
    with pytest.raises(InvalidDataError):
        await service.create_template(
            application.id,
            actor="a",
            name="Empty",
            event="empty",
            description=None,
            variables=[],
            channels={},
        )


async def test_create_template_missing_application(db, service) -> None:
    with pytest.raises(NotFoundError):
        await service.create_template(
            uuid4(),
            actor="a",
            name="X",
            event="x",
            description=None,
            variables=[],
            channels=_email_body(),
        )


async def test_list_templates_scoped(db, service) -> None:
    app_a = await _make_app(db, "a")
    app_b = await _make_app(db, "b")
    await service.create_template(
        app_a.id,
        actor="a",
        name="A1",
        event="a1",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    await service.create_template(
        app_b.id,
        actor="a",
        name="B1",
        event="b1",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    assert [t.event for t in await service.list_templates(app_a.id)] == ["a1"]
    assert [t.event for t in await service.list_templates(app_b.id)] == ["b1"]


async def test_get_template(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="W",
        event="w",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    fetched = await service.get_template(application.id, created.id)
    assert fetched.id == created.id


async def test_get_template_other_application_not_found(db, service) -> None:
    app_a = await _make_app(db, "a")
    app_b = await _make_app(db, "b")
    created = await service.create_template(
        app_a.id,
        actor="a",
        name="W",
        event="w",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    with pytest.raises(NotFoundError):
        await service.get_template(app_b.id, created.id)


async def test_update_template(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="Old",
        event="old",
        description="d",
        variables=[],
        channels=_email_body(),
    )
    updated = await service.update_template(
        application.id,
        created.id,
        actor="a",
        name="New",
        event="new",
        description=None,
        variables=_vars(),
        channels={ChannelType.SLACK: TemplateChannelContent(body="slack {{ name }}")},
    )
    assert updated.name == "New"
    assert updated.event == "new"
    assert updated.description is None
    assert set(updated.channels) == {ChannelType.SLACK}
    assert await _count_audit(db, AuditAction.TEMPLATE_UPDATED) == 1


async def test_update_template_keeps_id_and_created_at(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="Old",
        event="old",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    updated = await service.update_template(
        application.id,
        created.id,
        actor="a",
        name="New",
        event="new",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    assert updated.id == created.id
    assert updated.created_at == created.created_at


async def test_update_template_event_conflict(db, service) -> None:
    application = await _make_app(db)
    first = await service.create_template(
        application.id,
        actor="a",
        name="One",
        event="one",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    await service.create_template(
        application.id,
        actor="a",
        name="Two",
        event="two",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    with pytest.raises(AlreadyExistsError):
        await service.update_template(
            application.id,
            first.id,
            actor="a",
            name="One",
            event="two",
            description=None,
            variables=[],
            channels=_email_body(),
        )


async def test_delete_template(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="Gone",
        event="gone",
        description=None,
        variables=[],
        channels=_email_body(),
    )
    await service.delete_template(application.id, created.id, actor="a")
    with pytest.raises(NotFoundError):
        await service.get_template(application.id, created.id)
    assert await _count_audit(db, AuditAction.TEMPLATE_DELETED) == 1


async def test_delete_template_missing(db, service) -> None:
    application = await _make_app(db)
    with pytest.raises(NotFoundError):
        await service.delete_template(application.id, uuid4(), actor="a")


async def test_preview_template_renders(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="W",
        event="w",
        description=None,
        variables=_vars(),
        channels=_email_body(),
    )
    rendered = await service.preview_template(application.id, created.id, {"name": "Alice"})
    assert rendered[ChannelType.EMAIL].subject == "Hi Alice"
    assert rendered[ChannelType.EMAIL].body == "Welcome Alice"


async def test_preview_template_validation_error(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="W",
        event="w",
        description=None,
        variables=_vars(),
        channels=_email_body(),
    )
    with pytest.raises(VariableValidationError):
        await service.preview_template(application.id, created.id, {})


async def test_preview_template_render_error(db, service) -> None:
    application = await _make_app(db)
    created = await service.create_template(
        application.id,
        actor="a",
        name="W",
        event="w",
        description=None,
        variables=_vars(),
        channels={ChannelType.EMAIL: TemplateChannelContent(body="{{ other }}")},
    )
    with pytest.raises(TemplateRenderingError):
        await service.preview_template(application.id, created.id, {"name": "Alice"})
