from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_extension_health_reports_safe_durable_queue_aggregates(tmp_path):
    app = create_app(Settings(
        env="test",
        bootstrap_api_key="test",
        project_store_path=str(tmp_path / "projects.db"),
        asset_store_path=str(tmp_path / "assets"),
        job_queue_max_active=10,
        worker_enabled=False,
    ))
    projects = app.state.runtime.projects

    projects.enqueue_job(
        job_id="job_dispatching",
        generation_type="image",
        media_type="image",
        request_payload={"prompt": "dispatch"},
    )
    dispatching = projects.claim_next_queued_job()
    assert dispatching is not None
    assert dispatching.job_id == "job_dispatching"
    assert dispatching.status == "dispatching"

    projects.enqueue_job(
        job_id="job_running",
        generation_type="video",
        media_type="video",
        request_payload={"prompt": "running"},
    )
    running_claim = projects.claim_next_queued_job()
    assert running_claim is not None
    assert running_claim.job_id == "job_running"
    assert projects.update_job_running(
        "job_running",
        operation_name="operations/video-1",
        installation_id="install-1",
        google_project_id="project-1",
        claim_token=running_claim.claim_token,
    ) is True

    projects.enqueue_job(
        job_id="job_queued",
        generation_type="image",
        media_type="image",
        request_payload={"prompt": "queued"},
    )

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == {"queued": 1, "dispatching": 1, "running": 1}
    assert body["active_jobs"] == 3
    assert body["job_queue_capacity"] == 10
    assert body["job_queue_remaining"] == 7

    # Operator telemetry is aggregate only; it must not expose connector identity
    # or per-account financial/session details.
    assert "accounts" not in body
    assert "email" not in body
    assert "credits" not in body
