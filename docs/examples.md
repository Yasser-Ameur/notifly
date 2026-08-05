# Examples

## Setup

```bash
# create an app
curl -s -X POST http://localhost:8000/v1/apps \
  -H "Content-Type: application/json" \
  -d '{"name": "acme"}'

# → {"id": "9a5...", "name": "acme", "api_key": "notifly_<secret>"}
# store the api_key — it is only shown once.
```

## Configure channels

Email (SMTP):

```bash
curl -s -X POST http://localhost:8000/v1/apps/9a5.../channels \
  -H "X-Notifly-Key: notifly_..." -H "Content-Type: application/json" \
  -d '{
    "channel_type": "email",
    "enabled": true,
    "config": {"host": "smtp.example.com", "port": 587, "username": "u", "password": "p", "from_address": "no-reply@example.com", "start_tls": true}
  }'
```

Slack:

```bash
curl -s -X POST http://localhost:8000/v1/apps/9a5.../channels \
  -H "X-Notifly-Key: notifly_..." -H "Content-Type: application/json" \
  -d '{"channel_type": "slack", "enabled": true, "config": {"webhook_url": "https://hooks.slack.com/services/..."}}'
```

## Create a template

```bash
curl -s -X POST http://localhost:8000/v1/templates \
  -H "X-Notifly-Key: notifly_..." -H "Content-Type: application/json" \
  -d '{
    "name": "welcome",
    "event": "user_welcome",
    "description": "Sent when a user signs up",
    "variables": [{"name": "name", "type": "string", "required": true}],
    "channels": {
      "email": {"subject": "Welcome, {{ name }}!", "body": "Hi {{ name }}, thanks for joining."},
      "slack": {"body": "A new user joined: {{ name }}"}
    }
  }'
```

## Send a notification

Now, with an idempotency key:

```bash
curl -s -X POST http://localhost:8000/v1/notifications \
  -H "X-Notifly-Key: notifly_..." \
  -H "Idempotency-Key: welcome-alice-1" \
  -H "Content-Type: application/json" \
  -d '{
    "event": "user_welcome",
    "variables": {"name": "Alice"},
    "recipients": {"email": "alice@example.com", "slack": "#general"}
  }'
```

Sending the same request again returns the same notification ID — nothing is
duplicated.

## Schedule

```bash
curl -s -X POST http://localhost:8000/v1/notifications \
  -H "X-Notifly-Key: notifly_..." -H "Content-Type: application/json" \
  -d '{
    "event": "user_welcome",
    "variables": {"name": "Bob"},
    "recipients": {"email": "bob@example.com"},
    "scheduled_at": "2026-02-01T09:00:00Z"
  }'
```

## Python SDK

```python
import asyncio

from notifly import Client, AsyncClient

# Sync convenience wrapper
client = Client(api_key="notifly_...", base_url="http://localhost:8000")

client.email(
    to="user@example.com",
    template="welcome",
    variables={"name": "Alice"},
    idempotency_key="welcome-alice-2",
)

client.slack(webhook="#ops", template="deployment", variables={"service": "api"})


# Async, for asyncio applications
async def main() -> None:
    async with AsyncClient(api_key="notifly_...") as ac:
        notification = await ac.send_notification(
            event="user_welcome",
            variables={"name": "Carol"},
            recipients={"email": "carol@example.com"},
            idempotency_key="welcome-carol-1",
        )
        print(notification.id)


asyncio.run(main())
```

See the [SDK](sdk/) directory for the full client surface.
