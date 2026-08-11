from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy import select

from app.db.models import GenerationJob, MediaAsset, utcnow
from app.jobs import repository
from app.providers.base import ProviderDispatch

logger=logging.getLogger(__name__)


class JobWorker:
    def __init__(self, runtime):
        self.runtime=runtime
        self._tasks: list[asyncio.Task] = []
        self._stop=asyncio.Event()

    async def start(self):
        self._stop.clear()
        self._tasks=[asyncio.create_task(self.loop(lane),name=f"flow-provider-worker-{lane}") for lane in range(self.runtime.settings.worker_concurrency)]

    async def stop(self):
        self._stop.set();tasks=list(self._tasks);self._tasks=[]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def loop(self,lane:int):
        while not self._stop.is_set():
            try:worked=await self.run_once(lane)
            except asyncio.CancelledError:raise
            except Exception:
                logger.exception("worker lane failed lane=%s",lane);worked=False
            if not worked:await asyncio.sleep(self.runtime.settings.worker_poll_seconds)

    async def run_once(self,lane:int=0):
        db=self.runtime.session_factory();worker_id=f"{self.runtime.settings.worker_id}:{lane}"
        try:
            job=repository.due_poll(db,worker_id=worker_id,lease_seconds=self.runtime.settings.lease_seconds)
            if not job:job=repository.claim_next(db,worker_id=worker_id,lease_seconds=self.runtime.settings.lease_seconds)
            if not job:return False
            await self.process(db,job);return True
        finally:db.close()

    async def process(self,db,job:GenerationJob):
        if job.cancel_requested:return self._finish_cancel(db,job)
        provider=self.runtime.providers.get(job.provider);job_id=job.id
        try:
            if job.stage=="provider_running":
                await self._poll(db,job,provider);return
            account_id=None
            if provider.requires_account_pool:
                account_id=self.runtime.scheduler.choose_account(
                    db,
                    kind=job.kind,
                    payload=job.request_payload,
                    client_id=job.client_id,
                    workspace_key=job.workspace_key,
                    provider=job.provider,
                )
                job.provider_account_id=account_id;db.commit()
            if job.kind=="image":
                outputs=await provider.generate_image(job=job,db=db,account_id=account_id)
                await self._store_outputs(db,job,outputs,"image");return
            dispatch=await (provider.dispatch_video(job=job,db=db,account_id=account_id) if job.kind=="video" else provider.dispatch_omni(job=job,db=db,account_id=account_id))
            job.provider_operation_id=json.dumps({"operation_ids":dispatch.operation_ids,"workflows":dispatch.workflows})
            job.stage="provider_running";job.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);job.lease_owner=None;job.lease_expires_at=None;db.commit()
        except Exception as exc:
            db.rollback();fresh=db.get(GenerationJob,job_id)
            if fresh:await self._handle_error(db,fresh,exc)
            else:raise

    async def _poll(self,db,job,provider):
        if job.cancel_requested:return self._finish_cancel(db,job)
        job_id=job.id
        try:
            raw=json.loads(job.provider_operation_id or "{}")
            dispatch=ProviderDispatch(operation_ids=raw.get("operation_ids") or [],workflows=raw.get("workflows") or [])
            result=await provider.poll_video(job=job,db=db,account_id=job.provider_account_id,dispatch=dispatch)
            if result.error and result.done:
                job.status="failed";job.stage="completed";job.error_code="PROVIDER_TERMINAL_ERROR";job.error_message=result.error[:1000];job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None;db.commit();return
            if result.error or not result.done:
                job.error_code="PROVIDER_POLL_ERROR" if result.error else None;job.error_message=result.error[:1000] if result.error else None
                job.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);job.lease_owner=None;job.lease_expires_at=None;db.commit();return
            await self._store_outputs(db,job,result.outputs,"video")
        except Exception as exc:
            db.rollback();fresh=db.get(GenerationJob,job_id)
            if not fresh:raise
            fresh.stage="provider_running";fresh.error_code="PROVIDER_POLL_ERROR";fresh.error_message=str(exc)[:1000]
            fresh.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);fresh.lease_owner=None;fresh.lease_expires_at=None;db.commit()

    async def _store_outputs(self,db,job,outputs,asset_type):
        job.stage="storing_outputs";db.commit()
        asset_ids=list((job.result_payload or {}).get("asset_ids") or [])
        existing=list(db.scalars(select(MediaAsset).where(MediaAsset.client_id==job.client_id,MediaAsset.source_job_id==job.id).order_by(MediaAsset.created_at.asc())))
        if not asset_ids and existing:asset_ids=[a.id for a in existing]
        start_index=len(asset_ids)
        for media in list(outputs)[start_index:]:
            asset=await self.runtime.assets.ingest_provider_media(db,client_id=job.client_id,job_id=job.id,provider=job.provider,media=media,asset_type=asset_type)
            asset_ids=[*asset_ids,asset.id];job.result_payload={"asset_ids":list(asset_ids)};db.commit()
        if not asset_ids:raise RuntimeError("provider_returned_no_outputs")
        job.status="succeeded";job.stage="completed";job.result_payload={"asset_ids":list(asset_ids)};job.completed_at=utcnow();job.error_code=None;job.error_message=None;job.lease_owner=None;job.lease_expires_at=None;db.commit()

    async def _handle_error(self,db,job,exc):
        db.rollback();message=str(exc);safe_to_retry=job.stage in {"preparing"} and job.attempt_count<self.runtime.settings.max_attempts_before_dispatch
        if safe_to_retry:
            job.status="queued";job.stage="queued";job.error_code="PROVIDER_UNAVAILABLE";job.error_message=message[:1000];job.next_run_at=utcnow()+timedelta(seconds=min(30,2**job.attempt_count));job.lease_owner=None;job.lease_expires_at=None
        else:
            job.status="failed";job.stage="completed";job.error_code="PROVIDER_ERROR";job.error_message=message[:1000];job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None
        db.commit()

    def _finish_cancel(self,db,job):
        job.status="canceled";job.stage="completed";job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None;db.commit()
