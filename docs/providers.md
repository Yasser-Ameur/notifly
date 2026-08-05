# Providers

Providers are the only component that knows how to reach a delivery channel.
Everything else — templates, scheduling, retries, audit — is channel-agnostic.

## The Provider interface

```python
class Provider(ABC):
    channel_type: ClassVar[ChannelType]

    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def send(self, message: ProviderMessage) -> ProviderResult: ...
```

- `ProviderMessage` — recipient, rendered subject/body/payload, metadata
  (correlation ID), and any channel-specific config the provider needs.
- `ProviderResult` — `delivered: bool`, optional provider message ID,
  optional transient/permanent error classification.
- `ProviderCapabilities` — declares what the provider supports:

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    supports_html: bool = False
    supports_attachments: bool = False
    supports_templates: bool = True
    supports_scheduling: bool = False
    supports_delivery_callbacks: bool = False
    max_payload_bytes: int | None = None
```

Capabilities let the Notification Engine make decisions (e.g. only render an
HTML body for a provider that supports it) **without knowing which provider it
is**. This is what keeps provider-specific logic out of the core.

## Registration and discovery

Built-in providers are registered when the package loads. Third-party providers
register via the `notifly.providers` entry-point group:

```toml
[project.entry-points."notifly.providers"]
my_provider = "mypkg.providers:MyProvider"
```

The `ProviderRegistry` merges entry points with the built-ins and exposes
lookup by `channel_type`. A provider class must expose:

- `channel_type` — the channel it delivers to,
- a `from_config(config: ProviderConfig) -> Provider` factory,
- `capabilities`.

## Built-in providers

| Channel | Adapter | Notes |
|---|---|---|
| `email` | SMTP (`aiosmtplib`) | Supports plain-text and HTML bodies; message IDs returned |
| `slack` | Incoming Webhook (`httpx`) | Supports attachments (blocks) |
| `discord` | Webhook (`httpx`) | Supports embeds |
| `teams` | Incoming Webhook (`httpx`) | Adaptive Card payload |
| `webhook` | Generic HTTP (`httpx`) | Arbitrary URL + payload, headers, auth |

All HTTP providers share a common client factory providing timeouts, bounded
retries with exponential backoff, and correlation-ID propagation.

## Adding a provider

1. Implement `Provider` in `src/notifly/infrastructure/providers/`.
2. Declare `channel_type` and `capabilities`.
3. Add a `from_config` factory that validates the channel config.
4. Register it (built-in list or entry point).
5. Add provider tests using `httpx.MockTransport` or a mocked SMTP client.

That is the entire surface area for a new channel.

## Rate limiting

The Dispatcher applies a token-bucket rate limit keyed by
`(application_id, channel_type)` before every send. The limit is configured on
the channel; the default adapter is Redis-backed with an in-memory fallback for
tests and single-node deployments.
