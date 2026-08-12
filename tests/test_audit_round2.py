from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.assets.service import AssetService
from app.providers.base import ProviderError, ProviderMedia
from app.providers.google_flow.client import FlowBridge
from conftest import upload_media


class DummyWS:
    async def send(self,payload):pass
    async def close(self,*args,**kwargs):pass


class UnavailablePoolProvider:
    name="unavailable_pool";requires_account_pool=True
    async def generate_image(self,**kwargs):raise AssertionError("scheduler must reject before dispatch")
    async def dispatch_video(self,**kwargs):raise AssertionError("not used")
    async def dispatch_omni(self,**kwargs):raise AssertionError("not used")
    async def poll_video(self,**kwargs):raise AssertionError("not used")


def test_video_start_asset_must_be_image(client,auth):
    aid=upload_media(client,auth,filename="clip.mp4",data=b"video",content_type="video/mp4")
    response=client.post("/v1/videos/image-to-video",headers=auth,json={"prompt":"move","provider":"fake","start_media_id":aid,"workspace":{"key":"bad-ref"}})
    assert response.status_code==422
    assert response.json()["error"]["code"]=="INVALID_MEDIA_TYPE"


def test_direct_upload_enforces_stream_limit(client,app,auth):
    previous=app.state.runtime.settings.max_upload_bytes
    app.state.runtime.settings.max_upload_bytes=4
    try:
        response=client.post("/v1/media",headers=auth,files={"file":("tiny.png",b"12345","image/png")})
        assert response.status_code==413
        assert response.json()["error"]["code"]=="MEDIA_TOO_LARGE"
    finally:
        app.state.runtime.settings.max_upload_bytes=previous


class RecordingDB:
    def __init__(self):self.events=[]
    def rollback(self):self.events.append("rollback")
    def commit(self):self.events.append("commit")


def test_worker_error_handler_rolls_back_before_commit(app):
    db=RecordingDB();job=SimpleNamespace(stage="preparing",attempt_count=1,status="running",error_code=None,error_message=None,next_run_at=None,lease_owner="w",lease_expires_at=object(),completed_at=None)
    asyncio.run(app.state.runtime.worker._handle_error(db,job,RuntimeError("db trouble")))
    assert db.events[:2]==["rollback","commit"]
    assert job.status=="queued"


def test_worker_defers_when_provider_capacity_is_temporarily_unavailable(app):
    db=RecordingDB();job=SimpleNamespace(stage="preparing",attempt_count=5,status="running",provider_account_id="account",error_code=None,error_message=None,result_payload=None,next_run_at=None,lease_owner="w",lease_expires_at=object(),completed_at=None)
    asyncio.run(app.state.runtime.worker._handle_error(db,job,ProviderError("PROVIDER_ACCOUNT_UNAVAILABLE","No credits.",status_code=503,retryable=True)))
    assert db.events[:2]==["rollback","commit"]
    assert job.status=="queued"
    assert job.stage=="queued"
    assert job.provider_account_id is None
    assert job.error_code=="PROVIDER_ACCOUNT_UNAVAILABLE"


def test_capacity_shortage_keeps_task_queued_for_a_later_account(client,app,auth):
    app.state.runtime.providers.register(UnavailablePoolProvider())
    task_id=client.post("/v1/images/generations",headers=auth,json={"prompt":"wait for an account","provider":"unavailable_pool"}).json()["task_id"]
    assert asyncio.run(app.state.runtime.worker.run_once()) is True
    task=client.get(f"/v1/status/{task_id}",headers=auth).json()
    assert task["status"]=="queued"
    assert task["error"]["code"]=="PROVIDER_ACCOUNT_UNAVAILABLE"
    assert task["error"]["status_code"]==503
    assert task["error"]["retryable"] is True


class FailingCommitDB:
    def __init__(self):self.added=None;self.rolled_back=False
    def add(self,obj):self.added=obj
    def commit(self):raise RuntimeError("database unavailable")
    def rollback(self):self.rolled_back=True
    def refresh(self,obj):raise AssertionError("not reached")


class RecordingStorage:
    def __init__(self):self.put=[];self.deleted=[]
    async def put_bytes(self,key,data,content_type):self.put.append(key)
    async def put_file(self,key,path,content_type):self.put.append(key)
    async def delete(self,key):self.deleted.append(key)


def _service(storage):
    settings=SimpleNamespace(env="test",max_upload_bytes=1024,max_reference_bytes=1024,public_base_url="http://test")
    return AssetService(storage,settings)


def test_provider_asset_storage_is_cleaned_if_db_commit_fails():
    storage=RecordingStorage();service=_service(storage);db=FailingCommitDB()
    with pytest.raises(RuntimeError,match="database unavailable"):
        asyncio.run(service.ingest_provider_media(db,client_id="client",job_id="job",provider="fake",media=ProviderMedia(bytes_data=b"output",mime_type="image/png"),asset_type="image"))
    assert db.rolled_back is True
    assert storage.put and storage.deleted==storage.put


def test_direct_upload_storage_is_cleaned_if_db_commit_fails():
    storage=RecordingStorage();service=_service(storage);db=FailingCommitDB();asset=SimpleNamespace(storage_key="clients/c/asset.png",mime_type="image/png",size_bytes=None,status="pending")
    with pytest.raises(RuntimeError,match="database unavailable"):
        asyncio.run(service.write_upload_file(db,asset,Path("unused"),6))
    assert db.rolled_back is True
    assert storage.deleted==[asset.storage_key]


def test_suspect_extension_is_removed_from_ready_pool_until_message_arrives():
    bridge=FlowBridge(flow_api_key="test");ws=DummyWS();conn=bridge.register(ws,{"installationId":"heartbeat-install"})
    conn.flow_key="token";conn.account_email="flow@example.test";conn.paygate_tier="PAYGATE_TIER_ONE";conn.credits=100
    assert conn in bridge.ready_connections()
    bridge.mark_suspect(conn.id)
    assert conn.health_status=="suspect"
    assert conn not in bridge.ready_connections()
    asyncio.run(bridge.handle_message({"type":"pong"},ws))
    assert conn.health_status=="online"
    assert conn in bridge.ready_connections()
