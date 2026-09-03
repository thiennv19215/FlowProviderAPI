import asyncio
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


def test_production_requires_connector_auth():
    common = {
        "env": "production",
        "bootstrap_api_key": "fpa_prod_backend_secret",
        "public_base_url": "https://provider.example.com",
        "project_store_path": ":memory:",
    }
    with pytest.raises(ValueError, match="extension connector API key"):
        Settings(**common)

    settings = Settings(
        **common,
        extension_api_key="fpe_prod_connector_secret",
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
        "/v1/media", "/v1/images/generations",
        "/v1/videos/generations", "/v1/jobs/status",
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


def test_business_endpoints_are_public_but_still_validate_payloads():
    with TestClient(app()) as client:
        response = client.post(
            "/v1/images/generations",
            headers={"Content-Type": "application/json"},
            content=b"not-json",
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_JSON"


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


def test_job_worker_cost_and_omni_alias_classification():
    from app.workers.job_worker import _is_omni_job_type, _job_aspect_ratio, _job_credit_cost

    assert _job_credit_cost("image_to_video", 8) == 20
    assert _job_credit_cost("omni_r2v", 8) == 25
    assert _is_omni_job_type("omni_r2v") is True
    assert _job_aspect_ratio("image_to_video", {}) == "VIDEO_ASPECT_RATIO_LANDSCAPE"
    assert _job_aspect_ratio("i2v", {}) == "VIDEO_ASPECT_RATIO_PORTRAIT"
    assert _job_aspect_ratio("image_to_video", {"aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"}) == "VIDEO_ASPECT_RATIO_PORTRAIT"


def test_job_worker_refreshes_credits_after_paid_dispatch(monkeypatch):
    from app.workers.job_worker import JobWorker

    application = app()
    connection = connect(application, monkeypatch)
    connection.credits = 100
    runtime = application.state.runtime
    runtime.projects.put("installation-1", "project-1", "FlowProvider")
    runtime.mark_project_synced(connection, "installation-1")
    runtime.projects.enqueue_job(
        job_id="job_worker_refresh",
        job_type="omni",
        request_payload={"type": "omni", "prompt": "refresh credits"},
    )
    refreshes = []
    monkeypatch.setattr(
        runtime.bridge,
        "schedule_account_refresh",
        lambda connection_id, **kwargs: refreshes.append((connection_id, kwargs)),
    )

    async def fake_api(_connection_id, **kwargs):
        return {"status": 200, "data": {"operations": [{"name": "operations/refresh"}]}}

    monkeypatch.setattr(runtime.bridge, "api_request", fake_api)
    asyncio.run(JobWorker(runtime).process_queued_jobs())

    assert connection.credits is None
    assert refreshes == [(connection.id, {"initial_delay": 2})]
    assert runtime.active_jobs == {}


def test_job_worker_fails_paid_job_without_poll_identifier(monkeypatch):
    from app.workers.job_worker import JobWorker

    application = app()
    connection = connect(application, monkeypatch)
    connection.credits = 100
    runtime = application.state.runtime
    runtime.projects.put("installation-1", "project-1", "FlowProvider")
    runtime.mark_project_synced(connection, "installation-1")
    runtime.projects.enqueue_job(
        job_id="job_worker_missing_poll",
        job_type="omni",
        request_payload={"type": "omni", "prompt": "missing poll"},
    )
    monkeypatch.setattr(runtime.bridge, "schedule_account_refresh", lambda *_args, **_kwargs: None)

    async def fake_api(_connection_id, **kwargs):
        return {"status": 200, "data": {}}

    monkeypatch.setattr(runtime.bridge, "api_request", fake_api)
    asyncio.run(JobWorker(runtime).process_queued_jobs())

    stored_job = runtime.projects.get_job("job_worker_missing_poll")
    assert stored_job is not None
    assert stored_job.status == "failed"
    assert stored_job.operation_name is None
    assert "no poll identifier" in stored_job.error_message
    assert runtime.active_jobs == {}


def test_job_worker_unsupported_quality_releases_once(monkeypatch):
    from app.workers.job_worker import JobWorker

    application = app()
    connection = connect(application, monkeypatch)
    runtime = application.state.runtime
    runtime.projects.put("installation-1", "project-1", "FlowProvider")
    runtime.mark_project_synced(connection, "installation-1")
    runtime.projects.enqueue_job(
        job_id="job_worker_bad_quality",
        job_type="image_to_video",
        request_payload={
            "type": "image_to_video",
            "prompt": "bad quality",
            "quality": "lite_relaxed",
        },
    )

    async def fail_if_called(_connection_id, **kwargs):
        raise AssertionError(f"paid dispatch should not run: {kwargs}")

    monkeypatch.setattr(runtime.bridge, "api_request", fail_if_called)
    asyncio.run(JobWorker(runtime).process_queued_jobs())

    stored_job = runtime.projects.get_job("job_worker_bad_quality")
    assert stored_job is not None
    assert stored_job.status == "failed"
    assert runtime.active_jobs == {}
