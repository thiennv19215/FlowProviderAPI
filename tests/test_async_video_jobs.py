import asyncio
import base64
import hashlib
import tempfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.workers.job_worker import JobWorker


def async_app():
    return create_app(Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
        worker_enabled=False,
    ))


def connect(application, monkeypatch):
    connection = SimpleNamespace(
        id="account-1",
        installation_id="installation-1",
        max_slots=2,
        paygate_tier="PAYGATE_TIER_ONE",
        credits=100,
    )
    monkeypatch.setattr(
        application.state.runtime.bridge,
        "ready_connections",
        lambda **_kwargs: [connection],
    )
    monkeypatch.setattr(application.state.runtime.bridge, "pending_count", lambda _id: 0)
    application.state.runtime.projects.remember_project(
        "installation-1", "project-1", "Test project"
    )
    return connection


def test_video_is_enqueued_then_status_reads_database_only(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    calls = []

    async def fake_api(_connection_id, **kwargs):
        if "uploadImage" in kwargs.get("url", ""):
            return {"status": 200, "data": {"media": {"name": "media/start"}}}
        calls.append(kwargs)
        return {
            "status": 200,
            "data": {"operations": [{"operation": {
                "name": "operations/video-1", "done": False,
            }}]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        created = client.post(
            "/v1/videos/generations",
            headers={"Idempotency-Key": "order-1"},
            json={
                "type": "image_to_video",
                "project_id": "project-1",
                "prompt": "move",
                "input_images": [{
                    "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                    "mime_type": "image/png",
                }],
                "aspect_ratio": "16:9",
            },
        )
        assert created.status_code == 202
        job_id = created.json()["jobs"][0]["id"]
        assert created.json()["jobs"][0]["status"] == "queued"
        stored = application.state.runtime.projects.get_job(job_id)
        assert stored.request_payload["aspect_ratio"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"
        assert calls == []

        asyncio.run(JobWorker(application.state.runtime).process_queued_jobs())
        assert len(calls) == 1

        calls.clear()
        status = client.post("/v1/jobs/status", json={"job_ids": [job_id]})
        assert status.status_code == 200
        assert status.json()["jobs"][0]["status"] == "running"
        assert calls == []


def test_image_is_enqueued_worker_completes_it_and_status_only_reads_db(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    calls = []

    async def fake_api(_connection_id, **kwargs):
        calls.append(kwargs)
        return {
            "status": 200,
            "data": {"media": [{
                "name": "media/image-1",
                "image": {
                    "generatedImage": {"fifeUrl": "https://flow-content.google/image"},
                    "dimensions": {"width": 1024, "height": 1024},
                },
            }]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    with TestClient(application) as client:
        created = client.post(
            "/v1/images/generations",
            headers={"Idempotency-Key": "image-order-1"},
            json={"project_id": "project-1", "prompt": "draw"},
        )
        assert created.status_code == 202
        assert created.json()["jobs"][0]["type"] == "image"
        assert created.json()["jobs"][0]["status"] == "queued"
        job_id = created.json()["jobs"][0]["id"]
        assert calls == []

        asyncio.run(JobWorker(application.state.runtime).process_queued_jobs())
        assert len(calls) == 1

        calls.clear()
        asyncio.run(JobWorker(application.state.runtime).poll_running_jobs())
        assert calls == []
        status = client.post("/v1/jobs/status", json={"job_ids": [job_id]})
        assert status.status_code == 200
        assert status.json()["jobs"][0] == {
            "id": job_id,
            "type": "image",
            "generation_type": "image",
            "status": "complete",
            "media": [{
                "id": "media/image-1",
                "type": "image",
                "url": "https://flow-content.google/image",
                "width": 1024,
                "height": 1024,
            }],
            "error": None,
        }
        assert calls == []


def test_generic_inline_image_is_reloaded_from_asset_store_after_runtime_memory_reset(monkeypatch):
    with tempfile.TemporaryDirectory(dir=".") as root:
        db_path = f"{root}/projects.db"
        asset_dir = f"{root}/assets"
        application = create_app(Settings(
            env="test", bootstrap_api_key="fpa_test", public_base_url="https://provider.test",
            project_store_path=db_path, asset_store_path=asset_dir, worker_enabled=False,
        ))
        connect(application, monkeypatch)
        raw = b"durable-inline-image"
        with TestClient(application) as client:
            response = client.post(
                "/v1/images/generations",
                json={
                    "project_id": "project-1",
                    "prompt": "use this image",
                    "input_images": [{
                        "image_base64": base64.b64encode(raw).decode("ascii"),
                        "mime_type": "image/png",
                        "file_name": "inline.png",
                    }],
                },
            )
            assert response.status_code == 202
            job_id = response.json()["jobs"][0]["id"]

        # Reopen the API against the same DB and asset directory. The worker
        # must reconstruct the image from the persisted content-addressed file.
        restarted = create_app(Settings(
            env="test", bootstrap_api_key="fpa_test", public_base_url="https://provider.test",
            project_store_path=db_path, asset_store_path=asset_dir, worker_enabled=False,
        ))
        connect(restarted, monkeypatch)
        calls = []

        async def fake_api(_connection_id, **kwargs):
            calls.append(kwargs)
            if "/flow/uploadImage" in kwargs["url"]:
                return {"status": 200, "data": {"media": {"name": "media/reloaded"}}}
            return {"status": 200, "data": {"media": [{"name": "media/generated"}]}}

        monkeypatch.setattr(restarted.state.runtime.bridge, "api_request", fake_api)
        with TestClient(restarted) as client:
            asyncio.run(JobWorker(restarted.state.runtime).process_queued_jobs())
            assert restarted.state.runtime.projects.get_job(job_id).status == "completed"
            assert any("/flow/uploadImage" in call["url"] for call in calls)


def test_missing_persisted_inline_asset_fails_job_before_dispatch(monkeypatch):
    with tempfile.TemporaryDirectory(dir=".") as asset_dir:
        application = create_app(Settings(
            env="test", bootstrap_api_key="fpa_test", public_base_url="https://provider.test",
            project_store_path=":memory:", asset_store_path=asset_dir, worker_enabled=False,
        ))
        connect(application, monkeypatch)
        raw = b"asset-to-remove"
        async def unexpected_api(*_args, **_kwargs):
            raise AssertionError("missing asset must fail before calling Flow")

        monkeypatch.setattr(application.state.runtime.bridge, "api_request", unexpected_api)
        with TestClient(application) as client:
            response = client.post(
                "/v1/images/generations",
                json={
                    "project_id": "project-1",
                    "prompt": "use this image",
                    "input_images": [{"image_base64": base64.b64encode(raw).decode("ascii")}],
                },
            )
            assert response.status_code == 202
            job_id = response.json()["jobs"][0]["id"]
            digest = hashlib.sha256(raw).hexdigest()
            application.state.runtime.projects.asset_store.delete(digest)
            asyncio.run(JobWorker(application.state.runtime).process_queued_jobs())
            job = application.state.runtime.projects.get_job(job_id)
            assert job.status == "failed"
            assert job.error_code == "INPUT_IMAGE_ASSET_MISSING"


def test_cached_generic_image_404_invalidates_media_cache(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    runtime = application.state.runtime
    raw = b"cached-image"
    digest, _path, size = runtime.projects.asset_store.put_bytes(raw, "image/png")
    runtime.projects.record_asset(digest, "image/png", size, "cached.png")
    runtime.projects.put_media(
        "installation-1", "project-1", digest, "media/cached", "image/png", "cached.png",
        {}, 200, {},
    )

    async def stale_flow(_connection_id, **_kwargs):
        return {"status": 404, "error": "media is stale"}

    monkeypatch.setattr(runtime.bridge, "api_request", stale_flow)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            json={
                "project_id": "project-1",
                "prompt": "use cached image",
                "input_images": [{"image_base64": base64.b64encode(raw).decode("ascii")}],
            },
        )
        assert response.status_code == 202
        job_id = response.json()["jobs"][0]["id"]
        asyncio.run(JobWorker(runtime).process_queued_jobs())
        assert runtime.projects.get_job(job_id).status == "failed"
        assert runtime.projects.get_media("installation-1", "project-1", digest) is None


def test_unknown_generic_project_route_is_rejected(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            json={"project_id": "project-does-not-exist", "prompt": "draw"},
        )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_ROUTE_UNKNOWN"


def test_job_status_returns_image_and_video_types_in_request_order(monkeypatch):
    application = async_app()
    runtime = application.state.runtime
    runtime.projects.enqueue_job("image-1", "image", {"prompt": "draw"})
    runtime.projects.enqueue_job("video-1", "omni", {"prompt": "move"})
    monkeypatch.setattr(
        runtime.bridge,
        "ready_connections",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("status touched extension")),
    )

    with TestClient(application) as client:
        response = client.post(
            "/v1/jobs/status", json={"job_ids": ["video-1", "image-1"]}
        )

    assert response.status_code == 200
    assert [(job["id"], job["type"]) for job in response.json()["jobs"]] == [
        ("video-1", "video"), ("image-1", "image"),
    ]

def test_worker_persists_complete_video_and_status_returns_normalized_media(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    phase = "dispatch"

    async def fake_api(_connection_id, **_kwargs):
        if "uploadImage" in _kwargs.get("url", ""):
            return {"status": 200, "data": {"media": {"name": "media/start"}}}
        if phase == "dispatch":
            return {
                "status": 200,
                "data": {"operations": [{"operation": {
                    "name": "operations/video-1", "done": False,
                }}]},
            }
        return {
            "status": 200,
            "data": {"operations": [{"operation": {
                "name": "operations/video-1",
                "done": True,
                "response": {"media": [{
                    "name": "media/video-1",
                    "video": {"generatedVideo": {}},
                }]},
            }}]},
        }

    async def resolve_url(_connection_id, _media_id, *, thumbnail=False):
        kind = "thumbnail" if thumbnail else "video"
        return f"https://flow-content.google/{kind}/signed"

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)
    monkeypatch.setattr(application.state.runtime.bridge, "resolve_media_url", resolve_url)
    with TestClient(application) as client:
        created = client.post(
            "/v1/videos/generations",
            json={
                "type": "image_to_video",
                "project_id": "project-1",
                "prompt": "move",
                "input_images": [{
                    "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                    "mime_type": "image/png",
                }],
            },
        )
        job_id = created.json()["jobs"][0]["id"]
        worker = JobWorker(application.state.runtime)
        asyncio.run(worker.process_queued_jobs())
        phase = "poll"
        asyncio.run(worker.poll_running_jobs())

        status = client.post("/v1/jobs/status", json={"job_ids": [job_id]})
        job = status.json()["jobs"][0]
        assert job["status"] == "complete"
        assert job["media"] == [{
            "id": "media/video-1",
            "type": "video",
            "url": "https://flow-content.google/video/signed",
            "thumbnail_url": "https://flow-content.google/thumbnail/signed",
            "width": None,
            "height": None,
        }]


def test_idempotency_key_returns_same_job_and_rejects_different_request(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    body = {
        "type": "image_to_video",
        "project_id": "project-1",
        "prompt": "move",
        "input_images": [{
            "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
            "mime_type": "image/png",
        }],
    }
    with TestClient(application) as client:
        first = client.post(
            "/v1/videos/generations",
            headers={"Idempotency-Key": "order-1"},
            json=body,
        )
        repeated = client.post(
            "/v1/videos/generations",
            headers={"Idempotency-Key": "order-1"},
            json=body,
        )
        changed = client.post(
            "/v1/videos/generations",
            headers={"Idempotency-Key": "order-1"},
            json={**body, "prompt": "different"},
        )

    assert repeated.json()["jobs"][0]["id"] == first.json()["jobs"][0]["id"]
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_blank_idempotency_key_is_rejected(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    with TestClient(application) as client:
        response = client.post(
            "/v1/videos/generations",
            headers={"Idempotency-Key": "   "},
            json={
                "type": "image_to_video",
                "project_id": "project-1",
                "prompt": "move",
                "input_images": [{
                    "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                    "mime_type": "image/png",
                }],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_INVALID"


def test_video_contract_is_present_in_openapi():
    schema = async_app().openapi()
    create_schema = schema["paths"]["/v1/videos/generations"]["post"]["responses"]["202"]
    status_schema = schema["paths"]["/v1/jobs/status"]["post"]["responses"]["200"]
    assert create_schema["content"]["application/json"]["schema"]["$ref"].endswith("JobsResponse")
    assert status_schema["content"]["application/json"]["schema"]["$ref"].endswith("JobsResponse")
    assert schema["components"]["schemas"]["Job"]["properties"]["type"]["enum"] == [
        "image", "video",
    ]
    assert schema["components"]["schemas"]["Job"]["properties"]["status"]["enum"] == [
        "queued", "running", "complete", "failed",
    ]


def test_worker_marks_terminal_media_failure(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-failed",
        "omni",
        {"type": "omni", "prompt": "move", "reference_media_ids": ["media/ref"]},
        installation_id="installation-1",
        google_project_id="project-1",
    )

    async def dispatch(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {
                "workflows": [{"name": "workflow-1"}],
                "media": [{"name": "media/video-1", "workflowId": "workflow-1"}],
            },
        }

    monkeypatch.setattr(runtime.bridge, "api_request", dispatch)
    worker = JobWorker(runtime)
    asyncio.run(worker.process_queued_jobs())

    async def failed_poll(_connection_id, **_kwargs):
        return {
            "status": 200,
            "data": {"media": [{
                "name": "media/video-1",
                "mediaMetadata": {"mediaStatus": {
                    "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_UNSUCCESSFUL",
                }},
            }]},
        }

    monkeypatch.setattr(runtime.bridge, "api_request", failed_poll)
    asyncio.run(worker.poll_running_jobs())
    failed = runtime.projects.get_job("job-failed")
    assert failed.status == "failed"
    assert failed.error_code == "VIDEO_MEDIA_FAILED"
    with TestClient(application) as client:
        response = client.post("/v1/jobs/status", json={"job_ids": ["job-failed"]})
    assert response.json()["jobs"][0]["error"] == {
        "code": "VIDEO_MEDIA_FAILED",
        "message": "Google Flow video generation failed with status MEDIA_GENERATION_STATUS_UNSUCCESSFUL.",
        "retryable": False,
        "outcome_unknown": False,
    }


def test_deterministic_credit_rejection_fails_job_with_credit_exhausted(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-credit",
        "image_to_video",
        {"type": "image_to_video", "prompt": "move", "start_media_id": "media/start"},
        installation_id="installation-1",
        google_project_id="project-1",
    )

    async def rejected(_connection_id, **_kwargs):
        return {"status": 400, "error": {"message": "Insufficient credits"}}

    monkeypatch.setattr(runtime.bridge, "api_request", rejected)
    asyncio.run(JobWorker(runtime).process_queued_jobs())
    job = runtime.projects.get_job("job-credit")
    assert job.status == "failed"
    assert job.error_code == "CREDIT_EXHAUSTED"


@pytest.mark.parametrize(
    ("generation_type", "payload", "url_suffix", "model_key"),
    [
        (
            "image_to_video",
            {"prompt": "move", "start_media_id": "media/start", "quality": "fast", "aspect_ratio": "VIDEO_ASPECT_RATIO_PORTRAIT"},
            "video:batchAsyncGenerateVideoStartImage",
            "veo_3_1_i2v_s_fast_portrait",
        ),
        (
            "i2v",
            {"prompt": "move", "start_media_id": "media/start", "duration_seconds": 6},
            "video:batchAsyncGenerateVideoStartImage",
            "abra_i2v_6s",
        ),
        (
            "omni",
            {"prompt": "move", "reference_media_ids": ["media/ref"], "duration_seconds": 10},
            "video:batchAsyncGenerateVideoReferenceImages",
            "abra_r2v_10s",
        ),
    ],
)
def test_worker_preserves_video_endpoint_and_model_selection(
    monkeypatch, generation_type, payload, url_suffix, model_key,
):
    application = async_app()
    connect(application, monkeypatch)
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-model", generation_type, {"type": generation_type, **payload},
        installation_id="installation-1", google_project_id="project-1",
    )
    captured = {}

    async def dispatch(_connection_id, **kwargs):
        captured.update(kwargs)
        return {"status": 200, "data": {"operations": [{"operation": {"name": "operations/model"}}]}}

    monkeypatch.setattr(runtime.bridge, "api_request", dispatch)
    asyncio.run(JobWorker(runtime).process_queued_jobs())
    assert captured["url"].endswith(url_suffix)
    assert captured["body"]["requests"][0]["videoModelKey"] == model_key


def test_pending_poll_never_resolves_signed_urls(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-pending", "omni",
        {"type": "omni", "prompt": "move", "reference_media_ids": ["media/ref"]},
        installation_id="installation-1", google_project_id="project-1",
    )

    async def dispatch(_connection_id, **_kwargs):
        return {"status": 200, "data": {"operations": [{"operation": {"name": "operations/pending"}}]}}

    monkeypatch.setattr(runtime.bridge, "api_request", dispatch)
    worker = JobWorker(runtime)
    asyncio.run(worker.process_queued_jobs())

    async def pending(_connection_id, **_kwargs):
        return {"status": 200, "data": {"operations": [{"operation": {"name": "operations/pending", "done": False}}]}}

    async def unexpected_resolve(*_args, **_kwargs):
        raise AssertionError("pending media must not resolve a signed URL")

    monkeypatch.setattr(runtime.bridge, "api_request", pending)
    monkeypatch.setattr(runtime.bridge, "resolve_media_url", unexpected_resolve)
    asyncio.run(worker.poll_running_jobs())
    pending_job = runtime.projects.get_job("job-pending")
    assert pending_job.status == "running"
    assert pending_job.poll_attempts == 1
    assert pending_job.poll_error_count == 0
    assert pending_job.next_poll_at is not None


def test_status_batch_preserves_order_and_does_not_require_extension(monkeypatch):
    application = async_app()
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-1", "omni", {"type": "omni", "prompt": "one"},
        installation_id="installation-1", google_project_id="project-1",
    )
    runtime.projects.enqueue_job(
        "job-2", "omni", {"type": "omni", "prompt": "two"},
        installation_id="installation-1", google_project_id="project-1",
    )
    monkeypatch.setattr(
        runtime.bridge,
        "ready_connections",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("status touched extension")),
    )

    with TestClient(application) as client:
        response = client.post(
            "/v1/jobs/status",
            json={"job_ids": ["job-2", "job-1"]},
        )

    assert response.status_code == 200
    assert [job["id"] for job in response.json()["jobs"]] == ["job-2", "job-1"]
    assert response.json()["metadata"]["counts"] == {
        "queued": 2, "running": 0, "complete": 0, "failed": 0,
    }
    assert response.json()["metadata"]["done"] is False
    assert response.json()["metadata"]["routing_scope"] is None
    assert "x-provider-routing-scope" not in response.headers


def test_uncertain_paid_dispatch_is_terminal_and_not_retryable(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-uncertain",
        "image_to_video",
        {
            "type": "image_to_video",
            "prompt": "move",
            "start_media_id": "media/start",
            "aspect_ratio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
        },
        installation_id="installation-1",
        google_project_id="project-1",
    )

    async def timeout(_connection_id, **_kwargs):
        return {"error": "timeout"}

    monkeypatch.setattr(runtime.bridge, "api_request", timeout)
    asyncio.run(JobWorker(runtime).process_queued_jobs())
    with TestClient(application) as client:
        response = client.post(
            "/v1/jobs/status", json={"job_ids": ["job-uncertain"]}
        )

    error = response.json()["jobs"][0]["error"]
    assert response.json()["jobs"][0]["status"] == "failed"
    assert error["retryable"] is False
    assert error["outcome_unknown"] is True


def test_image_4_slots_and_video_3_slots_concurrency():
    application = async_app()
    runtime = application.state.runtime
    conn = SimpleNamespace(
        id="acc-1",
        installation_id="inst-1",
        max_slots=3,
        max_image_slots=4,
        max_video_slots=3,
        credits=500,
    )

    # Image jobs can reserve up to 4 slots
    for i in range(4):
        assert runtime.can_reserve(conn, credit_cost=0, job_type="image")
        assert runtime.reserve_connection(conn, credit_cost=0, job_type="image")
    # 5th image job is rejected
    assert not runtime.can_reserve(conn, credit_cost=0, job_type="image")
    assert not runtime.reserve_connection(conn, credit_cost=0, job_type="image")

    # Video jobs can independently reserve up to 3 slots
    for i in range(3):
        assert runtime.can_reserve(conn, credit_cost=20, job_type="video")
        assert runtime.reserve_connection(conn, credit_cost=20, job_type="video")
    # 4th video job is rejected
    assert not runtime.can_reserve(conn, credit_cost=20, job_type="video")
    assert not runtime.reserve_connection(conn, credit_cost=20, job_type="video")

    # Releasing 1 image slot allows another image
    runtime.release_connection("acc-1", credit_cost=0, job_type="image")
    assert runtime.can_reserve(conn, credit_cost=0, job_type="image")

    # Releasing 1 video slot allows another video
    runtime.release_connection("acc-1", credit_cost=20, job_type="video")
    assert runtime.can_reserve(conn, credit_cost=20, job_type="video")


def test_multiple_queued_image_jobs_processed_in_parallel(monkeypatch):
    application = async_app()
    connect(application, monkeypatch)
    # Set connection to allow 4 image slots
    conn = application.state.runtime.bridge.ready_connections()[0]
    conn.max_image_slots = 4

    calls = []

    async def fake_api(_connection_id, **kwargs):
        calls.append(kwargs)
        # Yield to event loop to simulate async I/O
        await asyncio.sleep(0.01)
        return {
            "status": 200,
            "data": {"media": [{
                "name": f"media/image-{len(calls)}",
                "image": {
                    "generatedImage": {"fifeUrl": "https://flow-content.google/image"},
                    "dimensions": {"width": 1024, "height": 1024},
                },
            }]},
        }

    monkeypatch.setattr(application.state.runtime.bridge, "api_request", fake_api)

    with TestClient(application) as client:
        job_ids = []
        for i in range(4):
            created = client.post(
                "/v1/images/generations",
                headers={"Idempotency-Key": f"multi-img-{i}"},
                json={"project_id": "project-1", "prompt": f"draw {i}"},
            )
            assert created.status_code == 202
            job_ids.append(created.json()["jobs"][0]["id"])

        assert len(calls) == 0

        # One worker call should process all 4 image jobs in parallel
        asyncio.run(JobWorker(application.state.runtime).process_queued_jobs())
        assert len(calls) == 4

        for jid in job_ids:
            stored = application.state.runtime.projects.get_job(jid)
            assert stored is not None
            assert stored.status == "completed"


def test_video_generation_requires_base64_input_images():
    application = async_app()
    with TestClient(application) as client:
        # Request with start_media_id but missing input_images is rejected with 422
        response = client.post(
            "/v1/videos/generations",
            json={
                "type": "image_to_video",
                "prompt": "animate this image",
                "start_media_id": "media/auto-inferred-start",
                "aspect_ratio": "9:16",
            },
        )
        assert response.status_code == 422

        # Request with input_images is accepted
        response_ok = client.post(
            "/v1/videos/generations",
            json={
                "type": "image_to_video",
                "prompt": "animate this image",
                "input_images": [{
                    "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                    "mime_type": "image/png",
                }],
                "aspect_ratio": "9:16",
            },
        )
        assert response_ok.status_code == 202
        job_id = response_ok.json()["jobs"][0]["id"]
        # Notice that installation_id is None, enabling free multi-account load balancing!
        stored_job = application.state.runtime.projects.get_job(job_id)
        assert stored_job is not None
        assert stored_job.installation_id is None


def test_job_dispatching_status_is_exposed_as_running():
    application = async_app()
    runtime = application.state.runtime
    runtime.projects.enqueue_job(
        "job-dispatch-run",
        "image_to_video",
        {"type": "image_to_video", "prompt": "move"},
    )
    # Claim job so its DB status becomes dispatching
    claimed = runtime.projects.claim_next_queued_job()
    assert claimed is not None
    assert claimed.status == "dispatching"

    with TestClient(application) as client:
        res = client.post("/v1/jobs/status", json={"job_ids": ["job-dispatch-run"]})
        assert res.status_code == 200
        # Public status must be running!
        assert res.json()["jobs"][0]["status"] == "running"


def test_enqueue_wakes_worker_immediately():
    application = async_app()
    runtime = application.state.runtime
    assert runtime.worker is not None
    runtime.worker._wake_event.clear()
    assert not runtime.worker._wake_event.is_set()

    with TestClient(application) as client:
        res = client.post(
            "/v1/videos/generations",
            json={
                "type": "image_to_video",
                "prompt": "animate this image",
                "input_images": [{
                    "image_base64": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
                    "mime_type": "image/png",
                }],
            },
        )
        assert res.status_code == 202
        # Verify that wake_event was set immediately upon enqueue!
        assert runtime.worker._wake_event.is_set()

