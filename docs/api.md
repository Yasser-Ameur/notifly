# NotiFly API

The API is versioned under `/v1`, authenticated with an application API key sent
in the `X-Notifly-Key` header, and documented automatically via OpenAPI
(`/docs`, `/redoc`, `/openapi.json`).

This page summarizes the surface. The interactive docs are authoritative.

## Authentication

```
X-Notifly-Key: notifly_...
```

Keys are scoped to a single application. They are shown once, at creation.

## Headers

| Header | Required | Purpose |
|---|---|---|
| `X-Notifly-Key` | yes (protected routes) | application API key |
| `Idempotency-Key` | no (creation routes) | dedupe retried requests |
| `X-Correlation-ID` | no | correlation ID; generated if absent |

## Errors

All errors use the following shape with a corresponding HTTP status code:

```json
{
  "type": "https://notifly.dev/errors/not_found",
  "title": "Notification not found",
  "status": 404,
  "detail": "No notification exists with id 9ab3...",
  "correlation_id": "55e9..."
}
```

Common codes: `400` validation, `401` unauthenticated, `403` forbidden,
`404` not found, `409` conflict (idempotency mismatch, duplicate name),
`429` rate limited, `5xx` server errors.

## Applications

### `POST /v1/apps`
Create an application. Returns `id`, `name`, and a one-time `api_key`.

### `GET /v1/apps`
List applications (requires auth).

### `POST /v1/apps/{id}/api-keys`
Issue a new API key for an application. Returns the plaintext once.

### `GET /v1/apps/{id}/api-keys`
List issued keys (metadata only — never the secret).

### `DELETE /v1/apps/{id}/api-keys/{key_id}`
Revoke an API key.

## Channels

### `POST /v1/apps/{id}/channels`
Configure a delivery channel (`email`, `slack`, `discord`, `teams`, `webhook`):
provider settings, retry policy, rate limit.

### `GET /v1/apps/{id}/channels`
List channels.

### `PATCH /v1/apps/{id}/channels/{channel_type}`
Enable/disable or update a channel.

## Templates

### `POST /v1/templates`
Create a template: name, event, variable declarations, per-channel content.

### `GET /v1/templates`
List templates.

### `GET /v1/templates/{id}`
Get a template.

### `PUT /v1/templates/{id}`
Replace a template.

### `DELETE /v1/templates/{id}`
Delete a template.

### `POST /v1/templates/{id}/preview`
Render a template with sample variables.

## Notifications

### `POST /v1/notifications`
Send a notification (now or at `scheduled_at`).

```json
{
  "event": "user_welcome",
  "variables": {"name": "Alice"},
  "recipients": {"email": "alice@example.com"},
  "scheduled_at": "2026-01-02T03:04:05Z"
}
```

Honors `Idempotency-Key`. Returns the notification with its deliveries.

### `GET /v1/notifications/{id}`
Get a notification and its delivery status.

### `GET /v1/notifications/{id}/deliveries`
List the per-channel deliveries.

### `POST /v1/notifications/{id}/cancel`
Cancel a pending/scheduled notification.

## Operations

### `GET /v1/operations/notifications`
Query notifications (by status, event, date range; paginated).

### `GET /v1/operations/deliveries`
Query deliveries (by channel, status; paginated).

### `GET /v1/operations/applications`
Query applications (paginated).

### `GET /v1/operations/deadletters`
List dead-lettered notifications (permanently failed deliveries).

### `POST /v1/operations/notifications/{id}/retry`
Requeue a dead-lettered notification.

## Audit

### `GET /v1/operations/audit`
Query the audit log (paginated).

## Health & metrics

### `GET /health/live`
Liveness.

### `GET /health/ready`
Readiness (checks the database).

### `GET /metrics`
Prometheus metrics.
