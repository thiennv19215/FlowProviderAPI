from __future__ import annotations

from sqlalchemy import select

from app.db.models import MediaAsset


def media_dict(runtime, asset):
    return {"media_id":int(asset.id),"object":"media","type":asset.type,"status":asset.status,"mime_type":asset.mime_type,"size_bytes":asset.size_bytes,"width":asset.width,"height":asset.height,"duration":asset.duration,"url":runtime.assets.content_url(asset) if asset.status=="ready" else None,"created_at":asset.created_at}


def job_dict(runtime, db, job):
    outputs=[]
    for aid in (job.result_payload or {}).get("asset_ids",[]):
        asset=db.scalar(select(MediaAsset).where(MediaAsset.id==aid,MediaAsset.client_id==job.client_id))
        if asset:
            output={"media_id":int(asset.id),"type":asset.type,"url":runtime.assets.content_url(asset) if asset.status=="ready" else None}
            if asset.type=="video":output["thumbnail_url"]=asset.thumbnail_url
            outputs.append(output)
    error=None
    if job.error_code or job.error_message:
        metadata=(job.result_payload or {}).get("_error") or {}
        fallback_status=504 if job.error_code=="PROVIDER_OPERATION_TIMEOUT" else 502 if (job.error_code or "").startswith("PROVIDER") else 500
        details=metadata.get("details") if isinstance(metadata.get("details"),list) else []
        request_id=(job.result_payload or {}).get("_request_id")
        error={"status_code":metadata.get("status_code") or fallback_status,"code":job.error_code or "PROVIDER_ERROR","message":job.error_message or "Generation failed.","details":details,"request_id":request_id if isinstance(request_id,str) else None,"retryable":bool(metadata.get("retryable",job.status not in {"failed","canceled"}))}
    return {"task_id":job.id,"status":job.status,"outputs":outputs,"error":error}
