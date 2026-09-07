"""Offline regression coverage for durable retries and account-bound dispatch."""
import base64
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import Client

from app.api.generations import _encode_routing_scope
from app.config import Settings
from app.main import create_app
from app.mcp_server import FlowProviderClient, MCPSettings, build_mcp_server
from app.providers.google_flow.sdk.constants import VIDEO_POLL_URL
from app.workers.job_worker import JobWorker

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


async def test_managed_image_mcp_route_survives_reopen(setup_flow, tmp_path, monkeypatch):
    app, runtime, _ = setup_flow

    async def managed(*args):
        return "project"

    async def api(*args, **kwargs):
        return {"status": 200, "data": {"media": [{"name": "generated", "image": {
            "generatedImage": {"fifeUrl": "https://example.test/image.png"},
        }}]}}

    monkeypatch.setattr("app.services.flow._managed_project", managed)
    monkeypatch.setattr(runtime.bridge, "api_request", api)
    provider = FlowProviderClient(MCPSettings(base_url="http://test", allowed_roots=str(tmp_path)),
                                  transport=httpx.ASGITransport(app=app))
    try:
        async with Client(build_mcp_server(provider)) as client:
            created = await client.call_tool("flow_generate_image", {"prompt": "draw"})
            jid = created.structured_content["data"]["jobs"][0]["id"]
            await JobWorker(runtime).process_queued_jobs()
            runtime.projects.close()
            result = await client.call_tool("flow_get_job_status", {"job_ids": [jid]})
            assert not result.is_error
            body = result.structured_content
            job = body["data"]["jobs"][0]
            assert job["status"] == "complete"
            assert job["project_id"] == "project"
            assert job["routing_scope"] == body["metadata"]["x-provider-routing-scope"]
            assert body["metadata"]["x-flow-project-id"] == "project"
            follow = await client.call_tool("flow_generate_image", {
                "prompt": "continue", "reference_media_ids": ["generated"],
                "project_id": job["project_id"], "routing_scope": job["routing_scope"],
            })
            assert not follow.is_error
            follow_id = follow.structured_content["data"]["jobs"][0]["id"]
            assert runtime.projects.get_job(follow_id).installation_id == "install\nfirst@test.com"
    finally:
        await provider.close()


@pytest.mark.parametrize("data", [{}, {"media": []}, {"media": [None]},
                                  {"media": [{"name": ""}]}, {"media": {"name": "wrong-shape"}}])
async def test_invalid_image_response_is_not_complete(setup_flow, monkeypatch, data):
    app, runtime, _ = setup_flow

    async def api(*args, **kwargs):
        return {"status": 200, "data": data}

    monkeypatch.setattr(runtime.bridge, "api_request", api)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/v1/images/generations", json={"prompt": "draw", "project_id": "project"})
        jid = created.json()["jobs"][0]["id"]
        await JobWorker(runtime).process_queued_jobs()
        result = await client.post("/v1/jobs/status", json={"job_ids": [jid]})
        job = result.json()["jobs"][0]
        assert job["status"] == "failed"
        assert job["error"]["outcome_unknown"] is True
        assert job["error"]["retryable"] is False


