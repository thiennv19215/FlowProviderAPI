from __future__ import annotations

from app.jobs.repository import active_count_for_account

VIDEO_MIN_CREDITS={"video":20,"omni":10}


class GlobalScheduler:
    def __init__(self, bridge): self.bridge=bridge

    def choose_account(self, db, *, kind: str) -> str:
        min_credits=VIDEO_MIN_CREDITS.get(kind,0)
        candidates=[]
        for conn in self.bridge.ready_connections(min_credits=min_credits):
            active=active_count_for_account(db,conn.id)
            if active>=conn.max_slots:continue
            candidates.append((active,-(conn.credits or 0),conn.connected_at,conn.id))
        if not candidates: raise RuntimeError("no_ready_provider_account")
        candidates.sort();return candidates[0][3]
