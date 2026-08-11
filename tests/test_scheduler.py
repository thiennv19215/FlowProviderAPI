from app.providers.google_flow.client import FlowBridge
from app.jobs.scheduler import GlobalScheduler


class DummyWS:
    async def send(self, payload): pass
    async def close(self, *args, **kwargs): pass


def test_scheduler_prefers_less_loaded_ready_account(app):
    bridge=FlowBridge(flow_api_key="x")
    a=bridge.register(DummyWS(),{"installationId":"install-a-123456","runtimeId":"chrome","profileId":"a"})
    b=bridge.register(DummyWS(),{"installationId":"install-b-123456","runtimeId":"chrome","profileId":"b"})
    for conn,credits in ((a,100),(b,200)):
        conn.flow_key="bearer";conn.account_email=f"{conn.profile_id}@example.com";conn.paygate_tier="PAYGATE_TIER_ONE";conn.credits=credits;conn.sku="test"
    scheduler=GlobalScheduler(bridge)
    with app.state.runtime.session_factory() as db:
        assert scheduler.choose_account(db,kind="video") == b.id

import asyncio
from app.providers.base import ProviderMedia


class SlowProvider:
    name = "slow"
    requires_account_pool = False

    def __init__(self):
        self.active = 0
        self.peak = 0
        self.gate = asyncio.Event()

    async def generate_image(self, *, job, db, account_id):
        self.active += 1
        self.peak = max(self.peak, self.active)
        if self.active >= 2:
            self.gate.set()
        await asyncio.wait_for(self.gate.wait(), timeout=2)
        await asyncio.sleep(0.01)
        self.active -= 1
        return [ProviderMedia(bytes_data=b"concurrent", mime_type="image/png")]

    async def dispatch_video(self, **kwargs):
        raise AssertionError("not used")

    async def dispatch_omni(self, **kwargs):
        raise AssertionError("not used")

    async def poll_video(self, **kwargs):
        raise AssertionError("not used")


async def test_worker_lanes_can_process_jobs_concurrently(client, app, auth):
    provider = SlowProvider()
    app.state.runtime.providers.register(provider)
    payload = {"prompt": "cat", "provider": "slow", "workspace": {"key": "concurrency:test"}}
    assert client.post("/v1/images/generations", headers=auth, json=payload).status_code == 202
    payload["workspace"] = {"key": "concurrency:test:2"}
    assert client.post("/v1/images/generations", headers=auth, json=payload).status_code == 202

    worked = await asyncio.gather(
        app.state.runtime.worker.run_once(0),
        app.state.runtime.worker.run_once(1),
    )
    assert worked == [True, True]
    assert provider.peak == 2


def test_saturated_high_priority_client_does_not_starve_other_client(app):
    from app.db.models import ApiClient, GenerationJob, utcnow
    from app.jobs.repository import claim_next

    with app.state.runtime.session_factory() as db:
        high=ApiClient(id="cli_high",name="High",key_prefix="high",key_hash="1"*64,priority=100,max_concurrent_jobs=1,rate_limit_per_minute=100)
        low=ApiClient(id="cli_low",name="Low",key_prefix="low",key_hash="2"*64,priority=10,max_concurrent_jobs=1,rate_limit_per_minute=100)
        db.add_all([high,low]);db.commit()
        running=GenerationJob(id="job_high_running",client_id=high.id,kind="image",provider="fake",workspace_key="fair:running",status="running",stage="dispatching",priority=100,request_payload={"prompt":"x"},next_run_at=utcnow(),attempt_count=1)
        db.add(running)
        for i in range(40):
            db.add(GenerationJob(id=f"job_high_{i}",client_id=high.id,kind="image",provider="fake",workspace_key=f"fair:high:{i}",status="queued",stage="queued",priority=100,request_payload={"prompt":"x"},next_run_at=utcnow()))
        db.add(GenerationJob(id="job_low_1",client_id=low.id,kind="image",provider="fake",workspace_key="fair:low",status="queued",stage="queued",priority=10,request_payload={"prompt":"x"},next_run_at=utcnow()))
        db.commit()
        claimed=claim_next(db,worker_id="fairness-test",lease_seconds=30)
        assert claimed is not None
        assert claimed.client_id == low.id
