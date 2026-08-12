from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from sqlalchemy import select

from app.auth.rate_limit import RateLimiter
from app.db.models import ApiClient, GenerationJob, utcnow
from app.providers.base import ProviderDispatch, ProviderPollResult
from conftest import upload_media


class StuckProvider:
    name="stuck";requires_account_pool=False
    async def generate_image(self,**kwargs):raise AssertionError("not used")
    async def dispatch_video(self,**kwargs):return ProviderDispatch(operation_ids=["op-stuck"])
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):return ProviderPollResult(done=False)


def _reference(client,auth):
    return upload_media(client,auth,filename="start.png",data=b"start",content_type="image/png")


def test_stuck_provider_operation_hits_deadline(client,app,auth):
    app.state.runtime.providers.register(StuckProvider())
    aid=_reference(client,auth)
    job_id=client.post("/v1/videos/image-to-video",headers=auth,json={"provider":"stuck","prompt":"x","start_media_id":aid,"workspace":{"key":"timeout"}}).json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once())
    with app.state.runtime.session_factory() as db:
        job=db.get(GenerationJob,job_id)
        metadata=json.loads(job.provider_operation_id or "{}")
        metadata["dispatched_at"]=(utcnow()-timedelta(seconds=app.state.runtime.settings.max_provider_operation_seconds+1)).isoformat()
        job.provider_operation_id=json.dumps(metadata)
        # Make the poll unambiguously due. Using exactly utcnow() here can be
        # flaky across DB timestamp precision/timezone normalization in CI.
        job.next_run_at=utcnow()-timedelta(seconds=1)
        job.lease_owner=None
        job.lease_expires_at=None
        db.commit()
    assert asyncio.run(app.state.runtime.worker.run_once())
    done=client.get(f"/v1/tasks/{job_id}",headers=auth).json()
    assert done["status"]=="failed"
    assert done["error"]["code"]=="PROVIDER_OPERATION_TIMEOUT"


def test_rate_limit_state_is_shared_between_limiter_instances(app,client):
    limiter_a=RateLimiter();limiter_b=RateLimiter()
    with app.state.runtime.session_factory() as db:
        client_id=db.scalar(select(ApiClient.id))
    with app.state.runtime.session_factory() as db:
        assert limiter_a.hit(db,client_id,2)[0] is True
    with app.state.runtime.session_factory() as db:
        assert limiter_b.hit(db,client_id,2)[0] is True
    with app.state.runtime.session_factory() as db:
        allowed,remaining,_=limiter_a.hit(db,client_id,2)
        assert allowed is False
        assert remaining==0
