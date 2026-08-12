from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import ImageGenerationRequest, ImageToVideoRequest, JobOutput, OmniVideoGenerationRequest
from app.api.serializers import job_dict
from app.db.models import MediaAsset
from app.jobs.repository import IdempotencyConflict, create_job

router=APIRouter(tags=["Generations"])
CLIENT_WORKSPACE_KEY="__api_client__"


def _reference_asset_ids(data:dict,kind:str)->list[str]:
    if kind=="video":return [data["start_asset_id"]]
    return data.get("reference_asset_ids") or []


def _validate_reference_assets(request:Request,db,client,data:dict,kind:str)->None:
    ids=_reference_asset_ids(data,kind)
    if not ids:return
    rows=list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids),MediaAsset.client_id==client.id)))
    by_id={row.id:row for row in rows};limit=request.app.state.runtime.settings.max_reference_bytes
    for asset_id in ids:
        asset=by_id.get(asset_id)
        if not asset or asset.status!="ready":
            raise APIError(422,"INVALID_ASSET_REFERENCE",f"Reference asset '{asset_id}' is missing or not ready.",field="reference_asset_ids")
        if asset.type!="image" or not asset.mime_type.lower().startswith("image/"):
            raise APIError(422,"INVALID_ASSET_TYPE",f"Reference asset '{asset_id}' must be an image.",field="reference_asset_ids")
        if asset.size_bytes is not None and asset.size_bytes>limit:
            raise APIError(413,"REFERENCE_ASSET_TOO_LARGE",f"Reference asset '{asset_id}' exceeds the {limit} byte reference limit.",field="reference_asset_ids")


def _submit(request: Request, db, client, payload, kind: str, idempotency_key: str | None):
    data=payload.model_dump(mode="json")
    provider=payload.provider;model=getattr(payload,"model",None)
    request.app.state.runtime.providers.get(provider)
    _validate_reference_assets(request,db,client,data,kind)
    key = idempotency_key.strip() if idempotency_key is not None else None
    if idempotency_key is not None and (not key or len(key) > 255):
        raise APIError(400,"INVALID_IDEMPOTENCY_KEY","Idempotency-Key must contain between 1 and 255 characters.",field="Idempotency-Key")
    try:
        job=create_job(db,client=client,kind=kind,provider=provider,model=model,workspace_key=CLIENT_WORKSPACE_KEY,payload=data,idempotency_key=key)
    except IdempotencyConflict:
        raise APIError(409,"IDEMPOTENCY_CONFLICT","Idempotency-Key was already used for a different request.",field="Idempotency-Key") from None
    return job_dict(request.app.state.runtime,db,job)


@router.post("/v1/images/generations",status_code=202,response_model=JobOutput)
def create_image_generation(payload: ImageGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
    return _submit(request,db,client,payload,"image",idempotency_key)


@router.post("/v1/videos/image-to-video",status_code=202,response_model=JobOutput)
def create_image_to_video(payload: ImageToVideoRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
    return _submit(request,db,client,payload,"video",idempotency_key)


@router.post("/v1/videos/omni-generations",status_code=202,response_model=JobOutput)
def create_omni_generation(payload: OmniVideoGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
    return _submit(request,db,client,payload,"omni",idempotency_key)
