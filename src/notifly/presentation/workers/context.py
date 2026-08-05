"""Worker composition root: builds the services an ARQ job needs from settings.

The worker process lives in the same clean-architecture stack as the API; this
module wires the concrete adapters (DB engine, provider registry, Redis-based
rate limiter and task transport, Prometheus metrics) exactly once per process,
in ARQ's ``on_startup``.
"""

from __future__ import annotations

from arq.connections import ArqRedis

from notifly.application.services.dispatcher import DispatcherService
from notifly.application.services.outbox import OutboxPublisher
from notifly.config import Settings
from notifly.domain.ports.metrics import Metrics
from notifly.domain.ports.repositories import UnitOfWorkFactory
from notifly.domain.providers import ProviderRegistry
from notifly.infrastructure.db.session import create_engine, create_session_factory
from notifly.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notifly.infrastructure.observability.metrics import PrometheusMetrics
from notifly.infrastructure.providers import create_provider_registry
from notifly.infrastructure.redis.rate_limit import RedisRateLimiter
from notifly.infrastructure.redis.tasks import ArqTaskDispatcher


class WorkerContext:
    """Services shared by every job in the worker process."""

    def __init__(self, settings: Settings, redis: ArqRedis) -> None:
        self.settings = settings
        self.redis = redis
        self.engine = create_engine(settings)
        self.session_factory = create_session_factory(self.engine)
        self.metrics: Metrics = PrometheusMetrics()
        self.registry: ProviderRegistry = create_provider_registry()

    @property
    def uow_factory(self) -> UnitOfWorkFactory:
        return lambda: SqlAlchemyUnitOfWork(self.session_factory())

    def rate_limiter(self) -> RedisRateLimiter:
        return RedisRateLimiter(self.redis)

    def task_dispatcher(self) -> ArqTaskDispatcher:
        return ArqTaskDispatcher(self.redis)

    def dispatcher(self) -> DispatcherService:
        return DispatcherService(
            self.uow_factory,
            registry=self.registry,
            rate_limiter=self.rate_limiter(),
            metrics=self.metrics,
        )

    def publisher(self) -> OutboxPublisher:
        return OutboxPublisher(
            self.uow_factory,
            self.task_dispatcher(),
            metrics=self.metrics,
        )

    async def dispose(self) -> None:
        """Release the database engine. The Redis pool is owned by ARQ."""
        await self.engine.dispose()
