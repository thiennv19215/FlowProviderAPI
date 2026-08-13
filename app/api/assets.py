from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import MediaId, MediaOutput
from app.api.serializers import media_dict
from app.db.models import MediaAsset

router = APIRouter(prefix="/v1/media", tags=["Media"])
delivery_router = APIRouter()


def _media_type(content_type: str, requested_type: str | None) -> str:
    inferred = (
        "image"
        if content_type.lower().startswith("image/")
        else "video"
        if content_type.lower().startswith("video/")
        else None
    )
    if not inferred:
        raise APIError(
            422,
            "INVALID_MEDIA_CONTENT_TYPE",
            "Media content type must be image/* or video/*.",
            field="file",
        )
    if requested_type and requested_type != inferred:
        raise APIError(
            422,
            "INVALID_MEDIA_CONTENT_TYPE",
            f"Media type '{requested_type}' does not match {content_type}.",
            field="type",
        )
    return inferred


def _existing_content_asset(db, *, client_id: str, checksum: str, asset_type: str):
    return db.scalar(
        select(MediaAsset).where(
            MediaAsset.client_id == client_id,
            MediaAsset.checksum_sha256 == checksum,
            MediaAsset.type == asset_type,
            MediaAsset.status == "done",
        )
    )


@router.post("", status_code=201, response_model=MediaOutput, summary="Upload media")
async def create_media(
    request: Request,
    file: UploadFile = File(...),
    media_type: str | None = Form(default=None, alias="type"),
    db=Depends(get_db),
    client=Depends(get_client),
):
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    resolved_type = _media_type(content_type, media_type)
    filename = file.filename or f"upload.{'png' if resolved_type == 'image' else 'mp4'}"
    tmp_path = None
    size = 0
    digest = hashlib.sha256()
    limit = request.app.state.runtime.settings.max_upload_bytes
    try:
        with tempfile.NamedTemporaryFile(prefix="flow-provider-media-", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise APIError(
                        413,
                        "MEDIA_TOO_LARGE",
                        f"Media exceeds the {limit} byte upload limit.",
                        field="file",
                    )
                digest.update(chunk)
                tmp.write(chunk)

        checksum = digest.hexdigest()
        existing = _existing_content_asset(
            db,
            client_id=client.id,
            checksum=checksum,
            asset_type=resolved_type,
        )
        if existing is not None:
            return media_dict(request.app.state.runtime, existing)

        asset = request.app.state.runtime.assets.create_pending(
            db,
            client_id=client.id,
            filename=filename,
            mime_type=content_type,
            asset_type=resolved_type,
            size_bytes=size,
        )
        asset.checksum_sha256 = checksum
        try:
            await request.app.state.runtime.assets.write_upload_file(db, asset, tmp_path, size)
            return media_dict(request.app.state.runtime, asset)
        except Exception:
            if asset.status == "pending":
                db.delete(asset)
                db.commit()
            raise
    finally:
        await file.close()
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass


@router.get("/{media_id}", response_model=MediaOutput, summary="Get media")
def get_media(
    media_id: MediaId,
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
):
    asset = request.app.state.runtime.assets.get_owned(db, str(media_id), client.id)
    if not asset:
        raise APIError(404, "MEDIA_NOT_FOUND", "The requested media does not exist.")
    return media_dict(request.app.state.runtime, asset)


@delivery_router.get("/media/{media_id}", include_in_schema=False)
async def get_media_file(
    media_id: MediaId,
    request: Request,
    db=Depends(get_db),
):
    asset = request.app.state.runtime.assets.get_public(db, media_id)
    if not asset or asset.status != "done":
        raise APIError(404, "MEDIA_NOT_FOUND", "The requested media is not done.")
    if asset.external_url:
        return RedirectResponse(asset.external_url, status_code=307)
    if not asset.storage_key:
        raise APIError(
            404,
            "MEDIA_CONTENT_NOT_FOUND",
            "The requested media has no available content.",
        )
    download_url = await request.app.state.runtime.assets.storage.create_download_url(
        asset.storage_key
    )
    if download_url:
        return RedirectResponse(download_url, status_code=307)
    data = await request.app.state.runtime.assets.bytes_for_asset(asset)
    return Response(
        content=data,
        media_type=asset.mime_type,
        headers={"Content-Disposition": f'inline; filename="{asset.filename or asset.id}"'},
    )
