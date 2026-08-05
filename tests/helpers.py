"""Shared helpers for application-layer tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from notifly.application.services.applications import ApplicationService
from notifly.application.services.templates import TemplateService
from notifly.domain.enums import ChannelType, VariableType
from notifly.domain.models.channel import ChannelConfig
from notifly.domain.models.template import TemplateChannelContent, VariableDef
from notifly.domain.ports.clock import Clock
from notifly.infrastructure.db.uow import create_uow_factory


class FakeClock(Clock):
    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        return self._current

    def advance(self, seconds: float) -> None:
        self._current += timedelta(seconds=seconds)


async def make_application(db, name: str = "acme"):
    created = await ApplicationService(
        create_uow_factory(db), key_prefix_str="notifly_", key_hash_iterations=1000
    ).create_application(name)
    return created.application


async def make_template(db, application_id) -> None:
    await TemplateService(create_uow_factory(db)).create_template(
        application_id,
        actor="a",
        name="Welcome",
        event="user_welcome",
        description=None,
        variables=[VariableDef(name="name", type=VariableType.STRING)],
        channels={
            ChannelType.EMAIL: TemplateChannelContent(
                subject="Hi {{ name }}", body="Welcome {{ name }}"
            ),
            ChannelType.SLACK: TemplateChannelContent(subject=None, body="Welcome {{ name }}"),
        },
    )


async def add_channel(
    db,
    application_id,
    *,
    channel_type: ChannelType = ChannelType.EMAIL,
    enabled: bool = True,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 5.0,
    rate_limit_per_minute: int | None = None,
    config: dict | None = None,
) -> None:
    now = datetime.now(UTC)
    async with create_uow_factory(db)() as uow:
        await uow.channels.add(
            ChannelConfig(
                id=uuid4(),
                application_id=application_id,
                channel_type=channel_type,
                name=channel_type.value,
                enabled=enabled,
                config=config or {},
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                rate_limit_per_minute=rate_limit_per_minute,
                created_at=now,
                updated_at=now,
            )
        )


async def get_channel(db, application_id, channel_type: ChannelType) -> ChannelConfig | None:
    async with create_uow_factory(db)() as uow:
        return await uow.channels.get_by_app_and_type(application_id, channel_type)
