"""M4 API tests: template CRUD + preview over HTTP."""

from __future__ import annotations

import uuid as uuidlib

TEMPLATE = {
    "name": "Welcome",
    "event": "user_welcome",
    "description": "Greets new users",
    "variables": [{"name": "name", "type": "string", "required": True}],
    "channels": {
        "email": {"subject": "Hi {{ name }}", "body": "Welcome, {{ name }}!"},
        "slack": {"subject": None, "body": "Welcome {{ name }}"},
    },
}


async def _create_app_and_key(client) -> tuple[dict, dict]:
    body = (await client.post("/v1/apps", json={"name": "acme"})).json()
    return body, {"X-Notifly-Key": body["api_key"]}


async def test_create_template(client) -> None:
    _, headers = await _create_app_and_key(client)
    response = await client.post("/v1/templates", json=TEMPLATE, headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Welcome"
    assert body["event"] == "user_welcome"
    assert "api_key" not in body
    assert set(body["channels"]) == {"email", "slack"}


async def test_create_template_requires_auth(client) -> None:
    response = await client.post("/v1/templates", json=TEMPLATE)
    assert response.status_code == 401


async def test_create_template_duplicate_event_conflicts(client) -> None:
    _, headers = await _create_app_and_key(client)
    first = await client.post("/v1/templates", json=TEMPLATE, headers=headers)
    assert first.status_code == 201
    second = await client.post("/v1/templates", json=TEMPLATE, headers=headers)
    assert second.status_code == 409
    assert second.json()["type"].endswith("/already_exists")


async def test_create_template_invalid_variable_name(client) -> None:
    _, headers = await _create_app_and_key(client)
    bad = dict(TEMPLATE, variables=[{"name": "not valid!", "type": "string"}])
    response = await client.post("/v1/templates", json=bad, headers=headers)
    assert response.status_code == 400
    assert "errors" in response.json()


async def test_create_template_empty_channels(client) -> None:
    _, headers = await _create_app_and_key(client)
    bad = dict(TEMPLATE, channels={})
    response = await client.post("/v1/templates", json=bad, headers=headers)
    assert response.status_code == 400


async def test_list_templates(client) -> None:
    _, headers = await _create_app_and_key(client)
    await client.post("/v1/templates", json=TEMPLATE, headers=headers)
    response = await client.get("/v1/templates", headers=headers)
    assert response.status_code == 200
    templates = response.json()
    assert len(templates) == 1
    assert templates[0]["event"] == "user_welcome"


async def test_get_template(client) -> None:
    _, headers = await _create_app_and_key(client)
    created = (await client.post("/v1/templates", json=TEMPLATE, headers=headers)).json()
    response = await client.get(f"/v1/templates/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["name"] == "Welcome"


async def test_get_template_not_found(client) -> None:
    _, headers = await _create_app_and_key(client)
    response = await client.get(f"/v1/templates/{uuidlib.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_update_template(client) -> None:
    _, headers = await _create_app_and_key(client)
    created = (await client.post("/v1/templates", json=TEMPLATE, headers=headers)).json()
    replacement = dict(TEMPLATE, name="Welcome v2", event="user_welcome_v2")
    response = await client.put(f"/v1/templates/{created['id']}", json=replacement, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Welcome v2"
    assert body["event"] == "user_welcome_v2"
    assert body["id"] == created["id"]


async def test_update_template_to_existing_event_conflicts(client) -> None:
    _, headers = await _create_app_and_key(client)
    first = (await client.post("/v1/templates", json=TEMPLATE, headers=headers)).json()
    other = dict(TEMPLATE, name="Other", event="other_event")
    await client.post("/v1/templates", json=other, headers=headers)

    conflicting = dict(TEMPLATE, name="First", event="other_event")
    response = await client.put(f"/v1/templates/{first['id']}", json=conflicting, headers=headers)
    assert response.status_code == 409


async def test_delete_template(client) -> None:
    _, headers = await _create_app_and_key(client)
    created = (await client.post("/v1/templates", json=TEMPLATE, headers=headers)).json()
    response = await client.delete(f"/v1/templates/{created['id']}", headers=headers)
    assert response.status_code == 204
    gone = await client.get(f"/v1/templates/{created['id']}", headers=headers)
    assert gone.status_code == 404


async def test_preview_template(client) -> None:
    _, headers = await _create_app_and_key(client)
    created = (await client.post("/v1/templates", json=TEMPLATE, headers=headers)).json()
    response = await client.post(
        f"/v1/templates/{created['id']}/preview",
        json={"variables": {"name": "Alice"}},
        headers=headers,
    )
    assert response.status_code == 200
    channels = response.json()["channels"]
    assert channels["email"] == {"subject": "Hi Alice", "body": "Welcome, Alice!"}
    assert channels["slack"]["body"] == "Welcome Alice"


async def test_preview_template_missing_required_variable(client) -> None:
    _, headers = await _create_app_and_key(client)
    created = (await client.post("/v1/templates", json=TEMPLATE, headers=headers)).json()
    response = await client.post(
        f"/v1/templates/{created['id']}/preview",
        json={"variables": {}},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("/variable_validation")


async def test_preview_template_undefined_variable(client) -> None:
    _, headers = await _create_app_and_key(client)
    template = dict(TEMPLATE, channels={"email": {"subject": None, "body": "{{ missing }}"}})
    created = (await client.post("/v1/templates", json=template, headers=headers)).json()
    response = await client.post(
        f"/v1/templates/{created['id']}/preview",
        json={"variables": {"name": "Alice"}},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["type"].endswith("/template_rendering")


async def test_cross_application_template_isolation(client) -> None:
    _, headers_a = await _create_app_and_key(client)
    second_body = (await client.post("/v1/apps", json={"name": "beta"})).json()
    headers_b = {"X-Notifly-Key": second_body["api_key"]}

    created = (await client.post("/v1/templates", json=TEMPLATE, headers=headers_a)).json()

    get_response = await client.get(f"/v1/templates/{created['id']}", headers=headers_b)
    assert get_response.status_code == 404
    update_response = await client.put(
        f"/v1/templates/{created['id']}", json=TEMPLATE, headers=headers_b
    )
    assert update_response.status_code == 404
    delete_response = await client.delete(f"/v1/templates/{created['id']}", headers=headers_b)
    assert delete_response.status_code == 404
