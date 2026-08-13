from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def app():
    return create_app(Settings(env="test", bootstrap_api_key="fpa_test", public_base_url="https://provider.test"))


def headers():
    return {"Authorization": "Bearer fpa_test"}


def connect(application, monkeypatch):
    connection = SimpleNamespace(id="account-1", max_slots=2, paygate_tier="PAYGATE_TIER_ONE")
    monkeypatch.setattr(application.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)


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
    assert captured["connection_id"] == "account-1"
    assert captured["captcha_action"] == "IMAGE_GENERATION"
    assert captured["url"].endswith("/v1/projects/project-1/flowMedia:batchGenerateImages")
    assert "url" not in body


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
        check = client.post("/v1/videos/status", headers=headers(), json={"operation_names": ["operations/1"]})
    assert project.status_code == check.status_code == 200
    assert project.json()["result"]["projectId"] == "projects/1"
    assert check.json()["operations"][0]["operation"]["done"] is False


def test_video_generation_uses_the_type_to_select_the_fixed_flow_operation(monkeypatch):
    application = app()
    connect(application, monkeypatch)
    urls = []

    async def fake_api(_connection_id, **kwargs):
        urls.append(kwargs["url"])
        return {"status": 200, "data": {"operations": []}}

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        image_to_video = client.post("/v1/videos/generations", headers=headers(), json={"type": "image_to_video", "project_id": "project-1", "prompt": "move", "start_media_id": "media/1"})
        omni = client.post("/v1/videos/generations", headers=headers(), json={"type": "omni", "project_id": "project-1", "prompt": "move", "reference_media_ids": ["media/1"]})
    assert image_to_video.status_code == omni.status_code == 200
    assert urls[0].endswith("video:batchAsyncGenerateVideoStartImage")
    assert urls[1].endswith("video:batchAsyncGenerateVideoReferenceImages")
