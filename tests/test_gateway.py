import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def app():
    return create_app(Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
    ))


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
    )
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    return connection


def test_public_surface_is_a_fixed_flow_facade():
    application = app()
    assert set(application.openapi()["paths"]) == {
        "/v1/projects", "/v1/media", "/v1/images/generations",
        "/v1/videos/generations", "/v1/videos/status",
    }
    with TestClient(application) as client:
        assert client.post("/v1/proxy", headers=headers(), json={}).status_code == 404


def test_generation_requires_auth_and_connection():
    request = {"project_id": "project-1", "prompt": "test"}
    with TestClient(app()) as client:
        assert client.post("/v1/images/generations", json=request).status_code == 401
        unavailable = client.post("/v1/images/generations", headers=headers(), json=request)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "PROVIDER_ACCOUNT_UNAVAILABLE"


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
            "status": 200,
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

    assert first_upload.status_code == cached_upload.status_code == 200
    assert calls == ["account-2"]
    assert first_upload.headers["x-flow-media-cache-hits"] == "0"
    assert cached_upload.headers["x-flow-media-cache-hits"] == "1"
    assert cached_upload.json() == {
        "media": {"name": "media/cached", "projectId": "projects/owned"},
    }


def test_concurrent_identical_media_requests_upload_only_once(monkeypatch):
    application = app()
    connect(application, monkeypatch)
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
    calls = []

    async def fake_api(connection_id, **_kwargs):
        calls.append(connection_id)
        return {"status": 200, "data": {"media": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations", headers=headers(),
            json={"project_id": "projects/existing", "prompt": "test"},
        )
        assert response.status_code == 200
        pending["account-1"] = 2
        response = client.post(
            "/v1/images/generations", headers=headers(),
            json={"project_id": "projects/existing", "prompt": "test"},
        )
        assert response.status_code == 200
        pending["account-1"] = 3
        response = client.post(
            "/v1/images/generations", headers=headers(),
            json={"project_id": "projects/existing", "prompt": "test"},
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


def test_managed_project_search_follows_cursor_before_creating_duplicate(monkeypatch):
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
    assert response.headers["x-flow-project-id"] == "projects/managed"
    assert len(trpc_calls) == 2


def test_stale_cached_media_is_uploaded_again(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    digest = hashlib.sha256(b"hello").hexdigest()
    application.state.runtime.projects.put("installation-1", "projects/managed", "FlowProvider")
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
        omni = client.post("/v1/videos/generations", headers=headers(), json={"type": "omni", "project_id": "project-1", "prompt": "move", "reference_media_ids": ["media/1"]})
    assert image_to_video.status_code == omni.status_code == 200
    assert urls[0].endswith("video:batchAsyncGenerateVideoStartImage")
    assert urls[1].endswith("video:batchAsyncGenerateVideoReferenceImages")
    assert application.state.runtime.projects.get_operation("operations/1") is not None
    assert application.state.runtime.projects.get_operation("operations/2") is not None


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

    async def fake_api(connection_id, **kwargs):
        names = [item["operation"]["name"] for item in kwargs["body"]["operations"]]
        calls.append((connection_id, names))
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
    accounts = [low, eligible]
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
        rejected = client.post("/v1/videos/generations", headers=headers(), json=body)

    assert accepted.status_code == 200
    assert calls == ["eligible"]
    assert eligible.credits is None
    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "VIDEO_ACCOUNT_UNAVAILABLE"
