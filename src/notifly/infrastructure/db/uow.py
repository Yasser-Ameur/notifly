"""Unit of Work implementation wrapping an async SQLAlchemy session.

A Unit of Work is the transactional boundary: every mutation a use case
performs inside one Unit of Work is committed atomically (or rolled back).
This is what makes the transactional outbox possible — the notification,
its deliveries, the outbox event, the audit entry, and the idempotency record
all share a single commit.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notifly.domain.ports.repositories import UnitOfWork, UnitOfWorkFactory
from notifly.infrastructure.db import repositories as repos


class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.applications = repos.SqlAlchemyApplicationRepository(session)
        self.api_keys = repos.SqlAlchemyApiKeyRepository(session)
        self.channels = repos.SqlAlchemyChannelRepository(session)
        self.templates = repos.SqlAlchemyTemplateRepository(session)
        self.notifications = repos.SqlAlchemyNotificationRepository(session)
        self.deliveries = repos.SqlAlchemyDeliveryRepository(session)
        self.delivery_attempts = repos.SqlAlchemyDeliveryAttemptRepository(session)
        self.audit = repos.SqlAlchemyAuditRepository(session)
        self.outbox = repos.SqlAlchemyOutboxRepository(session)
        self.idempotency = repos.SqlAlchemyIdempotencyRepository(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def close(self) -> None:
        await self._session.close()

    async def __aenter__(self) -> UnitOfWork:
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()
        await self.close()


def create_uow_factory(session_factory: async_sessionmaker[AsyncSession]) -> UnitOfWorkFactory:
    """Build a UnitOfWorkFactory bound to an ``async_sessionmaker``."""

    def _factory() -> UnitOfWork:
        session = session_factory()
        return SqlAlchemyUnitOfWork(session)

    return _factory
