from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import ImageGenerationRequest, ImageToVideoRequest, JobOutput, OmniVideoGenerationRequest
from app.api.serializers import job_dict
from app.db.models import MediaAsset
from app.jobs.repository import create_job

router=APIRouter(tags=["Generations"])
CLIENT_WORKSPACE_KEY="__api_client__"


def _reference_media_ids(data:dict,kind:str)->list[str]:
    if kind=="video":return [str(data["start_media_id"])]
    return [str(media_id) for media_id in data.get("reference_media_ids") or []]


def _validate_reference_assets(request:Request,db,client,data:dict,kind:str)->None:
    ids=_reference_media_ids(data,kind)
    if not ids:return
    rows=list(db.scalars(select(MediaAsset).where(MediaAsset.id.in_(ids),MediaAsset.client_id==client.id)))
    by_id={row.id:row for row in rows};limit=request.app.state.runtime.settings.max_reference_bytes
    for asset_id in ids:
        asset=by_id.get(asset_id)
        if not asset or asset.status!="ready":
            raise APIError(422,"INVALID_MEDIA_REFERENCE",f"Reference media '{asset_id}' is missing or not ready.",field="reference_media_ids")
        if asset.type!="image" or not asset.mime_type.lower().startswith("image/"):
            raise APIError(422,"INVALID_MEDIA_TYPE",f"Reference media '{asset_id}' must be an image.",field="reference_media_ids")
        if asset.size_bytes is not None and asset.size_bytes>limit:
            raise APIError(413,"REFERENCE_MEDIA_TOO_LARGE",f"Reference media '{asset_id}' exceeds the {limit} byte reference limit.",field="reference_media_ids")


def _submit(request: Request, db, client, payload, kind: str):
    data=payload.model_dump(mode="json")
    if kind=="video":data["start_media_id"]=str(data["start_media_id"])
    else:data["reference_media_ids"]=[str(media_id) for media_id in data.get("reference_media_ids") or []]
    provider=payload.provider;model=getattr(payload,"model",None)
    request.app.state.runtime.providers.get(provider)
    _validate_reference_assets(request,db,client,data,kind)
    job=create_job(db,client=client,kind=kind,provider=provider,model=model,workspace_key=CLIENT_WORKSPACE_KEY,payload=data,request_id=request.state.request_id)
    return job_dict(request.app.state.runtime,db,job)


@router.post("/v1/images/generations",status_code=202,response_model=JobOutput)
def create_image_generation(payload: ImageGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client)):
    return _submit(request,db,client,payload,"image")


@router.post("/v1/videos/image-to-video",status_code=202,response_model=JobOutput)
def create_image_to_video(payload: ImageToVideoRequest,request: Request,db=Depends(get_db),client=Depends(get_client)):
    return _submit(request,db,client,payload,"video")


@router.post("/v1/videos/omni-generations",status_code=202,response_model=JobOutput)
def create_omni_generation(payload: OmniVideoGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client)):
    return _submit(request,db,client,payload,"omni")
