"""Application and API key entities."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from notifly.domain.models.base import DomainModel


class Application(DomainModel):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    created_at: datetime
    updated_at: datetime


class ApiKey(DomainModel):
    id: UUID
    application_id: UUID
    name: str = Field(min_length=1, max_length=120)
    key_hash: str
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None
