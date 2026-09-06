from __future__ import annotations

from app.projects import ProjectStore, ProviderJob


_NO_GENERIC_TIMEOUT_SECONDS = 2_147_483_647


class RuntimeProjectStore(ProjectStore):
    """ProjectStore with runtime-safe timeout semantics for dispatch claims.

    A job in ``dispatching`` may already have reached Google Flow. Generic
    image/video age limits therefore must not mark it failed while the owning
    worker is still inside the provider request. Dispatch recovery is governed
    exclusively by the longer dispatch lease and always records an
    outcome-unknown failure.
    """

    def __init__(
        self,
        path: str,
        *,
        asset_store_path: str | None = None,
        dispatch_lease_seconds: int = 900,
    ):
        super().__init__(path, asset_store_path=asset_store_path)
        self.dispatch_lease_seconds = max(1, int(dispatch_lease_seconds))

    def _job_status(self, job_id: str) -> str | None:
        with self._lock:
            row = self._db().execute(
                "SELECT status FROM provider_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return str(row["status"]) if row is not None else None

    def get_job(
        self,
        job_id: str,
        *,
        image_timeout_seconds: int = 120,
        video_queue_timeout_seconds: int = 180,
        video_running_timeout_seconds: int = 600,
    ) -> ProviderJob | None:
        if self._job_status(job_id) == "dispatching":
            # Reconcile genuinely abandoned claims first. A live claim younger
            # than the dispatch lease must not be failed by the shorter generic
            # image/video timeouts used for queued/running states.
            self.fail_abandoned_dispatches(self.dispatch_lease_seconds)
            if self._job_status(job_id) == "dispatching":
                return super().get_job(
                    job_id,
                    image_timeout_seconds=_NO_GENERIC_TIMEOUT_SECONDS,
                    video_queue_timeout_seconds=_NO_GENERIC_TIMEOUT_SECONDS,
                    video_running_timeout_seconds=_NO_GENERIC_TIMEOUT_SECONDS,
                )

        return super().get_job(
            job_id,
            image_timeout_seconds=image_timeout_seconds,
            video_queue_timeout_seconds=video_queue_timeout_seconds,
            video_running_timeout_seconds=video_running_timeout_seconds,
        )

    def fail_expired_jobs(
        self,
        *,
        image_timeout_seconds: int,
        video_queue_timeout_seconds: int,
        video_running_timeout_seconds: int,
    ) -> int:
        """Expire only states whose outcome is known safe to conclude.

        ``dispatching`` is deliberately excluded and is handled by
        ``fail_abandoned_dispatches`` after the dispatch lease instead.
        """
        total = 0
        with self._lock:
            db = self._db()
            total += db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'IMAGE_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 0,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE media_type = 'image'
                  AND status = 'queued'
                  AND created_at <= datetime('now', ?)
                """,
                (
                    f"Image generation timed out after exceeding {image_timeout_seconds}s limit.",
                    f"-{max(1, image_timeout_seconds)} seconds",
                ),
            ).rowcount

            total += db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'QUEUE_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 0,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE media_type = 'video'
                  AND status = 'queued'
                  AND created_at <= datetime('now', ?)
                """,
                (
                    f"Video job timed out waiting in queue after exceeding {video_queue_timeout_seconds}s limit.",
                    f"-{max(1, video_queue_timeout_seconds)} seconds",
                ),
            ).rowcount

            total += db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'VIDEO_POLL_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 1,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE media_type = 'video'
                  AND status = 'running'
                  AND COALESCE(running_at, created_at) <= datetime('now', ?)
                """,
                (
                    f"Video generation timed out after exceeding {video_running_timeout_seconds}s limit.",
                    f"-{max(1, video_running_timeout_seconds)} seconds",
                ),
            ).rowcount

            if total:
                db.commit()
        return total
