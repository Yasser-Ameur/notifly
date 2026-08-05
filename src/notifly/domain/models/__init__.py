"""Domain entities and value objects."""

from notifly.domain.models.application import ApiKey, Application
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.models.base import DomainModel
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.idempotency import IdempotencyRecord
from notifly.domain.models.notification import Delivery, DeliveryAttempt, Notification
from notifly.domain.models.outbox import OutboxEvent
from notifly.domain.models.template import (
    Template,
    TemplateChannelContent,
    VariableDef,
)

__all__ = [
    "ApiKey",
    "Application",
    "AuditLogEntry",
    "ChannelConfig",
    "Delivery",
    "DeliveryAttempt",
    "DomainModel",
    "IdempotencyRecord",
    "Notification",
    "OutboxEvent",
    "Template",
    "TemplateChannelContent",
    "VariableDef",
]
