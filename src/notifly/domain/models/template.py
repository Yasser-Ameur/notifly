"""Template and variable entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from notifly.domain.enums import ChannelType, VariableType
from notifly.domain.errors import InvalidDataError
from notifly.domain.models.base import DomainModel

_VARIABLE_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class VariableDef(DomainModel):
    name: str = Field(pattern=_VARIABLE_NAME_PATTERN, min_length=1, max_length=120)
    type: VariableType = VariableType.STRING
    required: bool = True
    default: Any = None


class TemplateChannelContent(DomainModel):
    subject: str | None = Field(default=None, max_length=1000)
    body: str


class Template(DomainModel):
    id: UUID
    application_id: UUID
    name: str = Field(min_length=1, max_length=120)
    event: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    variables: list[VariableDef] = Field(default_factory=list)
    channels: dict[ChannelType, TemplateChannelContent]
    created_at: datetime
    updated_at: datetime

    @field_validator("channels")
    @classmethod
    def _channels_not_empty(
        cls, value: dict[ChannelType, TemplateChannelContent]
    ) -> dict[ChannelType, TemplateChannelContent]:
        if not value:
            raise InvalidDataError("A template must define content for at least one channel.")
        return value

    def variable_names(self) -> set[str]:
        return {variable.name for variable in self.variables}
