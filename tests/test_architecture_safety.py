"""Regression tests for scheduling, lifecycle, retention and process ownership."""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.process_lease import SingleProcessLease
from app.projects import ProjectStore
from app.workers.job_worker import JobWorker


@pytest.fixture
def store(tmp_path):
    value = ProjectStore(str(tmp_path / "jobs.db"), asset_store_path=str(tmp_path / "assets"))
    yield value
    value.close()


async def test_blocked_batch_does_not_starve_ready_video(store, monkeypatch):
    for i in range(4):
        store.enqueue_job(f"blocked-{i}", "image", {"prompt": "draw", "_routing_locked": True}, installation_id="offline")
    store.enqueue_job("ready", "frames_to_video", {"prompt": "move"})
    runtime = SimpleNamespace(projects=store, settings=SimpleNamespace(), inline_images={},
                              bridge=SimpleNamespace(ready_connections=lambda: []))
    worker = JobWorker(runtime)
    original_dispatch = worker._dispatch_job
    reached = []

    async def dispatch(job):
        if job.job_id == "ready":
            reached.append(job.job_id)
        else:
            await original_dispatch(job)

    monkeypatch.setattr(worker, "_dispatch_job", dispatch)
    await worker.process_queued_jobs(max_concurrent=4)
    # Simulate the next worker tick AFTER retry delay has elapsed: fairness
    # must not depend on the next batch running within one second.
    store._db().execute("UPDATE provider_jobs SET created_at = datetime('now', '-10 seconds')")
    store._db().execute("UPDATE provider_jobs SET next_dispatch_at = datetime('now', '-1 second') WHERE job_id LIKE 'blocked-%'")
    store._db().commit()
    await worker.process_queued_jobs(max_concurrent=4)
    assert reached == ["ready"]
    assert all(store.get_job(f"blocked-{i}").status == "queued" for i in range(4))


def test_old_video_precedes_new_images(store):
    store.enqueue_job("video", "frames_to_video", {})
    store._db().execute("UPDATE provider_jobs SET created_at = datetime('now', '-10 seconds')")
    store._db().commit()
    store.enqueue_job("image", "image", {})
    assert store.claim_next_queued_job().job_id == "video"


def test_dispatch_retry_survives_reopen_and_respects_claim_token(store):
    store.enqueue_job("retry", "image", {})
    claimed = store.claim_next_queued_job()
    assert not store.release_job_claim("retry", "wrong", delay_seconds=60)
    assert store.release_job_claim("retry", claimed.claim_token, delay_seconds=60)
    store.close()
    assert store.claim_next_queued_job() is None
    store._db().execute("UPDATE provider_jobs SET next_dispatch_at = datetime('now', '-1 second')")
    store._db().commit()
    assert store.claim_next_queued_job().job_id == "retry"


@pytest.mark.parametrize("kind", ["image", "frames_to_video"])
def test_base_store_has_the_same_dispatch_lease_policy(store, kind):
    store.enqueue_job("dispatch", kind, {})
    store.claim_next_queued_job()
    store._db().execute("UPDATE provider_jobs SET created_at = datetime('now', '-700 seconds'), claimed_at = datetime('now', '-700 seconds')")
    store._db().commit()
    assert store.fail_expired_jobs() == 0
    assert store.get_job("dispatch").status == "dispatching"
    store._db().execute("UPDATE provider_jobs SET claimed_at = datetime('now', '-901 seconds')")
    store._db().commit()
    job = store.get_job("dispatch")
    assert job.status == "failed"
    assert job.outcome_unknown
    assert not job.error_retryable


