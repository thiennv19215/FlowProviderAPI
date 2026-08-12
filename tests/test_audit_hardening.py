from __future__ import annotations

import asyncio

import pytest

from app.config import Settings
from app.jobs.scheduler import estimated_credit_cost
from app.providers.base import ProviderDispatch, ProviderMedia, ProviderPollResult
from app.providers.google_flow.client import FlowBridge, resolve_paygate_tier
from app.providers.google_flow.browser_bridge import FlowBridge as BrowserFlowBridge


class DummyWS:
    async def send(self,payload):pass
    async def close(self,*args,**kwargs):pass


def ready_connection(bridge,installation="stable-install"):
    conn=bridge.register(DummyWS(),{"installationId":installation,"runtimeId":"chrome","profileId":"profile"})
    conn.flow_key="bearer";conn.account_email="flow@example.com";conn.paygate_tier="PAYGATE_TIER_ONE";conn.credits=100
    return conn


def test_provider_account_id_survives_extension_reconnect():
    bridge=FlowBridge(flow_api_key="test")
    first=ready_connection(bridge,"install-123")
    first_id=first.id
    second=bridge.register(DummyWS(),{"installationId":"install-123","runtimeId":"chrome","profileId":"profile"})
    assert second.id==first_id=="install-123"


async def test_auth_error_invalidates_ready_account():
    bridge=FlowBridge(flow_api_key="test");ws=DummyWS();conn=bridge.register(ws,{"installationId":"install-auth"})
    conn.flow_key="bearer";conn.account_email="flow@example.com";conn.paygate_tier="PAYGATE_TIER_ONE";conn.credits=100
    assert conn.ready
    await bridge.handle_message({"type":"auth_sync_status","status":"needs_labs_sign_in","reason":"signed out"},ws)
    assert not conn.ready
    assert conn.credits is None
    assert conn.paygate_tier is None


async def test_connection_supplied_flow_api_key_makes_account_ready_without_server_fallback():
    class RecordingBridge(BrowserFlowBridge):
        def __init__(self):
            super().__init__(flow_api_key=None)
            self.request_url=None
            self.refresh_count=0

        async def send_rpc(self,connection_id,rpc_type,params,*,timeout=None):
            self.refresh_count+=1
            self.request_url=params["spec"]["url"]
            return {"data":{"data":{"userPaygateTier":"PAYGATE_TIER_ONE","credits":123,"sku":"test"}}}

    bridge=RecordingBridge();ws=DummyWS();conn=bridge.register(ws,{"installationId":"dynamic-key-install"})
    conn.flow_key="browser_owned";conn.account_email="flow@example.com"
    await bridge.handle_message({"type":"flow_api_key","apiKey":"AIzaDynamicFlowKey1234567890"},ws)
    await asyncio.sleep(0)
    await bridge.handle_message({"type":"flow_api_key","apiKey":"AIzaDynamicFlowKey1234567890"},ws)
    await asyncio.sleep(0)
    assert conn.ready
    assert conn.credits==123
    assert bridge.request_url=="https://aisandbox-pa.googleapis.com/v1/credits?key=AIzaDynamicFlowKey1234567890"
    assert bridge.refresh_count==1


def test_omni_credit_cost_is_duration_aware():
    assert estimated_credit_cost("omni",{"duration":2})==10
    assert estimated_credit_cost("omni",{"duration":4})==15
    assert estimated_credit_cost("omni",{"duration":8})==25
    assert estimated_credit_cost("omni",{"duration":10})==30


def test_freemium_sku_uses_tier_one_when_google_omits_legacy_tier():
    assert resolve_paygate_tier({"sku":"G1_FREEMIUM","credits":50})=="PAYGATE_TIER_ONE"


def test_production_configuration_rejects_dev_shape():
    with pytest.raises(ValueError):
        Settings(env="production",database_url="sqlite:///bad.db",storage_backend="local",public_base_url="http://localhost:8000",flow_api_key=None)


class TerminalVideoProvider:
    name="terminal_video";requires_account_pool=False
    async def generate_image(self,**kwargs):raise AssertionError("not used")
    async def dispatch_video(self,**kwargs):return ProviderDispatch(operation_ids=["op-terminal"])
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):return ProviderPollResult(done=True,error="MEDIA_GENERATION_STATUS_FAILED")


class RecoveringStorageProvider:
    name="recover_storage";requires_account_pool=False
    async def generate_image(self,**kwargs):raise AssertionError("not used")
    async def dispatch_video(self,**kwargs):return ProviderDispatch(operation_ids=["op-storage"])
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):return ProviderPollResult(done=True,outputs=[ProviderMedia(bytes_data=b"video",mime_type="video/mp4")])


def _reference(client,auth):
    created=client.post("/v1/assets/uploads",headers=auth,json={"filename":"start.png","content_type":"image/png","type":"image"}).json();aid=created["asset"]["id"]
    assert client.put(f"/v1/assets/{aid}/content",headers={**auth,"Content-Type":"application/octet-stream"},content=b"start").status_code==204
    return aid


def test_terminal_provider_failure_finishes_job(client,app,auth):
    app.state.runtime.providers.register(TerminalVideoProvider());aid=_reference(client,auth)
    job_id=client.post("/v1/videos/image-to-video",headers=auth,json={"provider":"terminal_video","prompt":"x","input":{"start_asset_id":aid},"workspace":{"key":"terminal"}}).json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once())
    assert asyncio.run(app.state.runtime.worker.run_once())
    body=client.get(f"/v1/jobs/{job_id}",headers=auth).json()
    assert body["status"]=="failed"
    assert body["error"]["code"]=="PROVIDER_TERMINAL_ERROR"


def test_output_storage_failure_returns_to_poll_and_recovers(client,app,auth):
    app.state.runtime.providers.register(RecoveringStorageProvider());aid=_reference(client,auth)
    job_id=client.post("/v1/videos/image-to-video",headers=auth,json={"provider":"recover_storage","prompt":"x","input":{"start_asset_id":aid},"workspace":{"key":"storage-retry"}}).json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once())
    original=app.state.runtime.assets.ingest_provider_media;calls={"n":0}
    async def flaky(*args,**kwargs):
        calls["n"]+=1
        if calls["n"]==1:raise RuntimeError("temporary storage outage")
        return await original(*args,**kwargs)
    app.state.runtime.assets.ingest_provider_media=flaky
    assert asyncio.run(app.state.runtime.worker.run_once())
    mid=client.get(f"/v1/jobs/{job_id}",headers=auth).json();assert mid["status"]=="running"
    assert asyncio.run(app.state.runtime.worker.run_once())
    done=client.get(f"/v1/jobs/{job_id}",headers=auth).json();assert done["status"]=="succeeded"
