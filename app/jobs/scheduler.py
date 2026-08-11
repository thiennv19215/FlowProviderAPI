from __future__ import annotations

from sqlalchemy import select

from app.db.models import GenerationJob
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

    def choose_account(self, db, *, kind: str, payload: dict | None = None) -> str:
        required=estimated_credit_cost(kind,payload)
        candidates=[]
        for conn in self.bridge.ready_connections():
            active=active_count_for_account(db,conn.id)
            if active>=conn.max_slots:continue
            available=(conn.credits or 0)-self._reserved_credits(db,conn.id)
            if available<required:continue
            candidates.append((active,-available,conn.connected_at,conn.id))
        if not candidates: raise RuntimeError("no_ready_provider_account")
        candidates.sort();return candidates[0][3]
