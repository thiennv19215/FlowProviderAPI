import asyncio
import base64
import hashlib
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.runtime import build_runtime
from app.workers.job_worker import JobWorker


def test_select_connection_prioritizes_sufficient_credits():
    settings = Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
        worker_enabled=False,
    )
    runtime = build_runtime(settings)

    low_credit_conn = SimpleNamespace(
        id="conn-low",
        installation_id="install-low",
        credits=13,
        connected_at=100.0,
    )
    high_credit_conn = SimpleNamespace(
        id="conn-high",
        installation_id="install-high",
        credits=920,
        connected_at=200.0,
    )

    # Even though low_credit_conn connected earlier (100.0 vs 200.0),
    # high_credit_conn has credits >= 20, so it must be selected first!
    selected = runtime.select_connection([low_credit_conn, high_credit_conn])
    assert selected.id == "conn-high"

    # Also when reversed in input list
    selected = runtime.select_connection([high_credit_conn, low_credit_conn])
    assert selected.id == "conn-high"


def test_worker_auto_failovers_inline_job_when_assigned_account_lacks_credits(monkeypatch):
    settings = Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
        worker_enabled=False,
    )
    app = create_app(settings)
    runtime = app.state.runtime

    low_conn = SimpleNamespace(
        id="conn-low",
        installation_id="install-low",
        account_email="low@test.com",
        credits=13,
        max_slots=3,
        max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=100.0,
    )
    high_conn = SimpleNamespace(
        id="conn-high",
        installation_id="install-high",
        account_email="high@test.com",
        credits=920,
        max_slots=3,
        max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=200.0,
    )

    conns = [low_conn, high_conn]
    monkeypatch.setattr(runtime.bridge, "ready_connections", lambda **_kwargs: conns)
    monkeypatch.setattr(runtime.bridge, "pending_count", lambda _id: 0)
    runtime.projects.remember_project("install-high", "project-high", "High Project")

    # Store an asset in the asset store
    raw_png = b"\x89PNG\r\n\x1a\n" + b"dummy-image-bytes"
    img_b64 = base64.b64encode(raw_png).decode("ascii")
    digest, _, _ = runtime.projects.asset_store.put_base64(img_b64, "image/png")
    runtime.projects.record_asset(digest, "image/png", len(raw_png), "ref.png")

    # Enqueue a video job assigned to install-low (which only has 13 credits, but job needs 25)
    job = runtime.projects.enqueue_job(
        job_id="job_test_failover",
        generation_type="reference_to_video",
        media_type="video",
        request_payload={
            "type": "reference_to_video",
            "prompt": "test prompt",
            "duration_seconds": 8,
            "input_image_hashes": [digest],
        },
        installation_id="install-low",
        google_project_id=None,
    )

    # Mock API call so dispatch succeeds when executed on high_conn
    dispatched_accounts = []

    async def fake_api(connection_id, **kwargs):
        dispatched_accounts.append(connection_id)
        if "uploadImage" in kwargs.get("url", ""):
            return {"status": 200, "data": {"media": {"name": "media/ref"}}}
        return {
            "status": 200,
            "data": {"operations": [{"operation": {"name": "operations/video-1", "done": False}}]},
        }

    monkeypatch.setattr(runtime.bridge, "api_request", fake_api)

    async def fake_trpc(connection_id, **kwargs):
        return {
            "status": 200,
            "data": {
                "result": {
                    "data": {
                        "json": {
                            "result": {
                                "projects": [{"projectId": "proj-managed", "projectInfo": {"name": "Managed Proj"}}],
                                "nextCursor": None,
                            }
                        }
                    }
                }
            },
        }

    monkeypatch.setattr(runtime.bridge, "trpc_request", fake_trpc)

    worker = JobWorker(runtime)
    claimed_job = runtime.projects.claim_next_queued_job()
    asyncio.run(worker._dispatch_job(claimed_job))

    # The job must NOT have failed with INSUFFICIENT_CREDITS!
    # It should have auto-failed over to conn-high!
    updated_job = runtime.projects.get_job("job_test_failover")
    assert updated_job.status != "failed"
    assert "conn-high" in dispatched_accounts


