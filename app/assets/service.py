from __future__ import annotations

import hashlib
import mimetypes
from pathlib import PurePosixPath

import httpx
from sqlalchemy import select

from app.db.models import MediaAsset
from app.ids import new_id
from app.providers.base import ProviderMedia


class AssetService:
    def __init__(self, storage, settings):
        self.storage=storage; self.settings=settings

    @staticmethod
    def storage_key(client_id: str, asset_id: str, filename: str|None, mime_type: str) -> str:
        suffix=PurePosixPath(filename or "").suffix
        if not suffix:
            suffix=mimetypes.guess_extension(mime_type) or ""
        return f"clients/{client_id}/{asset_id}{suffix[:12]}"

    def create_pending(self, db, *, client_id: str, filename: str, mime_type: str, asset_type: str, size_bytes: int|None=None) -> MediaAsset:
        aid=new_id("asset"); key=self.storage_key(client_id,aid,filename,mime_type)
        asset=MediaAsset(id=aid,client_id=client_id,status="pending",type=asset_type,storage_key=key,filename=filename,mime_type=mime_type,size_bytes=size_bytes)
        db.add(asset);db.commit();db.refresh(asset);return asset

    async def complete_pending(self, db, asset: MediaAsset) -> MediaAsset:
        meta=await self.storage.stat(asset.storage_key)
        if not meta:
            raise FileNotFoundError("uploaded_object_not_found")
        size=meta.get("size_bytes")
        if isinstance(size,int):
            asset.size_bytes=size
        asset.status="ready";db.commit();db.refresh(asset);return asset

    async def write_upload(self, db, asset: MediaAsset, data: bytes) -> MediaAsset:
        await self.storage.put_bytes(asset.storage_key,data,asset.mime_type)
        return await self.complete_pending(db,asset)

    async def ingest_provider_media(self, db, *, client_id: str, job_id: str, provider: str, media: ProviderMedia, asset_type: str) -> MediaAsset:
        if media.bytes_data is not None:
            data=media.bytes_data
        elif media.url:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp=await client.get(media.url);resp.raise_for_status();data=resp.content
        else:
            raise ValueError("provider_output_has_no_content")
        mime=media.mime_type or ("video/mp4" if asset_type=="video" else "image/png")
        aid=new_id("asset");key=self.storage_key(client_id,aid,None,mime)
        await self.storage.put_bytes(key,data,mime)
        asset=MediaAsset(id=aid,client_id=client_id,status="ready",type=asset_type,storage_key=key,mime_type=mime,size_bytes=len(data),width=media.width,height=media.height,duration=media.duration,checksum_sha256=hashlib.sha256(data).hexdigest(),source_provider=provider,source_job_id=job_id)
        db.add(asset);db.commit();db.refresh(asset);return asset

    async def bytes_for_asset(self, asset: MediaAsset) -> bytes:
        return await self.storage.read_bytes(asset.storage_key)

    def content_url(self, asset: MediaAsset) -> str:
        signed=self.storage.presign_get(asset.storage_key,self.settings.asset_url_ttl_seconds)
        if signed:return signed
        return f"{self.settings.public_base_url.rstrip('/')}/v1/assets/{asset.id}/content"

    def upload_descriptor(self, asset: MediaAsset) -> dict:
        signed=self.storage.presign_put(asset.storage_key,asset.mime_type,self.settings.asset_url_ttl_seconds)
        if signed:
            return {"method":"PUT","url":signed,"headers":{"Content-Type":asset.mime_type},"expires_in":self.settings.asset_url_ttl_seconds}
        return {"method":"PUT","url":f"{self.settings.public_base_url.rstrip('/')}/v1/assets/{asset.id}/content","headers":{"Content-Type":asset.mime_type,"Authorization":"Bearer <same API key>"},"expires_in":None}

    @staticmethod
    def get_owned(db, asset_id: str, client_id: str) -> MediaAsset|None:
        return db.scalar(select(MediaAsset).where(MediaAsset.id==asset_id,MediaAsset.client_id==client_id))
