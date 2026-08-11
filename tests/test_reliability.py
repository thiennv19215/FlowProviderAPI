from datetime import timedelta, timezone

from app.db.models import ApiClient, GenerationJob, utcnow
from app.jobs.repository import recover_expired
from app.providers.base import ProviderMedia


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


def test_preparation_failure_is_requeued(client, app, auth):
    app.state.runtime.providers.register(PreparationFailureProvider())
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":"prep_fail","workspace":{"key":"retry:prep"}})
    job_id=response.json()["id"]
    import asyncio
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    body=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert body["status"] == "queued"


def test_ambiguous_dispatch_failure_is_not_replayed(client, app, auth):
    app.state.runtime.providers.register(AmbiguousDispatchFailureProvider())
    response=client.post("/v1/images/generations",headers=auth,json={"prompt":"cat","provider":"dispatch_fail","workspace":{"key":"retry:dispatch"}})
    job_id=response.json()["id"]
    import asyncio
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    body=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert body["status"] == "failed"


def test_restart_recovery_resumes_poll_without_redispatch(app):
    with app.state.runtime.session_factory() as db:
        client_row=ApiClient(id="cli_recover",name="Recover",key_prefix="fpa_test",key_hash="0"*64,priority=20,max_concurrent_jobs=5,rate_limit_per_minute=100)
        db.add(client_row);db.commit()
        now=utcnow()
        job=GenerationJob(
            id="job_recover_poll",client_id=client_row.id,kind="video",provider="fake",
            workspace_key="recover:video",status="running",stage="provider_running",priority=20,
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


def test_omni_generation_uses_same_durable_job_contract(client, app, auth):
    upload=client.post("/v1/assets/uploads",headers=auth,json={"filename":"ref.png","content_type":"image/png","type":"image"}).json()
    asset_id=upload["asset"]["id"]
    assert client.put(f"/v1/assets/{asset_id}/content",headers={**auth,"Content-Type":"application/octet-stream"},content=b"ref").status_code==204
    response=client.post("/v1/videos/omni-generations",headers=auth,json={
        "prompt":"dance","provider":"fake","duration":8,"aspect_ratio":"9:16",
        "references":[{"asset_id":asset_id}],"workspace":{"key":"omni:e2e"},
    })
    job_id=response.json()["id"]
    import asyncio
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    body=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert body["status"] == "succeeded"
    assert body["outputs"][0]["type"] == "video"
