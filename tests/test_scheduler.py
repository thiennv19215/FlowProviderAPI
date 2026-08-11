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