def test_cross_account_media_rehydration_on_worker(monkeypatch):
    settings = Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
        worker_enabled=False,
    )
    app = create_app(settings)
    runtime = app.state.runtime

    low_conn = SimpleNamespace(
        id="conn-low",
        installation_id="install-low",
        account_email="low@test.com",
        credits=13,
        max_slots=3,
        max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=100.0,
    )
    high_conn = SimpleNamespace(
        id="conn-high",
        installation_id="install-high",
        account_email="high@test.com",
        credits=920,
        max_slots=3,
        max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=200.0,
    )

    conns = [low_conn, high_conn]
    monkeypatch.setattr(runtime.bridge, "ready_connections", lambda **_kwargs: conns)
    monkeypatch.setattr(runtime.bridge, "pending_count", lambda _id: 0)

    # Put an image belonging to install-low into DB and asset store
    raw_png = b"\x89PNG\r\n\x1a\n" + b"cross-account-image-bytes"
    img_b64 = base64.b64encode(raw_png).decode("ascii")
    digest, _, _ = runtime.projects.asset_store.put_base64(img_b64, "image/png")
    runtime.projects.put_media(
        "install-low",
        "project-low",
        digest,
        "media-on-low-account",
        "image/png",
        "start.png",
    )

    # Job is created with start_media_id from install-low
    job = runtime.projects.enqueue_job(
        job_id="job_cross_account_rehydrate",
        generation_type="frames_to_video",
        media_type="video",
        request_payload={
            "type": "frames_to_video",
            "prompt": "animate this image",
            "duration_seconds": 8,
            "start_media_id": "media-on-low-account",
        },
        installation_id="install-low",
        google_project_id="project-low",
    )

    uploaded_to_accounts = []
    video_requests = []

    async def fake_api(connection_id, **kwargs):
        if "uploadImage" in kwargs.get("url", ""):
            uploaded_to_accounts.append(connection_id)
            return {"status": 200, "data": {"media": {"name": "media-rehydrated-on-high"}}}
        if "batchAsyncGenerateVideo" in kwargs.get("url", "") or "batchGenerateVideos" in kwargs.get("url", ""):
            video_requests.append((connection_id, kwargs.get("body")))
            return {
                "status": 200,
                "data": {"operations": [{"operation": {"name": "operations/video-rehydrate", "done": False}}]},
            }
        return {"status": 200}

    monkeypatch.setattr(runtime.bridge, "api_request", fake_api)

    async def fake_trpc(connection_id, **kwargs):
        return {
            "status": 200,
            "data": {
                "result": {
                    "data": {
                        "json": {
                            "result": {
                                "projects": [{"projectId": "proj-managed-high", "projectInfo": {"name": "High Managed"}}],
                                "nextCursor": None,
                            }
                        }
                    }
                }
            },
        }

    monkeypatch.setattr(runtime.bridge, "trpc_request", fake_trpc)

    worker = JobWorker(runtime)
    claimed_job = runtime.projects.claim_next_queued_job()
    asyncio.run(worker._dispatch_job(claimed_job))

    # Assert: Image was re-uploaded to conn-high!
    assert "conn-high" in uploaded_to_accounts
    assert len(video_requests) == 1
    conn_used, body = video_requests[0]
    assert conn_used == "conn-high"
    # startImage must use the rehydrated media ID on high account!
    req_item = body["requests"][0]
    assert req_item["startImage"]["mediaId"] == "media-rehydrated-on-high"


def test_only_fails_when_all_connected_accounts_lack_credits(monkeypatch):
    settings = Settings(
        env="test",
        bootstrap_api_key="fpa_test",
        public_base_url="https://provider.test",
        project_store_path=":memory:",
        worker_enabled=False,
    )
    app = create_app(settings)
    runtime = app.state.runtime

    conn1 = SimpleNamespace(
        id="conn-1",
        installation_id="install-1",
        account_email="1@test.com",
        credits=5,
        max_slots=3,
        max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=100.0,
    )
    conn2 = SimpleNamespace(
        id="conn-2",
        installation_id="install-2",
        account_email="2@test.com",
        credits=10,
        max_slots=3,
        max_video_slots=3,
        paygate_tier="PAYGATE_TIER_ONE",
        connected_at=200.0,
    )

    conns = [conn1, conn2]
    monkeypatch.setattr(runtime.bridge, "ready_connections", lambda **_kwargs: conns)
    monkeypatch.setattr(runtime.bridge, "pending_count", lambda _id: 0)

    # Enqueue a video job requiring 20 credits
    job = runtime.projects.enqueue_job(
        job_id="job_all_out_of_credits",
        generation_type="frames_to_video",
        media_type="video",
        request_payload={
            "type": "frames_to_video",
            "prompt": "animate this",
            "duration_seconds": 8,
            "start_media_id": "dummy",
        },
        installation_id=None,
        google_project_id=None,
    )

    worker = JobWorker(runtime)
    claimed_job = runtime.projects.claim_next_queued_job()
    asyncio.run(worker._dispatch_job(claimed_job))

    updated_job = runtime.projects.get_job("job_all_out_of_credits")
    assert updated_job.status == "failed"
    assert updated_job.error_code == "INSUFFICIENT_CREDITS"
    assert "All 2 connected accounts have insufficient credits" in updated_job.error_message