@pytest.mark.parametrize("status", [403, 429, 503])
@pytest.mark.parametrize("fallback", [False, True])
async def test_poll_http_error_backoff_recovery_and_limit(setup_flow, monkeypatch, status, fallback):
    app, runtime, _ = setup_flow
    runtime.settings.worker_poll_seconds = 10
    runtime.projects.enqueue_job("http-poll", "frames_to_video", {"prompt": "move"}, media_type="video")
    claimed = runtime.projects.claim_next_queued_job()
    runtime.projects.update_job_running(claimed.job_id, operation_name="op", installation_id="install\nfirst@test.com",
                                       google_project_id="project", claim_token=claimed.claim_token)
    response = {"status": status, "data": {"error": {"code": status, "message": "error"}}}

    async def api(*args, **kwargs):
        if fallback and "operations" in kwargs["body"]:
            return {"status": 200, "data": "unrecognized operation response"}
        return response

    monkeypatch.setattr(runtime.bridge, "api_request", api)
    worker = JobWorker(runtime)
    for count in range(1, 3):
        await worker._poll_job(runtime.projects.get_job(claimed.job_id))
        stored = runtime.projects.get_job(claimed.job_id)
        assert stored.poll_error_count == count
        assert stored.poll_attempts == count
        assert str(status) in stored.last_poll_error
        from datetime import datetime
        delay = (datetime.fromisoformat(stored.next_poll_at) - datetime.fromisoformat(stored.last_poll_at)).total_seconds()
        assert delay == 10 * 2 ** (count - 1)
    response = {"status": 200, "data": {"operations": [{"name": "op", "done": False}]}}
    await worker._poll_job(runtime.projects.get_job(claimed.job_id))
    assert runtime.projects.get_job(claimed.job_id).poll_error_count == 0
    response = {"status": status, "data": {"error": {"code": status}}}
    for _ in range(11):
        await worker._poll_job(runtime.projects.get_job(claimed.job_id))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/v1/jobs/status", json={"job_ids": [claimed.job_id]})
    job = result.json()["jobs"][0]
    assert job["status"] == "failed"
    assert job["error"]["code"] == "VIDEO_POLL_MAX_ERRORS"
    assert job["error"]["outcome_unknown"] is True


def test_mixed_routes_have_per_job_metadata_only(setup_flow):
    app, runtime, _ = setup_flow
    for number in (1, 2):
        runtime.projects.enqueue_job(f"route-{number}", "image", {"prompt": "draw"},
                                    installation_id=f"account-{number}", google_project_id=f"project-{number}")
    with TestClient(app) as client:
        response = client.post("/v1/jobs/status", json={"job_ids": ["route-1", "route-2"]})
    assert "x-provider-routing-scope" not in response.headers
    assert "x-flow-project-id" not in response.headers
    assert response.json()["metadata"]["routing_scope"] is None
    jobs = response.json()["jobs"]
    assert [j["project_id"] for j in jobs] == ["project-1", "project-2"]
    assert jobs[0]["routing_scope"] != jobs[1]["routing_scope"]


@pytest.fixture
def setup_flow(tmp_path, monkeypatch):
    app = create_app(Settings(
        env="test", bootstrap_api_key="fpa_test", worker_enabled=False,
        project_store_path=str(tmp_path / "projects.db"),
        asset_store_path=str(tmp_path / "assets"), worker_poll_seconds=0,
    ))
    runtime = app.state.runtime
    conn = SimpleNamespace(
        id="conn", installation_id="install", account_email="first@test.com",
        credits=1000, max_slots=4, max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE", connected_at=1,
    )
    runtime.projects.remember_project("install\nfirst@test.com", "project", "Test")
    monkeypatch.setattr(runtime.bridge, "ready_connections", lambda **kw: [conn])
    monkeypatch.setattr(runtime.bridge, "pending_count", lambda _: 0)
    with TestClient(app):
        yield app, runtime, conn


