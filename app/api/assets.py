from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import AssetOutput, AssetUploadRequest, AssetUploadResponse
from app.api.serializers import asset_dict

router=APIRouter(prefix="/v1/assets",tags=["Assets"])


@router.post("/uploads",status_code=201,response_model=AssetUploadResponse)
def create_upload(payload: AssetUploadRequest,request: Request,db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.create_pending(db,client_id=client.id,filename=payload.filename,mime_type=payload.content_type,asset_type=payload.type,size_bytes=payload.size_bytes)
    return {"asset":asset_dict(request.app.state.runtime,asset),"upload":request.app.state.runtime.assets.upload_descriptor(asset)}


@router.put("/{asset_id}/content",status_code=204)
async def upload_content(asset_id: str,request: Request,data: bytes=Body(media_type="application/octet-stream"),db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.get_owned(db,asset_id,client.id)
    if not asset:raise APIError(404,"ASSET_NOT_FOUND","The requested asset does not exist.",error_type="not_found_error")
    if asset.status!="pending":raise APIError(409,"ASSET_ALREADY_COMPLETE","This asset upload is already complete.",error_type="conflict_error")
    await request.app.state.runtime.assets.write_upload(db,asset,data)
    return Response(status_code=204)


@router.post("/{asset_id}/complete",response_model=AssetOutput)
async def complete_upload(asset_id: str,request: Request,db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.get_owned(db,asset_id,client.id)
    if not asset:raise APIError(404,"ASSET_NOT_FOUND","The requested asset does not exist.",error_type="not_found_error")
    if asset.status!="ready":
        try:await request.app.state.runtime.assets.complete_pending(db,asset)
        except FileNotFoundError:raise APIError(409,"UPLOAD_NOT_FOUND","The uploaded object is not available yet.",error_type="conflict_error",retryable=True)
    return asset_dict(request.app.state.runtime,asset)


@router.get("/{asset_id}",response_model=AssetOutput)
def get_asset(asset_id: str,request: Request,db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.get_owned(db,asset_id,client.id)
    if not asset:raise APIError(404,"ASSET_NOT_FOUND","The requested asset does not exist.",error_type="not_found_error")
    return asset_dict(request.app.state.runtime,asset)


@router.get("/{asset_id}/content")
async def get_asset_content(asset_id: str,request: Request,db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.get_owned(db,asset_id,client.id)
    if not asset or asset.status!="ready":raise APIError(404,"ASSET_NOT_FOUND","The requested asset is not ready.",error_type="not_found_error")
    signed=request.app.state.runtime.storage.presign_get(asset.storage_key,request.app.state.runtime.settings.asset_url_ttl_seconds)
    if signed:return RedirectResponse(signed,status_code=307)
    data=await request.app.state.runtime.assets.bytes_for_asset(asset)
    return Response(content=data,media_type=asset.mime_type,headers={"Content-Disposition":f'inline; filename="{asset.filename or asset.id}"'})
