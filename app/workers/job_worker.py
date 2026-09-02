from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    CAPTCHA_VIDEO,
    OMNI_FLASH_CREDIT_COST,
    VIDEO_I2V_URL,
    VIDEO_OMNI_URL,
    VIDEO_POLL_URL,
)

logger = logging.getLogger(__name__)


def _account_key(connection: Any) -> str:
    email = str(getattr(connection, "account_email", "") or "").strip().lower()
    return f"{connection.installation_id}\n{email}" if email else str(connection.installation_id)


class JobWorker:
    """Background worker for asynchronous job dispatch and result polling."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_poll_time: dict[str, float] = {}

    async def start(self) -> None:
        if not getattr(self.runtime.settings, "worker_enabled", True):
            logger.info("JobWorker disabled by configuration.")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="flow-provider-job-worker")
        logger.info("JobWorker started successfully.")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await asyncio.gather(self._task, return_exceptions=True)
            except Exception:
                pass
        logger.info("JobWorker stopped.")

    async def _run_loop(self) -> None:
        poll_interval = float(getattr(self.runtime.settings, "worker_poll_seconds", 3.0))
        while self._running:
            try:
                await self.process_queued_jobs()
                await self.poll_running_jobs()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in JobWorker loop")
            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break

    async def process_queued_jobs(self) -> None:
        """Pick next queued job and dispatch to an available extension account."""
        job = self.runtime.projects.claim_next_queued_job()
        if not job or job.status != "queued":
            return

        cost = 20
        if job.job_type == "omni":
            duration = job.request_payload.get("duration_seconds", 8)
            cost = max(20, OMNI_FLASH_CREDIT_COST.get(duration, 25))

        available = [
            conn
            for conn in self.runtime.bridge.ready_connections()
            if not getattr(conn, "simulation_mode", False)
            and self.runtime.can_reserve(conn, cost)
        ]
        if not available:
            # All accounts are currently busy or lacking credits; leave in queue
            return

        connection = self.runtime.select_connection(available)
        if not self.runtime.reserve_connection(connection, cost):
            return

        client = BoundFlowClient(self.runtime.bridge, connection.id)
        account_key = _account_key(connection)

        try:
            from app.api.generations import _managed_project, _api

            resolved_project_id = await _managed_project(self.runtime, connection, client)
            payload = job.request_payload

            if job.job_type == "omni":
                duration_seconds = payload.get("duration_seconds", 8)
                model_name = {4: "abra_r2v_4s", 6: "abra_r2v_6s", 8: "abra_r2v_8s", 10: "abra_r2v_10s"}.get(
                    duration_seconds, "abra_r2v_8s"
                )
                ref_media_ids = payload.get("reference_media_ids", [])
                tier = connection.paygate_tier or "PAYGATE_TIER_ONE"
                body = {
                    "clientContext": {"projectId": resolved_project_id, "tool": "PINHOLE"},
                    "userPaygateTier": tier,
                    "requests": [{
                        "aspectRatio": payload.get("aspect_ratio", "VIDEO_ASPECT_RATIO_PORTRAIT"),
                        "videoModelName": model_name,
                        "prompt": payload.get("prompt", ""),
                        "metadata": {},
                        "referenceImages": [
                            {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                            for mid in ref_media_ids
                        ],
                    }],
                    "useV2ModelConfig": True,
                }
                result = await _api(client, url=VIDEO_OMNI_URL, body=body, captcha_action=CAPTCHA_VIDEO)
            else:
                body = {
                    "clientContext": {"projectId": resolved_project_id, "tool": "PINHOLE"},
                    "userPaygateTier": connection.paygate_tier or "PAYGATE_TIER_ONE",
                    "requests": [{
                        "aspectRatio": payload.get("aspect_ratio", "VIDEO_ASPECT_RATIO_LANDSCAPE"),
                        "videoModelName": payload.get("video_model", "veo_2_relaxed"),
                        "prompt": payload.get("prompt", ""),
                        "startImageMediaId": payload.get("start_media_id"),
                        "metadata": {},
                    }],
                }
                result = await _api(client, url=VIDEO_I2V_URL, body=body, captcha_action=CAPTCHA_VIDEO)

            status = result.get("status") if isinstance(result, dict) else None
            if not isinstance(status, int) or status >= 400 or result.get("error"):
                error_msg = str(result.get("error") or f"HTTP {status}")
                logger.warning("Job %s dispatch failed: %s", job.job_id, error_msg)
                self.runtime.projects.update_job_failed(job.job_id, error_msg)
                self.runtime.release_connection(connection.id, cost)
                return

            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            operation_name = None
            poll_name = None

            for wf in data.get("workflows") or []:
                if isinstance(wf, dict) and wf.get("name"):
                    operation_name = wf["name"]
                    metadata = wf.get("metadata") if isinstance(wf.get("metadata"), dict) else {}
                    poll_name = metadata.get("primaryMediaId") or operation_name
                    break

            if not operation_name:
                for op in data.get("operations") or []:
                    inner = op.get("operation") if isinstance(op, dict) and isinstance(op.get("operation"), dict) else op
                    if isinstance(inner, dict) and inner.get("name"):
                        operation_name = inner["name"]
                        poll_name = operation_name
                        break

            if not operation_name:
                for m in data.get("media") or []:
                    if isinstance(m, dict) and m.get("name"):
                        operation_name = m["name"]
                        poll_name = operation_name
                        break

            if not operation_name:
                operation_name = job.job_id
                poll_name = job.job_id

            self.runtime.projects.update_job_running(
                job.job_id,
                operation_name=operation_name,
                installation_id=account_key,
                google_project_id=resolved_project_id,
                poll_name=poll_name or operation_name,
            )
            logger.info("Job %s dispatched successfully, operation=%s", job.job_id, operation_name)
        except Exception as exc:
            logger.exception("Exception while dispatching job %s", job.job_id)
            self.runtime.projects.update_job_failed(job.job_id, str(exc))
            self.runtime.release_connection(connection.id, cost)

    async def poll_running_jobs(self) -> None:
        """Poll running jobs gently to update status and save completed video URLs."""
        running = self.runtime.projects.list_running_jobs()
        if not running:
            return

        now = time.time()
        for job in running:
            # Poll each running job at most once every 3 seconds
            last_poll = self._last_poll_time.get(job.job_id, 0)
            if now - last_poll < 3.0:
                continue
            self._last_poll_time[job.job_id] = now

            if not job.installation_id or not job.poll_name:
                continue

            # Find connection for this job's account
            conn = next(
                (
                    c
                    for c in self.runtime.bridge.ready_connections()
                    if _account_key(c) == job.installation_id
                ),
                None,
            )
            if not conn:
                continue

            if not self.runtime.can_reserve(conn, 0):
                continue

            self.runtime.reserve_connection(conn, 0)
            try:
                from app.api.generations import _api, _completed_video_media, _attach_video_urls

                client = BoundFlowClient(self.runtime.bridge, conn.id)
                body: dict[str, Any] = {
                    "operations": [{"operation": {"name": job.poll_name}}]
                }
                # If poll_name is a media ID or UUID format, try media or operations
                poll_result = await _api(client, url=VIDEO_POLL_URL, body=body)

                data = poll_result.get("data") if isinstance(poll_result, dict) else None
                if not isinstance(data, dict):
                    # Fallback to media poll if operations did not match
                    body = {"media": [{"name": job.poll_name, "projectId": job.google_project_id}]}
                    poll_result = await _api(client, url=VIDEO_POLL_URL, body=body)
                    data = poll_result.get("data") if isinstance(poll_result, dict) else None

                if isinstance(data, dict):
                    completed = _completed_video_media(data)
                    if completed:
                        await _attach_video_urls(client, poll_result)
                        self.runtime.projects.update_job_completed(job.job_id, data)
                        logger.info("Job %s completed successfully!", job.job_id)
                        continue

                    # Check for explicit failure
                    for item in data.get("operations") or []:
                        op = item.get("operation") if isinstance(item, dict) and isinstance(item.get("operation"), dict) else item
                        if isinstance(op, dict) and op.get("error"):
                            err = str(op["error"])
                            self.runtime.projects.update_job_failed(job.job_id, err)
                            logger.warning("Job %s failed with operation error: %s", job.job_id, err)
                            break
            except Exception as exc:
                logger.warning("Error polling job %s: %s", job.job_id, exc)
            finally:
                self.runtime.release_connection(conn.id, 0)
