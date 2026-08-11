from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import ImageGenerationRequest, JobOutput, OmniVideoGenerationRequest, VideoGenerationRequest
from app.api.serializers import job_dict
from app.db.models import MediaAsset
from app.jobs.repository import create_job

router=APIRouter(tags=["Generations"])


def _reference_asset_ids(data:dict,kind:str)->list[str]:
    if kind=="video":return [data["input"]["start_asset_id"]]
    return [ref["asset_id"] for ref in data.get("references") or []]


def _validate_reference_assets(request:Request,db,client,data:dict,kind:str)->None:
    ids=_reference_asset_ids(data,kind)
    if not ids:return
    rows=list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids),MediaAsset.client_id==client.id)))
    by_id={row.id:row for row in rows};limit=request.app.state.runtime.settings.max_reference_bytes
    for asset_id in ids:
        asset=by_id.get(asset_id)
        if not asset or asset.status!="ready":
            raise APIError(422,"INVALID_ASSET_REFERENCE",f"Reference asset '{asset_id}' is missing or not ready.",error_type="validation_error",param="references")
        if asset.type!="image" or not asset.mime_type.lower().startswith("image/"):
            raise APIError(422,"INVALID_ASSET_TYPE",f"Reference asset '{asset_id}' must be an image.",error_type="validation_error",param="references")
        if asset.size_bytes is not None and asset.size_bytes>limit:
            raise APIError(413,"REFERENCE_ASSET_TOO_LARGE",f"Reference asset '{asset_id}' exceeds the {limit} byte reference limit.",error_type="validation_error",param="references")


def _submit(request: Request, db, client, payload, kind: str, idempotency_key: str|None):
    data=payload.model_dump(mode="json")
    request.app.state.runtime.providers.get(data.get("provider","google_flow"))
    _validate_reference_assets(request,db,client,data,kind)
    job,created=create_job(db,client=client,kind=kind,provider=data.get("provider","google_flow"),model=data.get("model"),workspace_key=data["workspace"]["key"],payload=data,idempotency_key=idempotency_key)
    return job_dict(request.app.state.runtime,db,job),created


IdempotencyHeader=Header(default=None,alias="Idempotency-Key",min_length=1,max_length=255)


@router.post("/v1/images/generations",status_code=202,response_model=JobOutput)
def create_image_generation(payload: ImageGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=IdempotencyHeader):
    return _submit(request,db,client,payload,"image",idempotency_key)[0]


@router.post("/v1/videos/generations",status_code=202,response_model=JobOutput)
def create_video_generation(payload: VideoGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=IdempotencyHeader):
    return _submit(request,db,client,payload,"video",idempotency_key)[0]


@router.post("/v1/videos/omni-generations",status_code=202,response_model=JobOutput)
def create_omni_generation(payload: OmniVideoGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=IdempotencyHeader):
    return _submit(request,db,client,payload,"omni",idempotency_key)[0]