async def test_mcp_lost_response_replay_dispatch_and_url_recovery(setup_flow, tmp_path, monkeypatch):
    app, runtime, conn = setup_flow
    image_path = tmp_path / "ref.png"
    image_path.write_bytes(base64.b64decode(PNG))
    transport = httpx.ASGITransport(app=app)
    lost_response = True

    async def backend(request):
        nonlocal lost_response
        response = await transport.handle_async_request(request)
        await response.aread()
        if lost_response:
            lost_response = False
            raise httpx.ReadTimeout("Response lost after acceptance", request=request)
        return response

    paid_calls = []
    url_ready = False

    async def api(connection_id, **kwargs):
        assert connection_id == conn.id
        url = kwargs["url"]
        if "uploadImage" in url:
            return {"status": 200, "data": {"media": {"name": "media/start"}}}
        if url == VIDEO_POLL_URL:
            return {"status": 200, "data": {"operations": [{"operation": {
                "name": "operations/video", "done": True,
                "response": {"media": [{"name": "media/video", "video": {"generatedVideo": {}}}]},
            }}]}}
        paid_calls.append(url)
        return {"status": 200, "data": {"operations": [{"operation": {
            "name": "operations/video", "done": False,
        }}]}}

    async def resolve_url(*args, **kwargs):
        return "https://example.test/video.mp4" if url_ready else None

    monkeypatch.setattr(runtime.bridge, "api_request", api)
    monkeypatch.setattr(runtime.bridge, "resolve_media_url", resolve_url)
    provider = FlowProviderClient(MCPSettings(
        base_url="https://provider.test", allowed_roots=str(tmp_path),
    ), transport=httpx.MockTransport(backend))
    try:
        async with Client(build_mcp_server(provider)) as client:
            args = {"prompt": "animate", "type": "frames_to_video",
                    "image_paths": [str(image_path)], "project_id": "project",
                    "idempotency_key": "lost-response"}
            failed = await client.call_tool("flow_generate_video", args)
            assert failed.is_error
            accepted = runtime.projects.get_job_by_idempotency_key("lost-response")
            assert accepted is not None
            replay = await client.call_tool("flow_generate_video", args)
            assert not replay.is_error
            assert replay.structured_content["data"]["jobs"][0]["id"] == accepted.job_id
            stored = runtime.projects.get_job_by_idempotency_key("lost-response")
            assert stored is not None
            worker = JobWorker(runtime)
            await worker.process_queued_jobs()
            assert len(paid_calls) == 1
            await worker.poll_running_jobs()
            assert runtime.projects.get_job(stored.job_id).status == "running"
            url_ready = True
            await worker.poll_running_jobs()
            assert runtime.projects.get_job(stored.job_id).status == "completed"
            again = await client.call_tool("flow_generate_video", args)
            assert not again.is_error
            assert again.structured_content["data"]["jobs"][0]["status"] == "complete"
            await worker.process_queued_jobs()
            assert len(paid_calls) == 1
    finally:
        await provider._client.aclose()


def test_image_idempotency_keeps_original_payload(setup_flow):
    app, runtime, _ = setup_flow
    runtime.projects.put_media("install\nfirst@test.com", "project", "digest", "media/ref", "image/png", "ref.png")
    with TestClient(app) as client:
        body = {"prompt": "draw", "reference_media_ids": ["media/ref"]}
        headers = {"Idempotency-Key": "image-retry"}
        first = client.post("/v1/images/generations", json=body, headers=headers)
        assert first.status_code == 202
        job_id = first.json()["jobs"][0]["id"]
        stored = runtime.projects.get_job(job_id)
        assert stored.google_project_id == "project"
        assert stored.request_payload["project_id"] is None
        assert stored.request_payload["_routing_locked"] is False
        replay = client.post("/v1/images/generations", json=body, headers=headers)
        assert replay.json()["jobs"][0]["id"] == job_id
        changed = client.post("/v1/images/generations", json={**body, "prompt": "different"}, headers=headers)
        assert changed.status_code == 409


@pytest.mark.parametrize("route", ["scope", "project", "legacy"])
async def test_explicit_route_stays_queued_after_account_switch(setup_flow, monkeypatch, route):
    app, runtime, conn = setup_flow
    if route == "legacy":
        job = runtime.projects.enqueue_job(
            "legacy", "image", {"prompt": "draw"},
            installation_id="install\nfirst@test.com", google_project_id="project",
        )
        job_id = job.job_id
    else:
        body = {"prompt": "draw"}
        headers = {}
        if route == "project":
            body["project_id"] = "project"
        else:
            headers["X-Provider-Routing-Scope"] = _encode_routing_scope(runtime.settings, "install\nfirst@test.com")
        with TestClient(app) as client:
            response = client.post("/v1/images/generations", json=body, headers=headers)
        assert response.status_code == 202
        job_id = response.json()["jobs"][0]["id"]
    conn.account_email = "second@test.com"

    async def forbidden(*args, **kwargs):
        pytest.fail("Dispatched through the wrong signed-in account")

    monkeypatch.setattr(runtime.bridge, "api_request", forbidden)
    await JobWorker(runtime).process_queued_jobs()
    assert runtime.projects.get_job(job_id).status == "queued"


