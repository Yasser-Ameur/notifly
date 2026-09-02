# NotiFly

[![CI](https://github.com/Yasser-Ameur/notifly/actions/workflows/ci.yml/badge.svg)](https://github.com/Yasser-Ameur/notifly/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**NotiFly** is a production-grade, channel-agnostic **notification orchestration platform**.

It is **not** an email sender. It is a platform that orchestrates multiple delivery channels
(Email, Slack, Discord, Microsoft Teams, and generic Webhooks) through a single, clean REST API —
handling templates, variables, retries, scheduling, rate limiting, delivery tracking, audit logs,
idempotency, and a transactional outbox for guaranteed delivery.

NotiFly is completely independent from any consumer. It ships with a first-party, fully typed
Python SDK so products like FlowOS can integrate it in minutes.

## Why NotiFly?

- **One API for every channel.** Send once, deliver everywhere.
- **Guaranteed delivery.** A transactional outbox ensures no notification is ever lost when
  Redis or the worker is down. The database is the source of truth.
- **Clean architecture.** Strict layering (`domain → application → infrastructure → presentation`).
  No business logic in routes. No domain dependency on infrastructure.
- **Extensible by design.** Providers are small adapters behind a capabilities-based interface.
  Adding a new provider is one class and a registration line.
- **Operationally complete.** Idempotency keys, correlation IDs, retries with exponential backoff,
  scheduling, Prometheus metrics, health/readiness checks, structured JSON logs, and an
  operations API for debugging.

## Architecture

```
                        ┌─────────────────────────────────────────────┐
   REST API (FastAPI)   │  presentation  (routers, schemas, deps)      │
                        └──────────────────────┬──────────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────────┐
                        │  application  (use cases, orchestration)    │
                        └──────────────────────┬──────────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────────┐
                        │  domain  (entities, ports, business rules)   │
                        └──────────────────────┬──────────────────────┘
                                               │
                        ┌──────────────────────▼──────────────────────┐
                        │  infrastructure (SQLAlchemy, Redis, ARQ,    │
                        │                   providers, Prometheus)    │
                        └─────────────────────────────────────────────┘
```

See [docs/architecture.md](docs/architecture.md) for the full architecture, including the
transactional outbox flow, the notification state machines, and the provider model.

## Quick start

### Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000
- OpenAPI docs: http://localhost:8000/docs

### Local development

```bash
uv sync --extra dev
uv run alembic upgrade head
uv run uvicorn notifly.main:app --reload
```

### Send your first notification

```bash
# 1. Create an application
curl -s -X POST http://localhost:8000/v1/apps \
  -H "Content-Type: application/json" \
  -d '{"name": "acme"}'

# -> {"id": "...", "api_key": "notifly_..."}   (store this key!)

# 2. Create a template
curl -s -X POST http://localhost:8000/v1/templates \
  -H "X-Notifly-Key: notifly_..." \
  -H "Content-Type: application/json" \
  -d '{
        "name": "welcome",
        "event": "user_welcome",
        "variables": [{"name": "name", "type": "string", "required": true}],
        "channels": {
          "email": {"subject": "Welcome, {{ name }}!", "body": "Hi {{ name }}, thanks for joining."}
        }
      }'

# 3. Send
curl -s -X POST http://localhost:8000/v1/notifications \
  -H "X-Notifly-Key: notifly_..." \
  -H "Idempotency-Key: acme-welcome-001" \
  -H "Content-Type: application/json" \
  -d '{
        "event": "user_welcome",
        "variables": {"name": "Alice"},
        "recipients": {"email": "alice@example.com"}
      }'
```

### Using the Python SDK

```python
from notifly import Client

client = Client(api_key="notifly_...")

client.email(
    to="user@example.com",
    template="welcome",
    variables={"name": "Alice"},
    idempotency_key="welcome-alice-1",
)
```

See [docs/examples.md](docs/examples.md) and the [SDK](sdk/) for more.

## Features

- **Notification management** — create, schedule, list, inspect, cancel, retry.
- **Templates & variables** — per-channel content with sandboxed Jinja2 rendering and declared,
  validated variables.
- **Provider abstraction** — capabilities-based interface with automatic discovery.
  Built-in: Email (SMTP), Slack, Discord, Microsoft Teams, Webhook.
- **Reliability** — transactional outbox, exponential-backoff retries, dead-letter queue with
  manual retry, idempotency keys.
- **Scheduling** — DB-backed, restart-safe future sends.
- **Observability** — correlation IDs end-to-end, structured logs, Prometheus metrics,
  liveness/readiness probes.
- **Security** — scoped API keys (hashed at rest), no secrets in API responses.
- **Operations** — query notifications/deliveries/dead letters, retry failures.

## Documentation

- [Architecture](docs/architecture.md)
- [API reference](docs/api.md)
- [Providers](docs/providers.md)
- [Deployment](docs/deployment.md)
- [Examples](docs/examples.md)
- [Contributing](CONTRIBUTING.md)

## License

MIT — see [LICENSE](LICENSE).
