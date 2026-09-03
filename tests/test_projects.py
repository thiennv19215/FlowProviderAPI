import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from app.projects import ProjectStore


def test_operation_route_schema_migrates_existing_database():
    path = Path(f".test-run-project-store-migration-{uuid4().hex}.db")
    try:
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE provider_operations (
                operation_name TEXT PRIMARY KEY,
                installation_id TEXT NOT NULL,
                google_project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE provider_media (
                installation_id TEXT NOT NULL,
                google_project_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                google_media_id TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                file_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (installation_id, google_project_id, content_sha256)
            )
            """
        )
        connection.execute(
            "INSERT INTO provider_operations (operation_name, installation_id, google_project_id) VALUES (?, ?, ?)",
            ("operations/legacy", "installation-1", "projects/one"),
        )
        connection.commit()
        connection.close()

        store = ProjectStore(str(path))
        route = store.get_operation("operations/legacy")
        assert route is not None
        assert route.route_kind == "operation"
        assert route.poll_name == "operations/legacy"
        store.put_media(
            "installation-1", "projects/one", "sha", "media/one", "image/png", "one.png",
            {"media": {"name": "media/one"}}, 201, {"x-upload-id": "one"},
        )
        media = store.get_media("installation-1", "projects/one", "sha")
        assert media is not None
        assert media.response_status == 201
        assert media.response_headers == {"x-upload-id": "one"}
        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_project_mapping_survives_store_reopen():
    path = Path(f".test-run-project-store-{uuid4().hex}.db")
    try:
        first = ProjectStore(str(path))
        first.put("installation-1", "projects/one", "FlowProvider")
        first.close()

        second = ProjectStore(str(path))
        assert second.check() is True
        project = second.get("installation-1")
        assert project is not None
        assert project.google_project_id == "projects/one"
        assert second.installation_for_project("projects/one") == "installation-1"
        second.put_media(
            "installation-1", "projects/one", "sha-one", "media/one", "image/png", "one.png"
        )
        cached = second.get_media("installation-1", "projects/one", "sha-one")
        assert cached is not None
        assert cached.google_media_id == "media/one"
        assert second.get_media("installation-2", "projects/one", "sha-one") is None
        assert second.get_media("installation-1", "projects/two", "sha-one") is None
        second.invalidate_media("installation-1", "projects/one", "sha-one")
        assert second.get_media("installation-1", "projects/one", "sha-one") is None
        second.put_operation(
            "workflows/one", "installation-1", "projects/one", "media", "media/primary"
        )
        operation = second.get_operation("workflows/one")
        assert operation is not None
        assert operation.google_project_id == "projects/one"
        assert operation.route_kind == "media"
        assert operation.poll_name == "media/primary"
        second.invalidate("installation-1")
        assert second.get_operation("workflows/one") is None
        assert second.get("installation-1") is None
        second.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_job_lifecycle_in_project_store():
    path = Path(f".test-run-jobs-{uuid4().hex}.db")
    try:
        store = ProjectStore(str(path))
        job = store.enqueue_job("job-1", "omni", {"prompt": "hello", "duration_seconds": 8})
        assert job.job_id == "job-1"
        assert job.status == "queued"
        assert job.request_payload == {"prompt": "hello", "duration_seconds": 8}

        claimed = store.claim_next_queued_job()
        assert claimed is not None
        assert claimed.job_id == "job-1"

        store.update_job_running(
            "job-1",
            operation_name="op-1",
            installation_id="install-1",
            google_project_id="proj-1",
            poll_name="poll-1",
        )
        running = store.get_job("job-1")
        assert running is not None
        assert running.status == "running"
        assert running.operation_name == "op-1"
        assert running.poll_name == "poll-1"

        # Search by operation name or poll name
        assert store.get_job_by_operation("op-1") is not None
        assert store.get_job_by_operation("poll-1") is not None

        # Complete job
        store.update_job_completed("job-1", {"downloadUrl": "https://flow-content.google/video/1"})
        completed = store.get_job("job-1")
        assert completed is not None
        assert completed.status == "completed"
        assert completed.result_data == {"downloadUrl": "https://flow-content.google/video/1"}

        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_legacy_job_type_column_migrates_to_generation_type():
    path = Path(f".test-run-job-type-migration-{uuid4().hex}.db")
    try:
        store = ProjectStore(str(path))
        store.enqueue_job("legacy-video", "omni", {"prompt": "move"})
        store.close()

        connection = sqlite3.connect(path)
        connection.execute(
            "ALTER TABLE provider_jobs RENAME COLUMN generation_type TO job_type"
        )
        connection.commit()
        connection.close()

        migrated = ProjectStore(str(path))
        job = migrated.get_job("legacy-video")
        columns = {
            row["name"]
            for row in migrated._db().execute("PRAGMA table_info(provider_jobs)")
        }
        assert job is not None
        assert job.media_type == "video"
        assert job.generation_type == "omni"
        assert "generation_type" in columns
        assert "job_type" not in columns
        migrated.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_claim_next_queued_job_is_atomic_across_workers():
    path = Path(f".test-run-job-claim-{uuid4().hex}.db")
    store = None
    try:
        store = ProjectStore(str(path))
        store.enqueue_job("job-1", "omni", {"prompt": "hello"})
        store.close()

        def claim_job():
            worker_store = ProjectStore(str(path))
            try:
                job = worker_store.claim_next_queued_job()
                return (job.job_id, job.claim_token) if job else None
            finally:
                worker_store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _index: claim_job(), range(2)))

        winners = [item for item in claims if item is not None]
        assert [item[0] for item in winners] == ["job-1"]
        assert claims.count(None) == 1
        store = ProjectStore(str(path))
        assert store.release_job_claim("job-1", winners[0][1])
        assert store.claim_next_queued_job() is not None
        store._db().execute(
            "UPDATE provider_jobs SET claimed_at = datetime('now', '-1 hour') WHERE job_id = ?",
            ("job-1",),
        )
        store._db().commit()
        assert store.claim_next_queued_job() is None
        assert store.fail_abandoned_dispatches() == 1
        assert store.get_job("job-1").status == "failed"
        store.close()
        store = None
    finally:
        if store is not None:
            store.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_image_jobs_are_distinct_and_claimed_before_video_jobs():
    store = ProjectStore(":memory:")
    try:
        video = store.enqueue_job("video-job", "omni", {"prompt": "move"})
        image = store.enqueue_job("image-job", "image", {"prompt": "draw"})

        assert video.media_type == "video"
        assert image.media_type == "image"
        claimed = store.claim_next_queued_job()
        assert claimed is not None
        assert claimed.job_id == "image-job"
        assert claimed.media_type == "image"
    finally:
        store.close()


def test_stale_worker_cannot_release_or_transition_new_claim():
    path = Path(f".test-run-job-claim-owner-{uuid4().hex}.db")
    store = None
    try:
        store = ProjectStore(str(path))
        store.enqueue_job("job-1", "omni", {"prompt": "hello"})
        old_claim = store.claim_next_queued_job()
        assert old_claim is not None and old_claim.claim_token
        store._db().execute(
            "UPDATE provider_jobs SET claimed_at = datetime('now', '-1 hour') WHERE job_id = ?",
            ("job-1",),
        )
        store._db().commit()
        assert store.claim_next_queued_job() is None
        assert not store.release_job_claim("job-1", "not-the-owner")
        assert not store.update_job_running(
            "job-1",
            operation_name="operations/stale",
            installation_id="account-stale",
            google_project_id="project-stale",
            claim_token="not-the-owner",
        )
        assert not store.update_job_failed("job-1", "stale failure", "not-the-owner")
        assert store.fail_abandoned_dispatches() == 1
        current = store.get_job("job-1")
        assert current is not None and current.status == "failed"
        assert "outcome is unknown" in current.error_message
        store.close()
        store = None
    finally:
        if store is not None:
            store.close()
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_live_dispatch_claim_is_not_failed_by_another_worker_start():
    store = ProjectStore(":memory:")
    store.enqueue_job("job-live", "omni", {"prompt": "hello"})
    claimed = store.claim_next_queued_job()
    assert claimed is not None and claimed.status == "dispatching"

    assert store.fail_abandoned_dispatches(900) == 0
    current = store.get_job("job-live")
    assert current is not None and current.status == "dispatching"


def test_expired_running_job_has_structured_terminal_error():
    store = ProjectStore(":memory:")
    store.enqueue_job("job-stale", "omni", {"prompt": "hello"})
    claimed = store.claim_next_queued_job()
    assert claimed is not None
    assert store.update_job_running(
        "job-stale",
        operation_name="operations/stale",
        installation_id="account-1",
        google_project_id="project-1",
        claim_token=claimed.claim_token,
    )
    store._db().execute(
        "UPDATE provider_jobs SET running_at = datetime('now', '-2 hours') WHERE job_id = ?",
        ("job-stale",),
    )
    store._db().commit()

    assert store.fail_expired_running_jobs(3600) == 1
    expired = store.get_job("job-stale")
    assert expired is not None and expired.status == "failed"
    assert expired.error_code == "VIDEO_POLL_TIMEOUT"
    assert expired.outcome_unknown is True


def test_poll_schedule_is_persisted_and_only_due_jobs_are_returned():
    store = ProjectStore(":memory:")
    store.enqueue_job("job-poll", "omni", {"prompt": "hello"})
    claimed = store.claim_next_queued_job()
    assert claimed is not None
    assert store.update_job_running(
        "job-poll",
        operation_name="operations/poll",
        installation_id="account-1",
        google_project_id="project-1",
        claim_token=claimed.claim_token,
    )
    assert [job.job_id for job in store.list_running_jobs()] == ["job-poll"]

    assert store.schedule_job_poll("job-poll", 60, error_message="temporary disconnect")
    scheduled = store.get_job("job-poll")
    assert scheduled is not None
    assert scheduled.poll_attempts == 1
    assert scheduled.poll_error_count == 1
    assert scheduled.last_poll_error == "temporary disconnect"
    assert scheduled.next_poll_at is not None
    assert store.list_running_jobs() == []

    store._db().execute(
        "UPDATE provider_jobs SET next_poll_at = datetime('now', '-1 second') WHERE job_id = ?",
        ("job-poll",),
    )
    store._db().commit()
    assert [job.job_id for job in store.list_running_jobs()] == ["job-poll"]


def test_due_running_poll_is_claimed_once_across_workers():
    path = Path(f".test-run-poll-claim-{uuid4().hex}.db")
    try:
        setup = ProjectStore(str(path))
        setup.enqueue_job("job-poll", "omni", {"prompt": "hello"})
        queued_claim = setup.claim_next_queued_job()
        assert queued_claim is not None
        assert setup.update_job_running(
            "job-poll",
            operation_name="operations/poll",
            installation_id="account-1",
            google_project_id="project-1",
            claim_token=queued_claim.claim_token,
        )
        setup.close()

        def claim_poll():
            worker_store = ProjectStore(str(path))
            try:
                return [job.job_id for job in worker_store.claim_due_running_jobs(lease_seconds=60)]
            finally:
                worker_store.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(lambda _index: claim_poll(), range(2)))
        assert sorted(claims, key=len) == [[], ["job-poll"]]
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_fail_expired_jobs_all_types():
    store = ProjectStore(":memory:")
    # 1. Image job that exceeded timeout
    store.enqueue_job("img-stale", "image", {"prompt": "portrait"}, media_type="image")
    store._db().execute(
        "UPDATE provider_jobs SET created_at = datetime('now', '-3 minutes') WHERE job_id = 'img-stale'"
    )
    # 2. Video job stuck in queue past queue timeout
    store.enqueue_job("vid-queue-stale", "frames_to_video", {"prompt": "anim"}, media_type="video")
    store._db().execute(
        "UPDATE provider_jobs SET created_at = datetime('now', '-5 minutes') WHERE job_id = 'vid-queue-stale'"
    )
    # 3. Video job running past running timeout
    store.enqueue_job("vid-run-stale", "frames_to_video", {"prompt": "anim"}, media_type="video")
    store._db().execute(
        "UPDATE provider_jobs SET status = 'running', running_at = datetime('now', '-15 minutes') WHERE job_id = 'vid-run-stale'"
    )
    store._db().commit()

    failed_count = store.fail_expired_jobs(
        image_timeout_seconds=120,
        video_queue_timeout_seconds=180,
        video_running_timeout_seconds=600,
    )
    assert failed_count == 3

    img = store.get_job("img-stale")
    assert img is not None and img.status == "failed"
    assert img.error_code == "IMAGE_TIMEOUT"

    vid_q = store.get_job("vid-queue-stale")
    assert vid_q is not None and vid_q.status == "failed"
    assert vid_q.error_code == "QUEUE_TIMEOUT"

    vid_r = store.get_job("vid-run-stale")
    assert vid_r is not None and vid_r.status == "failed"
    assert vid_r.error_code == "VIDEO_POLL_TIMEOUT"


def test_get_job_immediate_timeout():
    store = ProjectStore(":memory:")
    store.enqueue_job("img-instant", "image", {"prompt": "portrait"}, media_type="image")
    store._db().execute(
        "UPDATE provider_jobs SET created_at = datetime('now', '-5 minutes') WHERE job_id = 'img-instant'"
    )
    store._db().commit()

    # Querying get_job immediately catches and fails the job
    job = store.get_job("img-instant", image_timeout_seconds=120)
    assert job is not None
    assert job.status == "failed"
    assert job.error_code == "IMAGE_TIMEOUT"

