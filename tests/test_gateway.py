from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def gateway_app():
    return create_app(Settings(env="test", bootstrap_api_key="fpa_gateway_test",
        caller_owned_allowed_hosts="storage.example.test", public_base_url="https://provider.test",
        video_poll_seconds=0))


def headers(key="job-1"):
    return {"Authorization": "Bearer fpa_gateway_test", "Idempotency-Key": key}


def image_payload():
    return {"kind": "image", "prompt": "test", "storage_mode": "caller_owned",
        "output_destinations": [{"output_index": 0, "upload_url": "https://storage.example.test/output.png"}]}


def test_runtime_and_surface_are_stateless_gateway_only():
    app = gateway_app()
    assert set(app.openapi()["paths"]) == {"/v2/gateway/generations"}
    assert set(vars(app.state.runtime)) == {"settings", "bridge", "extension_manager"}
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready", "provider_accounts": 0, "video_lite_ready_accounts": 0}
        assert client.get("/v1/generations").status_code == 404
        assert client.get("/admin").status_code == 404


def test_gateway_requires_auth_idempotency_and_online_extension():
    app = gateway_app()
    with TestClient(app) as client:
        assert client.post("/v2/gateway/generations", json=image_payload()).status_code == 401
        missing_key = client.post("/v2/gateway/generations",
            headers={"Authorization": "Bearer fpa_gateway_test"}, json=image_payload())
        unavailable = client.post("/v2/gateway/generations", headers=headers(), json=image_payload())
    assert missing_key.status_code == 400
    assert missing_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "PROVIDER_ACCOUNT_UNAVAILABLE"


def test_gateway_image_happy_path(monkeypatch):
    import app.api.gateway_only as gateway
    uploaded = []
    connection = SimpleNamespace(id="account-1", paygate_tier="PAYGATE_TIER_ONE",
        success_count=0, max_slots=2)

    class FakeSDK:
        def __init__(self, _client): pass
        async def create_project(self, _title): return {"project_id": "project-1"}
        async def gen_image(self, **_kwargs): return {"media_entries": [{"bytes_data": b"image-bytes"}]}

    async def fake_put(destination, data, mime_type, allowed):
        uploaded.append((destination.output_index, data, mime_type, allowed))

    monkeypatch.setattr(gateway, "FlowSDK", FakeSDK)
    monkeypatch.setattr(gateway, "_put_output", fake_put)
    app = gateway_app()
    monkeypatch.setattr(app.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    with TestClient(app) as client:
        first = client.post("/v2/gateway/generations", headers=headers("flowcanvas-job-42"), json=image_payload())
        second = client.post("/v2/gateway/generations", headers=headers("flowcanvas-job-42"), json=image_payload())
    assert first.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]
    assert first.json()["outputs"][0]["uploaded"] is True
    assert uploaded[0][1:] == (b"image-bytes", "image/png", {"storage.example.test"})


def test_gateway_video_and_omni_happy_paths(monkeypatch):
    import app.api.gateway_only as gateway
    calls = []
    connection = SimpleNamespace(id="account-1", paygate_tier="PAYGATE_TIER_ONE",
        success_count=0, max_slots=2)

    class FakeSDK:
        def __init__(self, _client): pass
        async def create_project(self, _title): return {"project_id": "project-1"}
        async def upload_image(self, *_args): return {"media_id": "reference-1"}
        async def gen_video(self, **_kwargs): calls.append("video"); return {"operation_names": ["op-1"]}
        async def gen_video_omni(self, **_kwargs): calls.append("omni"); return {"operation_names": ["op-2"]}
        async def check_async(self, **_kwargs):
            return {"operations": [{"done": True, "media_entries": [{"bytes_data": b"video-bytes"}]}]}

    async def fake_read(*_args): return b"reference"
    async def fake_put(_destination, data, mime_type, _allowed):
        assert data == b"video-bytes" and mime_type == "video/mp4"

    monkeypatch.setattr(gateway, "FlowSDK", FakeSDK)
    monkeypatch.setattr(gateway, "_read_caller_input", fake_read)
    monkeypatch.setattr(gateway, "_put_output", fake_put)
    app = gateway_app()
    monkeypatch.setattr(app.state.runtime.bridge, "ready_connections", lambda **_kwargs: [connection])
    base = {"prompt": "test", "storage_mode": "caller_owned",
        "inputs": [{"asset_key": "ref", "mime_type": "image/png", "size_bytes": 9,
            "download_url": "https://storage.example.test/input.png"}],
        "output_destinations": [{"output_index": 0, "upload_url": "https://storage.example.test/output.mp4"}]}
    with TestClient(app) as client:
        video = client.post("/v2/gateway/generations", headers=headers("video"), json={**base, "kind": "video"})
        omni = client.post("/v2/gateway/generations", headers=headers("omni"), json={**base, "kind": "omni"})
    assert video.status_code == omni.status_code == 200
    assert calls == ["video", "omni"]


def test_schema_rejects_legacy_provider_owned_shape():
    app = gateway_app()
    with TestClient(app) as client:
        response = client.post("/v2/gateway/generations", headers=headers(), json={
            "kind": "image", "prompt": "x", "storage_mode": "provider_owned",
            "media_ids": ["123456789012345"],
            "output_destinations": [{"output_index": 0, "upload_url": "https://storage.example.test/out"}],
        })
    assert response.status_code == 422
