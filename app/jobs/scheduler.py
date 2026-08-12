from __future__ import annotations

from sqlalchemy import select, text

from app.db.models import GenerationJob, WorkspaceProject
from app.jobs.repository import active_count_for_account
from app.providers.base import ProviderError

VIDEO_MIN_CREDITS = {"video": 20}
OMNI_CREDIT_COST = {2: 10, 4: 15, 8: 25, 10: 30}


def estimated_credit_cost(kind: str, payload: dict | None = None) -> int:
    payload = payload or {}
    if kind == "omni":
        return OMNI_CREDIT_COST.get(int(payload.get("duration", 8)), 30)
    return VIDEO_MIN_CREDITS.get(kind, 0)


class GlobalScheduler:
    def __init__(self, bridge):
        self.bridge = bridge

    @staticmethod
    def _lock_account(db, account_id: str) -> None:
        if db.bind and db.bind.dialect.name == "postgresql":
            db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"provider-account:{account_id}"},
            )

    def _reserved_credits(self, db, account_id: str) -> int:
        rows = db.scalars(
            select(GenerationJob).where(
                GenerationJob.provider_account_id == account_id,
                GenerationJob.status == "running",
                GenerationJob.stage.in_(
                    ["preparing", "dispatching", "provider_running", "storing_outputs"]
                ),
            )
        )
        return sum(estimated_credit_cost(job.kind, job.request_payload) for job in rows)

    def _client_accounts(
        self,
        db,
        *,
        client_id: str | None,
        provider: str | None,
    ) -> set[str]:
        if not client_id or not provider:
            return set()
        return set(
            db.scalars(
                select(WorkspaceProject.provider_account_id).where(
                    WorkspaceProject.client_id == client_id,
                    WorkspaceProject.provider == provider,
                )
            )
        )

    def _ranked_candidates(
        self,
        db,
        *,
        kind: str,
        payload: dict | None,
        client_id: str | None,
        provider: str | None,
    ) -> list[str]:
        required = estimated_credit_cost(kind, payload)
        sticky_accounts = self._client_accounts(
            db,
            client_id=client_id,
            provider=provider,
        )
        candidates = []
        for conn in self.bridge.ready_connections():
            active = active_count_for_account(db, conn.id)
            available = (conn.credits or 0) - self._reserved_credits(db, conn.id)
            if active >= conn.max_slots or available < required:
                continue
            candidates.append(
                (
                    0 if conn.id in sticky_accounts else 1,
                    active,
                    -available,
                    conn.connected_at,
                    conn.id,
                )
            )
        candidates.sort()
        return [item[4] for item in candidates]

    def reserve_account(self, db, job: GenerationJob) -> str:
        required = estimated_credit_cost(job.kind, job.request_payload)
        for account_id in self._ranked_candidates(
            db,
            kind=job.kind,
            payload=job.request_payload,
            client_id=job.client_id,
            provider=job.provider,
        ):
            self._lock_account(db, account_id)
            conn = self.bridge.get(account_id)
            if not conn or not conn.ready:
                continue
            active = active_count_for_account(db, account_id)
            available = (conn.credits or 0) - self._reserved_credits(db, account_id)
            if active >= conn.max_slots or available < required:
                continue
            job.provider_account_id = account_id
            db.commit()
            db.refresh(job)
            return account_id
        raise ProviderError(
            "PROVIDER_ACCOUNT_UNAVAILABLE",
            "No ready Google Flow account is currently available.",
            status_code=503,
            retryable=True,
        )
