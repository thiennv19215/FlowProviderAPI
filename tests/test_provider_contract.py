import asyncio

import pytest

from app.providers.base import (
    ProviderCapabilities,
    ProviderContext,
    ProviderDispatch,
    ProviderMedia,
    ProviderPollResult,
)
from app.providers.registry import ProviderRegistry


class ModernProvider:
    name="modern"
    capabilities=ProviderCapabilities(image=True,video=True)

    def __init__(self):
        self.prepared=[]

    async def prepare(self,*,job,db):
        self.prepared.append(job.id)
        return ProviderContext()

    async def generate_image(self,*,job,db,context):
        assert isinstance(context,ProviderContext)
        return [ProviderMedia(bytes_data=b"modern-image",mime_type="image/png")]

    async def dispatch_video(self,*,job,db,context):
        assert isinstance(context,ProviderContext)
        return ProviderDispatch(operation_ids=["modern-operation"])

    async def poll(self,*,job,db,context,dispatch):
        assert dispatch.operation_ids==["modern-operation"]
        return ProviderPollResult(done=True,outputs=[ProviderMedia(bytes_data=b"modern-video",mime_type="video/mp4")])


def test_registry_rejects_duplicate_provider_names():
    registry=ProviderRegistry();registry.register(ModernProvider())
    with pytest.raises(ValueError,match="already registered"):
        registry.register(ModernProvider())


def test_registry_exposes_declared_capabilities():
    registry=ProviderRegistry();registry.register(ModernProvider())
    assert registry.supports("modern","image")
    assert registry.supports("modern","video")
    assert not registry.supports("modern","omni")


def test_modern_provider_image_and_video_run_end_to_end(client,app,auth):
    provider=ModernProvider();app.state.runtime.providers.register(provider)

    image=client.post("/v1/images/generations",headers=auth,json={"provider":"modern","prompt":"image"})
    assert image.status_code==202
    asyncio.run(app.state.runtime.worker.run_once())
    image_result=client.get(f"/v1/status/{image.json()['task_id']}",headers=auth).json()
    assert image_result["status"]=="done"
    assert len(image_result["outputs"])==1

    media=client.post("/v1/media",headers=auth,files={"file":("start.png",b"start","image/png")})
    video=client.post("/v1/videos/image-to-video",headers=auth,json={"provider":"modern","prompt":"video","start_media_id":media.json()["media_id"]})
    assert video.status_code==202
    asyncio.run(app.state.runtime.worker.run_once())
    asyncio.run(app.state.runtime.worker.run_once())
    video_result=client.get(f"/v1/status/{video.json()['task_id']}",headers=auth).json()
    assert video_result["status"]=="done"
    assert len(video_result["outputs"])==1
    assert len(provider.prepared)==2


def test_unsupported_capability_is_rejected_before_job_creation(client,app,auth):
    app.state.runtime.providers.register(ModernProvider())
    media=client.post("/v1/media",headers=auth,files={"file":("ref.png",b"ref","image/png")})
    response=client.post("/v1/videos/omni-generations",headers=auth,json={"provider":"modern","prompt":"omni","reference_media_ids":[media.json()["media_id"]]})
    assert response.status_code==400
    assert response.json()["error"]["code"]=="UNSUPPORTED_PROVIDER_CAPABILITY"
