# Contributing

NotiFly is a production-grade open-source project. The bar for merging is high.

## Development setup

```bash
uv sync --extra dev
uv run alembic upgrade head
```

## Checks — must pass before any commit

```bash
uv run ruff check .
uv run ruff format .
uv run mypy --strict src
uv run pytest            # enforces ≥95% coverage
```

## Project layout

- `src/notifly/domain` — entities, ports, business rules (no infrastructure).
- `src/notifly/application` — use-case services (depend on ports only).
- `src/notifly/infrastructure` — adapters (DB, providers, Redis, ARQ, metrics).
- `src/notifly/presentation` — FastAPI routers, schemas, worker jobs.
- `sdk/` — the first-party Python client (separate package).
- `tests/` — server tests. `sdk/tests/` — SDK tests.

## Architecture rules

1. Domain never imports infrastructure.
2. Business logic never lives in a router.
3. New delivery channels = a `Provider` adapter + registration; nothing else.
4. Every mutating use case writes to the outbox in the same transaction.
5. Every log line must carry a correlation ID.

## Testing

- Unit tests use in-memory fakes for all ports.
- Integration tests run against SQLite locally and the identical suite against
  PostgreSQL + Redis in CI.
- Provider adapters are tested with `httpx.MockTransport` and mocked SMTP.

## Committing

- Small, logical commits; one coherent feature each.
- Professional commit messages.
- Never commit broken code.
