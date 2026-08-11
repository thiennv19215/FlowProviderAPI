from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from app.auth.rate_limit import RateLimiter
from app.db.models import ApiClient, GenerationJob, utcnow
from app.providers.base import ProviderDispatch, ProviderPollResult


class StuckProvider:
    name="stuck";requires_account_pool=False
    async def generate_image(self,**kwargs):raise AssertionError("not used")
    async def dispatch_video(self,**kwargs):return ProviderDispatch(operation_ids=["op-stuck"])
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):return ProviderPollResult(done=False)


def _reference(client,auth):
    created=client.post("/v1/assets/uploads",headers=auth,json={"filename":"start.png","content_type":"image/png","type":"image"}).json()
    aid=created["asset"]["id"]
    assert client.put(f"/v1/assets/{aid}/content",headers={**auth,"Content-Type":"application/octet-stream"},content=b"start").status_code==204
    return aid


def test_stuck_provider_operation_hits_deadline(client,app,auth):
    app.state.runtime.providers.register(StuckProvider())
    aid=_reference(client,auth)
    job_id=client.post("/v1/videos/generations",headers=auth,json={"provider":"stuck","prompt":"x","input":{"start_asset_id":aid},"workspace":{"key":"timeout"}}).json()["id"]
    assert asyncio.run(app.state.runtime.worker.run_once())
    with app.state.runtime.session_factory() as db:
        job=db.get(GenerationJob,job_id)
        payload=dict(job.result_payload or {})
        payload["_provider_dispatched_at"]=(utcnow()-timedelta(seconds=app.state.runtime.settings.max_provider_operation_seconds+1)).isoformat()
        job.result_payload=payload;job.next_run_at=utcnow();db.commit()
    assert asyncio.run(app.state.runtime.worker.run_once())
    done=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert done["status"]=="failed"
    assert done["error"]["code"]=="PROVIDER_OPERATION_TIMEOUT"


def test_rate_limit_state_is_shared_between_limiter_instances(app):
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
