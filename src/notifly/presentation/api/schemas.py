"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from notifly.domain.enums import (
    ChannelType,
    DeliveryStatus,
    NotificationStatus,
    VariableType,
)

_VARIABLE_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class ApplicationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime


class ApplicationCreatedResponse(BaseModel):
    id: UUID
    name: str
    api_key: str


class ApiKeyCreate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None


class ApiKeyCreatedResponse(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    created_at: datetime
    api_key: str


class NotFoundResponse(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    correlation_id: str


class VariableDefInput(BaseModel):
    name: str = Field(pattern=_VARIABLE_NAME_PATTERN, min_length=1, max_length=120)
    type: VariableType = VariableType.STRING
    required: bool = True
    default: Any = None


class TemplateChannelContentInput(BaseModel):
    subject: str | None = Field(default=None, max_length=1000)
    body: str = Field(min_length=1)


class TemplateChannelContentResponse(BaseModel):
    subject: str | None
    body: str


class TemplateUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    event: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    variables: list[VariableDefInput] = Field(default_factory=list)
    channels: dict[ChannelType, TemplateChannelContentInput]


class TemplateResponse(BaseModel):
    id: UUID
    name: str
    event: str
    description: str | None
    variables: list[VariableDefInput]
    channels: dict[ChannelType, TemplateChannelContentResponse]
    created_at: datetime
    updated_at: datetime


class TemplatePreviewRequest(BaseModel):
    variables: dict[str, Any] = Field(default_factory=dict)


class TemplatePreviewResponse(BaseModel):
    channels: dict[ChannelType, TemplateChannelContentResponse]


class NotificationCreateRequest(BaseModel):
    template_id: UUID | None = None
    event: str = Field(min_length=1, max_length=200)
    variables: dict[str, Any] = Field(default_factory=dict)
    recipients: dict[ChannelType, str]
    scheduled_at: datetime | None = None


class NotificationResponse(BaseModel):
    id: UUID
    template_id: UUID | None
    event: str
    variables: dict[str, Any]
    status: NotificationStatus
    scheduled_at: datetime | None
    correlation_id: str
    created_at: datetime
    updated_at: datetime


class DeliveryResponse(BaseModel):
    id: UUID
    channel_type: ChannelType
    provider: str
    recipient: str
    subject: str | None
    body: str
    status: DeliveryStatus
    attempts: int
    next_attempt_at: datetime | None
    last_error: str | None
    provider_message_id: str | None
    created_at: datetime
    updated_at: datetime


class NotificationDetailResponse(BaseModel):
    notification: NotificationResponse
    deliveries: list[DeliveryResponse]
