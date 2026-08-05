"""M6 API tests: notification send, idempotency, inspect, and cancel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

TEMPLATE = {
    "name": "Welcome",
    "event": "user_welcome",
    "variables": [{"name": "name", "type": "string", "required": True}],
    "channels": {
        "email": {"subject": "Hi {{ name }}", "body": "Welcome, {{ name }}!"},
        "slack": {"subject": None, "body": "Welcome {{ name }}"},
    },
}

PAYLOAD = {
    "event": "user_welcome",
    "variables": {"name": "Alice"},
    "recipients": {"email": "alice@example.com"},
}


async def _app_and_headers(client, name: str = "acme") -> tuple[dict, dict]:
    body = (await client.post("/v1/apps", json={"name": name})).json()
    return body, {"X-Notifly-Key": body["api_key"]}


async def _app_with_template(client, name: str = "acme") -> tuple[dict, dict]:
    _, headers = await _app_and_headers(client, name)
    response = await client.post("/v1/templates", json=TEMPLATE, headers=headers)
    assert response.status_code == 201, response.text
    return _, headers


async def test_send_notification_201(client) -> None:
    _, headers = await _app_with_template(client)
    response = await client.post("/v1/notifications", json=PAYLOAD, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["notification"]["event"] == "user_welcome"
    assert body["notification"]["status"] == "pending"
    assert body["notification"]["variables"] == {"name": "Alice"}
    assert len(body["deliveries"]) == 1
    delivery = body["deliveries"][0]
    assert delivery["channel_type"] == "email"
    assert delivery["recipient"] == "alice@example.com"
    assert delivery["subject"] == "Hi Alice"
    assert delivery["body"] == "Welcome, Alice!"
    assert delivery["status"] == "pending"
    assert body["notification"]["correlation_id"]


async def test_send_notification_requires_auth(client) -> None:
    response = await client.post("/v1/notifications", json=PAYLOAD)
    assert response.status_code == 401


async def test_send_with_multiple_channels(client) -> None:
    _, headers = await _app_with_template(client)
    payload = dict(
        PAYLOAD,
        recipients={"email": "a@example.com", "slack": "https://hooks.example/s"},
    )
    response = await client.post("/v1/notifications", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    assert {d["channel_type"] for d in response.json()["deliveries"]} == {"email", "slack"}


async def test_scheduled_notification(client) -> None:
    _, headers = await _app_with_template(client)
    scheduled_at = datetime.now(UTC) + timedelta(hours=2)
    payload = dict(PAYLOAD, scheduled_at=scheduled_at.isoformat())
    response = await client.post("/v1/notifications", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    returned = datetime.fromisoformat(
        response.json()["notification"]["scheduled_at"].replace("Z", "+00:00")
    )
    assert returned == scheduled_at
    assert response.json()["notification"]["status"] == "pending"


async def test_idempotency_replay_returns_200(client) -> None:
    _, headers = await _app_with_template(client)
    headers = {**headers, "Idempotency-Key": "send-1"}
    first = await client.post("/v1/notifications", json=PAYLOAD, headers=headers)
    second = await client.post("/v1/notifications", json=PAYLOAD, headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["notification"]["id"] == second.json()["notification"]["id"]
    assert len(second.json()["deliveries"]) == 1


async def test_idempotency_conflict_409(client) -> None:
    _, headers = await _app_with_template(client)
    headers = {**headers, "Idempotency-Key": "send-1"}
    first = await client.post("/v1/notifications", json=PAYLOAD, headers=headers)
    assert first.status_code == 201
    conflicting = dict(PAYLOAD, variables={"name": "Bob"})
    second = await client.post("/v1/notifications", json=conflicting, headers=headers)
    assert second.status_code == 409
    assert second.json()["type"].endswith("/idempotency_conflict")


async def test_get_notification(client) -> None:
    _, headers = await _app_with_template(client)
    created = (await client.post("/v1/notifications", json=PAYLOAD, headers=headers)).json()
    response = await client.get(
        f"/v1/notifications/{created['notification']['id']}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["notification"]["id"] == created["notification"]["id"]
    assert len(response.json()["deliveries"]) == 1


async def test_get_notification_other_application_404(client) -> None:
    _, headers_a = await _app_with_template(client, "a")
    _, headers_b = await _app_with_template(client, "b")
    created = (await client.post("/v1/notifications", json=PAYLOAD, headers=headers_a)).json()
    response = await client.get(
        f"/v1/notifications/{created['notification']['id']}", headers=headers_b
    )
    assert response.status_code == 404


async def test_list_deliveries(client) -> None:
    _, headers = await _app_with_template(client)
    created = (
        await client.post(
            "/v1/notifications",
            json=dict(PAYLOAD, recipients={"email": "a@example.com", "slack": "https://h/s"}),
            headers=headers,
        )
    ).json()
    response = await client.get(
        f"/v1/notifications/{created['notification']['id']}/deliveries", headers=headers
    )
    assert response.status_code == 200
    assert len(response.json()) == 2


async def test_cancel_notification(client) -> None:
    _, headers = await _app_with_template(client)
    created = (await client.post("/v1/notifications", json=PAYLOAD, headers=headers)).json()
    response = await client.post(
        f"/v1/notifications/{created['notification']['id']}/cancel", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["notification"]["status"] == "cancelled"
    fetched = await client.get(
        f"/v1/notifications/{created['notification']['id']}", headers=headers
    )
    assert fetched.json()["notification"]["status"] == "cancelled"


async def test_cancel_sent_notification_conflicts(app, client) -> None:
    _, headers = await _app_with_template(client)
    created = (await client.post("/v1/notifications", json=PAYLOAD, headers=headers)).json()
    notification_id = UUID(created["notification"]["id"])
    from sqlalchemy import update

    from notifly.infrastructure.db.orm import NotificationRow

    async with app.state.session_factory() as session:
        await session.execute(
            update(NotificationRow)
            .where(NotificationRow.id == notification_id)
            .values(status="sent")
        )
        await session.commit()
    response = await client.post(f"/v1/notifications/{notification_id}/cancel", headers=headers)
    assert response.status_code == 409


async def test_missing_template_404(client) -> None:
    _, headers = await _app_with_template(client)
    response = await client.post(
        "/v1/notifications", json=dict(PAYLOAD, event="nope"), headers=headers
    )
    assert response.status_code == 404


async def test_unknown_variable_400(client) -> None:
    _, headers = await _app_with_template(client)
    response = await client.post(
        "/v1/notifications", json=dict(PAYLOAD, variables={"oops": "x"}), headers=headers
    )
    assert response.status_code == 400


async def test_recipient_channel_without_content_400(client) -> None:
    _, headers = await _app_with_template(client)
    response = await client.post(
        "/v1/notifications",
        json=dict(PAYLOAD, recipients={"discord": "https://hooks.example/d"}),
        headers=headers,
    )
    assert response.status_code == 400
