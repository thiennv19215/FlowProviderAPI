from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import get_client, get_db
from app.api.schemas import ImageGenerationRequest, JobOutput, OmniVideoGenerationRequest, VideoGenerationRequest
from app.api.serializers import job_dict
from app.jobs.repository import create_job

router=APIRouter(tags=["Generations"])


def _submit(request: Request, db, client, payload, kind: str, idempotency_key: str|None):
    data=payload.model_dump(mode="json")
    request.app.state.runtime.providers.get(data.get("provider","google_flow"))
    job,created=create_job(db,client=client,kind=kind,provider=data.get("provider","google_flow"),model=data.get("model"),workspace_key=data["workspace"]["key"],payload=data,idempotency_key=idempotency_key)
    return job_dict(request.app.state.runtime,db,job),created


@router.post("/v1/images/generations",status_code=202,response_model=JobOutput)
def create_image_generation(payload: ImageGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
    return _submit(request,db,client,payload,"image",idempotency_key)[0]


@router.post("/v1/videos/generations",status_code=202,response_model=JobOutput)
def create_video_generation(payload: VideoGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
    return _submit(request,db,client,payload,"video",idempotency_key)[0]


@router.post("/v1/videos/omni-generations",status_code=202,response_model=JobOutput)
def create_omni_generation(payload: OmniVideoGenerationRequest,request: Request,db=Depends(get_db),client=Depends(get_client),idempotency_key: str|None=Header(default=None,alias="Idempotency-Key")):
    return _submit(request,db,client,payload,"omni",idempotency_key)[0]
