from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy import select

from app.db.models import MediaAsset, ProjectMediaMapping
from app.ids import new_compact_id, new_id
from app.providers.base import ProviderMedia

PROVIDER_MEDIA_HOSTS={"labs.google","flow.google","flow-content.google","storage.googleapis.com","googleusercontent.com"}


class AssetService:
    def __init__(self,storage,settings):self.storage=storage;self.settings=settings

    @staticmethod
    def storage_key(client_id:str,asset_id:str,filename:str|None,mime_type:str)->str:
        suffix=PurePosixPath(filename or "").suffix
        if not suffix:suffix=mimetypes.guess_extension(mime_type) or ""
        return f"clients/{client_id}/{asset_id}{suffix[:12]}"

    def _provider_url_allowed(self,value:str)->bool:
        try:
            parsed=urlparse(value);host=(parsed.hostname or "").lower()
            if parsed.scheme!="https":return self.settings.env in {"development","test"} and host in {"127.0.0.1","localhost"}
            return any(host==allowed or host.endswith("."+allowed) for allowed in PROVIDER_MEDIA_HOSTS)
        except Exception:return False

    def create_pending(self,db,*,client_id:str,filename:str,mime_type:str,asset_type:str,size_bytes:int|None=None)->MediaAsset:
        aid=new_compact_id("media");key=self.storage_key(client_id,aid,filename,mime_type)
        asset=MediaAsset(id=aid,client_id=client_id,status="pending",type=asset_type,storage_key=key,filename=filename,mime_type=mime_type,size_bytes=size_bytes)
        db.add(asset);db.commit();db.refresh(asset);return asset

    async def _reject_pending_object(self,asset:MediaAsset,code:str):
        try:
            if asset.storage_key:await self.storage.delete(asset.storage_key)
        except Exception:pass
        raise ValueError(code)

    async def complete_pending(self,db,asset:MediaAsset)->MediaAsset:
        meta=await self.storage.stat(asset.storage_key)
        if not meta:raise FileNotFoundError("uploaded_object_not_found")
        size=meta.get("size_bytes")
        if isinstance(size,int) and size>self.settings.max_upload_bytes:
            await self._reject_pending_object(asset,"uploaded_object_too_large")
        if asset.size_bytes is not None and isinstance(size,int) and size!=asset.size_bytes:
            await self._reject_pending_object(asset,"uploaded_size_mismatch")
        content_type=meta.get("content_type")
        if isinstance(content_type,str) and content_type and content_type.split(";",1)[0].strip().lower()!=asset.mime_type.split(";",1)[0].strip().lower():
            await self._reject_pending_object(asset,"uploaded_content_type_mismatch")
        if isinstance(size,int):asset.size_bytes=size
        asset.status="ready";db.commit();db.refresh(asset);return asset

    async def write_upload(self,db,asset:MediaAsset,data:bytes)->MediaAsset:
        await self.storage.put_bytes(asset.storage_key,data,asset.mime_type)
        try:return await self.complete_pending(db,asset)
        except Exception:
            db.rollback()
            try:await self.storage.delete(asset.storage_key)
            except Exception:pass
            raise

    async def write_upload_file(self,db,asset:MediaAsset,path:Path,size_bytes:int)->MediaAsset:
        if size_bytes>self.settings.max_upload_bytes:raise ValueError("uploaded_object_too_large")
        await self.storage.put_file(asset.storage_key,path,asset.mime_type)
        try:
            asset.size_bytes=size_bytes;asset.status="ready";db.commit();db.refresh(asset);return asset
        except Exception:
            db.rollback()
            try:await self.storage.delete(asset.storage_key)
            except Exception:pass
            raise

    async def ingest_provider_media(self,db,*,client_id:str,job_id:str,provider:str,media:ProviderMedia,asset_type:str,provider_project_id:str|None=None)->MediaAsset:
        mime=media.mime_type or ("video/mp4" if asset_type=="video" else "image/png");aid=new_compact_id("media")
        key=None;external_url=None;size=None;checksum_value=None;stored=False
        limit=getattr(self.settings,"max_provider_output_bytes",1024*1024*1024)
        if media.url:
            if not self._provider_url_allowed(media.url):raise ValueError("provider_output_url_not_allowed")
            if media.thumbnail_url and not self._provider_url_allowed(media.thumbnail_url):raise ValueError("provider_output_thumbnail_url_not_allowed")
            # Flow already returns a caller-consumable media URL. Persist only
            # its metadata so completing a generation never copies large output
            # files through this backend or into its local input volume.
            external_url=media.url
        elif media.bytes_data is not None:
            data=media.bytes_data;size=len(data)
            if size>limit:raise ValueError("provider_output_too_large")
            key=self.storage_key(client_id,aid,None,mime)
            checksum_value=hashlib.sha256(data).hexdigest()
            await self.storage.put_bytes(key,data,mime);stored=True
        else:raise ValueError("provider_output_has_no_content")
        asset=MediaAsset(id=aid,client_id=client_id,status="ready",type=asset_type,storage_key=key,external_url=external_url,thumbnail_url=media.thumbnail_url if asset_type=="video" else None,mime_type=mime,size_bytes=size,width=media.width,height=media.height,duration=media.duration,checksum_sha256=checksum_value,source_provider=provider,source_job_id=job_id)
        db.add(asset)
        if media.media_id and provider_project_id:
            db.add(ProjectMediaMapping(id=new_id("map"),asset_id=aid,provider=provider,provider_project_id=provider_project_id,provider_media_id=media.media_id))
        try:db.commit()
        except Exception:
            db.rollback()
            if stored:
                try:await self.storage.delete(key)
                except Exception:pass
            raise
        db.refresh(asset);return asset

    async def _external_bytes(self,url:str,limit:int)->bytes:
        if not self._provider_url_allowed(url):raise ValueError("external_asset_url_not_allowed")
        data=bytearray();current_url=url
        async with httpx.AsyncClient(timeout=httpx.Timeout(120,connect=20),follow_redirects=False) as client:
            for _ in range(6):
                async with client.stream("GET",current_url) as response:
                    if response.is_redirect:
                        location=response.headers.get("location")
                        if not location:raise ValueError("external_asset_redirect_missing_location")
                        next_url=urljoin(current_url,location)
                        if not self._provider_url_allowed(next_url):raise ValueError("external_asset_redirect_not_allowed")
                        current_url=next_url;continue
                    response.raise_for_status()
                    declared=response.headers.get("content-length")
                    if declared:
                        try:
                            if int(declared)>limit:raise ValueError("external_asset_too_large")
                        except ValueError as exc:
                            if str(exc)=="external_asset_too_large":raise
                    async for chunk in response.aiter_bytes(1024*1024):
                        if not chunk:continue
                        data.extend(chunk)
                        if len(data)>limit:raise ValueError("external_asset_too_large")
                    return bytes(data)
        raise ValueError("external_asset_too_many_redirects")

    async def bytes_for_asset(self,asset:MediaAsset,*,max_bytes:int|None=None)->bytes:
        if asset.external_url:
            limit=max_bytes or self.settings.max_reference_in_memory_bytes
            return await self._external_bytes(asset.external_url,limit)
        if not asset.storage_key:raise FileNotFoundError("asset_has_no_content")
        return await self.storage.read_bytes(asset.storage_key)

    def content_url(self,asset:MediaAsset)->str:
        if asset.external_url:return asset.external_url
        if not asset.storage_key:raise FileNotFoundError("asset_has_no_content")
        return f"{self.settings.public_base_url.rstrip('/')}/media/{asset.id}"

    @staticmethod
    def get_owned(db,asset_id:str,client_id:str)->MediaAsset|None:return db.scalar(select(MediaAsset).where(MediaAsset.id==asset_id,MediaAsset.client_id==client_id))
