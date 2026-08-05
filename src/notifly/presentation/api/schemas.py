"""Pydantic request/response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
