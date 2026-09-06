from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass
from dataclasses import field

from app.extension.manager import ExtensionManager
from app.projects import ProjectStore
from app.providers.google_flow.browser_bridge import FlowBridge
from app.runtime_store import RuntimeProjectStore
from app.workers.job_worker import JobWorker


class ConfiguredJobWorker(JobWorker):
    """Apply the runtime's global dispatch-concurrency setting to worker batches."""

    async def process_queued_jobs(self, max_concurrent: int | None = None) -> None:
        configured = max(1, int(getattr(self.runtime.settings, "worker_concurrency", 4)))
        limit = configured if max_concurrent is None else min(configured, max(1, int(max_concurrent)))
        await super().process_queued_jobs(max_concurrent=limit)


@dataclass
class Runtime:
    settings: object
    bridge: FlowBridge
    extension_manager: ExtensionManager
    projects: ProjectStore
    project_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    project_sync_sessions: dict[str, tuple[float, str]] = field(default_factory=dict)
    media_locks: weakref.WeakValueDictionary = field(default_factory=weakref.WeakValueDictionary)
    media_transfer_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(2))
    active_jobs: dict[str, int] = field(default_factory=dict)
    active_image_jobs: dict[str, int] = field(default_factory=dict)
    active_video_jobs: dict[str, int] = field(default_factory=dict)
    reserved_credits: dict[str, int] = field(default_factory=dict)
    inline_images: dict[str, list] = field(default_factory=dict)
    job_admission_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_job_admissions: int = 0
    worker: JobWorker | None = None

    def connection_load(self, connection) -> int:
        return max(
            self.active_jobs.get(connection.id, 0),
            self.bridge.pending_count(connection.id),
        )

    def available_credits(self, connection) -> int | None:
        if not isinstance(getattr(connection, "credits", None), int):
            return None
        return connection.credits - self.reserved_credits.get(connection.id, 0)

    def can_reserve(self, connection, credit_cost: int = 0, job_type: str | None = None) -> bool:
        image_capacity = getattr(connection, "max_image_slots", getattr(self.settings, "account_image_slot_capacity", 4))
        video_capacity = getattr(connection, "max_video_slots", getattr(self.settings, "account_video_slot_capacity", getattr(connection, "max_slots", 3)))

        if job_type == "image":
            if self.active_image_jobs.get(connection.id, 0) >= image_capacity:
                return False
        elif job_type == "video" or credit_cost > 0:
            if self.active_video_jobs.get(connection.id, 0) >= video_capacity:
                return False
        else:
            if self.connection_load(connection) >= getattr(connection, "max_slots", 3):
                return False

        if credit_cost:
            available = self.available_credits(connection)
            if available is None or available < credit_cost:
                return False
        return True

    def reserve_connection(self, connection, credit_cost: int = 0, job_type: str | None = None) -> bool:
        if not self.can_reserve(connection, credit_cost, job_type=job_type):
            return False
        self.active_jobs[connection.id] = self.active_jobs.get(connection.id, 0) + 1
        if job_type == "image":
            self.active_image_jobs[connection.id] = self.active_image_jobs.get(connection.id, 0) + 1
        elif job_type == "video" or credit_cost > 0:
            self.active_video_jobs[connection.id] = self.active_video_jobs.get(connection.id, 0) + 1
        if credit_cost:
            self.reserved_credits[connection.id] = (
                self.reserved_credits.get(connection.id, 0) + credit_cost
            )
        return True

    def release_connection(self, connection_id: str, credit_cost: int = 0, job_type: str | None = None) -> None:
        remaining = self.active_jobs.get(connection_id, 0) - 1
        if remaining > 0:
            self.active_jobs[connection_id] = remaining
        else:
            self.active_jobs.pop(connection_id, None)

        if job_type == "image":
            rem_img = self.active_image_jobs.get(connection_id, 0) - 1
            if rem_img > 0:
                self.active_image_jobs[connection_id] = rem_img
            else:
                self.active_image_jobs.pop(connection_id, None)
        elif job_type == "video" or credit_cost > 0:
            rem_vid = self.active_video_jobs.get(connection_id, 0) - 1
            if rem_vid > 0:
                self.active_video_jobs[connection_id] = rem_vid
            else:
                self.active_video_jobs.pop(connection_id, None)

        if credit_cost:
            credits_remaining = self.reserved_credits.get(connection_id, 0) - credit_cost
            if credits_remaining > 0:
                self.reserved_credits[connection_id] = credits_remaining
            else:
                self.reserved_credits.pop(connection_id, None)

        self.wake_worker()

    def wake_worker(self) -> None:
        if self.worker is not None and hasattr(self.worker, "wake"):
            self.worker.wake()

    def select_connection(self, available):
        def _sort_key(item):
            avail = self.available_credits(item)
            credit_tier = 1 if (avail is not None and avail < 20) else 0
            credit_val = -(avail if avail is not None else 0)
            return (
                credit_tier,
                self.connection_load(item),
                credit_val,
                item.connected_at if hasattr(item, "connected_at") else 0,
                item.installation_id,
            )

        return min(available, key=_sort_key)

    def durable_job_status_counts(self) -> dict[str, int]:
        """Return aggregate durable active-job counts for safe operator telemetry."""
        counts = {"queued": 0, "dispatching": 0, "running": 0}
        with self.projects._lock:
            rows = self.projects._db().execute(
                "SELECT status, COUNT(*) FROM provider_jobs "
                "WHERE status IN ('queued', 'dispatching', 'running') GROUP BY status"
            ).fetchall()
        for status, count in rows:
            if status in counts:
                counts[str(status)] = int(count)
        return counts

    def durable_active_job_count(self) -> int:
        """Count durable work that still consumes queue/worker capacity."""
        return sum(self.durable_job_status_counts().values())

    async def try_reserve_job_admission(self, idempotency_key: str | None) -> tuple[bool, bool]:
        """Atomically reserve one HTTP admission slot for a new generation request.

        Returns ``(allowed, reserved)``. Existing idempotency keys are always
        allowed through without consuming a temporary admission slot so the
        route can return/validate the durable original job even when the queue
        is full.
        """
        normalized_key = (idempotency_key or "").strip() or None
        async with self.job_admission_lock:
            if normalized_key and self.projects.get_job_by_idempotency_key(normalized_key):
                return True, False
            active = self.durable_active_job_count()
            limit = int(getattr(self.settings, "job_queue_max_active", 200))
            if active + self.pending_job_admissions >= limit:
                return False, False
            self.pending_job_admissions += 1
            return True, True

    async def release_job_admission(self) -> None:
        async with self.job_admission_lock:
            if self.pending_job_admissions > 0:
                self.pending_job_admissions -= 1

    def project_lock(self, installation_id: str) -> asyncio.Lock:
        return self.project_locks.setdefault(installation_id, asyncio.Lock())

    def project_is_synced(self, connection, account_key: str) -> bool:
        session = (float(getattr(connection, "connected_at", 0)), account_key)
        return self.project_sync_sessions.get(connection.id) == session

    def mark_project_synced(self, connection, account_key: str) -> None:
        self.project_sync_sessions[connection.id] = (
            float(getattr(connection, "connected_at", 0)), account_key,
        )

    def media_lock(self, account_key: str, project_id: str, digest: str) -> asyncio.Lock:
        key = (account_key, project_id, digest)
        lock = self.media_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.media_locks[key] = lock
        return lock


def build_runtime(settings) -> Runtime:
    bridge = FlowBridge(
        flow_api_key=settings.flow_api_key,
        slot_capacity=settings.account_slot_capacity,
        image_slot_capacity=getattr(settings, "account_image_slot_capacity", 4),
        video_slot_capacity=getattr(settings, "account_video_slot_capacity", 3),
        cooldown_seconds=settings.account_rate_limit_cooldown_seconds,
    )
    projects = RuntimeProjectStore(
        settings.project_store_path,
        asset_store_path=settings.asset_store_path,
        dispatch_lease_seconds=getattr(settings, "worker_dispatch_lease_seconds", 900),
    )
    projects.prune(asset_retention_days=settings.asset_retention_days)
    runtime = Runtime(
        settings,
        bridge,
        ExtensionManager(bridge),
        projects,
    )
    runtime.worker = ConfiguredJobWorker(runtime)
    return runtime
