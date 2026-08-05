"""SMTP email provider built on aiosmtplib."""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

import aiosmtplib
from aiosmtplib.errors import (
    SMTPConnectError,
    SMTPException,
    SMTPRecipientsRefused,
    SMTPResponseException,
    SMTPServerDisconnected,
)
from pydantic import BaseModel, Field

from notifly.domain.enums import ChannelType, ProviderErrorKind
from notifly.domain.errors import ProviderConfigurationError
from notifly.domain.providers import (
    Provider,
    ProviderCapabilities,
    ProviderMessage,
    ProviderResult,
)

_TRANSIENT_SMTP_ERRORS: tuple[type[Exception], ...] = (
    SMTPConnectError,
    SMTPServerDisconnected,
    OSError,
)


class _EmailSettings(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(default=587, ge=1, le=65535)
    from_address: str = Field(min_length=1)
    username: str | None = None
    password: str | None = None
    use_tls: bool = False
    use_starttls: bool = True
    timeout: float = Field(default=10.0, gt=0)


class EmailProvider(Provider):
    """Deliver rendered notifications as email over SMTP."""

    channel_type = ChannelType.EMAIL
    capabilities = ProviderCapabilities(
        supports_html=True,
        supports_attachments=False,
        supports_templates=True,
        supports_scheduling=False,
        supports_delivery_callbacks=False,
    )

    def __init__(self, settings: _EmailSettings) -> None:
        self._settings = settings
        self._client = aiosmtplib.SMTP(
            hostname=settings.host,
            port=settings.port,
            username=settings.username,
            password=settings.password,
            timeout=settings.timeout,
            use_tls=settings.use_tls,
            start_tls=settings.use_starttls and not settings.use_tls,
        )

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> Provider:
        try:
            validated = _EmailSettings.model_validate(settings)
        except Exception as exc:
            raise ProviderConfigurationError(f"Invalid email provider settings: {exc}") from exc
        return cls(validated)

    async def send(self, message: ProviderMessage) -> ProviderResult:
        envelope = self._build_envelope(message)
        try:
            async with self._client:
                await self._client.send_message(envelope)
        except SMTPRecipientsRefused as exc:
            return ProviderResult(
                delivered=False,
                error=f"Recipients refused: {exc}",
                error_kind=ProviderErrorKind.PERMANENT,
            )
        except SMTPResponseException as exc:
            kind = ProviderErrorKind.PERMANENT if exc.code >= 500 else ProviderErrorKind.TRANSIENT
            return ProviderResult(delivered=False, error=str(exc), error_kind=kind)
        except _TRANSIENT_SMTP_ERRORS as exc:
            return ProviderResult(
                delivered=False, error=str(exc), error_kind=ProviderErrorKind.TRANSIENT
            )
        except SMTPException as exc:
            return ProviderResult(
                delivered=False, error=str(exc), error_kind=ProviderErrorKind.TRANSIENT
            )
        except Exception as exc:
            return ProviderResult(
                delivered=False, error=str(exc), error_kind=ProviderErrorKind.TRANSIENT
            )
        return ProviderResult(delivered=True, provider_message_id=envelope["Message-ID"])

    def _build_envelope(self, message: ProviderMessage) -> EmailMessage:
        envelope = EmailMessage()
        envelope["From"] = self._settings.from_address
        envelope["To"] = message.recipient
        envelope["Subject"] = message.subject or ""
        envelope["Message-ID"] = make_msgid(idstring="notifly")
        if message.correlation_id:
            envelope["X-Correlation-ID"] = message.correlation_id
        if message.html_body:
            envelope.set_content(message.body)
            envelope.add_alternative(message.html_body, subtype="html")
        else:
            envelope.set_content(message.body)
        return envelope
