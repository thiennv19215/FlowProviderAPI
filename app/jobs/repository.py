from __future__ import annotations

from datetime import timedelta, timezone

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import ApiClient, GenerationJob, utcnow
from app.ids import new_id


class IdempotencyConflict(RuntimeError):
    """Raised when one client reuses a key for a different logical submission."""


def _advisory_xact_lock(db, key: str) -> None:
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": key},
        )


def _as_utc(value):
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def get_job_by_idempotency(db, *, client_id: str, idempotency_key: str):
    return db.scalar(
        select(GenerationJob).where(
            GenerationJob.client_id == client_id,
            GenerationJob.idempotency_key == idempotency_key,
        )
    )


def _matches_submission(
    job: GenerationJob,
    *,
    kind: str,
    provider: str,
    model: str | None,
    payload: dict,
) -> bool:
    return (
        job.kind == kind
        and job.provider == provider
        and job.model == model
        and job.request_payload == payload
    )


def assert_idempotent_submission(
    job: GenerationJob,
    *,
    kind: str,
    provider: str,
    model: str | None,
    payload: dict,
) -> None:
    if not _matches_submission(
        job,
        kind=kind,
        provider=provider,
        model=model,
        payload=payload,
    ):
        raise IdempotencyConflict("Idempotency key was already used for another submission.")


def create_job(
    db,
    *,
    client,
    kind: str,
    provider: str,
    model: str | None,
    workspace_key: str,
    payload: dict,
    request_id: str | None = None,
    idempotency_key: str | None = None,
):
    """Create one durable Provider job or return the matching idempotent job."""
    clean_key = idempotency_key.strip() if isinstance(idempotency_key, str) else None
    if clean_key:
        existing = get_job_by_idempotency(
            db,
            client_id=client.id,
            idempotency_key=clean_key,
        )
        if existing is not None:
            assert_idempotent_submission(
                existing,
                kind=kind,
                provider=provider,
                model=model,
                payload=payload,
            )
            return existing

    result_payload = {"_request_id": request_id} if request_id else None
    row = GenerationJob(
        id=new_id("job"),
        client_id=client.id,
        kind=kind,
        provider=provider,
        model=model,
        workspace_key=workspace_key,
        status="queued",
        stage="queued",
        priority=client.priority,
        request_payload=payload,
        result_payload=result_payload,
        idempotency_key=clean_key,
        next_run_at=utcnow(),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if clean_key:
            winner = get_job_by_idempotency(
                db,
                client_id=client.id,
                idempotency_key=clean_key,
            )
            if winner is not None:
                assert_idempotent_submission(
                    winner,
                    kind=kind,
                    provider=provider,
                    model=model,
                    payload=payload,
                )
                return winner
        raise
    db.refresh(row)
    return row


def active_count_for_client(db, client_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(GenerationJob)
            .where(
                GenerationJob.client_id == client_id,
                GenerationJob.status == "running",
            )
        )
        or 0
    )


def active_count_for_account(db, account_id: str) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(GenerationJob)
            .where(
                GenerationJob.provider_account_id == account_id,
                GenerationJob.status == "running",
                GenerationJob.stage.in_(
                    ["preparing", "dispatching", "provider_running", "storing_outputs"]
                ),
            )
        )
        or 0
    )


def _claimable_query(now):
    active = (
        select(
            GenerationJob.client_id.label("client_id"),
            func.count().label("active_count"),
        )
        .where(GenerationJob.status == "running")
        .group_by(GenerationJob.client_id)
        .subquery()
    )
    return (
        select(GenerationJob)
        .join(ApiClient, ApiClient.id == GenerationJob.client_id)
        .outerjoin(active, active.c.client_id == GenerationJob.client_id)
        .where(
            GenerationJob.status == "queued",
            GenerationJob.next_run_at <= now,
            ApiClient.enabled.is_(True),
            func.coalesce(active.c.active_count, 0) < ApiClient.max_concurrent_jobs,
        )
        .order_by(
            GenerationJob.priority.desc(),
            GenerationJob.created_at.asc(),
        )
        .with_for_update(skip_locked=True, of=GenerationJob)
        .limit(1)
    )


def claim_next(db, *, worker_id: str, lease_seconds: int):
    now = utcnow()
    job = db.scalar(_claimable_query(now))
    if not job:
        return None
    _advisory_xact_lock(db, f"client-capacity:{job.client_id}")
    client = db.scalar(
        select(ApiClient).where(ApiClient.id == job.client_id).with_for_update()
    )
    db.refresh(job)
    if (
        not client
        or not client.enabled
        or job.status != "queued"
        or _as_utc(job.next_run_at) > now
    ):
        db.rollback()
        return None
    if active_count_for_client(db, job.client_id) >= client.max_concurrent_jobs:
        db.rollback()
        return None
    job.status = "running"
    job.stage = "preparing"
    job.started_at = job.started_at or now
    job.lease_owner = worker_id
    job.lease_expires_at = now + timedelta(seconds=lease_seconds)
    job.attempt_count += 1
    db.commit()
    db.refresh(job)
    return job


def due_poll(db, *, worker_id: str, lease_seconds: int):
    now = utcnow()
    query = (
        select(GenerationJob)
        .where(
            GenerationJob.status == "running",
            GenerationJob.stage.in_(["provider_running", "storing_outputs"]),
            GenerationJob.next_run_at <= now,
            or_(
                GenerationJob.lease_expires_at.is_(None),
                GenerationJob.lease_expires_at <= now,
            ),
        )
        .order_by(GenerationJob.next_run_at.asc())
        .with_for_update(skip_locked=True, of=GenerationJob)
        .limit(1)
    )
    job = db.scalar(query)
    if job:
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        db.commit()
        db.refresh(job)
    return job


def recover_expired(db):
    now = utcnow()
    rows = list(
        db.scalars(
            select(GenerationJob).where(
                GenerationJob.status == "running",
                GenerationJob.lease_expires_at.is_not(None),
                GenerationJob.lease_expires_at < now,
            )
        )
    )
    for job in rows:
        if job.stage == "provider_running":
            job.lease_owner = None
            job.lease_expires_at = None
            job.next_run_at = now
        elif job.stage == "storing_outputs" and isinstance(
            (job.result_payload or {}).get("_provider_outputs"), list
        ):
            job.lease_owner = None
            job.lease_expires_at = None
            job.next_run_at = now
        elif job.stage in {"preparing"}:
            job.status = "queued"
            job.stage = "queued"
            job.lease_owner = None
            job.lease_expires_at = None
            job.next_run_at = now
        else:
            job.status = "failed"
            job.error_code = "INTERRUPTED_DURING_DISPATCH"
            job.error_message = (
                "Worker lease expired after provider dispatch may have started; "
                "request was not replayed to avoid duplicate generation."
            )
            job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
    if rows:
        db.commit()
    return len(rows)
