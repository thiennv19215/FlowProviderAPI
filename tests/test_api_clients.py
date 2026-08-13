from sqlalchemy import select

from app.auth.api_keys import hash_api_key
from app.db.models import ApiClient


def test_admin_can_issue_list_and_revoke_api_key(client, app, admin_auth):
    created = client.post(
        "/v1/api-clients",
        headers=admin_auth,
        json={
            "name": "FlowCanvas user",
            "priority": 40,
            "max_concurrent_jobs": 8,
            "rate_limit_per_minute": 300,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["api_key"].startswith("fpa_live_")
    assert body["key_prefix"] == body["api_key"][:12]
    assert body["name"] == "FlowCanvas user"

    with app.state.runtime.session_factory() as db:
        row = db.scalar(select(ApiClient).where(ApiClient.id == body["id"]))
        assert row is not None
        assert row.key_hash == hash_api_key(body["api_key"])
        assert body["api_key"] not in row.key_hash

    issued_auth = {"Authorization": f"Bearer {body['api_key']}"}
    assert client.get("/v1/status", headers=issued_auth).status_code == 200

    listed = client.get("/v1/api-clients", headers=admin_auth)
    assert listed.status_code == 200
    listed_client = next(item for item in listed.json()["data"] if item["id"] == body["id"])
    assert "api_key" not in listed_client
    assert "key_hash" not in listed_client

    revoked = client.delete(f"/v1/api-clients/{body['id']}", headers=admin_auth)
    assert revoked.status_code == 204
    denied = client.get("/v1/status", headers=issued_auth)
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "INVALID_API_KEY"


def test_api_client_management_requires_admin_key(client, auth):
    response = client.post("/v1/api-clients", headers=auth, json={"name": "Nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ADMIN_KEY"


def test_api_client_create_validates_limits(client, admin_auth):
    response = client.post(
        "/v1/api-clients",
        headers=admin_auth,
        json={"name": "Invalid", "max_concurrent_jobs": 0},
    )
    assert response.status_code == 422


def test_revoking_unknown_api_client_returns_not_found(client, admin_auth):
    response = client.delete("/v1/api-clients/cli_missing", headers=admin_auth)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "API_CLIENT_NOT_FOUND"


def test_admin_ui_and_assets_are_served(client):
    page = client.get("/admin")
    assert page.status_code == 200
    assert "FlowProvider" in page.text
    assert "/admin-assets/app.js" in page.text
    assert "Sao chép key" in page.text
    assert client.get("/admin-assets/app.js").status_code == 200
    assert client.get("/admin-assets/styles.css").status_code == 200
