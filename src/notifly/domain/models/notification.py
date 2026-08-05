"""Notification, delivery, and delivery-attempt entities with state transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from notifly.domain.enums import (
    AttemptStatus,
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
)
from notifly.domain.errors import InvalidStateError
from notifly.domain.models.base import DomainModel


class Delivery(DomainModel):
    id: UUID
    notification_id: UUID
    channel_type: ChannelType
    provider: str
    recipient: str
    subject: str | None = None
    body: str
    html_body: str | None = None
    provider_settings: dict[str, Any] = Field(default_factory=dict)
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    max_attempts: int = Field(default=3, ge=1)
    retry_backoff_seconds: float = Field(default=5.0, ge=0.0)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    provider_message_id: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    def mark_processing(self) -> None:
        if self.status is DeliveryStatus.SENT or self.status is DeliveryStatus.FAILED:
            raise InvalidStateError(f"Delivery {self.id} is already terminal.")
        self.status = DeliveryStatus.PROCESSING

    def mark_sent(self, provider_message_id: str | None) -> None:
        if self.status is DeliveryStatus.FAILED and self.attempts >= self.max_attempts:
            raise InvalidStateError(f"Delivery {self.id} is permanently failed.")
        self.status = DeliveryStatus.SENT
        self.provider_message_id = provider_message_id
        self.last_error = None
        self.next_attempt_at = None
        self.completed_at = datetime.now(UTC)

    def mark_failed(self, error: str, *, transient: bool, now: datetime) -> None:
        self.last_error = error
        self.attempts += 1
        if not transient or self.attempts >= self.max_attempts:
            self.status = DeliveryStatus.FAILED
            self.next_attempt_at = None
            self.completed_at = now
            return
        self.status = DeliveryStatus.PENDING
        self.next_attempt_at = self._next_attempt(now)

    def reset_for_retry(self, now: datetime) -> None:
        self.status = DeliveryStatus.PENDING
        self.attempts = 0
        self.next_attempt_at = now
        self.last_error = None
        self.provider_message_id = None
        self.completed_at = None

    def _next_attempt(self, now: datetime) -> datetime:
        delay = self.retry_backoff_seconds * (2 ** max(self.attempts - 1, 0))
        return now + timedelta(seconds=delay)

    @property
    def is_terminal(self) -> bool:
        return self.status in (DeliveryStatus.SENT, DeliveryStatus.FAILED)


class DeliveryAttempt(DomainModel):
    id: UUID
    delivery_id: UUID
    attempt_number: int
    status: AttemptStatus
    error: str | None = None
    duration_ms: int | None = None
    created_at: datetime


class Notification(DomainModel):
    id: UUID
    application_id: UUID
    template_id: UUID | None = None
    event: str
    variables: dict[str, Any] = Field(default_factory=dict)
    status: NotificationStatus = NotificationStatus.PENDING
    scheduled_at: datetime | None = None
    correlation_id: str
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None

    def mark_processing(self) -> None:
        if self.status is NotificationStatus.CANCELLED:
            raise InvalidStateError(f"Notification {self.id} is cancelled.")
        self.status = NotificationStatus.PROCESSING

    def mark_completed(self, deliveries: list[Delivery]) -> None:
        terminal = [d for d in deliveries if d.is_terminal]
        if not terminal:
            self.status = NotificationStatus.PROCESSING
            return
        delivered = [d for d in terminal if d.status is DeliveryStatus.SENT]
        if len(delivered) == len(terminal):
            self.status = NotificationStatus.SENT
        elif delivered:
            self.status = NotificationStatus.PARTIAL
        else:
            self.status = NotificationStatus.FAILED
        self.processed_at = datetime.now(UTC)

    def cancel(self) -> None:
        if self.status not in (NotificationStatus.PENDING, NotificationStatus.PROCESSING):
            raise InvalidStateError(
                f"Notification {self.id} cannot be cancelled from state {self.status}."
            )
        self.status = NotificationStatus.CANCELLED

    @model_validator(mode="after")
    def _validate_scheduling(self) -> Notification:
        if self.scheduled_at is not None and self.scheduled_at.tzinfo is None:
            raise InvalidStateError("scheduled_at must be timezone-aware.")
        return self
