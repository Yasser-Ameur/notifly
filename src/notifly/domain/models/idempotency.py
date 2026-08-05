"""Idempotency record entity."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from notifly.domain.models.base import DomainModel


class IdempotencyRecord(DomainModel):
    id: UUID
    application_id: UUID
    key: str = Field(min_length=1, max_length=200)
    request_hash: str
    notification_id: UUID
    created_at: datetime
