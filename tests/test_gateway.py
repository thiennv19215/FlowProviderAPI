import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.config import Settings
from app.main import create_app


def app():
    return create_app(Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
    ))


def test_production_requires_connector_auth_and_disables_simulation():
    common = {
        "env": "production",
        "bootstrap_api_key": "fpa_prod_backend_secret",
        "public_base_url": "https://provider.example.com",
        "project_store_path": ":memory:",
    }
    with pytest.raises(ValueError, match="extension connector API key"):
        Settings(**common, allow_simulation_mode=False)
    with pytest.raises(ValueError, match="disable extension simulation"):
        Settings(**common, extension_api_key="fpe_prod_connector_secret")

    settings = Settings(
        **common,
        extension_api_key="fpe_prod_connector_secret",
        allow_simulation_mode=False,
    )
    assert settings.extension_api_key == "fpe_prod_connector_secret"


def headers(scope: str | None = None):
    value = {"Authorization": "Bearer fpa_test"}
    if scope:
        value["X-Provider-Routing-Scope"] = scope
    return value


def connect(application, monkeypatch):
    connection = SimpleNamespace(
        id="account-1",
        installation_id="installation-1",
        max_slots=2,
        paygate_tier="PAYGATE_TIER_ONE",
        credits=100,
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    application.state.runtime.projects.remember_project(
        "installation-1", "project-1", "Test project"
    )
    return connection


def test_public_surface_is_a_fixed_flow_facade():
    application = app()
    assert set(application.openapi()["paths"]) == {
        "/v1/projects", "/v1/media", "/v1/images/generations",
        "/v1/videos/generations", "/v1/videos/status",
    }
    with TestClient(application) as client:
        assert client.post("/v1/proxy", headers=headers(), json={}).status_code == 404


def test_readiness_fails_when_project_store_is_unavailable(monkeypatch):
    application = app()

    def unavailable():
        raise OSError("database unavailable")

    monkeypatch.setattr(application.state.runtime.projects, "check", unavailable)
    with TestClient(application) as client:
        response = client.get("/health/ready")
        extension_health = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert extension_health.json()["ok"] is False


def test_content_length_limit_rejects_request_before_parsing_body():
    application = app()
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            headers={**headers(), "Content-Length": str(71 * 1024 * 1024)},
            content=b"{}",
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_streamed_body_limit_rejects_request_without_content_length(monkeypatch):
    monkeypatch.setattr(main_module, "MAX_REQUEST_BYTES", 10)
    application = app()
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            headers=headers(),
            content=iter((b'{"prompt":', b'"too large"}')),
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_business_auth_is_checked_before_json_body_parsing():
    with TestClient(app()) as client:
        response = client.post(
            "/v1/images/generations",
            headers={"Content-Type": "application/json"},
            content=b"not-json",
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_API_KEY"


def test_generation_requires_auth_and_connection():
    request = {"project_id": "project-1", "prompt": "test"}
    with TestClient(app()) as client:
        assert client.post("/v1/images/generations", json=request).status_code == 401
        unavailable = client.post("/v1/images/generations", headers=headers(), json=request)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "PROVIDER_ACCOUNT_UNAVAILABLE"


def test_simulation_connection_is_never_used_for_generation(monkeypatch):
    application = app()
    connection = connect(application, monkeypatch)
    connection.simulation_mode = True

    with TestClient(application) as client:
        image = client.post(
            "/v1/images/generations", headers=headers(),
            json={"prompt": "must not be mocked", "variant_count": 2},
        )

    assert image.status_code == 503
    assert image.json()["error"]["code"] == "PROVIDER_ACCOUNT_UNAVAILABLE"
    assert "x-flow-mock" not in image.headers


def test_image_generation_calls_fixed_extension_operation_and_returns_raw_response(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    captured = {}

    async def fake_api(connection_id, **kwargs):
        captured.update({"connection_id": connection_id, **kwargs})
        return {"status": 201, "headers": {"x-flow-id": "123"}, "data": {"media": [{"name": "media/1"}]}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    body = {"project_id": "project-1", "prompt": "test", "model": "NANO_BANANA_PRO", "aspect_ratio": "IMAGE_ASPECT_RATIO_SQUARE", "reference_media_ids": ["media/ref"], "variant_count": 1}
    with TestClient(application) as client:
        response = client.post("/v1/images/generations", headers=headers(), json=body)
    assert response.status_code == 201
    assert response.json() == {"media": [{"name": "media/1"}]}
    assert response.headers["x-flow-id"] == "123"
    assert response.headers["x-provider-routing-scope"]
    assert captured["connection_id"] == "account-1"
    assert captured["captcha_action"] == "IMAGE_GENERATION"
    assert captured["url"].endswith("/v1/projects/project-1/flowMedia:batchGenerateImages")
    assert "url" not in body


def test_automatic_image_generation_creates_and_reuses_account_project(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    trpc_calls = []
    api_calls = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_calls.append(kwargs)
        return {
            "status": 200,
            "data": {"result": {"data": {"json": {"result": {"projects": [{
                "projectId": "projects/managed",
                "projectInfo": {"projectTitle": "FlowProvider"},
            }]}}}}},
        }

    async def fake_api(_connection_id, **kwargs):
        api_calls.append(kwargs)
        if kwargs["url"].endswith("/v1/flow/uploadImage"):
            return {"status": 200, "data": {"media": {"name": "media/uploaded"}}}
        return {"status": 200, "data": {"media": [{"name": "media/generated"}]}}

    async def unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("media cache hits must not perform a preflight request")

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", unexpected_resolve)
    with TestClient(application) as client:
        first = client.post(
            "/v1/images/generations",
            headers=headers(),
            json={
                "prompt": "use this reference",
                "input_images": [{"image_base64": "aGVsbG8=", "mime_type": "image/png"}],
            },
        )
        second = client.post(
            "/v1/images/generations",
            headers=headers(),
            json={
                "prompt": "reuse this reference",
                "input_images": [{"image_base64": "aGVsbG8=", "mime_type": "image/png"}],
            },
        )

    assert first.status_code == second.status_code == 200
    assert len(trpc_calls) == 1
    assert trpc_calls[0]["method"] == "GET"
    assert "project.searchUserProjects" in trpc_calls[0]["url"]
    assert api_calls[0]["body"]["clientContext"]["projectId"] == "projects/managed"
    assert api_calls[1]["body"]["requests"][0]["imageInputs"] == [
        {"name": "media/uploaded", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
    ]
    assert api_calls[2]["url"].endswith("/v1/projects/projects/managed/flowMedia:batchGenerateImages")
    assert api_calls[2]["body"]["requests"][0]["imageInputs"] == [
        {"name": "media/uploaded", "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
    ]
    assert first.headers["x-flow-project-id"] == "projects/managed"
    assert first.headers["x-flow-media-cache-hits"] == "0"
    assert second.headers["x-flow-media-cache-hits"] == "1"


def test_upload_routes_by_known_project_and_reuses_cached_media(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="account-1", installation_id="installation-1", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", connected_at=1,
    )
    owner = SimpleNamespace(
        id="account-2", installation_id="installation-2", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", connected_at=2,
    )
    application.state.runtime.projects.put("installation-2", "projects/owned", "FlowProvider")
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first, owner])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []

    async def fake_api(connection_id, **_kwargs):
        calls.append(connection_id)
        return {
            "status": 201,
            "headers": {"x-upload-id": "upload-1"},
            "data": {"media": {"name": "media/cached", "projectId": "projects/owned"}},
        }

    async def unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("media cache hits must not perform a preflight request")

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", unexpected_resolve)
    body = {
        "project_id": "projects/owned",
        "image_base64": "aGVsbG8=",
        "mime_type": "image/png",
        "file_name": "reference.png",
    }
    with TestClient(application) as client:
        first_upload = client.post("/v1/media", headers=headers(), json=body)
        cached_upload = client.post("/v1/media", headers=headers(), json=body)

    assert first_upload.status_code == cached_upload.status_code == 201
    assert first_upload.headers["x-upload-id"] == cached_upload.headers["x-upload-id"] == "upload-1"
    assert calls == ["account-2"]
    assert first_upload.headers["x-flow-media-cache-hits"] == "0"
    assert cached_upload.headers["x-flow-media-cache-hits"] == "1"
    assert cached_upload.json() == {
        "media": {"name": "media/cached", "projectId": "projects/owned"},
    }


def test_concurrent_identical_media_requests_upload_only_once(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    application.state.runtime.projects.remember_project(
        "installation-1", "projects/one", "Test project"
    )
    calls = []

    async def fake_api(_connection_id, **_kwargs):
        calls.append("upload")
        await asyncio.sleep(0.05)
        return {"status": 200, "data": {"media": {"name": "media/one"}}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    body = {
        "project_id": "projects/one", "image_base64": "aGVsbG8=",
        "mime_type": "image/png", "file_name": "one.png",
    }
    with TestClient(application) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(
                lambda _index: client.post("/v1/media", headers=headers(), json=body),
                range(2),
            ))

    assert [response.status_code for response in responses] == [200, 200]
    assert calls == ["upload"]
    assert sorted(response.headers["x-flow-media-cache-hits"] for response in responses) == ["0", "1"]


def test_google_account_change_cannot_reuse_previous_account_project(monkeypatch):
    application = app()
    current = SimpleNamespace(
        id="account-current",
        installation_id="shared-installation",
        account_email="new@example.com",
        max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=1,
    )
    application.state.runtime.projects.put(
        "shared-installation\nold@example.com", "projects/old-account", "FlowProvider"
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [current])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    with TestClient(application) as client:
        response = client.post(
            "/v1/media",
            headers=headers(),
            json={
                "project_id": "projects/old-account",
                "image_base64": "aGVsbG8=",
                "mime_type": "image/png",
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PROJECT_ACCOUNT_UNAVAILABLE"


def test_unknown_explicit_project_is_never_guessed_even_with_one_account(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations", headers=headers(),
            json={"project_id": "projects/not-synced", "prompt": "test"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_ROUTE_UNKNOWN"


def test_scheduler_fills_three_slots_before_using_next_connection(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="account-1", installation_id="installation-1", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", connected_at=1,
    )
    second = SimpleNamespace(
        id="account-2", installation_id="installation-2", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", connected_at=2,
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first, second])
    pending = {"account-1": 0, "account-2": 0}
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda connection_id: pending[connection_id])
    application.state.runtime.projects.put("installation-1", "projects/one", "FlowProvider")
    application.state.runtime.projects.put("installation-2", "projects/two", "FlowProvider")
    application.state.runtime.mark_project_synced(first, "installation-1")
    application.state.runtime.mark_project_synced(second, "installation-2")
    calls = []

    async def fake_api(connection_id, **_kwargs):
        calls.append(connection_id)
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations", headers=headers(),
                json={"prompt": "test"},
        )
        assert response.status_code == 200
        pending["account-1"] = 2
        response = client.post(
            "/v1/images/generations", headers=headers(),
                json={"prompt": "test"},
        )
        assert response.status_code == 200
        pending["account-1"] = 3
        response = client.post(
            "/v1/images/generations", headers=headers(),
                json={"prompt": "test"},
        )
        assert response.status_code == 200

    assert calls == ["account-1", "account-1", "account-2"]


def test_job_reservation_enforces_capacity_between_extension_rpcs(monkeypatch):
    application = app()
    connection = SimpleNamespace(id="account-1", max_slots=3)
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    assert application.state.runtime.reserve_connection(connection)
    assert application.state.runtime.reserve_connection(connection)
    assert application.state.runtime.reserve_connection(connection)
    assert not application.state.runtime.reserve_connection(connection)
    assert application.state.runtime.active_jobs["account-1"] == 3

    application.state.runtime.release_connection("account-1")
    assert application.state.runtime.reserve_connection(connection)


def test_paid_credit_is_reserved_before_video_request_completes(monkeypatch):
    application = app()
    connection = SimpleNamespace(id="account-1", max_slots=3, credits=20)
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    assert application.state.runtime.reserve_connection(connection, 20)
    assert application.state.runtime.available_credits(connection) == 0
    assert not application.state.runtime.reserve_connection(connection, 20)
    application.state.runtime.release_connection("account-1", 20)
    assert application.state.runtime.reserve_connection(connection, 20)


def test_automatic_image_generation_creates_project_when_account_has_no_managed_project(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    trpc_methods = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_methods.append(kwargs["method"])
        if kwargs["method"] == "GET":
            return {
                "status": 200,
                "data": {"result": {"data": {"json": {"result": {"projects": []}}}}},
            }
        return {
            "status": 200,
            "data": {"result": {"data": {"json": {"result": {"projectId": "projects/new"}}}}},
        }

    async def fake_api(_connection_id, **kwargs):
        assert "/v1/projects/projects/new/" in kwargs["url"]
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            headers=headers(),
            json={"prompt": "test"},
        )

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/new"
    assert trpc_methods == ["GET", "POST"]


def test_managed_project_does_not_create_when_lookup_response_is_invalid(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    trpc_methods = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_methods.append(kwargs["method"])
        return {"status": 200, "data": {"unexpected": "shape"}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations", headers=headers(), json={"prompt": "test"}
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROJECT_LIST_INVALID"
    assert trpc_methods == ["GET"]


def test_managed_project_search_reuses_newest_existing_project_without_creating(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    trpc_calls = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_calls.append(kwargs)
        if len(trpc_calls) == 1:
            return {
                "status": 200,
                "data": {"result": {"data": {"json": {"result": {
                    "projects": [{"projectId": "projects/other", "projectInfo": {"projectTitle": "Other"}}],
                    "nextCursor": "page-2",
                }}}}},
            }
        assert kwargs["method"] == "GET"
        assert "%22cursor%22%3A%22page-2%22" in kwargs["url"]
        return {
            "status": 200,
            "data": {"result": {"data": {"json": {"result": {"projects": [{
                "projectId": "projects/managed",
                "projectInfo": {"projectTitle": "FlowProvider"},
            }]}}}}},
        }

    async def fake_api(_connection_id, **_kwargs):
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post("/v1/images/generations", headers=headers(), json={"prompt": "test"})

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/other"
    assert len(trpc_calls) == 2
    assert all(call["method"] == "GET" for call in trpc_calls)


def test_managed_project_search_selects_newest_across_all_pages(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    trpc_calls = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_calls.append(kwargs)
        if len(trpc_calls) == 1:
            return {
                "status": 200,
                "data": {"result": {"data": {"json": {"result": {
                    "projects": [{
                        "projectId": "projects/older",
                        "projectInfo": {
                            "projectTitle": "FlowProvider",
                            "createTime": "2026-08-20T10:00:00Z",
                        },
                    }],
                    "nextCursor": "page-2",
                }}}}},
            }
        return {
            "status": 200,
            "data": {"result": {"data": {"json": {"result": {"projects": [{
                "projectId": "projects/newest",
                "projectInfo": {
                    "projectTitle": "FlowProvider",
                    "createTime": "2026-08-23T10:00:00Z",
                },
            }]}}}}},
        }

    api_urls = []

    async def fake_api(_connection_id, **kwargs):
        api_urls.append(kwargs["url"])
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations", headers=headers(), json={"prompt": "test"}
        )

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/newest"
    assert len(trpc_calls) == 2
    assert all(call["method"] == "GET" for call in trpc_calls)
    assert len(api_urls) == 1
    assert "/projects/newest/" in api_urls[0]


def test_managed_project_refreshes_stale_db_mapping_and_selects_newest(monkeypatch):
    application = app()
    connection = connect(application, monkeypatch)
    application.state.runtime.projects.put(
        "installation-1", "projects/old", "FlowProvider",
    )
    trpc_calls = []
    api_urls = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_calls.append(kwargs)
        newest_id = "projects/newest" if len(trpc_calls) == 1 else "projects/after-reconnect"
        return {
            "status": 200,
            "data": {"result": {"data": {"json": {"result": {"projects": [
                {"projectId": newest_id, "projectInfo": {"projectTitle": "FlowProvider"}},
                {"projectId": "projects/old", "projectInfo": {"projectTitle": "FlowProvider"}},
            ]}}}}},
        }

    async def fake_api(_connection_id, **kwargs):
        api_urls.append(kwargs["url"])
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        first = client.post("/v1/images/generations", headers=headers(), json={"prompt": "one"})
        second = client.post("/v1/images/generations", headers=headers(), json={"prompt": "two"})
        connection.connected_at = 2
        after_reconnect = client.post(
            "/v1/images/generations", headers=headers(), json={"prompt": "three"},
        )
        stored_project = application.state.runtime.projects.get("installation-1")

    assert first.headers["x-flow-project-id"] == "projects/newest"
    assert second.headers["x-flow-project-id"] == "projects/newest"
    assert after_reconnect.headers["x-flow-project-id"] == "projects/after-reconnect"
    assert len(trpc_calls) == 2
    assert "/v1/projects/projects/newest/" in api_urls[0]
    assert "/v1/projects/projects/newest/" in api_urls[1]
    assert "/v1/projects/projects/after-reconnect/" in api_urls[2]
    assert stored_project.google_project_id == "projects/after-reconnect"
    assert application.state.runtime.project_is_synced(connection, "installation-1")


def test_stale_cached_media_is_uploaded_again(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    digest = hashlib.sha256(b"hello").hexdigest()
    application.state.runtime.projects.put("installation-1", "projects/managed", "FlowProvider")
    connection = application.state.runtime.bridge.ready_connections()[0]
    application.state.runtime.mark_project_synced(connection, "installation-1")
    application.state.runtime.projects.put_media(
        "installation-1", "projects/managed", digest, "media/stale", "image/png", "reference.png"
    )
    calls = []

    async def fake_api(_connection_id, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert kwargs["body"]["requests"][0]["imageInputs"][0]["name"] == "media/stale"
            return {"status": 404, "data": {"error": {"message": "media not found"}}}
        if kwargs["url"].endswith("/v1/flow/uploadImage"):
            return {"status": 200, "data": {"media": {"name": "media/refreshed"}}}
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            headers=headers(),
            json={"prompt": "test", "input_images": [{"image_base64": "aGVsbG8="}]},
        )
        assert response.status_code == 200
        assert len(calls) == 3
        assert calls[1]["url"].endswith("/v1/flow/uploadImage")
        assert calls[2]["body"]["requests"][0]["imageInputs"][0]["name"] == "media/refreshed"
        refreshed = application.state.runtime.projects.get_media(
            "installation-1", "projects/managed", digest
        )
        assert refreshed is not None
        assert refreshed.google_media_id == "media/refreshed"


def test_create_project_and_check_operations_return_upstream_results(monkeypatch):
    application = app()
    connect(application, monkeypatch)

    async def fake_trpc(_connection_id, **kwargs):
        assert kwargs["body"] == {"json": {"projectTitle": "My project", "toolName": "PINHOLE"}}
        return {"status": 200, "data": {"result": {"projectId": "projects/1"}}}

    async def fake_api(_connection_id, **kwargs):
        assert kwargs["body"] == {"operations": [{"operation": {"name": "operations/1"}}]}
        return {"status": 200, "data": {"operations": [{"operation": {"name": "operations/1", "done": False}}]}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        project = client.post("/v1/projects", headers=headers(), json={"title": "My project"})
        scope = project.headers["x-provider-routing-scope"]
        check = client.post(
            "/v1/videos/status",
            headers=headers(scope),
            json={"operation_names": ["operations/1"]},
        )
    assert project.status_code == check.status_code == 200
    assert project.json()["result"]["projectId"] == "projects/1"
    assert check.json()["operations"][0]["operation"]["done"] is False
    assert check.headers["x-provider-routing-scope"] == scope


def test_list_projects_encodes_pagination_and_returns_routing_scope(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    captured = []

    async def fake_trpc(_connection_id, **kwargs):
        captured.append(kwargs)
        return {"status": 200, "data": {"result": {"data": {"json": {"result": {"projects": [{"projectId": "projects/1"}]}}}}}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    with TestClient(application) as client:
        default_page = client.get("/v1/projects", headers=headers())
        first = client.get("/v1/projects?page_size=25", headers=headers())
        scope = first.headers["x-provider-routing-scope"]
        second = client.get("/v1/projects?page_size=10&cursor=next/page", headers=headers(scope))

    assert default_page.status_code == first.status_code == second.status_code == 200
    assert first.json()["result"]["data"]["json"]["result"]["projects"][0]["projectId"] == "projects/1"
    assert captured[0]["method"] == "GET"
    assert "project.searchUserProjects?input=" in captured[0]["url"]
    assert "%22pageSize%22%3A10" in captured[0]["url"]
    assert "%22pageSize%22%3A25" in captured[1]["url"]
    assert "%22cursor%22%3Anull" in captured[1]["url"]
    assert "%22undefined%22" in captured[1]["url"]
    assert "%22cursor%22%3A%22next%2Fpage%22" in captured[2]["url"]
    assert second.headers["x-provider-routing-scope"] == scope


def test_routing_scope_pins_follow_up_calls_to_the_same_installation(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="account-1",
        installation_id="installation-1",
        max_slots=2,
        paygate_tier="PAYGATE_TIER_ONE",
    )
    second = SimpleNamespace(
        id="account-2",
        installation_id="installation-2",
        max_slots=2,
        paygate_tier="PAYGATE_TIER_ONE",
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first, second])
    monkeypatch.setattr(
        application.state.runtime.bridge,
        "pending_count",
        lambda connection_id: 0 if connection_id == "account-1" else 1,
    )
    calls = []

    async def fake_trpc(connection_id, **_kwargs):
        calls.append(connection_id)
        return {"status": 200, "data": {"result": {"projectId": "projects/1"}}}

    async def fake_api(connection_id, **_kwargs):
        calls.append(connection_id)
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        project = client.post("/v1/projects", headers=headers(), json={"title": "Pinned"})
        scope = project.headers["x-provider-routing-scope"]
        generated = client.post(
            "/v1/images/generations",
            headers=headers(scope),
            json={"project_id": "projects/1", "prompt": "test"},
        )
    assert project.status_code == generated.status_code == 200
    assert calls == ["account-1", "account-1"]


def test_routing_scope_never_falls_back_to_another_installation(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="account-1",
        installation_id="installation-1",
        max_slots=2,
        paygate_tier="PAYGATE_TIER_ONE",
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    async def fake_trpc(_connection_id, **_kwargs):
        return {"status": 200, "data": {"result": {"projectId": "projects/1"}}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    with TestClient(application) as client:
        project = client.post("/v1/projects", headers=headers(), json={"title": "Pinned"})
        scope = project.headers["x-provider-routing-scope"]
        monkeypatch.setattr(
            application.state.runtime.bridge,
            "ready_connections",
            lambda **_kwargs: [
                SimpleNamespace(
                    id="account-2",
                    installation_id="installation-2",
                    max_slots=2,
                    paygate_tier="PAYGATE_TIER_ONE",
                )
            ],
        )
        unavailable = client.post(
            "/v1/images/generations",
            headers=headers(scope),
            json={"project_id": "projects/1", "prompt": "test"},
        )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "ROUTING_SCOPE_UNAVAILABLE"


def test_routing_scope_is_invalid_for_new_google_account_on_same_installation(monkeypatch):
    application = app()
    old_account = SimpleNamespace(
        id="old", installation_id="shared-installation", account_email="old@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [old_account])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    async def fake_trpc(_connection_id, **_kwargs):
        return {"status": 200, "data": {"result": {"projectId": "projects/old"}}}

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    with TestClient(application) as client:
        created = client.post("/v1/projects", headers=headers(), json={"title": "Old"})
        scope = created.headers["x-provider-routing-scope"]
        new_account = SimpleNamespace(
            id="new", installation_id="shared-installation", account_email="new@example.com",
            max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=2,
        )
        monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [new_account])
        response = client.post(
            "/v1/images/generations", headers=headers(scope),
            json={"project_id": "projects/old", "prompt": "test"},
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ROUTING_SCOPE_UNAVAILABLE"


def test_video_generation_uses_the_type_to_select_the_fixed_flow_operation(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    urls = []

    async def fake_api(_connection_id, **kwargs):
        urls.append(kwargs["url"])
        return {
            "status": 200,
            "data": {"operations": [{"operation": {"name": f"operations/{len(urls)}"}}]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        image_to_video = client.post("/v1/videos/generations", headers=headers(), json={"type": "image_to_video", "project_id": "project-1", "prompt": "move", "start_media_id": "media/1"})
        connection = application.state.runtime.bridge.ready_connections()[0]
        connection.credits = 100
        omni = client.post("/v1/videos/generations", headers=headers(), json={"type": "omni", "project_id": "project-1", "prompt": "move", "reference_media_ids": ["media/1"]})
        assert application.state.runtime.projects.get_operation("operations/1") is not None
        assert application.state.runtime.projects.get_operation("operations/2") is not None
    assert image_to_video.status_code == omni.status_code == 200
    assert urls[0].endswith("video:batchAsyncGenerateVideoStartImage")
    assert urls[1].endswith("video:batchAsyncGenerateVideoReferenceImages")


def test_video_status_routes_and_merges_operations_across_accounts(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="account-1", installation_id="installation-1", account_email="one@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    second = SimpleNamespace(
        id="account-2", installation_id="installation-2", account_email="two@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=2,
    )
    application.state.runtime.projects.put_operation(
        "operations/one", "installation-1\none@example.com", "projects/one"
    )
    application.state.runtime.projects.put_operation(
        "operations/two", "installation-2\ntwo@example.com", "projects/two"
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first, second])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []
    active = 0
    max_active = 0

    async def fake_api(connection_id, **kwargs):
        nonlocal active, max_active
        names = [item["operation"]["name"] for item in kwargs["body"]["operations"]]
        calls.append((connection_id, names))
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return {
            "status": 200,
            "data": {"operations": [{"operation": {"name": name, "done": False}} for name in names]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status",
            headers=headers(),
            json={"operation_names": ["operations/one", "operations/two"]},
        )

    assert response.status_code == 200
    assert calls == [
        ("account-1", ["operations/one"]),
        ("account-2", ["operations/two"]),
    ]
    assert [item["operation"]["name"] for item in response.json()["operations"]] == [
        "operations/one", "operations/two",
    ]
    assert response.headers["x-flow-operation-groups"] == "2"
    assert max_active == 2


def test_video_status_preserves_requested_order_when_accounts_are_interleaved(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="account-1", installation_id="installation-1", account_email="one@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    second = SimpleNamespace(
        id="account-2", installation_id="installation-2", account_email="two@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=2,
    )
    for name, key in (
        ("operations/a1", "installation-1\none@example.com"),
        ("operations/b1", "installation-2\ntwo@example.com"),
        ("operations/a2", "installation-1\none@example.com"),
    ):
        application.state.runtime.projects.put_operation(name, key, "projects/one")
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first, second])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    async def fake_api(_connection_id, **kwargs):
        names = [item["operation"]["name"] for item in kwargs["body"]["operations"]]
        return {
            "status": 200,
            "data": {"operations": [{"operation": {"name": name, "done": False}} for name in names]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["operations/a1", "operations/b1", "operations/a2"]},
        )

    assert [item["operation"]["name"] for item in response.json()["operations"]] == [
        "operations/a1", "operations/b1", "operations/a2",
    ]


def test_video_status_polls_workflow_route_as_project_media(monkeypatch):
    application = app()
    connection = SimpleNamespace(
        id="account-1", installation_id="installation-1", account_email="one@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    application.state.runtime.projects.put_operation(
        "workflows/one", "installation-1\none@example.com", "projects/one",
        "media", "media/primary",
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    captured = {}

    async def fake_api(_connection_id, **kwargs):
        captured.update(kwargs)
        return {"status": 200, "data": {"media": [{"name": "media/primary"}]}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["workflows/one"]},
        )

    assert response.status_code == 200
    assert captured["body"] == {
        "media": [{"name": "media/primary", "projectId": "projects/one"}],
    }


def test_video_status_attaches_download_url_for_successful_media(monkeypatch):
    application = app()
    connection = SimpleNamespace(
        id="account-1", installation_id="installation-1", account_email="one@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    application.state.runtime.projects.put_operation(
        "workflow-1", "installation-1\none@example.com", "project-1",
        "media", "media/video-1",
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)

    async def fake_api(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {"media": [{
                "name": "media/video-1",
                "mediaMetadata": {"mediaStatus": {
                    "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                }},
                "video": {"generatedVideo": {"model": "abra_r2v_4s"}},
            }]},
        }

    resolved = []

    async def fake_resolve(connection_id, media_id, **_kwargs):
        resolved.append((connection_id, media_id))
        return "https://flow-content.google/video/signed"

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", fake_resolve)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["workflow-1"]},
        )

    media = response.json()["media"][0]
    assert response.status_code == 200
    assert response.headers["x-flow-video-urls"] == "1"
    assert media["downloadUrl"] == "https://flow-content.google/video/signed"
    assert media["video"]["generatedVideo"]["fifeUrl"] == media["downloadUrl"]
    assert resolved == [("account-1", "media/video-1")]


def test_video_status_does_not_resolve_url_before_success(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    application.state.runtime.projects.put_operation(
        "workflow-1", "installation-1", "project-1", "media", "media/video-1",
    )

    async def fake_api(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {"media": [{
                "name": "media/video-1",
                "mediaMetadata": {"mediaStatus": {
                    "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SCHEDULED",
                }},
                "video": {"generatedVideo": {}},
            }]},
        }

    async def unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("pending video must not resolve a download URL")

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", unexpected_resolve)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["workflow-1"]},
        )

    assert response.status_code == 200
    assert response.headers["x-flow-video-urls"] == "0"
    assert "downloadUrl" not in response.json()["media"][0]


def test_video_status_does_not_treat_unsuccessful_as_success(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    application.state.runtime.projects.put_operation(
        "workflow-1", "installation-1", "project-1", "media", "media/video-1",
    )

    async def fake_api(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {"media": [{
                "name": "media/video-1",
                "mediaMetadata": {"mediaStatus": {
                    "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_UNSUCCESSFUL",
                }},
                "video": {"generatedVideo": {}},
            }]},
        }

    async def unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("unsuccessful video must not resolve a download URL")

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", unexpected_resolve)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["workflow-1"]},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "VIDEO_MEDIA_FAILED"
    assert "MEDIA_GENERATION_STATUS_UNSUCCESSFUL" in response.json()["error"]["message"]


def test_video_status_surfaces_operation_error_from_http_200(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    application.state.runtime.projects.put_operation(
        "operations/failed", "installation-1", "project-1",
    )

    async def fake_api(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {"operations": [{"operation": {
                "name": "operations/failed",
                "done": True,
                "error": {"code": 13, "message": "Generation failed in Flow"},
            }}]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["operations/failed"]},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "VIDEO_OPERATION_FAILED"
    assert "Generation failed in Flow" in response.json()["error"]["message"]


def test_video_status_attaches_url_to_media_nested_in_completed_operation(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    application.state.runtime.projects.put_operation(
        "operations/one", "installation-1", "project-1",
    )

    async def fake_api(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {"operations": [{"operation": {
                "name": "operations/one",
                "done": True,
                "response": {"media": [{
                    "name": "media/video-1",
                    "video": {"generatedVideo": {}},
                }]},
            }}]},
        }

    async def fake_resolve(_connection_id, media_id, **_kwargs):
        assert media_id == "media/video-1"
        return "https://flow-content.google/video/nested"

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", fake_resolve)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status", headers=headers(),
            json={"operation_names": ["operations/one"]},
        )

    nested = response.json()["operations"][0]["operation"]["response"]["media"][0]
    assert response.headers["x-flow-video-urls"] == "1"
    assert nested["downloadUrl"] == "https://flow-content.google/video/nested"
    assert nested["video"]["generatedVideo"]["fifeUrl"] == nested["downloadUrl"]


def test_video_status_rejects_unknown_operation_without_scope(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/status",
            headers=headers(),
            json={"operation_names": ["operations/unknown"]},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "OPERATION_ROUTE_UNKNOWN"


def test_video_generation_excludes_accounts_below_twenty_credits(monkeypatch):
    application = app()
    low = SimpleNamespace(
        id="low", installation_id="low-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=19, connected_at=1,
    )
    eligible = SimpleNamespace(
        id="eligible", installation_id="eligible-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=20, connected_at=2,
    )
    accounts = [eligible]
    application.state.runtime.projects.remember_project(
        "eligible-installation", "project-1", "Eligible project"
    )
    application.state.runtime.projects.remember_project(
        "low-installation", "project-2", "Low-credit project"
    )
    monkeypatch.setattr(
        application.state.runtime.bridge,
        "ready_connections",
        lambda min_credits=0: [item for item in accounts if (item.credits or 0) >= min_credits],
    )
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []

    async def fake_api(connection_id, **_kwargs):
        calls.append(connection_id)
        return {"status": 200, "data": {"operations": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    body = {
        "type": "image_to_video", "project_id": "project-1", "prompt": "move",
        "start_media_id": "media/1",
    }
    with TestClient(application) as client:
        accepted = client.post("/v1/videos/generations", headers=headers(), json=body)
        accounts[:] = [low]
        rejected = client.post(
            "/v1/videos/generations", headers=headers(),
            json={**body, "project_id": "project-2"},
        )

    assert accepted.status_code == 200
    assert calls == ["eligible"]
    assert eligible.credits is None
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "VIDEO_ACCOUNT_UNAVAILABLE"


def test_video_reference_upload_selects_credit_eligible_account(monkeypatch):
    application = app()
    low = SimpleNamespace(
        id="low", installation_id="low-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=19, connected_at=1,
    )
    eligible = SimpleNamespace(
        id="eligible", installation_id="eligible-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=2,
    )
    application.state.runtime.projects.put(
        "eligible-installation", "projects/eligible", "FlowProvider"
    )
    application.state.runtime.mark_project_synced(eligible, "eligible-installation")
    monkeypatch.setattr(
        application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [low, eligible]
    )
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []

    async def fake_api(connection_id, **kwargs):
        calls.append((connection_id, kwargs["body"]["clientContext"]["projectId"]))
        return {"status": 200, "data": {"media": {"name": "media/eligible"}}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/media",
            headers=headers(),
            json={
                "image_base64": "aGVsbG8=",
                "mime_type": "image/png",
                "required_credits": 20,
            },
        )

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/eligible"
    assert calls == [("eligible", "projects/eligible")]


def test_video_reference_upload_excludes_failed_project_account(monkeypatch):
    application = app()
    first = SimpleNamespace(
        id="first", installation_id="first-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    second = SimpleNamespace(
        id="second", installation_id="second-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=2,
    )
    application.state.runtime.projects.put(
        "first-installation", "projects/first", "FlowProvider"
    )
    application.state.runtime.projects.put(
        "second-installation", "projects/second", "FlowProvider"
    )
    application.state.runtime.mark_project_synced(first, "first-installation")
    application.state.runtime.mark_project_synced(second, "second-installation")
    monkeypatch.setattr(
        application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [first, second]
    )
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []

    async def fake_api(connection_id, **kwargs):
        calls.append((connection_id, kwargs["body"]["clientContext"]["projectId"]))
        return {"status": 200, "data": {"media": {"name": "media/retry"}}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/media",
            headers=headers(),
            json={
                "image_base64": "aGVsbG8=",
                "mime_type": "image/png",
                "required_credits": 20,
                "excluded_project_ids": ["projects/first"],
            },
        )

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/second"
    assert calls == [("second", "projects/second")]


def test_unscoped_video_routes_to_the_extension_that_uploaded_its_media(monkeypatch):
    application = app()
    other = SimpleNamespace(
        id="other", installation_id="other-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=1,
    )
    owner = SimpleNamespace(
        id="owner", installation_id="owner-installation", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=100, connected_at=2,
    )
    application.state.runtime.projects.remember_project(
        "owner-installation", "projects/owner", "Owner project"
    )
    application.state.runtime.projects.put_media(
        "owner-installation",
        "projects/owner",
        "content-sha",
        "media/reference",
        "image/png",
        "reference.png",
    )
    monkeypatch.setattr(
        application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [other, owner]
    )
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []

    async def fake_api(connection_id, **kwargs):
        calls.append((connection_id, kwargs["body"]["clientContext"]["projectId"]))
        return {"status": 200, "data": {"operations": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/generations",
            headers=headers(),
            json={
                "type": "image_to_video",
                "prompt": "move",
                "start_media_id": "media/reference",
            },
        )

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/owner"
    assert calls == [("owner", "projects/owner")]


def test_unscoped_image_routes_to_the_extension_that_owns_reference_media(monkeypatch):
    application = app()
    other = SimpleNamespace(
        id="other", installation_id="other-installation", account_email="other@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100,
        connected_at=1, simulation_mode=False,
    )
    owner = SimpleNamespace(
        id="owner", installation_id="owner-installation", account_email="owner@example.com",
        max_slots=3, paygate_tier="PAYGATE_TIER_ONE", credits=100,
        connected_at=2, simulation_mode=False,
    )
    owner_key = "owner-installation\nowner@example.com"
    application.state.runtime.projects.put_media(
        owner_key,
        "projects/owner",
        "content-sha",
        "media/reference",
        "image/png",
        "reference.png",
    )
    monkeypatch.setattr(
        application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [other, owner]
    )
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    calls = []

    async def fake_api(connection_id, **kwargs):
        calls.append((connection_id, kwargs["body"]["clientContext"]["projectId"]))
        return {"status": 200, "data": {"media": [{"name": "media/generated"}]}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            headers=headers(),
            json={"prompt": "use owner media", "reference_media_ids": ["media/reference"]},
        )
        generated_route = application.state.runtime.projects.get_media_by_google_id(
            "media/generated"
        )

    assert response.status_code == 200
    assert response.headers["x-flow-project-id"] == "projects/owner"
    assert calls == [("owner", "projects/owner")]
    assert generated_route is not None
    assert generated_route.installation_id == owner_key
    assert generated_route.google_project_id == "projects/owner"


def test_failed_paid_attempt_still_invalidates_and_refreshes_credit(monkeypatch):
    application = app()
    connection = connect(application, monkeypatch)
    refreshes = []

    async def fake_api(_connection_id, **_kwargs):
        return {"status": 500, "data": {"error": {"message": "uncertain outcome"}}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(
        application.state.runtime.bridge,
        "schedule_account_refresh",
        lambda connection_id, **kwargs: refreshes.append((connection_id, kwargs)),
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/generations", headers=headers(),
            json={
                "type": "image_to_video", "project_id": "project-1",
                "prompt": "move", "start_media_id": "media/1",
            },
        )
        assert connection.credits is None

    assert response.status_code == 500
    assert refreshes == [("account-1", {"initial_delay": 2})]


def test_paid_video_timeout_is_not_advertised_as_retryable(monkeypatch):
    application = app()
    connection = connect(application, monkeypatch)

    async def fake_api(_connection_id, **_kwargs):
        return {"error": "timeout"}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(
        application.state.runtime.bridge, "schedule_account_refresh", lambda *_args, **_kwargs: None
    )
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/generations",
            headers=headers(),
            json={
                "type": "image_to_video",
                "project_id": "project-1",
                "prompt": "move",
                "start_media_id": "media/1",
            },
        )

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "EXTENSION_TIMEOUT"
    assert response.json()["error"]["retryable"] is False


def test_omni_reserves_its_higher_known_credit_cost(monkeypatch):
    application = app()
    connection = SimpleNamespace(
        id="account-1", installation_id="installation-1", max_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", credits=24, connected_at=1,
    )
    application.state.runtime.projects.remember_project(
        "installation-1", "project-1", "Project"
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/generations", headers=headers(),
            json={
                "type": "omni", "project_id": "project-1", "prompt": "move",
                "reference_media_ids": ["media/1"], "duration_seconds": 8,
            },
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "VIDEO_ACCOUNT_UNAVAILABLE"


def test_optional_project_id_across_media_and_video(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    trpc_calls = []
    api_calls = []

    async def fake_trpc(_connection_id, **kwargs):
        trpc_calls.append(kwargs)
        return {
            "status": 200,
            "data": {"result": {"data": {"json": {"result": {"projects": [{
                "projectId": "projects/managed-auto",
                "projectInfo": {"projectTitle": "FlowProvider"},
            }]}}}}},
        }

    async def fake_api(_connection_id, **kwargs):
        api_calls.append(kwargs)
        if kwargs["url"].endswith("/v1/flow/uploadImage"):
            return {"status": 200, "data": {"media": {"name": "media/uploaded-auto"}}}
        return {
            "status": 200,
            "data": {"operations": [{"operation": {"name": "operations/video-1"}}]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "trpc_request", fake_trpc)
    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)

    with TestClient(application) as client:
        # 1. Media upload without project_id
        upload_resp = client.post(
            "/v1/media",
            headers=headers(),
            json={"image_base64": "aGVsbG8=", "mime_type": "image/png"},
        )
        assert upload_resp.status_code == 200
        assert upload_resp.headers["X-Flow-Project-Id"] == "projects/managed-auto"

        # 2. Image generation with reference_media_ids without project_id
        gen_img_resp = client.post(
            "/v1/images/generations",
            headers=headers(),
            json={
                "prompt": "test prompt",
                "reference_media_ids": ["media/uploaded-auto"],
            },
        )
        assert gen_img_resp.status_code == 200
        assert gen_img_resp.headers["X-Flow-Project-Id"] == "projects/managed-auto"

        # 3. Video generation (image_to_video) without project_id
        connection = application.state.runtime.bridge.ready_connections()[0]
        connection.credits = 100
        video_resp = client.post(
            "/v1/videos/generations",
            headers=headers(),
            json={
                "type": "image_to_video",
                "prompt": "animate this",
                "start_media_id": "media/uploaded-auto",
            },
        )
        assert video_resp.status_code == 200
        assert video_resp.headers["X-Flow-Project-Id"] == "projects/managed-auto"

        # 4. Omni video generation without project_id
        connection.credits = 100
        omni_resp = client.post(
            "/v1/videos/generations",
            headers=headers(),
            json={
                "type": "omni",
                "prompt": "animate omni",
                "reference_media_ids": ["media/uploaded-auto"],
            },
        )
        assert omni_resp.status_code == 200
        assert omni_resp.headers["X-Flow-Project-Id"] == "projects/managed-auto"
