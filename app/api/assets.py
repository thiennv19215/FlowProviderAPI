from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import AssetOutput, AssetUploadRequest, AssetUploadResponse
from app.api.serializers import asset_dict

router=APIRouter(prefix="/v1/assets",tags=["Assets"])


def _validate_upload_request(request: Request,payload: AssetUploadRequest)->None:
    limit=request.app.state.runtime.settings.max_upload_bytes
    if payload.size_bytes is not None and payload.size_bytes>limit:
        raise APIError(413,"ASSET_TOO_LARGE",f"Asset exceeds the {limit} byte upload limit.",error_type="validation_error",param="size_bytes")
    expected="image/" if payload.type=="image" else "video/"
    if not payload.content_type.lower().startswith(expected):
        raise APIError(422,"INVALID_ASSET_CONTENT_TYPE",f"Asset type '{payload.type}' requires a {expected}* content type.",error_type="validation_error",param="content_type")


@router.post("/uploads",status_code=201,response_model=AssetUploadResponse)
def create_upload(payload: AssetUploadRequest,request: Request,db=Depends(get_db),client=Depends(get_client)):
    _validate_upload_request(request,payload)
    asset=request.app.state.runtime.assets.create_pending(db,client_id=client.id,filename=payload.filename,mime_type=payload.content_type,asset_type=payload.type,size_bytes=payload.size_bytes)
    return {"asset":asset_dict(request.app.state.runtime,asset),"upload":request.app.state.runtime.assets.upload_descriptor(asset)}


@router.put("/{asset_id}/content",status_code=204)
async def upload_content(asset_id: str,request: Request,db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.get_owned(db,asset_id,client.id)
    if not asset:raise APIError(404,"ASSET_NOT_FOUND","The requested asset does not exist.",error_type="not_found_error")
    if asset.status!="pending":raise APIError(409,"ASSET_ALREADY_COMPLETE","This asset upload is already complete.",error_type="conflict_error")
    limit=request.app.state.runtime.settings.max_upload_bytes
    content_length=request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length)>limit:raise APIError(413,"ASSET_TOO_LARGE",f"Asset exceeds the {limit} byte upload limit.",error_type="validation_error")
        except ValueError:raise APIError(400,"INVALID_CONTENT_LENGTH","Content-Length must be an integer.",error_type="validation_error")
    tmp_path=None;size=0
    try:
        with tempfile.NamedTemporaryFile(prefix="flow-provider-upload-",delete=False) as tmp:
            tmp_path=Path(tmp.name)
            async for chunk in request.stream():
                if not chunk:continue
                size+=len(chunk)
                if size>limit:raise APIError(413,"ASSET_TOO_LARGE",f"Asset exceeds the {limit} byte upload limit.",error_type="validation_error")
                tmp.write(chunk)
        if asset.size_bytes is not None and asset.size_bytes!=size:
            raise APIError(409,"UPLOAD_SIZE_MISMATCH",f"Uploaded {size} bytes but {asset.size_bytes} bytes were declared.",error_type="conflict_error")
        await request.app.state.runtime.assets.write_upload_file(db,asset,tmp_path,size)
    finally:
        if tmp_path:
            try:os.unlink(tmp_path)
            except FileNotFoundError:pass
    return Response(status_code=204)


@router.post("/{asset_id}/complete",response_model=AssetOutput)
async def complete_upload(asset_id: str,request: Request,db=Depends(get_db),client=Depends(get_client)):
    asset=request.app.state.runtime.assets.get_owned(db,asset_id,client.id)
    if not asset:raise APIError(404,"ASSET_NOT_FOUND","The requested asset does not exist.",error_type="not_found_error")
    if asset.status!="ready":
        try:await request.app.state.runtime.assets.complete_pending(db,asset)
        except FileNotFoundError:raise APIError(409,"UPLOAD_NOT_FOUND","The uploaded object is not available yet.",error_type="conflict_error",retryable=True)
    if asset.size_bytes is not None and asset.size_bytes>request.app.state.runtime.settings.max_upload_bytes:
        raise APIError(413,"ASSET_TOO_LARGE","Uploaded asset exceeds the configured upload limit.",error_type="validation_error")
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
