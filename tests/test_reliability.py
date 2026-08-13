import asyncio
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import ApiClient, GenerationJob, MediaAsset, utcnow
from app.jobs.repository import recover_expired
from app.providers.base import ProviderMedia
from conftest import upload_media


class PreparationFailureProvider:
    name = "prep_fail"
    requires_account_pool = False

    async def generate_image(self, *, job, db, account_id):
        raise RuntimeError("temporary_preparation_failure")

    async def dispatch_video(self, **kwargs): raise AssertionError("not used")
    async def dispatch_omni(self, **kwargs): raise AssertionError("not used")
    async def poll_video(self, **kwargs): raise AssertionError("not used")


class AmbiguousDispatchFailureProvider(PreparationFailureProvider):
    name = "dispatch_fail"

    async def generate_image(self, *, job, db, account_id):
        job.stage = "dispatching"
        db.commit()
        raise RuntimeError("connection_lost_after_dispatch")


def _mock_output_fetch(app):
    original = app.state.runtime.assets._external_to_temp_file

    async def fake_fetch(url, limit):
        payload = f"owned:{url}".encode()
        assert len(payload) <= limit
        with tempfile.NamedTemporaryFile(prefix="provider-output-test-", delete=False) as tmp:
            tmp.write(payload)
            path = Path(tmp.name)
        return path, len(payload), "a" * 64, "image/png"

    app.state.runtime.assets._external_to_temp_file = fake_fetch
    return original


def test_preparation_failure_is_requeued(client, app, auth):
    app.state.runtime.providers.register(PreparationFailureProvider())
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":"prep_fail"})
    job_id=response.json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    body=client.get(f"/v1/status/{job_id}",headers=auth).json()
    assert body["status"] == "queued"


def test_ambiguous_dispatch_failure_is_not_replayed(client, app, auth):
    app.state.runtime.providers.register(AmbiguousDispatchFailureProvider())
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":"dispatch_fail"})
    job_id=response.json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    body=client.get(f"/v1/status/{job_id}",headers=auth).json()
    assert body["status"] == "failed"


def test_restart_recovery_resumes_poll_without_redispatch(app):
    with app.state.runtime.session_factory() as db:
        client_row=ApiClient(id="cli_recover",name="Recover",key_prefix="fpa_test",key_hash="0"*64,priority=20,max_concurrent_jobs=5,rate_limit_per_minute=100)
        db.add(client_row);db.commit()
        now=utcnow()
        job=GenerationJob(
            id="job_recover_poll",client_id=client_row.id,kind="video",provider="fake",
            status="running",stage="provider_running",priority=20,
            request_payload={"prompt":"x"},provider_operation_id='{"operation_ids":["op_1"],"workflows":[]}',
            next_run_at=now+timedelta(minutes=5),lease_owner="dead-worker",
            lease_expires_at=now-timedelta(seconds=1),attempt_count=1,
        )
        db.add(job);db.commit()
        assert recover_expired(db) == 1
        db.refresh(job)
        assert job.status == "running"
        assert job.stage == "provider_running"
        assert job.provider_operation_id
        assert job.lease_owner is None
        recovered_at=job.next_run_at if job.next_run_at.tzinfo else job.next_run_at.replace(tzinfo=timezone.utc)
        assert recovered_at <= utcnow()


