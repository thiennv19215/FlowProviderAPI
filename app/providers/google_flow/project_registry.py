from __future__ import annotations

import asyncio
import weakref

from sqlalchemy import select

from app.db.models import WorkspaceProject
from app.ids import new_id


class ProjectRegistry:
    """Resolve one Flow project per API client/account without holding DB transactions across network calls."""

    def __init__(self):
        self._locks: weakref.WeakValueDictionary[tuple[str,str],asyncio.Lock]=weakref.WeakValueDictionary()

    def _lock_for(self,client_id:str,account_id:str)->asyncio.Lock:
        key=(client_id,account_id);lock=self._locks.get(key)
        if lock is None:
            lock=asyncio.Lock();self._locks[key]=lock
        return lock

    async def get_or_create(self,db,*,client_id:str,account_id:str,sdk)->str:
        lock=self._lock_for(client_id,account_id)
        async with lock:
            existing=db.scalar(select(WorkspaceProject).where(WorkspaceProject.client_id==client_id,WorkspaceProject.provider=="google_flow",WorkspaceProject.provider_account_id==account_id).order_by(WorkspaceProject.created_at.asc()))
            if existing:
                project_id=existing.provider_project_id;db.commit();return project_id
            db.commit();result=await sdk.create_project("FlowProvider")
            if result.get("error") or not result.get("project_id"):raise RuntimeError(result.get("error") or "flow_project_create_failed")
            row=WorkspaceProject(id=new_id("wsp"),client_id=client_id,workspace_key="__api_client__",provider="google_flow",provider_account_id=account_id,provider_project_id=result["project_id"])
            db.add(row);db.commit();return row.provider_project_id