def test_retention_is_opt_in_bounded_and_preserves_paid_replay(store):
    for name in ["a", "b", "keyed", "unknown"]:
        store.enqueue_job(name, "frames_to_video", {"prompt": name}, idempotency_key="keep-key" if name == "keyed" else None)
        store.update_job_failed(name, "terminal", outcome_unknown=name == "unknown")
    store._db().execute("UPDATE provider_jobs SET completed_at = datetime('now', '-100 days')")
    store._db().commit()
    assert store.prune()["jobs"] == 0
    assert store.prune(job_retention_days=30, batch_size=1)["jobs"] == 1
    assert store.prune(job_retention_days=30, batch_size=1)["jobs"] == 1
    assert store.prune(job_retention_days=30)["jobs"] == 0
    assert store.get_job_by_idempotency_key("keep-key").job_id == "keyed"
    assert store.get_job("unknown").outcome_unknown


@pytest.mark.parametrize("reference_kind", ["hash", "media_id"])
def test_asset_gc_rotates_batches_and_rechecks_references(store, reference_kind):
    digests = []
    for i in range(3):
        digest, _, size = store.asset_store.put_bytes(f"asset-{i}".encode(), "image/png")
        store.record_asset(digest, "image/png", size, f"{i}.png")
        digests.append(digest)
    store._db().execute("UPDATE provider_assets SET orphaned_at = datetime('now', '-40 days')")
    store._db().commit()
    store.put_media("owner", "project", digests[0], "media/ref", "image/png", "ref.png")
    payload = {"input_image_hashes": [digests[0]]} if reference_kind == "hash" else {"reference_media_ids": ["media/ref"]}
    store.enqueue_job("active", "image", payload)
    removed = 0
    for _ in range(5):
        counts = store.prune(batch_size=1)
        assert counts["assets"] <= 1
        removed += counts["assets"]
    assert removed == 2
    assert store.asset_store.path_for(digests[0]) is not None


async def test_maintenance_runs_periodically_without_restart(store, monkeypatch):
    runtime = SimpleNamespace(projects=store, settings=SimpleNamespace(maintenance_interval_seconds=60))
    worker = JobWorker(runtime)
    calls = []
    monkeypatch.setattr(store, "prune", lambda **kwargs: calls.append(kwargs) or {})
    await worker.run_maintenance()
    await worker.run_maintenance()
    assert len(calls) == 1
    worker._last_maintenance_at -= 61
    await worker.run_maintenance()
    assert len(calls) == 2


def test_process_lease_rejects_second_owner_and_can_be_reacquired(tmp_path):
    first = SingleProcessLease(str(tmp_path / "jobs.db"))
    second = SingleProcessLease(str(tmp_path / "jobs.db"))
    first.acquire()
    try:
        with pytest.raises(RuntimeError, match="one process"):
            second.acquire()
    finally:
        first.release()
    second.acquire()
    second.release()


def test_production_lifespan_enforces_single_owner(tmp_path):
    settings = Settings(env="production", bootstrap_api_key="business-secret", extension_api_key="connector-secret",
                        public_base_url="https://example.test", worker_enabled=False,
                        project_store_path=str(tmp_path / "jobs.db"), asset_store_path=str(tmp_path / "assets"))
    with TestClient(create_app(settings)):
        with pytest.raises(RuntimeError, match="one process"):
            with TestClient(create_app(settings)):
                pytest.fail("Second runtime was allowed to start")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200


