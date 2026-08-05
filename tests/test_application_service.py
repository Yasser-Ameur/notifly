"""M3 application-layer tests for ApplicationService (apps + API keys + auth)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notifly.application.security import verify_key
from notifly.application.services.applications import ApplicationService
from notifly.domain.enums import AuditAction
from notifly.domain.errors import (
    AlreadyExistsError,
    AuthenticationError,
    NotFoundError,
)
from notifly.infrastructure.db.base import Base
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
def service(db) -> ApplicationService:
    return ApplicationService(
        create_uow_factory(db), key_prefix_str="notifly_", key_hash_iterations=1000
    )


async def _count_audit(db, action: AuditAction) -> int:
    from sqlalchemy import func, select

    from notifly.infrastructure.db.orm import AuditLogRow

    async with db() as session:
        count = await session.scalar(
            select(func.count(AuditLogRow.id)).where(AuditLogRow.action == action.value)
        )
        return int(count or 0)


async def test_create_application_returns_verifiable_key(db, service) -> None:
    created = await service.create_application("acme")
    assert created.application.name == "acme"
    assert created.issued_key.api_key.key_prefix == created.issued_key.plaintext[:16]
    assert verify_key(
        created.issued_key.plaintext,
        created.issued_key.api_key.key_hash,
    )

    async with db() as session:
        from sqlalchemy import select

        from notifly.infrastructure.db.orm import ApiKeyRow, AuditLogRow

        keys = (await session.scalars(select(ApiKeyRow))).all()
        assert len(keys) == 1
        audits = (await session.scalars(select(AuditLogRow))).all()
        assert len(audits) == 2

    assert await _count_audit(db, AuditAction.APPLICATION_CREATED) == 1
    assert await _count_audit(db, AuditAction.API_KEY_ISSUED) == 1


async def test_create_application_duplicate_name_conflicts(db, service) -> None:
    await service.create_application("acme")
    with pytest.raises(AlreadyExistsError):
        await service.create_application("acme")


async def test_create_application_conflict_rolls_back_audit(db, service) -> None:
    await service.create_application("acme")
    with pytest.raises(AlreadyExistsError):
        await service.create_application("acme")
    assert await _count_audit(db, AuditAction.APPLICATION_CREATED) == 1


async def test_issue_api_key_default_and_custom_name(db, service) -> None:
    created = await service.create_application("acme")
    app_id = created.application.id

    default_issued = await service.issue_api_key(app_id, None)
    assert default_issued.api_key.name == "default"
    assert verify_key(default_issued.plaintext, default_issued.api_key.key_hash)

    named = await service.issue_api_key(app_id, "ci-runner")
    assert named.api_key.name == "ci-runner"
    assert await _count_audit(db, AuditAction.API_KEY_ISSUED) == 3


async def test_issue_api_key_missing_application(db, service) -> None:
    with pytest.raises(NotFoundError):
        await service.issue_api_key(uuid4(), None)


async def test_list_api_keys_scoped_to_application(db, service) -> None:
    acme = await service.create_application("acme")
    other = await service.create_application("other")
    await service.issue_api_key(acme.application.id, "extra")

    acme_keys = await service.list_api_keys(acme.application.id)
    other_keys = await service.list_api_keys(other.application.id)
    assert len(acme_keys) == 2
    assert len(other_keys) == 1
    assert all(key.application_id == acme.application.id for key in acme_keys)


async def test_revoke_api_key(db, service) -> None:
    created = await service.create_application("acme")
    app_id = created.application.id
    issued = await service.issue_api_key(app_id, "ephemeral")

    await service.revoke_api_key(app_id, issued.api_key.id)
    keys = await service.list_api_keys(app_id)
    revoked = next(key for key in keys if key.id == issued.api_key.id)
    assert revoked.revoked_at is not None
    assert await _count_audit(db, AuditAction.API_KEY_REVOKED) == 1


async def test_revoke_api_key_idempotent(db, service) -> None:
    created = await service.create_application("acme")
    app_id = created.application.id
    issued = await service.issue_api_key(app_id, "ephemeral")

    await service.revoke_api_key(app_id, issued.api_key.id)
    await service.revoke_api_key(app_id, issued.api_key.id)
    assert await _count_audit(db, AuditAction.API_KEY_REVOKED) == 1


async def test_revoke_api_key_missing_application(db, service) -> None:
    with pytest.raises(NotFoundError):
        await service.revoke_api_key(uuid4(), uuid4())


async def test_revoke_api_key_wrong_application(db, service) -> None:
    acme = await service.create_application("acme")
    other = await service.create_application("other")
    key = await service.issue_api_key(acme.application.id, "x")
    with pytest.raises(NotFoundError):
        await service.revoke_api_key(other.application.id, key.api_key.id)


async def test_authenticate_returns_context(db, service) -> None:
    created = await service.create_application("acme")
    context = await service.authenticate(created.issued_key.plaintext)
    assert context.application.id == created.application.id
    assert context.api_key.id == created.issued_key.api_key.id


async def test_authenticate_updates_last_used_at(db, service) -> None:
    created = await service.create_application("acme")
    await service.authenticate(created.issued_key.plaintext)
    keys = await service.list_api_keys(created.application.id)
    assert keys[0].last_used_at is not None


async def test_authenticate_second_issued_key(db, service) -> None:
    created = await service.create_application("acme")
    extra = await service.issue_api_key(created.application.id, "extra")
    context = await service.authenticate(extra.plaintext)
    assert context.api_key.id == extra.api_key.id


async def test_authenticate_revoked_key_rejected(db, service) -> None:
    created = await service.create_application("acme")
    await service.revoke_api_key(created.application.id, created.issued_key.api_key.id)
    with pytest.raises(AuthenticationError):
        await service.authenticate(created.issued_key.plaintext)


async def test_authenticate_unknown_key_rejected(db, service) -> None:
    with pytest.raises(AuthenticationError):
        await service.authenticate("notifly_definitelynotakeyvalue")


async def test_authenticate_empty_key_rejected(db, service) -> None:
    with pytest.raises(AuthenticationError):
        await service.authenticate(None)
    with pytest.raises(AuthenticationError):
        await service.authenticate("   ")


async def test_authenticate_stale_hash_skipped(db, service) -> None:
    """A key whose stored prefix matches but hash fails must not authenticate."""
    created = await service.create_application("acme")
    other = await service.issue_api_key(created.application.id, "other")
    tampered = other.plaintext[:-1] + ("a" if other.plaintext[-1] != "a" else "b")
    with pytest.raises(AuthenticationError):
        await service.authenticate(tampered)


async def test_list_applications_paginated(db, service) -> None:
    for name in ("a", "b", "c", "d"):
        await service.create_application(name)
    first = await service.list_applications(limit=2, offset=0)
    second = await service.list_applications(limit=2, offset=2)
    assert [app.name for app in first] == ["a", "b"]
    assert [app.name for app in second] == ["c", "d"]


async def test_get_application(db, service) -> None:
    created = await service.create_application("acme")
    fetched = await service.get_application(created.application.id)
    assert fetched.name == "acme"


async def test_get_application_missing(db, service) -> None:
    with pytest.raises(NotFoundError):
        await service.get_application(uuid4())
