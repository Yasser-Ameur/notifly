# Deployment

NotiFly runs as two long-lived processes (API and worker) in front of
PostgreSQL and Redis.

## Architecture

```
                 ┌────────────┐
 internet ─────▶ │ API        │ ─▶ PostgreSQL
                 │ (uvicorn)  │
                 └────────────┘
                 ┌────────────┐
                 │ Worker     │ ─▶ PostgreSQL
                 │ (ARQ)      │ ─▶ Redis
                 └────────────┘
```

The worker runs three concerns: the **outbox relay** (cron), the
**scheduled/retry poller** (cron), and the **dispatch job** handler.

## Environment variables

All settings are read from environment variables prefixed with `NOTIFLY_`.
See `src/notifly/config.py` and `.env.example` for the full list.

| Variable | Default | Description |
|---|---|---|
| `NOTIFLY_DATABASE_URL` | `sqlite+aiosqlite:///./notifly.db` | PostgreSQL in production |
| `NOTIFLY_REDIS_URL` | `redis://localhost:6379/0` | Redis for rate limiting + ARQ |
| `NOTIFLY_ENVIRONMENT` | `development` | `development` / `test` / `production` |
| `NOTIFLY_LOG_LEVEL` | `INFO` | log level |
| `NOTIFLY_JSON_LOGS` | `false` | structured JSON logs |
| `NOTIFLY_METRICS_ENABLED` | `true` | expose `/metrics` |

## Docker

```bash
cp .env.example .env
docker compose up --build
```

`docker-compose.yml` provisions:

- `postgres` (16, with healthcheck)
- `redis` (7, with healthcheck)
- `api` — runs migrations on boot, then `uvicorn`
- `worker` — runs the ARQ worker (relay + poller + dispatcher)

The API image includes healthchecks for `/health/live` and `/health/ready`.

## Scaling

- **API**: stateless, scale horizontally behind a load balancer.
- **Worker**: any number of replicas; the outbox relay uses a `FOR UPDATE SKIP
  LOCKED` claim so multiple workers do not double-publish.
- **Database**: the outbox, notifications, and deliveries tables are the hot
  paths; index on `outbox_events(status)`, `deliveries(next_attempt_at)`, and
  `notifications(scheduled_at)` are provided by migrations.

## Migration

Migrations are managed with Alembic:

```bash
uv run alembic upgrade head   # apply
uv run alembic revision --autogenerate -m "..."  # new migration
```

## Monitoring

- `/metrics` — Prometheus scrape target.
- `/health/live` and `/health/ready` — container probes.
- Structured JSON logs carry `correlation_id` on every line.
