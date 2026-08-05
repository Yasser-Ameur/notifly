"""M8 API tests: operations endpoints for query and dead-letter recovery."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import update

from notifly.infrastructure.db.orm import DeliveryRow, NotificationRow

TEMPLATE = {
    "name": "Welcome",
    "event": "user_welcome",
    "variables": [{"name": "name", "type": "string", "required": True}],
    "channels": {
        "email": {"subject": "Hi {{ name }}", "body": "Welcome, {{ name }}!"},
    },
}

PAYLOAD = {
    "event": "user_welcome",
    "variables": {"name": "Alice"},
    "recipients": {"email": "alice@example.com"},
}


async def _app_with_template(client, name: str = "acme") -> tuple[dict, dict]:
    body = (await client.post("/v1/apps", json={"name": name})).json()
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.post("/v1/templates", json=TEMPLATE, headers=headers)
    assert response.status_code == 201, response.text
    return body, headers


async def _send(client, headers) -> dict:
    response = await client.post("/v1/notifications", json=PAYLOAD, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["notification"]


async def _fail_notification(app, notification_id: UUID, *, status: str = "failed") -> None:
    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        await session.execute(
            update(NotificationRow)
            .where(NotificationRow.id == notification_id)
            .values(status=status, updated_at=now)
        )
        await session.execute(
            update(DeliveryRow)
            .where(DeliveryRow.notification_id == notification_id)
            .values(status="failed", attempts=3, last_error="boom", completed_at=now)
        )
        await session.commit()


async def test_operations_requires_auth(client) -> None:
    response = await client.get("/v1/operations/notifications")
    assert response.status_code == 401


async def test_get_application(client) -> None:
    body, headers = await _app_with_template(client)
    response = await client.get("/v1/operations/applications", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == body["id"]
    assert response.json()["name"] == "acme"


async def test_list_notifications(app, client) -> None:
    _, headers = await _app_with_template(client)
    notification = await _send(client, headers)
    await _fail_notification(app, UUID(notification["id"]), status="sent")

    response = await client.get("/v1/operations/notifications", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == notification["id"]
    assert body["limit"] == 50
    assert body["offset"] == 0

    filtered = await client.get("/v1/operations/notifications?status=failed", headers=headers)
    assert filtered.json()["total"] == 0


async def test_list_notifications_pagination(client) -> None:
    _, headers = await _app_with_template(client)
    await _send(client, headers)
    response = await client.get("/v1/operations/notifications?limit=1&offset=0", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1


async def test_list_deliveries(app, client) -> None:
    _, headers = await _app_with_template(client)
    notification = await _send(client, headers)
    await _fail_notification(app, UUID(notification["id"]))

    response = await client.get("/v1/operations/deliveries", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["channel_type"] == "email"
    assert body["items"][0]["status"] == "failed"
    assert body["items"][0]["last_error"] == "boom"

    by_status = await client.get("/v1/operations/deliveries?status=sent", headers=headers)
    assert by_status.json()["total"] == 0


async def test_list_deliveries_scoped_to_notification(app, client) -> None:
    _, headers = await _app_with_template(client)
    first = await _send(client, headers)
    await _fail_notification(app, UUID(first["id"]))
    second = await _send(client, headers)

    response = await client.get(
        f"/v1/operations/deliveries?notification_id={first['id']}", headers=headers
    )
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["notification_id"] == first["id"]
    assert second["id"] != first["id"]


async def test_list_deadletters(app, client) -> None:
    _, headers = await _app_with_template(client)
    failed = await _send(client, headers)
    await _fail_notification(app, UUID(failed["id"]))
    sent = await _send(client, headers)
    await _fail_notification(app, UUID(sent["id"]), status="sent")

    response = await client.get("/v1/operations/deadletters", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == failed["id"]


async def test_list_audit(client) -> None:
    _, headers = await _app_with_template(client)
    await _send(client, headers)
    response = await client.get("/v1/operations/audit", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    assert body["items"][0]["action"] == "notification.created"
    assert body["items"][0]["actor"]
    assert body["items"][0]["resource_type"] == "notification"


async def test_retry_deadletter(app, client) -> None:
    _, headers = await _app_with_template(client)
    notification = await _send(client, headers)
    await _fail_notification(app, UUID(notification["id"]))

    response = await client.post(
        f"/v1/operations/notifications/{notification['id']}/retry", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["notification"]["id"] == notification["id"]
    assert body["notification"]["status"] == "pending"
    assert len(body["deliveries"]) == 1
    assert body["deliveries"][0]["status"] == "pending"
    assert body["deliveries"][0]["attempts"] == 0
    assert body["deliveries"][0]["last_error"] is None

    audit = await client.get("/v1/operations/audit", headers=headers)
    assert any(entry["action"] == "notification.retried" for entry in audit.json()["items"])


async def test_retry_sent_notification_conflicts(app, client) -> None:
    _, headers = await _app_with_template(client)
    notification = await _send(client, headers)
    await _fail_notification(app, UUID(notification["id"]), status="sent")

    response = await client.post(
        f"/v1/operations/notifications/{notification['id']}/retry", headers=headers
    )
    assert response.status_code == 409


async def test_retry_unknown_notification_404(client) -> None:
    _, headers = await _app_with_template(client)
    response = await client.post(
        f"/v1/operations/notifications/{UUID(int=1)}/retry", headers=headers
    )
    assert response.status_code == 404


async def test_operations_scoped_to_application(app, client) -> None:
    _, headers_a = await _app_with_template(client, "a")
    _, headers_b = await _app_with_template(client, "b")
    notification = await _send(client, headers_a)
    await _fail_notification(app, UUID(notification["id"]))

    response = await client.get("/v1/operations/notifications", headers=headers_b)
    assert response.status_code == 200
    assert response.json()["total"] == 0
