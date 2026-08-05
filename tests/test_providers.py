"""M5 tests: provider adapters, shared HTTP transport, and registry wiring."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, ClassVar

import aiosmtplib
import httpx
import pytest
from aiosmtplib.errors import (
    SMTPConnectError,
    SMTPRecipientsRefused,
    SMTPResponseException,
    SMTPServerDisconnected,
)

from notifly.domain.enums import ChannelType, ProviderErrorKind
from notifly.domain.errors import ProviderConfigurationError
from notifly.domain.providers import ProviderMessage, ProviderRegistry
from notifly.infrastructure.providers import (
    BUILTIN_PROVIDERS,
    build_provider,
    create_provider_registry,
    register_builtins,
)
from notifly.infrastructure.providers.discord import DiscordProvider
from notifly.infrastructure.providers.email import EmailProvider
from notifly.infrastructure.providers.http import (
    HttpDeliverySettings,
    HttpTransport,
    _extract_retry_settings,
    deliver,
    is_transient_status,
    with_correlation,
)
from notifly.infrastructure.providers.slack import SlackProvider
from notifly.infrastructure.providers.teams import TeamsProvider
from notifly.infrastructure.providers.webhook import WebhookProvider


async def _no_sleep(_: float) -> None:
    return None


def _message(
    recipient: str = "https://hooks.example/x",
    *,
    subject: str | None = "Subject",
    body: str = '{"hello": "world"}',
    correlation_id: str = "corr-123",
    settings: dict[str, Any] | None = None,
) -> ProviderMessage:
    return ProviderMessage(
        recipient=recipient,
        subject=subject,
        body=body,
        correlation_id=correlation_id,
        settings=settings or {},
    )


class _RequestRecorder:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _transport(recorder: _RequestRecorder, *, max_retries: int = 2) -> HttpTransport:
    return HttpTransport(
        HttpDeliverySettings(max_retries=max_retries, retry_backoff_seconds=0.01),
        transport=httpx.MockTransport(recorder),
    )


class TestHttpHelpers:
    def test_is_transient_status(self) -> None:
        assert is_transient_status(503)
        assert is_transient_status(429)
        assert not is_transient_status(400)
        assert not is_transient_status(201)

    def test_with_correlation(self) -> None:
        assert with_correlation({"A": "1"}, "corr") == {"A": "1", "X-Correlation-ID": "corr"}
        assert with_correlation(None, "corr") == {"X-Correlation-ID": "corr"}
        assert with_correlation({"A": "1"}, "") == {"A": "1"}

    def test_extract_retry_settings_defaults(self) -> None:
        settings = _extract_retry_settings({})
        assert settings.timeout_seconds == 10.0
        assert settings.max_retries == 3
        assert settings.retry_backoff_seconds == 1.0
        assert settings.retry_backoff_factor == 2.0
        assert settings.retry_max_backoff_seconds == 30.0

    def test_extract_retry_settings_custom(self) -> None:
        settings = _extract_retry_settings(
            {
                "timeout_seconds": "5",
                "max_retries": "7",
                "retry_backoff_seconds": "0.5",
                "retry_backoff_factor": "3",
                "retry_max_backoff_seconds": "60",
            }
        )
        assert settings.timeout_seconds == 5.0
        assert settings.max_retries == 7
        assert settings.retry_backoff_seconds == 0.5
        assert settings.retry_backoff_factor == 3.0
        assert settings.retry_max_backoff_seconds == 60.0

    def test_backoff_caps_at_max(self) -> None:
        transport = _transport(_RequestRecorder(lambda r: httpx.Response(200)))
        assert transport._backoff(1) == 0.01
        assert transport._backoff(10) == 0.01 * (2**9)

        capped = HttpTransport(
            HttpDeliverySettings(retry_backoff_seconds=1.0, retry_max_backoff_seconds=0.05),
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        )
        assert capped._backoff(10) == 0.05


class TestHttpTransport:
    @pytest.mark.parametrize("status", [503, 429])
    async def test_retries_transient_status_then_success(self, monkeypatch, status: int) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(status)
            return httpx.Response(200, json={"ok": True})

        recorder = _RequestRecorder(handler)
        transport = _transport(recorder, max_retries=2)
        response = await transport.post("https://example.com", json={"a": 1})
        assert response.status_code == 200
        assert calls["n"] == 2
        assert json.loads(recorder.requests[0].content) == {"a": 1}
        await transport.aclose()

    async def test_does_not_retry_permanent_status(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400)

        transport = _transport(_RequestRecorder(handler))
        response = await transport.post("https://example.com", json={})
        assert response.status_code == 400
        assert calls["n"] == 1
        await transport.aclose()

    async def test_retries_transport_error_then_success(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200)

        transport = _transport(_RequestRecorder(handler))
        response = await transport.post("https://example.com", json={})
        assert response.status_code == 200
        assert calls["n"] == 2
        await transport.aclose()

    async def test_raises_after_transport_error_retries_exhausted(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectError("boom")

        transport = _transport(_RequestRecorder(handler), max_retries=2)
        with pytest.raises(httpx.TransportError):
            await transport.post("https://example.com", json={})
        assert calls["n"] == 3
        await transport.aclose()

    async def test_returns_last_response_when_transient_status_exhausted(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        transport = _transport(_RequestRecorder(handler), max_retries=2)
        response = await transport.post("https://example.com", json={})
        assert response.status_code == 503
        assert calls["n"] == 3
        await transport.aclose()


class TestDeliver:
    async def test_success_json(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        transport = _transport(recorder)
        result = await deliver(
            transport, "https://example.com", json={"a": 1}, correlation_id="c", provider_name="X"
        )
        assert result.delivered
        assert recorder.requests[0].headers["X-Correlation-ID"] == "c"
        assert json.loads(recorder.requests[0].content) == {"a": 1}
        await transport.aclose()

    async def test_success_raw_content(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        transport = _transport(recorder)
        result = await deliver(
            transport,
            "https://example.com",
            content="hello",
            correlation_id="",
            provider_name="X",
        )
        assert result.delivered
        assert recorder.requests[0].content == b"hello"
        await transport.aclose()

    async def test_put_method(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        transport = _transport(recorder)
        result = await deliver(
            transport,
            "https://example.com",
            json={},
            correlation_id="c",
            provider_name="X",
            method="PUT",
        )
        assert result.delivered
        assert recorder.requests[0].method == "PUT"
        await transport.aclose()

    async def test_transient_status_after_retries(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        transport = _transport(lambda r: httpx.Response(503), max_retries=2)
        result = await deliver(
            transport, "https://example.com", json={}, correlation_id="c", provider_name="X"
        )
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT
        await transport.aclose()

    async def test_permanent_status(self) -> None:
        transport = _transport(lambda r: httpx.Response(401))
        result = await deliver(
            transport, "https://example.com", json={}, correlation_id="c", provider_name="X"
        )
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.PERMANENT
        await transport.aclose()

    async def test_transport_error(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        transport = _transport(_RequestRecorder(handler), max_retries=1)
        result = await deliver(
            transport, "https://example.com", json={}, correlation_id="c", provider_name="X"
        )
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT
        await transport.aclose()


class _FakeSMTP:
    instances: ClassVar[list[_FakeSMTP]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.sent: list[Any] = []
        self.raise_on_send: Exception | None = None
        _FakeSMTP.instances.append(self)

    async def __aenter__(self) -> _FakeSMTP:
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False

    async def send_message(self, message: Any, **kwargs: Any) -> Any:
        self.sent.append(message)
        if self.raise_on_send is not None:
            raise self.raise_on_send
        return {}, ""


def _email_provider(
    monkeypatch, *, raise_on_send: Exception | None = None, **settings: Any
) -> EmailProvider:
    fake = _FakeSMTP()
    fake.raise_on_send = raise_on_send
    monkeypatch.setattr(aiosmtplib, "SMTP", lambda *a, **k: fake)
    config = {"host": "smtp.example.com", "from_address": "no-reply@example.com", **settings}
    provider = EmailProvider.from_settings(config)
    assert isinstance(provider, EmailProvider)
    return provider


class TestEmailProvider:
    def test_from_settings_validation_error(self) -> None:
        with pytest.raises(ProviderConfigurationError):
            EmailProvider.from_settings({"port": 587})

    def test_use_tls_disables_starttls(self, monkeypatch) -> None:
        _FakeSMTP.instances.clear()
        monkeypatch.setattr(aiosmtplib, "SMTP", _FakeSMTP)
        provider = EmailProvider.from_settings(
            {
                "host": "smtp.example.com",
                "port": 465,
                "from_address": "a@b.c",
                "use_tls": True,
            }
        )
        assert isinstance(provider, EmailProvider)
        client = _FakeSMTP.instances[-1]
        assert client.kwargs["use_tls"] is True
        assert client.kwargs["start_tls"] is False

    async def test_send_success_plain(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch)
        result = await provider.send(_message("a@example.com", body="hi"))
        assert result.delivered
        assert result.provider_message_id
        envelope = provider._client.sent[0]
        assert envelope["To"] == "a@example.com"
        assert envelope["From"] == "no-reply@example.com"
        assert envelope["Subject"] == "Subject"
        assert envelope["X-Correlation-ID"] == "corr-123"
        assert envelope.get_content().strip() == "hi"
        assert envelope.get_content_subtype() == "plain"

    async def test_send_success_with_html(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch)
        message = _message("a@example.com", body="plain")
        message.html_body = "<p>html</p>"
        result = await provider.send(message)
        assert result.delivered
        envelope = provider._client.sent[0]
        assert "multipart/alternative" in envelope.get_content_type()
        assert envelope.is_multipart()
        parts = list(envelope.iter_parts())
        assert parts[0].get_content_subtype() == "plain"
        assert parts[1].get_content_subtype() == "html"
        assert parts[1].get_content().strip() == "<p>html</p>"

    async def test_no_correlation_header_when_empty(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch)
        message = _message("a@example.com", body="hi")
        message.correlation_id = ""
        await provider.send(message)
        envelope = provider._client.sent[0]
        assert "X-Correlation-ID" not in envelope

    async def test_recipients_refused_is_permanent(self, monkeypatch) -> None:
        provider = _email_provider(
            monkeypatch, raise_on_send=SMTPRecipientsRefused({"bad@example.com": (550, "no")})
        )
        result = await provider.send(_message("a@example.com", body="hi"))
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.PERMANENT

    async def test_5xx_response_is_permanent(self, monkeypatch) -> None:
        provider = _email_provider(
            monkeypatch, raise_on_send=SMTPResponseException(550, "rejected")
        )
        result = await provider.send(_message("a@example.com", body="hi"))
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.PERMANENT

    async def test_4xx_response_is_transient(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch, raise_on_send=SMTPResponseException(450, "busy"))
        result = await provider.send(_message("a@example.com", body="hi"))
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT

    async def test_connect_error_is_transient(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch, raise_on_send=SMTPConnectError("down"))
        result = await provider.send(_message("a@example.com", body="hi"))
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT

    async def test_disconnected_is_transient(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch, raise_on_send=SMTPServerDisconnected("gone"))
        result = await provider.send(_message("a@example.com", body="hi"))
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT

    async def test_generic_error_is_transient(self, monkeypatch) -> None:
        provider = _email_provider(monkeypatch, raise_on_send=RuntimeError("unexpected"))
        result = await provider.send(_message("a@example.com", body="hi"))
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT


class TestHttpWebhookProviders:
    async def test_slack_success_and_payload(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200, text="ok"))
        provider = SlackProvider.from_settings(
            {"username": "bot", "icon_emoji": ":wave:", "channel": "#alerts"}
        )
        assert isinstance(provider, SlackProvider)
        assert provider.channel_type is ChannelType.SLACK
        provider._transport = _transport(recorder)
        message = _message(settings={"blocks": [{"type": "section"}]})
        message.body = "hello"
        result = await provider.send(message)
        assert result.delivered
        payload = json.loads(recorder.requests[0].content)
        assert payload["text"] == "hello"
        assert payload["username"] == "bot"
        assert payload["icon_emoji"] == ":wave:"
        assert payload["channel"] == "#alerts"
        assert payload["blocks"] == [{"type": "section"}]
        assert recorder.requests[0].headers["X-Correlation-ID"] == "corr-123"
        await provider._transport.aclose()

    async def test_slack_attachments_via_settings(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = SlackProvider.from_settings({})
        assert isinstance(provider, SlackProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "hi"
        message.settings = {"attachments": [{"text": "attach"}]}
        result = await provider.send(message)
        assert result.delivered
        payload = json.loads(recorder.requests[0].content)
        assert payload["attachments"] == [{"text": "attach"}]
        await provider._transport.aclose()

    async def test_slack_failure_classified(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        recorder = _RequestRecorder(lambda r: httpx.Response(500))
        provider = SlackProvider.from_settings({})
        assert isinstance(provider, SlackProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "hi"
        result = await provider.send(message)
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.TRANSIENT
        await provider._transport.aclose()

    def test_slack_invalid_settings(self) -> None:
        with pytest.raises(ProviderConfigurationError):
            SlackProvider.from_settings({"username": 123})

    async def test_discord_success_and_payload(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(204))
        provider = DiscordProvider.from_settings({"username": "notifier", "tts": True})
        assert isinstance(provider, DiscordProvider)
        provider._transport = _transport(recorder)
        message = _message(settings={"embeds": [{"title": "T"}]})
        message.body = "hello"
        result = await provider.send(message)
        assert result.delivered
        payload = json.loads(recorder.requests[0].content)
        assert payload["content"] == "hello"
        assert payload["username"] == "notifier"
        assert payload["tts"] is True
        assert payload["embeds"] == [{"title": "T"}]
        await provider._transport.aclose()

    async def test_discord_avatar_url(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(204))
        provider = DiscordProvider.from_settings({"avatar_url": "https://img/x.png"})
        assert isinstance(provider, DiscordProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "hi"
        result = await provider.send(message)
        assert result.delivered
        payload = json.loads(recorder.requests[0].content)
        assert payload["avatar_url"] == "https://img/x.png"
        assert "tts" not in payload
        await provider._transport.aclose()

    def test_discord_invalid_settings(self) -> None:
        with pytest.raises(ProviderConfigurationError):
            DiscordProvider.from_settings({"username": []})

    async def test_teams_default_card(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(202))
        provider = TeamsProvider.from_settings({"title": "My title", "theme_color": "0072C6"})
        assert isinstance(provider, TeamsProvider)
        provider._transport = _transport(recorder)
        result = await provider.send(_message())
        assert result.delivered
        payload = json.loads(recorder.requests[0].content)
        assert payload["type"] == "message"
        content = payload["attachments"][0]["content"]
        assert content["type"] == "AdaptiveCard"
        assert content["title"] == "My title"
        assert content["themeColor"] == "0072C6"
        texts = [block["text"] for block in content["body"]]
        assert "Subject" in texts and json.dumps({"hello": "world"}) in texts
        await provider._transport.aclose()

    async def test_teams_no_subject_and_custom_card(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(202))
        provider = TeamsProvider.from_settings({})
        assert isinstance(provider, TeamsProvider)
        provider._transport = _transport(recorder)
        message = _message(settings={"adaptive_card": {"type": "AdaptiveCard", "body": []}})
        message.subject = None
        message.body = "just body"
        result = await provider.send(message)
        assert result.delivered
        content = json.loads(recorder.requests[0].content)["attachments"][0]["content"]
        assert content["body"] == []
        await provider._transport.aclose()

    def test_teams_invalid_settings(self) -> None:
        with pytest.raises(ProviderConfigurationError):
            TeamsProvider.from_settings({"title": 1})

    async def test_webhook_json_success(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = WebhookProvider.from_settings({"token": "secret"})
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = '{"a": 1}'
        result = await provider.send(message)
        assert result.delivered
        request = recorder.requests[0]
        assert json.loads(request.content) == {"a": 1}
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.headers["X-Correlation-ID"] == "corr-123"
        await provider._transport.aclose()

    async def test_webhook_basic_auth(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = WebhookProvider.from_settings({"username": "u", "password": "p"})
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "{}"
        result = await provider.send(message)
        assert result.delivered
        assert recorder.requests[0].headers["Authorization"] == "Basic dTpw"
        await provider._transport.aclose()

    async def test_webhook_raw_payload(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = WebhookProvider.from_settings(
            {"as_json": False, "content_type": "text/plain", "headers": {"X-Extra": "1"}}
        )
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "raw text"
        message.settings = {"headers": {"X-Msg": "2"}}
        result = await provider.send(message)
        assert result.delivered
        request = recorder.requests[0]
        assert request.content == b"raw text"
        assert request.headers["Content-Type"] == "text/plain"
        assert request.headers["X-Extra"] == "1"
        assert request.headers["X-Msg"] == "2"
        await provider._transport.aclose()

    async def test_webhook_raw_payload_default_content_type(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = WebhookProvider.from_settings({"as_json": False})
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "raw text"
        result = await provider.send(message)
        assert result.delivered
        assert "Content-Type" not in recorder.requests[0].headers
        await provider._transport.aclose()

    async def test_webhook_put_method(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = WebhookProvider.from_settings({"method": "PUT"})
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "{}"
        result = await provider.send(message)
        assert result.delivered
        assert recorder.requests[0].method == "PUT"
        await provider._transport.aclose()

    async def test_webhook_invalid_json_body(self) -> None:
        recorder = _RequestRecorder(lambda r: httpx.Response(200))
        provider = WebhookProvider.from_settings({})
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "not json"
        result = await provider.send(message)
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.PERMANENT
        await provider._transport.aclose()

    async def test_webhook_failure_classified(self, monkeypatch) -> None:
        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        recorder = _RequestRecorder(lambda r: httpx.Response(403))
        provider = WebhookProvider.from_settings({})
        assert isinstance(provider, WebhookProvider)
        provider._transport = _transport(recorder)
        message = _message()
        message.body = "{}"
        result = await provider.send(message)
        assert not result.delivered
        assert result.error_kind is ProviderErrorKind.PERMANENT
        await provider._transport.aclose()

    def test_webhook_invalid_settings(self) -> None:
        with pytest.raises(ProviderConfigurationError):
            WebhookProvider.from_settings({"method": "DELETE"})


class TestProviderWiring:
    def test_register_builtins(self) -> None:
        registry = ProviderRegistry()
        register_builtins(registry)
        assert registry.channels() == [
            ChannelType.EMAIL,
            ChannelType.SLACK,
            ChannelType.DISCORD,
            ChannelType.TEAMS,
            ChannelType.WEBHOOK,
        ]

    def test_builtin_providers_registered(self) -> None:
        classes = {cls.channel_type: cls for cls in BUILTIN_PROVIDERS}
        assert classes[ChannelType.EMAIL] is EmailProvider
        assert classes[ChannelType.SLACK] is SlackProvider
        assert classes[ChannelType.DISCORD] is DiscordProvider
        assert classes[ChannelType.TEAMS] is TeamsProvider
        assert classes[ChannelType.WEBHOOK] is WebhookProvider

    def test_create_registry_includes_builtins(self) -> None:
        registry = create_provider_registry()
        assert registry.get(ChannelType.EMAIL) is EmailProvider
        assert registry.get(ChannelType.WEBHOOK) is WebhookProvider

    def test_build_provider_instantiates(self) -> None:
        registry = create_provider_registry()
        provider = build_provider(
            registry,
            ChannelType.WEBHOOK,
            {"token": "x", "max_retries": 1},
        )
        assert isinstance(provider, WebhookProvider)
