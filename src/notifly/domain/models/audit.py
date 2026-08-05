"""Audit log entry entity."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from notifly.domain.enums import AuditAction
from notifly.domain.models.base import DomainModel


class AuditLogEntry(DomainModel):
    id: UUID
    application_id: UUID
    actor: str
    action: AuditAction
    resource_type: str
    resource_id: UUID | None = None
    correlation_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
