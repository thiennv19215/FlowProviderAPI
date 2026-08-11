from __future__ import annotations

from sqlalchemy import select

from app.db.models import GenerationJob, WorkspaceProject
from app.jobs.repository import active_count_for_account

VIDEO_MIN_CREDITS={"video":20}
OMNI_CREDIT_COST={2:10,4:15,8:25,10:30}


def estimated_credit_cost(kind: str, payload: dict | None = None) -> int:
    payload=payload or {}
    if kind=="omni":
        return OMNI_CREDIT_COST.get(int(payload.get("duration",8)),30)
    return VIDEO_MIN_CREDITS.get(kind,0)


class GlobalScheduler:
    def __init__(self, bridge): self.bridge=bridge

    def _reserved_credits(self, db, account_id: str) -> int:
        rows=db.scalars(select(GenerationJob).where(
            GenerationJob.provider_account_id==account_id,
            GenerationJob.status=="running",
            GenerationJob.stage.in_(["preparing","dispatching","provider_running","storing_outputs"]),
        ))
        return sum(estimated_credit_cost(job.kind,job.request_payload) for job in rows)

    def _workspace_accounts(self,db,*,client_id:str|None,workspace_key:str|None,provider:str|None)->set[str]:
        if not client_id or not workspace_key or not provider:return set()
        return set(db.scalars(select(WorkspaceProject.provider_account_id).where(
            WorkspaceProject.client_id==client_id,
            WorkspaceProject.workspace_key==workspace_key,
            WorkspaceProject.provider==provider,
        )))

    def choose_account(self, db, *, kind: str, payload: dict | None = None, client_id: str|None = None, workspace_key: str|None = None, provider: str|None = None) -> str:
        """Choose a ready account with sticky preference for an existing workspace project.

        Existing workspace/account mappings win while they still have slot and credit
        capacity. If every mapped account is saturated, unavailable, or underfunded,
        the scheduler deliberately spills over to another eligible account so API
        throughput is not blocked; ProjectRegistry will then reuse/create that
        workspace's project on the selected account.
        """
        required=estimated_credit_cost(kind,payload)
        sticky_accounts=self._workspace_accounts(db,client_id=client_id,workspace_key=workspace_key,provider=provider)
        candidates=[]
        for conn in self.bridge.ready_connections():
            active=active_count_for_account(db,conn.id)
            if active>=conn.max_slots:continue
            available=(conn.credits or 0)-self._reserved_credits(db,conn.id)
            if available<required:continue
            sticky_rank=0 if conn.id in sticky_accounts else 1
            candidates.append((sticky_rank,active,-available,conn.connected_at,conn.id))
        if not candidates: raise RuntimeError("no_ready_provider_account")
        candidates.sort();return candidates[0][4]
