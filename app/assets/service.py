from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.db.models import MediaAsset
from app.ids import new_id
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
        aid=new_id("asset");key=self.storage_key(client_id,aid,filename,mime_type)
        asset=MediaAsset(id=aid,client_id=client_id,status="pending",type=asset_type,storage_key=key,filename=filename,mime_type=mime_type,size_bytes=size_bytes)
        db.add(asset);db.commit();db.refresh(asset);return asset

    async def _reject_pending_object(self,asset:MediaAsset,code:str):
        try:await self.storage.delete(asset.storage_key)
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

    async def ingest_provider_media(self,db,*,client_id:str,job_id:str,provider:str,media:ProviderMedia,asset_type:str)->MediaAsset:
        mime=media.mime_type or ("video/mp4" if asset_type=="video" else "image/png");aid=new_id("asset");key=self.storage_key(client_id,aid,None,mime)
        checksum=hashlib.sha256();size=0;stored=False;limit=getattr(self.settings,"max_provider_output_bytes",1024*1024*1024)
        if media.bytes_data is not None:
            data=media.bytes_data;size=len(data)
            if size>limit:raise ValueError("provider_output_too_large")
            checksum.update(data);await self.storage.put_bytes(key,data,mime);stored=True
        elif media.url:
            if not self._provider_url_allowed(media.url):raise ValueError("provider_output_url_not_allowed")
            tmp_path=None
            try:
                with tempfile.NamedTemporaryFile(prefix="flow-provider-",delete=False) as tmp:
                    tmp_path=Path(tmp.name)
                    async with httpx.AsyncClient(timeout=httpx.Timeout(120,connect=20),follow_redirects=True) as client:
                        async with client.stream("GET",media.url) as resp:
                            resp.raise_for_status();final_url=str(resp.url)
                            if not self._provider_url_allowed(final_url):raise ValueError("provider_output_redirect_not_allowed")
                            declared=resp.headers.get("content-length")
                            if declared:
                                try:
                                    if int(declared)>limit:raise ValueError("provider_output_too_large")
                                except ValueError as exc:
                                    if str(exc)=="provider_output_too_large":raise
                            async for chunk in resp.aiter_bytes(1024*1024):
                                if not chunk:continue
                                size+=len(chunk)
                                if size>limit:raise ValueError("provider_output_too_large")
                                tmp.write(chunk);checksum.update(chunk)
                await self.storage.put_file(key,tmp_path,mime);stored=True
            finally:
                if tmp_path:
                    try:os.unlink(tmp_path)
                    except FileNotFoundError:pass
        else:raise ValueError("provider_output_has_no_content")
        asset=MediaAsset(id=aid,client_id=client_id,status="ready",type=asset_type,storage_key=key,mime_type=mime,size_bytes=size,width=media.width,height=media.height,duration=media.duration,checksum_sha256=checksum.hexdigest(),source_provider=provider,source_job_id=job_id)
        db.add(asset)
        try:db.commit()
        except Exception:
            db.rollback()
            if stored:
                try:await self.storage.delete(key)
                except Exception:pass
            raise
        db.refresh(asset);return asset

    async def bytes_for_asset(self,asset:MediaAsset)->bytes:return await self.storage.read_bytes(asset.storage_key)

    def content_url(self,asset:MediaAsset)->str:
        signed=self.storage.presign_get(asset.storage_key,self.settings.asset_url_ttl_seconds)
        if signed:return signed
        return f"{self.settings.public_base_url.rstrip('/')}/v1/assets/{asset.id}/content"

    def upload_descriptor(self,asset:MediaAsset)->dict:
        signed=self.storage.presign_put(asset.storage_key,asset.mime_type,self.settings.asset_url_ttl_seconds)
        if signed:return {"method":"PUT","url":signed,"headers":{"Content-Type":asset.mime_type},"expires_in":self.settings.asset_url_ttl_seconds}
        return {"method":"PUT","url":f"{self.settings.public_base_url.rstrip('/')}/v1/assets/{asset.id}/content","headers":{"Content-Type":asset.mime_type,"Authorization":"Bearer <same API key>"},"expires_in":None}

    @staticmethod
    def get_owned(db,asset_id:str,client_id:str)->MediaAsset|None:return db.scalar(select(MediaAsset).where(MediaAsset.id==asset_id,MediaAsset.client_id==client_id))
