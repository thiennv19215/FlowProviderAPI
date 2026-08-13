from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, or_, select

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.serializers import job_dict
from app.api.schemas import JobListResponse, JobOutput
from app.db.models import GenerationJob

router = APIRouter(prefix="/v1/status", tags=["Status"])


@router.get("/{task_id}", response_model=JobOutput, summary="Get generation status")
def get_generation_status(
    task_id: str,
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
):
    job = db.scalar(
        select(GenerationJob).where(
            GenerationJob.id == task_id,
            GenerationJob.client_id == client.id,
        )
    )
    if not job:
        raise APIError(
            404,
            "JOB_NOT_FOUND",
            "The requested generation job does not exist.",
        )
    return job_dict(request.app.state.runtime, db, job)


@router.post(
    "/{task_id}/cancel",
    response_model=JobOutput,
    summary="Cancel generation",
)
def cancel_generation(
    task_id: str,
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
):
    job = db.scalar(
        select(GenerationJob).where(
            GenerationJob.id == task_id,
            GenerationJob.client_id == client.id,
        )
    )
    if not job:
        raise APIError(
            404,
            "JOB_NOT_FOUND",
            "The requested generation job does not exist.",
        )
    if job.status in {"done", "failed", "canceled"}:
        return job_dict(request.app.state.runtime, db, job)

    job.cancel_requested = True
    if job.status == "queued":
        from app.db.models import utcnow

        job.status = "canceled"
        job.stage = "completed"
        job.completed_at = utcnow()
    db.commit()
    db.refresh(job)
    return job_dict(request.app.state.runtime, db, job)


@router.get("", response_model=JobListResponse, summary="List generation statuses")
def list_generation_statuses(
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
    limit: int = Query(default=20, ge=1, le=100),
    after: str | None = None,
    status: Literal["queued", "running", "done", "failed", "canceled"] | None = None,
    type: Literal["image", "video", "omni"] | None = Query(default=None),
):
    q = select(GenerationJob).where(GenerationJob.client_id == client.id)
    if status:
        q = q.where(GenerationJob.status == status)
    if type:
        q = q.where(GenerationJob.kind == type)
    if after:
        cursor = db.scalar(
            select(GenerationJob).where(
                GenerationJob.id == after,
                GenerationJob.client_id == client.id,
            )
        )
        if not cursor:
            raise APIError(
                400,
                "INVALID_CURSOR",
                "The status cursor is invalid for this client.",
                field="after",
            )
        q = q.where(
            or_(
                GenerationJob.created_at < cursor.created_at,
                and_(
                    GenerationJob.created_at == cursor.created_at,
                    GenerationJob.id < cursor.id,
                ),
            )
        )
    rows = list(
        db.scalars(
            q.order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc()).limit(
                limit + 1
            )
        )
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "object": "list",
        "data": [job_dict(request.app.state.runtime, db, job) for job in rows],
        "has_more": has_more,
        "next_cursor": rows[-1].id if has_more and rows else None,
    }
