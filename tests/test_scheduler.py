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
