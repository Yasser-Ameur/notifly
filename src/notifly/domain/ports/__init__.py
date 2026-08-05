"""Domain ports (abstract interfaces) implemented by the infrastructure layer."""

from notifly.domain.ports.clock import Clock, SystemClock
from notifly.domain.ports.rate_limit import InMemoryRateLimiter, RateLimiter
from notifly.domain.ports.repositories import (
    ApiKeyRepository,
    ApplicationRepository,
    AuditRepository,
    ChannelRepository,
    DeliveryAttemptRepository,
    DeliveryRepository,
    IdempotencyRepository,
    NotificationRepository,
    OutboxRepository,
    TemplateRepository,
    UnitOfWork,
    UnitOfWorkFactory,
)
from notifly.domain.ports.tasks import TaskDispatcher

__all__ = [
    "ApiKeyRepository",
    "ApplicationRepository",
    "AuditRepository",
    "ChannelRepository",
    "Clock",
    "DeliveryAttemptRepository",
    "DeliveryRepository",
    "IdempotencyRepository",
    "InMemoryRateLimiter",
    "NotificationRepository",
    "OutboxRepository",
    "RateLimiter",
    "SystemClock",
    "TaskDispatcher",
    "TemplateRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
]