async def test_poll_account_switch_and_unknown_outcome(setup_flow, monkeypatch):
    _, runtime, conn = setup_flow
    runtime.projects.enqueue_job("poll", "frames_to_video", {"prompt": "move"})
    job = runtime.projects.claim_next_queued_job()
    runtime.projects.update_job_running(
        job.job_id, installation_id="install\nfirst@test.com", google_project_id="project",
        operation_name="operations/video", claim_token=job.claim_token,
    )
    conn.account_email = "second@test.com"

    async def forbidden(*args, **kwargs):
        pytest.fail("Polled through a different signed-in account")

    monkeypatch.setattr(runtime.bridge, "api_request", forbidden)
    worker = JobWorker(runtime)
    for _ in range(11):
        await worker.poll_running_jobs()
    stored = runtime.projects.get_job(job.job_id)
    assert stored.status == "failed"
    assert stored.error_code == "VIDEO_POLL_MAX_ERRORS"
    assert stored.outcome_unknown is True


async def test_dispatch_yields_after_one_batch(setup_flow, monkeypatch):
    _, runtime, _ = setup_flow
    for i in range(5):
        runtime.projects.enqueue_job(f"batch-{i}", "image", {"prompt": "draw"})
    dispatched = []

    async def dispatch(job):
        dispatched.append(job.job_id)

    worker = JobWorker(runtime)
    monkeypatch.setattr(worker, "_dispatch_job", dispatch)
    await worker.process_queued_jobs(max_concurrent=2)
    assert len(dispatched) == 2
    assert sum(runtime.projects.get_job(f"batch-{i}").status == "queued" for i in range(5)) == 3


@pytest.mark.parametrize("character", [False, True])
@pytest.mark.parametrize("media_type", ["images", "videos"])
def test_idempotency_rejects_changed_routing_scope(setup_flow, character, media_type):
    app, runtime, _ = setup_flow
    body = {"prompt": "draw or animate"}
    with TestClient(app) as client:
        if character:
            digest, _, size = runtime.projects.asset_store.put_base64(PNG, "image/png")
            runtime.projects.record_asset(digest, "image/png", size, "ref.png")
            runtime.projects.put_media("install\nfirst@test.com", "project", digest, "media/ref", "image/png", "ref.png")
            created = client.post("/v1/characters", json={"name": "Test", "reference_media_ids": ["media/ref"]})
            assert created.status_code == 201
            character_id = created.json()["id"]
            url = f"/v1/characters/{character_id}/{media_type}/generations"
        else:
            url = f"/v1/{media_type}/generations"
            if media_type == "videos":
                body.update(type="frames_to_video", input_images=[{"image_base64": PNG, "mime_type": "image/png"}])
        headers = {"Idempotency-Key": "scope-retry", "X-Provider-Routing-Scope": _encode_routing_scope(runtime.settings, "install\nfirst@test.com")}
        first = client.post(url, json=body, headers=headers)
        assert first.status_code == 202
        replay = client.post(url, json=body, headers=headers)
        assert replay.status_code == 202
        assert replay.json()["jobs"][0]["id"] == first.json()["jobs"][0]["id"]
        headers["X-Provider-Routing-Scope"] = _encode_routing_scope(runtime.settings, "install\nsecond@test.com")
        changed = client.post(url, json=body, headers=headers)
        assert changed.status_code == 409
        assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
