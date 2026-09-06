import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def backpressure_app(tmp_path, *, limit: int = 1):
    return create_app(Settings(
        env="test",
        project_store_path=str(tmp_path / "projects.db"),
        asset_store_path=str(tmp_path / "assets"),
        job_queue_max_active=limit,
        worker_enabled=False,
    ))


def normalized_image_payload(prompt: str) -> dict:
    return {
        "project_id": None,
        "prompt": prompt,
        "model": "NANO_BANANA_PRO",
        "aspect_ratio": "IMAGE_ASPECT_RATIO_PORTRAIT",
        "reference_media_ids": [],
        "input_images": [],
        "variant_count": 1,
    }


def test_generation_returns_retryable_503_when_active_backlog_is_full(tmp_path):
    application = backpressure_app(tmp_path)
    application.state.runtime.projects.enqueue_job(
        job_id="job_existing",
        generation_type="image",
        media_type="image",
        request_payload=normalized_image_payload("existing"),
    )

    with TestClient(application) as client:
        response = client.post("/v1/images/generations", json={"prompt": "new request"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "10"
    assert response.json()["error"]["code"] == "JOB_QUEUE_FULL"
    assert response.json()["error"]["retryable"] is True
    assert application.state.runtime.durable_active_job_count() == 1


def test_idempotent_replay_bypasses_full_backlog_admission(tmp_path):
    application = backpressure_app(tmp_path)
    application.state.runtime.projects.enqueue_job(
        job_id="job_existing",
        generation_type="image",
        media_type="image",
        request_payload=normalized_image_payload("same request"),
        idempotency_key="image-order-1",
    )

    with TestClient(application) as client:
        response = client.post(
            "/v1/images/generations",
            headers={"Idempotency-Key": "image-order-1"},
            json={"prompt": "same request"},
        )

    assert response.status_code == 202
    assert response.json()["jobs"][0]["id"] == "job_existing"
    assert application.state.runtime.durable_active_job_count() == 1


def test_terminal_work_frees_backlog_capacity(tmp_path):
    application = backpressure_app(tmp_path)
    projects = application.state.runtime.projects
    projects.enqueue_job(
        job_id="job_finished",
        generation_type="image",
        media_type="image",
        request_payload=normalized_image_payload("finished"),
    )
    assert projects.update_job_failed(
        "job_finished",
        "done",
        error_code="TEST_DONE",
        retryable=False,
    ) is True

    with TestClient(application) as client:
        response = client.post("/v1/images/generations", json={"prompt": "new request"})

    assert response.status_code == 202
    assert response.json()["jobs"][0]["status"] == "queued"
    assert application.state.runtime.durable_active_job_count() == 1


@pytest.mark.asyncio
async def test_pending_admission_reservations_prevent_concurrent_overcommit(tmp_path):
    application = backpressure_app(tmp_path)
    runtime = application.state.runtime

    first = await runtime.try_reserve_job_admission(None)
    second = await runtime.try_reserve_job_admission(None)
    await runtime.release_job_admission()
    third = await runtime.try_reserve_job_admission(None)
    await runtime.release_job_admission()

    assert first == (True, True)
    assert second == (False, False)
    assert third == (True, True)
    assert runtime.pending_job_admissions == 0
