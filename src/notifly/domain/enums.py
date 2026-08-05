"""Domain enumerations."""

from __future__ import annotations

from enum import StrEnum


class ChannelType(StrEnum):
    EMAIL = "email"
    SLACK = "slack"
    DISCORD = "discord"
    TEAMS = "teams"
    WEBHOOK = "webhook"


class VariableType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class AttemptStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class OutboxEventType(StrEnum):
    NOTIFICATION_CREATED = "notification.created"
    NOTIFICATION_RETRIED = "notification.retried"
    DELIVERY_RETRY = "delivery.retry"


class ProviderErrorKind(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class AuditAction(StrEnum):
    APPLICATION_CREATED = "application.created"
    API_KEY_ISSUED = "api_key.issued"
    API_KEY_REVOKED = "api_key.revoked"
    CHANNEL_CREATED = "channel.created"
    CHANNEL_UPDATED = "channel.updated"
    TEMPLATE_CREATED = "template.created"
    TEMPLATE_UPDATED = "template.updated"
    TEMPLATE_DELETED = "template.deleted"
    NOTIFICATION_CREATED = "notification.created"
    NOTIFICATION_CANCELLED = "notification.cancelled"
    NOTIFICATION_RETRIED = "notification.retried"
    DELIVERY_SENT = "delivery.sent"
    DELIVERY_FAILED = "delivery.failed"
