"""SQLAlchemy ORM models.

Table/column names mirror the domain entities. Enum values are stored as
strings; JSON columns use the portable ``JSON`` type so the schema works on
both PostgreSQL and SQLite.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from notifly.infrastructure.db.base import Base


class ApplicationRow(Base):
    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_prefix: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChannelRow(Base):
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("application_id", "channel_type", name="uq_channels_app_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    channel_type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TemplateRow(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("application_id", "event", name="uq_templates_app_event"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    event: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    variables: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    channels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NotificationRow(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_status_scheduled", "status", "scheduled_at"),
        Index("ix_notifications_app_created", "application_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE")
    )
    template_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    event: Mapped[str] = mapped_column(String(200))
    variables: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeliveryRow(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        Index("ix_deliveries_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_deliveries_notification_id", "notification_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    notification_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("notifications.id", ondelete="CASCADE")
    )
    channel_type: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(120))
    recipient: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text)
    html_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    retry_backoff_seconds: Mapped[float] = mapped_column(Float, default=5.0)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeliveryAttemptRow(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    delivery_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("deliveries.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_app_created", "application_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(120))
    resource_type: Mapped[str] = mapped_column(String(120))
    resource_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (Index("ix_outbox_events_status_created", "status", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("application_id", "key", name="uq_idempotency_app_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("applications.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    notification_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