def test_worker_does_not_import_http_router():
    tree = ast.parse(Path("app/workers/job_worker.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.ImportFrom) and node.module == "app.api.generations" for node in ast.walk(tree))


@pytest.mark.parametrize("ingestion", ["generic", "character", "upload"])
async def test_reupload_registration_is_atomic_with_gc(store, monkeypatch, ingestion):
    from app.api import generations
    from app.api.characters import _persist_inline_images
    from app.api.schemas import ImageUploadRequest, InlineImageInput
    from app.services.flow import _persist_inline_assets

    image = InlineImageInput(image_base64="cmV1cGxvYWQ=", mime_type="image/png")
    digest, _, _ = store.persist_asset(image.image_base64, image.mime_type, image.file_name)
    store._db().execute("UPDATE provider_assets SET orphaned_at = datetime('now', '-40 days')")
    store._db().commit()
    bytes_written, lock_probed = Event(), Event()
    observed = {}
    original_put = store.asset_store.put_base64

    def put(*args, **kwargs):
        result = original_put(*args, **kwargs)
        bytes_written.set()
        assert lock_probed.wait(5), "GC thread did not probe the upload window"
        return result

    def concurrent_gc():
        assert bytes_written.wait(5), "Upload did not reach byte persistence"
        acquired = store._lock.acquire(blocking=False)
        observed["upload_holds_gc_lock"] = not acquired
        if acquired:
            store._lock.release()
        lock_probed.set()
        return store.prune()

    monkeypatch.setattr(store.asset_store, "put_base64", put)
    runtime = SimpleNamespace(projects=store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        sweep = pool.submit(concurrent_gc)
        if ingestion == "generic":
            assert _persist_inline_assets(runtime, [image]) == [digest]
        elif ingestion == "character":
            assert _persist_inline_images(runtime, [image]) == [digest]
        else:
            def stop_before_upstream(*args, **kwargs):
                raise RuntimeError("stop before upstream")
            monkeypatch.setattr(generations, "_connection", stop_before_upstream)
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)))
            with pytest.raises(RuntimeError, match="stop before upstream"):
                await generations.upload_image(
                    ImageUploadRequest(image_base64=image.image_base64, mime_type=image.mime_type),
                    request, routing_scope=None,
                )
        assert sweep.result(timeout=5)["assets"] == 0
    assert observed["upload_holds_gc_lock"]
    store.enqueue_job("reupload", "image", {"input_image_hashes": [digest]})
    assert store.asset_store.read(digest) is not None
    assert store.get_job("reupload").status == "queued"


@pytest.mark.parametrize("status", ["queued", "dispatching", "running"])
@pytest.mark.parametrize("field", ["reference_media_ids", "start_media_id", "end_media_id"])
def test_gc_preserves_expired_media_mapping_for_active_job(store, status, field):
    digest, _, _ = store.persist_asset("cmVmZXJlbmNl", "image/png", "ref.png")
    store.put_media("owner", "project", digest, "media/ref", "image/png", "ref.png")
    store.put_media("other", "project", digest, "media/unused", "image/png", "unused.png")
    value = ["media/ref"] if field == "reference_media_ids" else "media/ref"
    store.enqueue_job("active-reference", "frames_to_video", {field: value})
    store._db().execute("UPDATE provider_jobs SET status = ?", (status,))
    store._db().execute("UPDATE provider_media SET last_used_at = datetime('now', '-91 days')")
    store._db().execute("UPDATE provider_assets SET orphaned_at = datetime('now', '-40 days')")
    store._db().commit()
    removed = 0
    for _ in range(5):
        counts = store.prune(batch_size=1)
        assert counts["media"] <= 1
        assert counts["assets"] == 0
        removed += counts["media"]
    assert removed == 1  # Protected first candidate does not block later cleanup.
    assert store._db().execute("SELECT google_media_id FROM provider_media").fetchone()[0] == "media/ref"
    assert store.asset_store.read(digest) is not None
    store.update_job_failed("active-reference", "done")
    assert sum(store.prune(batch_size=1)["media"] for _ in range(3)) == 1


def test_reupload_restores_source_when_gc_finishes_first(store):
    digest, _, _ = store.persist_asset("cmV1cGxvYWQ=", "image/png", "ref.png")
    store._db().execute("UPDATE provider_assets SET orphaned_at = datetime('now', '-40 days')")
    store._db().commit()
    assert store.prune()["assets"] == 1
    assert store.asset_store.read(digest) is None
    restored, _, _ = store.persist_asset("cmV1cGxvYWQ=", "image/png", "ref.png")
    assert restored == digest
    assert store.prune()["assets"] == 0
    assert store.asset_store.read(digest) is not None
