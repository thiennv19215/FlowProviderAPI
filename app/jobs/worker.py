from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.models import GenerationJob, MediaAsset, utcnow
from app.jobs import repository
from app.providers.base import ProviderContext, ProviderDispatch, ProviderError, ProviderMedia, provider_capabilities

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
            if job.stage=="storing_outputs":
                await self._resume_outputs(db,job);return
            if job.stage=="provider_running":
                await self._poll(db,job,provider);return
            prepare=getattr(provider,"prepare",None)
            if callable(prepare):
                context=await prepare(job=job,db=db)
            elif provider_capabilities(provider).account_pool:
                # Compatibility for V1 adapters. New providers own capacity
                # selection in prepare() and never enter this branch.
                account_id=self.runtime.scheduler.reserve_account(db,job)
                context=ProviderContext(account_id=account_id)
            else:
                context=ProviderContext()
            if job.kind=="image":
                outputs=await self._generate_image(provider,job,db,context)
                await self._store_outputs(db,job,outputs,"image");return
            dispatch=await self._dispatch(provider,job,db,context)
            job.provider_operation_id=json.dumps({"operation_ids":dispatch.operation_ids,"workflows":dispatch.workflows,"dispatched_at":utcnow().isoformat()})
            job.stage="provider_running";job.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);job.lease_owner=None;job.lease_expires_at=None;db.commit()
        except Exception as exc:
            db.rollback();fresh=db.get(GenerationJob,job_id)
            if fresh and fresh.stage=="storing_outputs" and isinstance((fresh.result_payload or {}).get("_provider_outputs"),list):
                await self._retry_output_registration(db,fresh,exc)
            elif fresh:await self._handle_error(db,fresh,exc)
            else:raise

    @staticmethod
    async def _generate_image(provider,job,db,context:ProviderContext):
        if callable(getattr(provider,"prepare",None)):
            return await provider.generate_image(job=job,db=db,context=context)
        return await provider.generate_image(job=job,db=db,account_id=context.account_id)

    @staticmethod
    async def _dispatch(provider,job,db,context:ProviderContext):
        method=provider.dispatch_video if job.kind=="video" else provider.dispatch_omni
        if callable(getattr(provider,"prepare",None)):
            return await method(job=job,db=db,context=context)
        return await method(job=job,db=db,account_id=context.account_id)

    @staticmethod
    async def _poll_provider(provider,job,db,context:ProviderContext,dispatch:ProviderDispatch):
        poll=getattr(provider,"poll",None)
        if callable(poll):return await poll(job=job,db=db,context=context,dispatch=dispatch)
        return await provider.poll_video(job=job,db=db,account_id=context.account_id,dispatch=dispatch)

    @staticmethod
    def _poll_error_count(job:GenerationJob)->int:
        payload=job.result_payload or {}
        try:return max(0,int(payload.get("_poll_error_count",0)))
        except (TypeError,ValueError):return 0

    @staticmethod
    def _set_poll_error_count(job:GenerationJob,count:int)->None:
        payload=dict(job.result_payload or {})
        if count>0:payload["_poll_error_count"]=count
        else:payload.pop("_poll_error_count",None)
        job.result_payload=payload or None

    @staticmethod
    def _set_error(job,exc:Exception,*,fallback_code:str,fallback_status:int=502,retryable:bool=False)->None:
        is_provider=isinstance(exc,ProviderError)
        job.error_code=exc.code if is_provider else fallback_code
        job.error_message=(exc.message if is_provider else str(exc))[:1000]
        payload=dict(getattr(job,"result_payload",None) or {})
        payload["_error"]={"status_code":exc.status_code or fallback_status if is_provider else fallback_status,"retryable":exc.retryable if is_provider else retryable,"details":exc.details if is_provider else []}
        job.result_payload=payload

    @staticmethod
    def _operation_metadata(job:GenerationJob)->dict:
        try:
            raw=json.loads(job.provider_operation_id or "{}")
            return raw if isinstance(raw,dict) else {}
        except (TypeError,ValueError,json.JSONDecodeError):return {}

    @classmethod
    def _provider_dispatched_at(cls,job:GenerationJob):
        raw=cls._operation_metadata(job).get("dispatched_at")
        if isinstance(raw,str):
            try:return datetime.fromisoformat(raw)
            except ValueError:pass
        return job.started_at

    def _operation_timed_out(self,job:GenerationJob)->bool:
        started=self._provider_dispatched_at(job)
        if not started:return False
        now=utcnow()
        if started.tzinfo is None:started=started.replace(tzinfo=now.tzinfo)
        return now>=started+timedelta(seconds=self.runtime.settings.max_provider_operation_seconds)

    def _finish_operation_timeout(self,db,job:GenerationJob):
        self._set_error(job,RuntimeError("Provider operation exceeded the configured maximum runtime."),fallback_code="PROVIDER_OPERATION_TIMEOUT",fallback_status=504,retryable=True)
        job.status="failed";job.stage="completed";job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None;db.commit()

    async def _poll(self,db,job,provider):
        if job.cancel_requested:return self._finish_cancel(db,job)
        if self._operation_timed_out(job):return self._finish_operation_timeout(db,job)
        job_id=job.id
        try:
            raw=self._operation_metadata(job)
            dispatch=ProviderDispatch(operation_ids=raw.get("operation_ids") or [],workflows=raw.get("workflows") or [])
            context=ProviderContext(account_id=job.provider_account_id)
            result=await self._poll_provider(provider,job,db,context,dispatch)
            if result.error and result.done:
                self._set_error(job,RuntimeError(result.error),fallback_code="PROVIDER_TERMINAL_ERROR")
                job.status="failed";job.stage="completed";job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None;db.commit();return
            if result.error:
                failures=self._poll_error_count(job)+1;self._set_poll_error_count(job,failures)
                if failures>=self.runtime.settings.max_consecutive_poll_errors:
                    self._set_error(job,RuntimeError(result.error),fallback_code="PROVIDER_POLL_RETRIES_EXHAUSTED")
                    job.status="failed";job.stage="completed";job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None;db.commit();return
                self._set_error(job,RuntimeError(result.error),fallback_code="PROVIDER_POLL_ERROR",retryable=True)
                job.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);job.lease_owner=None;job.lease_expires_at=None;db.commit();return
            self._set_poll_error_count(job,0)
            if not result.done:
                if self._operation_timed_out(job):return self._finish_operation_timeout(db,job)
                job.error_code=None;job.error_message=None;job.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);job.lease_owner=None;job.lease_expires_at=None;db.commit();return
            await self._store_outputs(db,job,result.outputs,"video")
        except Exception as exc:
            db.rollback();fresh=db.get(GenerationJob,job_id)
            if not fresh:raise
            if self._operation_timed_out(fresh):return self._finish_operation_timeout(db,fresh)
            if isinstance(exc,ProviderError) and not exc.retryable:
                self._set_error(fresh,exc,fallback_code="PROVIDER_ERROR")
                fresh.status="failed";fresh.stage="completed";fresh.completed_at=utcnow();fresh.lease_owner=None;fresh.lease_expires_at=None;db.commit();return
            failures=self._poll_error_count(fresh)+1;self._set_poll_error_count(fresh,failures)
            if failures>=self.runtime.settings.max_consecutive_poll_errors:
                self._set_error(fresh,exc,fallback_code="PROVIDER_POLL_RETRIES_EXHAUSTED")
                fresh.status="failed";fresh.stage="completed";fresh.completed_at=utcnow();fresh.lease_owner=None;fresh.lease_expires_at=None;db.commit();return
            fresh.stage="provider_running";self._set_error(fresh,exc,fallback_code="PROVIDER_POLL_ERROR",retryable=True)
            fresh.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.video_poll_seconds);fresh.lease_owner=None;fresh.lease_expires_at=None;db.commit()

    @staticmethod
    def _serializable_outputs(outputs)->list[dict]|None:
        items=list(outputs)
        if not items or any(not media.url for media in items):return None
        return [{"media_id":media.media_id,"url":media.url,"thumbnail_url":media.thumbnail_url,"mime_type":media.mime_type,"width":media.width,"height":media.height,"duration":media.duration} for media in items]

    async def _resume_outputs(self,db,job):
        raw=(job.result_payload or {}).get("_provider_outputs")
        if not isinstance(raw,list) or not raw:raise RuntimeError("provider_outputs_not_recoverable")
        outputs=[ProviderMedia(**item) for item in raw if isinstance(item,dict)]
        if len(outputs)!=len(raw):raise RuntimeError("provider_outputs_not_recoverable")
        await self._store_outputs(db,job,outputs,"image" if job.kind=="image" else "video")

    async def _retry_output_registration(self,db,job,exc):
        payload=dict(job.result_payload or {})
        failures=max(0,int(payload.get("_output_error_count",0) or 0))+1
        payload["_output_error_count"]=failures
        job.result_payload=payload
        if failures>=self.runtime.settings.max_consecutive_poll_errors:
            self._set_error(job,exc,fallback_code="OUTPUT_REGISTRATION_RETRIES_EXHAUSTED")
            job.status="failed";job.stage="completed";job.completed_at=utcnow()
        else:
            job.status="running";job.stage="storing_outputs"
            self._set_error(job,exc,fallback_code="OUTPUT_REGISTRATION_ERROR",retryable=True)
            job.next_run_at=utcnow()+timedelta(seconds=min(30,2**failures))
        job.lease_owner=None;job.lease_expires_at=None;db.commit()

    async def _store_outputs(self,db,job,outputs,asset_type):
        outputs=list(outputs)
        payload=dict(job.result_payload or {});payload.pop("_poll_error_count",None);payload.pop("_output_error_count",None)
        raw_persisted=payload.get("_provider_outputs")
        if isinstance(raw_persisted,list) and raw_persisted and all(isinstance(item,dict) for item in raw_persisted):
            outputs=[ProviderMedia(**item) for item in raw_persisted]
        if "_provider_outputs" not in payload:
            if serialized:=self._serializable_outputs(outputs):payload["_provider_outputs"]=serialized
        job.stage="storing_outputs";job.result_payload=payload or None;db.commit()
        asset_ids=list(payload.get("asset_ids") or [])
        existing=list(db.scalars(select(MediaAsset).where(MediaAsset.client_id==job.client_id,MediaAsset.source_job_id==job.id).order_by(MediaAsset.created_at.asc(),MediaAsset.id.asc())))
        if len(existing)>len(outputs):raise RuntimeError("provider_output_registration_inconsistent")
        if existing:asset_ids=[a.id for a in existing]
        start_index=len(asset_ids)
        for media in outputs[start_index:]:
            asset=await self.runtime.assets.ingest_provider_media(db,client_id=job.client_id,job_id=job.id,provider=job.provider,media=media,asset_type=asset_type,provider_project_id=job.provider_project_id)
            asset_ids=[*asset_ids,asset.id]
            progress=dict(job.result_payload or {});progress["asset_ids"]=list(asset_ids);job.result_payload=progress;db.commit()
        if not asset_ids:raise RuntimeError("provider_returned_no_outputs")
        job.status="done";job.stage="completed";job.result_payload={"asset_ids":list(asset_ids)};job.completed_at=utcnow();job.error_code=None;job.error_message=None;job.lease_owner=None;job.lease_expires_at=None;db.commit()

    async def _handle_error(self,db,job,exc):
        db.rollback();safe_to_retry=job.stage in {"preparing"} and job.attempt_count<self.runtime.settings.max_attempts_before_dispatch and (not isinstance(exc,ProviderError) or exc.retryable)
        # Capacity is transient: credits can refresh and another account can
        # connect. Do not burn the normal dispatch-attempt budget in seconds
        # and turn an otherwise valid task into a terminal failure.
        if isinstance(exc,ProviderError) and exc.code=="PROVIDER_ACCOUNT_UNAVAILABLE" and job.stage=="preparing":
            job.status="queued";job.stage="queued";job.provider_account_id=None
            self._set_error(job,exc,fallback_code="PROVIDER_ACCOUNT_UNAVAILABLE",retryable=True)
            job.next_run_at=utcnow()+timedelta(seconds=self.runtime.settings.account_unavailable_retry_seconds);job.lease_owner=None;job.lease_expires_at=None
        elif safe_to_retry:
            job.status="queued";job.stage="queued";job.provider_account_id=None;self._set_error(job,exc,fallback_code="PROVIDER_UNAVAILABLE",retryable=True);job.next_run_at=utcnow()+timedelta(seconds=min(30,2**job.attempt_count));job.lease_owner=None;job.lease_expires_at=None
        else:
            job.status="failed";job.stage="completed";self._set_error(job,exc,fallback_code="PROVIDER_ERROR");job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None
        db.commit()

    def _finish_cancel(self,db,job):
        job.status="canceled";job.stage="completed";job.completed_at=utcnow();job.lease_owner=None;job.lease_expires_at=None;db.commit()
