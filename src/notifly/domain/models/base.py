"""Shared base for domain entities."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DomainModel(BaseModel):
    """Base class for all domain entities.

    ``from_attributes`` enables mapping ORM rows onto domain models.
    """

    model_config = ConfigDict(from_attributes=True)
