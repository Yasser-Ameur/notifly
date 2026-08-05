"""Channel configuration entity."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from notifly.domain.enums import ChannelType
from notifly.domain.models.base import DomainModel


class ChannelConfig(DomainModel):
    id: UUID
    application_id: UUID
    channel_type: ChannelType
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1)
    retry_backoff_seconds: float = Field(default=5.0, ge=0.0)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    created_at: datetime
    updated_at: datetime
