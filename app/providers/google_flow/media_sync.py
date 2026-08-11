from __future__ import annotations

import asyncio
import base64
import weakref

from sqlalchemy import select

from app.db.models import MediaAsset, ProjectMediaMapping
from app.ids import new_id


class MediaSync:
    """Map stable Provider assets to project-local Google Flow media IDs."""

    def __init__(self,asset_service):
        self.assets=asset_service
        self._locks: weakref.WeakValueDictionary[tuple[str,str],asyncio.Lock]=weakref.WeakValueDictionary()

    def _lock_for(self,asset_id:str,project_id:str)->asyncio.Lock:
        key=(asset_id,project_id);lock=self._locks.get(key)
        if lock is None:
            lock=asyncio.Lock();self._locks[key]=lock
        return lock

    async def ensure_media(self,db,*,client_id:str,asset_id:str,project_id:str,sdk)->str:
        lock=self._lock_for(asset_id,project_id)
        async with lock:
            mapping=db.scalar(select(ProjectMediaMapping).where(ProjectMediaMapping.asset_id==asset_id,ProjectMediaMapping.provider_project_id==project_id))
            if mapping:
                media_id=mapping.provider_media_id;db.commit();return media_id
            asset=db.scalar(select(MediaAsset).where(MediaAsset.id==asset_id,MediaAsset.client_id==client_id,MediaAsset.status=="ready",MediaAsset.type=="image"))
            if not asset:
                db.rollback();raise ValueError(f"asset_not_ready:{asset_id}")
            if not asset.mime_type.lower().startswith("image/"):
                db.rollback();raise ValueError(f"asset_not_image:{asset_id}")
            if asset.size_bytes is not None and asset.size_bytes>self.assets.settings.max_reference_bytes:
                db.rollback();raise ValueError(f"asset_too_large:{asset_id}")
            db.commit();data=await self.assets.bytes_for_asset(asset)
            result=await sdk.upload_image(base64.b64encode(data).decode("ascii"),asset.mime_type,project_id,asset.filename or "reference.png")
            if result.get("error") or not result.get("media_id"):raise RuntimeError(result.get("error") or "flow_upload_failed")
            mapping=ProjectMediaMapping(id=new_id("map"),asset_id=asset_id,provider="google_flow",provider_project_id=project_id,provider_media_id=result["media_id"])
            db.add(mapping);db.commit();return mapping.provider_media_id
