from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.assets.service import AssetService
from app.db.models import MediaAsset
from app.providers.base import ProviderDispatch, ProviderMedia, ProviderPollResult
from conftest import upload_media


class PersistentPollErrorProvider:
    name="persistent_poll_error";requires_account_pool=False
    async def generate_image(self,**kwargs):raise AssertionError("not used")
    async def dispatch_video(self,**kwargs):return ProviderDispatch(operation_ids=["op-poll-error"])
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):return ProviderPollResult(done=False,error="temporary provider polling outage")


def _reference(client,auth,data=b"start"):
    return upload_media(client,auth,filename="start.png",data=data,content_type="image/png")


def test_persistent_poll_errors_eventually_fail_job(client,app,auth):
    app.state.runtime.providers.register(PersistentPollErrorProvider())
    previous=app.state.runtime.settings.max_consecutive_poll_errors
    app.state.runtime.settings.max_consecutive_poll_errors=2
    try:
        aid=_reference(client,auth)
        job_id=client.post("/v1/videos/image-to-video",headers=auth,json={"provider":"persistent_poll_error","prompt":"x","start_media_id":aid,"workspace":{"key":"poll:bounded"}}).json()["task_id"]
        assert asyncio.run(app.state.runtime.worker.run_once())
        assert asyncio.run(app.state.runtime.worker.run_once())
        mid=client.get(f"/v1/tasks/{job_id}",headers=auth).json()
        assert mid["status"]=="running"
        assert asyncio.run(app.state.runtime.worker.run_once())
        done=client.get(f"/v1/tasks/{job_id}",headers=auth).json()
        assert done["status"]=="failed"
        assert done["error"]["code"]=="PROVIDER_POLL_RETRIES_EXHAUSTED"
    finally:
        app.state.runtime.settings.max_consecutive_poll_errors=previous


def test_provider_output_bytes_are_bounded(app):
    previous=app.state.runtime.settings.max_provider_output_bytes
    app.state.runtime.settings.max_provider_output_bytes=4
    try:
        with app.state.runtime.session_factory() as db:
            with pytest.raises(ValueError,match="provider_output_too_large"):
                asyncio.run(app.state.runtime.assets.ingest_provider_media(db,client_id="client",job_id="job",provider="fake",media=ProviderMedia(bytes_data=b"12345",mime_type="image/png"),asset_type="image"))
    finally:
        app.state.runtime.settings.max_provider_output_bytes=previous


class PendingStorage:
    def __init__(self,meta):self.meta=meta;self.deleted=[]
    async def stat(self,key):return dict(self.meta)
    async def delete(self,key):self.deleted.append(key)


class NoCommitDB:
    def commit(self):raise AssertionError("commit should not happen for rejected object")
    def refresh(self,obj):raise AssertionError("refresh should not happen")


def test_pending_upload_size_mismatch_deletes_object():
    storage=PendingStorage({"size_bytes":6,"content_type":"image/png"})
    settings=SimpleNamespace(max_upload_bytes=100)
    service=AssetService(storage,settings)
    asset=SimpleNamespace(storage_key="clients/c/a.png",size_bytes=5,mime_type="image/png",status="pending")
    with pytest.raises(ValueError,match="uploaded_size_mismatch"):
        asyncio.run(service.complete_pending(NoCommitDB(),asset))
    assert storage.deleted==[asset.storage_key]


def test_rejected_pending_content_type_deletes_object():
    storage=PendingStorage({"size_bytes":5,"content_type":"video/mp4"})
    settings=SimpleNamespace(max_upload_bytes=100)
    service=AssetService(storage,settings)
    asset=SimpleNamespace(storage_key="clients/c/a.png",size_bytes=5,mime_type="image/png",status="pending")
    with pytest.raises(ValueError,match="uploaded_content_type_mismatch"):
        asyncio.run(service.complete_pending(NoCommitDB(),asset))
    assert storage.deleted==[asset.storage_key]


def test_flow_reference_memory_cap_is_enforced_before_read(client,app,auth):
    aid=_reference(client,auth,b"12345")
    previous=app.state.runtime.settings.max_reference_in_memory_bytes
    app.state.runtime.settings.max_reference_in_memory_bytes=4
    class NeverUpload:
        async def upload_image(self,*args,**kwargs):raise AssertionError("oversized reference must not be loaded or uploaded")
    try:
        with app.state.runtime.session_factory() as db:
            with pytest.raises(ValueError,match="asset_too_large_for_flow_upload"):
                asyncio.run(app.state.runtime.providers.get("google_flow").media_sync.ensure_media(db,client_id=db.scalar(select(MediaAsset).where(MediaAsset.id==aid)).client_id,asset_id=aid,project_id="p",sdk=NeverUpload()))
    finally:
        app.state.runtime.settings.max_reference_in_memory_bytes=previous


def test_extension_gateway_accepts_connection_without_token(client):
    with client.websocket_connect("/api/extensions/ws",subprotocols=["flow-provider-v7"]) as ws:
        assert ws.accepted_subprotocol=="flow-provider-v7"
        ws.send_json({"type":"extension_ready","protocolVersion":7,"installationId":"install-open-test","runtimeId":"chrome","profileId":"p"})
        assert client.get("/v1/health").json()["extension_connected"] is True


def test_extension_gateway_rejects_overlong_installation_id(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/extensions/ws") as ws:
            ws.send_json({"type":"extension_ready","protocolVersion":7,"installationId":"x"*121})
            ws.receive_json()