def test_restart_recovery_resumes_partial_direct_output_registration(app):
    direct_outputs=[
        {"media_id":"flow-output-1","url":"https://lh3.googleusercontent.com/output-1.png","mime_type":"image/png","width":None,"height":None,"duration":None},
        {"media_id":"flow-output-2","url":"https://lh3.googleusercontent.com/output-2.png","mime_type":"image/png","width":None,"height":None,"duration":None},
    ]
    original_fetch = _mock_output_fetch(app)
    try:
        with app.state.runtime.session_factory() as db:
            client_row=ApiClient(id="cli_output_recover",name="Output Recover",key_prefix="fpa_output",key_hash="1"*64,priority=20,max_concurrent_jobs=5,rate_limit_per_minute=100)
            now=utcnow()
            job=GenerationJob(
                id="job_recover_outputs",client_id=client_row.id,kind="image",provider="fake",
                status="running",stage="storing_outputs",priority=20,
                request_payload={"prompt":"x"},result_payload={"_provider_outputs":direct_outputs,"asset_ids":[]},
                provider_project_id="flow-project-recover",next_run_at=now+timedelta(minutes=5),
                lease_owner="dead-worker",lease_expires_at=now-timedelta(seconds=1),attempt_count=1,
            )
            db.add_all([client_row,job]);db.commit()
            asyncio.run(app.state.runtime.assets.ingest_provider_media(
                db,
                client_id=client_row.id,
                job_id=job.id,
                provider="fake",
                media=ProviderMedia(**direct_outputs[0]),
                asset_type="image",
                provider_project_id=job.provider_project_id,
            ))
            assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.source_job_id==job.id))==1
            assert recover_expired(db)==1

        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        with app.state.runtime.session_factory() as db:
            job=db.get(GenerationJob,"job_recover_outputs")
            assert job.status=="succeeded"
            assert len(job.result_payload["asset_ids"])==2
            assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.source_job_id==job.id))==2
    finally:
        app.state.runtime.assets._external_to_temp_file = original_fetch


def test_recoverable_output_registration_error_retries_without_generation(app):
    direct_output={"media_id":"flow-retry-1","url":"https://lh3.googleusercontent.com/retry-1.png","mime_type":"image/png","width":None,"height":None,"duration":None}
    with app.state.runtime.session_factory() as db:
        client_row=ApiClient(id="cli_output_retry",name="Output Retry",key_prefix="fpa_retry",key_hash="2"*64,priority=20,max_concurrent_jobs=5,rate_limit_per_minute=100)
        job=GenerationJob(
            id="job_retry_outputs",client_id=client_row.id,kind="image",provider="fake",
            status="running",stage="storing_outputs",priority=20,
            request_payload={"prompt":"x"},result_payload={"_provider_outputs":[direct_output]},
            provider_project_id="flow-project-retry",next_run_at=utcnow(),attempt_count=1,
        )
        db.add_all([client_row,job]);db.commit()

    original_fetch = _mock_output_fetch(app)
    original=app.state.runtime.assets.ingest_provider_media
    calls={"count":0}
    async def fail_once(*args,**kwargs):
        calls["count"]+=1
        if calls["count"]==1:raise RuntimeError("temporary_metadata_failure")
        return await original(*args,**kwargs)
    app.state.runtime.assets.ingest_provider_media=fail_once
    try:
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
        with app.state.runtime.session_factory() as db:
            job=db.get(GenerationJob,"job_retry_outputs")
            assert job.status=="running"
            assert job.stage=="storing_outputs"
            assert job.error_code=="OUTPUT_REGISTRATION_ERROR"
            job.next_run_at=utcnow();db.commit()
        assert asyncio.run(app.state.runtime.worker.run_once()) is True
    finally:
        app.state.runtime.assets.ingest_provider_media=original
        app.state.runtime.assets._external_to_temp_file = original_fetch

    with app.state.runtime.session_factory() as db:
        job=db.get(GenerationJob,"job_retry_outputs")
        assert job.status=="succeeded"
        assert len(job.result_payload["asset_ids"])==1
        assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.source_job_id==job.id))==1


def test_omni_generation_uses_same_durable_job_contract(client, app, auth):
    asset_id=upload_media(client,auth,filename="ref.png",data=b"ref",content_type="image/png")
    response=client.post("/v1/videos/omni-generations",headers=auth,json={
        "prompt":"dance","provider":"fake","duration":8,"aspect_ratio":"9:16",
        "reference_media_ids":[asset_id],
    })
    job_id=response.json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    body=client.get(f"/v1/status/{job_id}",headers=auth).json()
    assert body["status"] == "done"
    assert body["outputs"][0]["type"] == "video"
