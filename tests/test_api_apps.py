"""M3 API tests: applications + API keys over HTTP."""

from __future__ import annotations

import uuid as uuidlib


async def _create_app(client, name: str = "acme") -> dict:
    response = await client.post("/v1/apps", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_application_returns_bootstrap_key(client) -> None:
    body = await _create_app(client, "acme")
    assert body["name"] == "acme"
    assert uuidlib.UUID(body["id"])
    assert body["api_key"].startswith("notifly_")


async def test_create_application_bootstrap_key_authenticates(client) -> None:
    body = await _create_app(client, "acme")
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.get("/v1/apps", headers=headers)
    assert response.status_code == 200
    assert [app["name"] for app in response.json()] == ["acme"]


async def test_create_application_duplicate_name_conflicts(client) -> None:
    await _create_app(client, "acme")
    response = await client.post("/v1/apps", json={"name": "acme"})
    assert response.status_code == 409
    body = response.json()
    assert body["type"] == "https://notifly.dev/errors/already_exists"
    assert body["status"] == 409


async def test_create_application_validation_error(client) -> None:
    response = await client.post("/v1/apps", json={"name": ""})
    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "https://notifly.dev/errors/invalid_data"
    assert "errors" in body


async def test_create_application_missing_name(client) -> None:
    response = await client.post("/v1/apps", json={})
    assert response.status_code == 400


async def test_list_applications_requires_auth(client) -> None:
    response = await client.get("/v1/apps")
    assert response.status_code == 401
    body = response.json()
    assert body["status"] == 401
    assert body["type"] == "https://notifly.dev/errors/unauthenticated"
    assert "correlation_id" in body


async def test_list_applications_invalid_key(client) -> None:
    response = await client.get("/v1/apps", headers={"X-Notifly-Key": "notifly_bogus"})
    assert response.status_code == 401


async def test_list_applications_paginated(client) -> None:
    await _create_app(client, "first")
    await _create_app(client, "second")
    body = await _create_app(client, "third")
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.get("/v1/apps", params={"limit": 2}, headers=headers)
    assert response.status_code == 200
    assert [app["name"] for app in response.json()] == ["first", "second"]
    assert all("api_key" not in app for app in response.json())


async def test_issue_api_key_returns_plaintext_once(client) -> None:
    body = await _create_app(client)
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.post(
        f"/v1/apps/{body['id']}/api-keys",
        json={"name": "ci"},
        headers=headers,
    )
    assert response.status_code == 201
    key = response.json()
    assert key["name"] == "ci"
    assert key["api_key"].startswith("notifly_")
    assert "key_hash" not in key

    auth = await client.get("/v1/apps", headers={"X-Notifly-Key": key["api_key"]})
    assert auth.status_code == 200


async def test_issue_api_key_without_body(client) -> None:
    body = await _create_app(client)
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.post(f"/v1/apps/{body['id']}/api-keys", headers=headers)
    assert response.status_code == 201
    assert response.json()["name"] == "default"


async def test_issue_api_key_for_other_application_forbidden(client) -> None:
    app_a = await _create_app(client, "a")
    app_b = await _create_app(client, "b")
    headers_b = {"X-Notifly-Key": app_b["api_key"]}
    response = await client.post(
        f"/v1/apps/{app_a['id']}/api-keys",
        json={"name": "x"},
        headers=headers_b,
    )
    assert response.status_code == 403
    assert response.json()["type"] == "https://notifly.dev/errors/forbidden"


async def test_issue_api_key_missing_application_forbidden(client) -> None:
    body = await _create_app(client)
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.post(
        f"/v1/apps/{uuidlib.uuid4()}/api-keys", json={"name": "x"}, headers=headers
    )
    assert response.status_code == 403


async def test_list_api_keys_metadata_only(client) -> None:
    body = await _create_app(client)
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.get(f"/v1/apps/{body['id']}/api-keys", headers=headers)
    assert response.status_code == 200
    keys = response.json()
    assert len(keys) == 1
    assert "api_key" not in keys[0]
    assert "key_hash" not in keys[0]
    assert keys[0]["key_prefix"] == body["api_key"][:16]


async def test_revoke_api_key(client) -> None:
    body = await _create_app(client)
    headers = {"X-Notifly-Key": body["api_key"]}
    issued = await client.post(
        f"/v1/apps/{body['id']}/api-keys", json={"name": "temp"}, headers=headers
    )

    response = await client.delete(
        f"/v1/apps/{body['id']}/api-keys/{issued.json()['id']}", headers=headers
    )
    assert response.status_code == 204

    listed = await client.get(f"/v1/apps/{body['id']}/api-keys", headers=headers)
    revoked = next(key for key in listed.json() if key["id"] == issued.json()["id"])
    assert revoked["revoked_at"] is not None

    denied = await client.get("/v1/apps", headers={"X-Notifly-Key": issued.json()["api_key"]})
    assert denied.status_code == 401


async def test_revoke_api_key_not_found(client) -> None:
    body = await _create_app(client)
    headers = {"X-Notifly-Key": body["api_key"]}
    response = await client.delete(
        f"/v1/apps/{body['id']}/api-keys/{uuidlib.uuid4()}", headers=headers
    )
    assert response.status_code == 404


async def test_revoke_other_applications_key_not_found(client) -> None:
    app_a = await _create_app(client, "a")
    app_b = await _create_app(client, "b")
    key_b = await client.post(
        f"/v1/apps/{app_b['id']}/api-keys",
        json={"name": "b-key"},
        headers={"X-Notifly-Key": app_b["api_key"]},
    )
    response = await client.delete(
        f"/v1/apps/{app_a['id']}/api-keys/{key_b.json()['id']}",
        headers={"X-Notifly-Key": app_a["api_key"]},
    )
    assert response.status_code == 404


async def test_correlation_id_echoed_and_generated(client) -> None:
    response = await client.get("/v1/apps")
    assert response.status_code == 401
    generated = response.headers.get("x-correlation-id")
    assert generated

    supplied = uuidlib.uuid4().hex
    response = await client.get("/v1/apps", headers={"X-Correlation-ID": supplied})
    assert response.headers.get("x-correlation-id") == supplied
    assert response.json()["correlation_id"] == supplied


async def test_unknown_route_returns_json_error(client) -> None:
    response = await client.get("/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert "detail" in body
    assert "correlation_id" in body
