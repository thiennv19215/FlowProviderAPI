from __future__ import annotations

import asyncio

from app.providers.base import ProviderDispatch, ProviderError, ProviderMedia, ProviderPollResult
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk import FlowSDK
from app.providers.google_flow.project_registry import ProjectRegistry
from app.providers.google_flow.media_sync import MediaSync

IMAGE_ASPECT={"1:1":"IMAGE_ASPECT_RATIO_SQUARE","16:9":"IMAGE_ASPECT_RATIO_LANDSCAPE","9:16":"IMAGE_ASPECT_RATIO_PORTRAIT"}
VIDEO_ASPECT={"16:9":"VIDEO_ASPECT_RATIO_LANDSCAPE","9:16":"VIDEO_ASPECT_RATIO_PORTRAIT"}
PUBLIC_IMAGE_MODELS={"banana_pro":"NANO_BANANA_PRO","banana_2":"NANO_BANANA_2"}


class GoogleFlowProvider:
    name="google_flow"
    requires_account_pool=True

    def __init__(self, bridge, asset_service):
        self.bridge=bridge;self.projects=ProjectRegistry();self.media_sync=MediaSync(asset_service)

    def _sdk(self, account_id: str): return FlowSDK(BoundFlowClient(self.bridge,account_id))

    async def refresh_video_capacity(self) -> None:
        """Refresh credits before assigning a paid video generation.

        Images remain eligible for a connected Flow account even when credits
        are zero or unknown. Video/omni work, however, is scheduled only after
        a fresh credits lookup confirms enough balance.
        """
        accounts=list(self.bridge.ready_connections())
        if accounts:
            await asyncio.gather(*(self.bridge.refresh_account(conn.id) for conn in accounts),return_exceptions=True)

    async def _context(self, job, db, account_id: str):
        conn=self.bridge.get(account_id)
        if not conn or not conn.ready: raise ProviderError("PROVIDER_ACCOUNT_UNAVAILABLE","The selected Google Flow account is no longer ready.",status_code=503,retryable=True)
        sdk=self._sdk(account_id)
        project_id=job.provider_project_id or await self.projects.get_or_create(db,client_id=job.client_id,account_id=account_id,sdk=sdk)
        job.provider_project_id=project_id;db.commit()
        return conn,sdk,project_id

    async def generate_image(self, *, job, db, account_id: str|None):
        if not account_id: raise ProviderError("PROVIDER_ACCOUNT_UNAVAILABLE","No ready Google Flow account is currently available.",status_code=503,retryable=True)
        conn,sdk,pid=await self._context(job,db,account_id);payload=job.request_payload
        refs=[]
        for asset_id in payload.get("reference_media_ids") or []:
            refs.append(await self.media_sync.ensure_media(db,client_id=job.client_id,asset_id=asset_id,project_id=pid,sdk=sdk))
        job.stage="dispatching";db.commit()
        image_model=PUBLIC_IMAGE_MODELS[payload.get("model","banana_pro")]
        result=await sdk.gen_image(prompt=payload["prompt"],project_id=pid,paygate_tier=conn.paygate_tier,aspect_ratio=IMAGE_ASPECT[payload.get("aspect_ratio","9:16")],ref_media_ids=refs,variant_count=payload.get("output_count",1),image_model=image_model)
        if result.get("error"):
            exc=result.get("exception") or RuntimeError(result["error"]);self.bridge.mark_provider_failure(account_id,result["error"],status_code=getattr(exc,"status_code",None));raise exc
        await self.bridge.refresh_account(account_id)
        return [ProviderMedia(media_id=e.get("media_id"),url=e.get("url"),mime_type="image/png") for e in result.get("media_entries") or []]

    async def dispatch_video(self, *, job, db, account_id: str|None):
        if not account_id: raise ProviderError("PROVIDER_ACCOUNT_UNAVAILABLE","No ready Google Flow account is currently available.",status_code=503,retryable=True)
        conn,sdk,pid=await self._context(job,db,account_id);p=job.request_payload
        start=await self.media_sync.ensure_media(db,client_id=job.client_id,asset_id=p["start_media_id"],project_id=pid,sdk=sdk)
        job.stage="dispatching";db.commit()
        result=await sdk.gen_video(prompt=p["prompt"],project_id=pid,start_media_id=start,aspect_ratio=VIDEO_ASPECT[p.get("aspect_ratio","16:9")],paygate_tier=conn.paygate_tier,video_quality=p.get("quality","lite"))
        if result.get("error"):
            exc=result.get("exception") or RuntimeError(result["error"]);self.bridge.mark_provider_failure(account_id,result["error"],status_code=getattr(exc,"status_code",None));raise exc
        await self.bridge.refresh_account(account_id)
        return ProviderDispatch(operation_ids=result["operation_names"],workflows=result.get("workflows") or [])

    async def dispatch_omni(self, *, job, db, account_id: str|None):
        if not account_id: raise ProviderError("PROVIDER_ACCOUNT_UNAVAILABLE","No ready Google Flow account is currently available.",status_code=503,retryable=True)
        conn,sdk,pid=await self._context(job,db,account_id);p=job.request_payload
        refs=[]
        for asset_id in p.get("reference_media_ids") or []:
            refs.append(await self.media_sync.ensure_media(db,client_id=job.client_id,asset_id=asset_id,project_id=pid,sdk=sdk))
        job.stage="dispatching";db.commit()
        result=await sdk.gen_video_omni(prompt=p["prompt"],project_id=pid,ref_media_ids=refs,duration_s=p.get("duration",8),aspect_ratio=VIDEO_ASPECT[p.get("aspect_ratio","9:16")],paygate_tier=conn.paygate_tier)
        if result.get("error"):
            exc=result.get("exception") or RuntimeError(result["error"]);self.bridge.mark_provider_failure(account_id,result["error"],status_code=getattr(exc,"status_code",None));raise exc
        await self.bridge.refresh_account(account_id)
        return ProviderDispatch(operation_ids=result["operation_names"],workflows=result.get("workflows") or [])

    async def poll_video(self, *, job, db, account_id: str|None, dispatch: ProviderDispatch):
        if not account_id or not job.provider_project_id:return ProviderPollResult(done=False,error="provider_context_missing")
        sdk=self._sdk(account_id)
        result=await sdk.check_async(operation_names=dispatch.operation_ids,project_id=job.provider_project_id,workflows_data=dispatch.workflows)
        if result.get("error"):
            exc=result.get("exception");self.bridge.mark_provider_failure(account_id,str(result["error"]),status_code=getattr(exc,"status_code",None));
            if exc:raise exc
            return ProviderPollResult(done=False,error=result["error"])
        ops=result.get("operations") or []
        failed=next((op.get("error") for op in ops if op.get("error")),None)
        if failed:
            self.bridge.mark_provider_failure(account_id,str(failed));return ProviderPollResult(done=True,error=str(failed))
        if not ops or not all(op.get("done") for op in ops):return ProviderPollResult(done=False)
        outputs=[]
        for op in ops:
            for e in op.get("media_entries") or []:
                outputs.append(ProviderMedia(media_id=e.get("media_id"),url=e.get("url"),thumbnail_url=e.get("thumbnail_url"),mime_type="video/mp4"))
        return ProviderPollResult(done=True,outputs=outputs)
