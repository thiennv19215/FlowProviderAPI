from __future__ import annotations

from app.config import Settings
from app.runtime import build_runtime
from app.runtime_store import RuntimeProjectStore


def _runtime(tmp_path, *, dispatch_lease_seconds: int = 900):
    return build_runtime(Settings(
        env="test",
        project_store_path=str(tmp_path / "projects.db"),
        asset_store_path=str(tmp_path / "assets"),
        worker_enabled=False,
        worker_dispatch_lease_seconds=dispatch_lease_seconds,
    ))


def _age_dispatch(store, job_id: str, seconds: int) -> None:
    with store._lock:
        store._db().execute(
            """
            UPDATE provider_jobs
            SET status = 'dispatching',
                created_at = datetime('now', ?),
                claimed_at = datetime('now', ?),
                claim_token = 'claim-test'
            WHERE job_id = ?
            """,
            (f"-{seconds} seconds", f"-{seconds} seconds", job_id),
        )
        store._db().commit()


def _age_queued(store, job_id: str, seconds: int) -> None:
    with store._lock:
        store._db().execute(
            "UPDATE provider_jobs SET created_at = datetime('now', ?) WHERE job_id = ?",
            (f"-{seconds} seconds", job_id),
        )
        store._db().commit()


def test_runtime_uses_dispatch_safe_store(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        assert isinstance(runtime.projects, RuntimeProjectStore)
        assert runtime.projects.dispatch_lease_seconds == 900
    finally:
        runtime.projects.close()


def test_status_read_does_not_timeout_live_image_dispatch(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.projects
    try:
        store.enqueue_job("job-image", generation_type="image", media_type="image", request_payload={})
        _age_dispatch(store, "job-image", 130)

        job = store.get_job("job-image", image_timeout_seconds=120)

        assert job is not None
        assert job.status == "dispatching"
        assert job.error_code is None
        assert job.outcome_unknown is False
    finally:
        store.close()


def test_status_read_does_not_apply_video_poll_timeout_before_dispatch_lease(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.projects
    try:
        store.enqueue_job(
            "job-video",
            generation_type="reference_to_video",
            media_type="video",
            request_payload={},
        )
        _age_dispatch(store, "job-video", 700)

        job = store.get_job("job-video", video_running_timeout_seconds=600)

        assert job is not None
        assert job.status == "dispatching"
        assert job.error_code is None
    finally:
        store.close()


def test_abandoned_dispatch_uses_outcome_unknown_after_dispatch_lease(tmp_path):
    runtime = _runtime(tmp_path, dispatch_lease_seconds=900)
    store = runtime.projects
    try:
        store.enqueue_job("job-abandoned", generation_type="image", media_type="image", request_payload={})
        _age_dispatch(store, "job-abandoned", 901)

        job = store.get_job("job-abandoned", image_timeout_seconds=120)

        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "IMAGE_DISPATCH_OUTCOME_UNKNOWN"
        assert job.outcome_unknown is True
        assert job.error_retryable is False
    finally:
        store.close()


def test_periodic_expiry_skips_dispatching_but_still_expires_safe_states(tmp_path):
    runtime = _runtime(tmp_path)
    store = runtime.projects
    try:
        store.enqueue_job("job-queued-image", generation_type="image", media_type="image", request_payload={})
        store.enqueue_job("job-dispatch-image", generation_type="image", media_type="image", request_payload={})
        _age_queued(store, "job-queued-image", 130)
        _age_dispatch(store, "job-dispatch-image", 130)

        expired = store.fail_expired_jobs(
            image_timeout_seconds=120,
            video_queue_timeout_seconds=180,
            video_running_timeout_seconds=600,
        )

        assert expired == 1
        queued = store.get_job("job-queued-image")
        dispatching = store.get_job("job-dispatch-image")
        assert queued is not None and queued.status == "failed"
        assert queued.error_code == "IMAGE_TIMEOUT"
        assert dispatching is not None and dispatching.status == "dispatching"
    finally:
        store.close()
