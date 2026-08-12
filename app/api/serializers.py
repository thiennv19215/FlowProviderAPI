from __future__ import annotations

from sqlalchemy import select

from app.db.models import MediaAsset


def asset_dict(runtime, asset):
    return {"id":asset.id,"object":"asset","type":asset.type,"status":asset.status,"mime_type":asset.mime_type,"size_bytes":asset.size_bytes,"width":asset.width,"height":asset.height,"duration":asset.duration,"content_url":runtime.assets.content_url(asset) if asset.status=="ready" else None,"created_at":asset.created_at}


def job_dict(runtime, db, job):
    outputs=[]
    for aid in (job.result_payload or {}).get("asset_ids",[]):
        asset=db.scalar(select(MediaAsset).where(MediaAsset.id==aid,MediaAsset.client_id==job.client_id))
        if asset: outputs.append({"asset_id":asset.id,"type":asset.type,"url":runtime.assets.content_url(asset) if asset.status=="ready" else None})
    error=None
    if job.error_code or job.error_message:
        metadata=(job.result_payload or {}).get("_error") or {}
        fallback_status=504 if job.error_code=="PROVIDER_OPERATION_TIMEOUT" else 502 if (job.error_code or "").startswith("PROVIDER") else 500
        error={"status_code":metadata.get("status_code") or fallback_status,"code":job.error_code or "PROVIDER_ERROR","message":job.error_message or "Generation failed.","details":[],"request_id":None,"retryable":bool(metadata.get("retryable",job.status not in {"failed","canceled"}))}
    return {"task_id":job.id,"status":job.status,"outputs":outputs,"error":error}
