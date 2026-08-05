"""Outbox event entity."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from notifly.domain.enums import OutboxEventType, OutboxStatus
from notifly.domain.models.base import DomainModel


class OutboxEvent(DomainModel):
    id: UUID
    event_type: OutboxEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    correlation_id: str = ""
    created_at: datetime
    published_at: datetime | None = None

    def mark_published(self, now: datetime) -> None:
        self.status = OutboxStatus.PUBLISHED
        self.published_at = now

    def mark_failed(self, error: str) -> None:
        self.status = OutboxStatus.FAILED
        self.attempts += 1
        self.last_error = error
