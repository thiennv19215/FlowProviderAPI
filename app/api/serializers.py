from __future__ import annotations

from sqlalchemy import select

from app.db.models import MediaAsset


def asset_dict(runtime, asset):
    return {"id":asset.id,"object":"asset","type":asset.type,"status":asset.status,"mime_type":asset.mime_type,"size_bytes":asset.size_bytes,"width":asset.width,"height":asset.height,"duration":asset.duration,"content_url":runtime.assets.content_url(asset) if asset.status=="ready" else None,"created_at":asset.created_at}


def job_dict(runtime, db, job):
    outputs=[]
    for aid in (job.result_payload or {}).get("asset_ids",[]):
        asset=db.scalar(select(MediaAsset).where(MediaAsset.id==aid,MediaAsset.client_id==job.client_id))
        if asset: outputs.append(asset_dict(runtime,asset))
    error=None
    if job.error_code or job.error_message:error={"code":job.error_code or "PROVIDER_ERROR","message":job.error_message or "Generation failed.","retryable":job.status not in {"failed","canceled"}}
    return {"id":job.id,"task_id":job.id,"object":"generation_job","type":job.kind,"provider":job.provider,"model":job.model,"status":job.status,"stage":job.stage,"outputs":outputs,"error":error,"created_at":job.created_at,"started_at":job.started_at,"completed_at":job.completed_at}
