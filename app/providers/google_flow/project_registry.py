from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.models import WorkspaceProject
from app.ids import new_id


class ProjectRegistry:
    """Resolve one Flow project per client/workspace/account without holding DB transactions across network calls."""

    def __init__(self):
        self._locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    def _lock_for(self, client_id: str, workspace_key: str, account_id: str) -> asyncio.Lock:
        return self._locks.setdefault((client_id, workspace_key, account_id), asyncio.Lock())

    async def get_or_create(self, db, *, client_id: str, workspace_key: str, account_id: str, sdk) -> str:
        async with self._lock_for(client_id, workspace_key, account_id):
            existing=db.scalar(select(WorkspaceProject).where(
                WorkspaceProject.client_id==client_id,
                WorkspaceProject.workspace_key==workspace_key,
                WorkspaceProject.provider=="google_flow",
                WorkspaceProject.provider_account_id==account_id,
            ))
            if existing:
                project_id=existing.provider_project_id
                db.commit()
                return project_id

            db.commit()
            title=f"Provider {workspace_key}"[:120]
            result=await sdk.create_project(title)
            if result.get("error") or not result.get("project_id"):
                raise RuntimeError(result.get("error") or "flow_project_create_failed")
            row=WorkspaceProject(
                id=new_id("wsp"),client_id=client_id,workspace_key=workspace_key,
                provider="google_flow",provider_account_id=account_id,
                provider_project_id=result["project_id"],
            )
            db.add(row);db.commit()
            return row.provider_project_id
