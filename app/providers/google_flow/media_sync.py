from __future__ import annotations

import base64
from sqlalchemy import select

from app.db.models import MediaAsset, ProjectMediaMapping
from app.ids import new_id


class MediaSync:
    def __init__(self, asset_service): self.assets=asset_service

    async def ensure_media(self, db, *, client_id: str, asset_id: str, project_id: str, sdk) -> str:
        mapping=db.scalar(select(ProjectMediaMapping).where(ProjectMediaMapping.asset_id==asset_id,ProjectMediaMapping.provider_project_id==project_id))
        if mapping:return mapping.provider_media_id
        asset=db.scalar(select(MediaAsset).where(MediaAsset.id==asset_id,MediaAsset.client_id==client_id,MediaAsset.status=="ready"))
        if not asset: raise ValueError(f"asset_not_ready:{asset_id}")
        data=await self.assets.bytes_for_asset(asset)
        result=await sdk.upload_image(base64.b64encode(data).decode("ascii"),asset.mime_type,project_id,asset.filename or "reference.png")
        if result.get("error") or not result.get("media_id"): raise RuntimeError(result.get("error") or "flow_upload_failed")
        mapping=ProjectMediaMapping(id=new_id("map"),asset_id=asset_id,provider="google_flow",provider_project_id=project_id,provider_media_id=result["media_id"])
        db.add(mapping);db.commit();return mapping.provider_media_id
