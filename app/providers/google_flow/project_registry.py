from __future__ import annotations

from sqlalchemy import select

from app.db.models import WorkspaceProject
from app.ids import new_id


class ProjectRegistry:
    async def get_or_create(self, db, *, client_id: str, workspace_key: str, account_id: str, sdk) -> str:
        existing=db.scalar(select(WorkspaceProject).where(WorkspaceProject.client_id==client_id,WorkspaceProject.workspace_key==workspace_key,WorkspaceProject.provider=="google_flow",WorkspaceProject.provider_account_id==account_id))
        if existing:return existing.provider_project_id
        title=f"Provider {workspace_key}"[:120]
        result=await sdk.create_project(title)
        if result.get("error") or not result.get("project_id"):
            raise RuntimeError(result.get("error") or "flow_project_create_failed")
        row=WorkspaceProject(id=new_id("wsp"),client_id=client_id,workspace_key=workspace_key,provider="google_flow",provider_account_id=account_id,provider_project_id=result["project_id"])
        db.add(row);db.commit();return row.provider_project_id
