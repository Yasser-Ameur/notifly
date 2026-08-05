"""Audit entry writing, shared by all mutating use cases.

Audit entries are appended inside the same Unit of Work as the mutation they
describe, so the record and its audit trail commit atomically.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from notifly.domain.enums import AuditAction
from notifly.domain.models.audit import AuditLogEntry
from notifly.domain.ports.repositories import UnitOfWork


async def write_audit(
    uow: UnitOfWork,
    *,
    application_id: UUID,
    actor: str,
    action: AuditAction,
    resource_type: str,
    resource_id: UUID | None,
    correlation_id: str,
    now: datetime,
    payload: dict[str, Any] | None = None,
) -> None:
    """Append an audit entry to the current transaction."""
    await uow.audit.add(
        AuditLogEntry(
            id=uuid4(),
            application_id=application_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            correlation_id=correlation_id,
            payload=payload or {},
            created_at=now,
        )
    )
