from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    CAPTCHA_VIDEO,
    OMNI_FLASH_CREDIT_COST,
    VIDEO_I2V_URL,
    VIDEO_I2V_FL_URL,
    VIDEO_OMNI_URL,
    VIDEO_POLL_URL,
)
from app.providers.google_flow.sdk.helpers import resolve_video_model

logger = logging.getLogger(__name__)


def _job_credit_cost(job_type: str, duration_seconds: int) -> int:
    if job_type == "image_to_video":
        return 20
    return max(15, OMNI_FLASH_CREDIT_COST.get(duration_seconds, 20))


def _is_omni_job_type(job_type: str) -> bool:
    return job_type in {"omni", "r2v", "omni_r2v"}


def _job_aspect_ratio(job_type: str, payload: dict) -> str:
    default = "VIDEO_ASPECT_RATIO_LANDSCAPE" if job_type == "image_to_video" else "VIDEO_ASPECT_RATIO_PORTRAIT"
    return payload.get("aspect_ratio", default)


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
        poll_interval = float(getattr(self.runtime.settings, "worker_poll_seconds", 10.0))
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
        claim_token = job.claim_token
        if not claim_token:
            logger.error("Queued job %s was claimed without an ownership token", job.job_id)
            return

        payload = job.request_payload
        has_media_references = bool(
            payload.get("start_media_id")
            or payload.get("end_media_id")
            or payload.get("reference_media_ids")
            or payload.get("input_images")
        )
        if has_media_references:
            self.runtime.projects.update_job_failed(
                job.job_id,
                "Queued video requests with media references cannot be dispatched safely; retry the request when its owning account is available.",
                claim_token,
            )
            return

        duration = job.request_payload.get("duration_seconds", 8)
        cost = _job_credit_cost(job.job_type, duration)

        available = [
            conn
            for conn in self.runtime.bridge.ready_connections()
            if not getattr(conn, "simulation_mode", False)
            and self.runtime.can_reserve(conn, cost)
        ]
        if not available:
            # All accounts are currently busy or lacking credits; leave in queue
            self.runtime.projects.release_job_claim(job.job_id, claim_token)
            return

        connection = self.runtime.select_connection(available)
        if not self.runtime.reserve_connection(connection, cost):
            self.runtime.projects.release_job_claim(job.job_id, claim_token)
            return

        client = BoundFlowClient(self.runtime.bridge, connection.id)
        account_key = _account_key(connection)

        paid_attempted = False
        try:
            from app.api.generations import (
                _managed_project,
                _api,
                _remember_project_on_success,
                _remember_operations,
            )

            from app.providers.google_flow.sdk.helpers import client_context

            resolved_project_id = await _managed_project(self.runtime, connection, client)
            tier = connection.paygate_tier or "PAYGATE_TIER_ONE"
            ctx = client_context(resolved_project_id, tier)

            if _is_omni_job_type(job.job_type):
                duration_seconds = payload.get("duration_seconds", 8)
                model_key = {4: "abra_r2v_4s", 6: "abra_r2v_6s", 8: "abra_r2v_8s", 10: "abra_r2v_10s"}.get(
                    duration_seconds, "abra_r2v_8s"
                )
                ref_media_ids = payload.get("reference_media_ids", [])
                body = {
                    "mediaGenerationContext": {
                        "batchId": str(uuid.uuid4()),
                        "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
                    },
                    "clientContext": ctx,
                    "requests": [{
                        "aspectRatio": _job_aspect_ratio(job.job_type, payload),
                        "textInput": {"prompt": payload.get("prompt", "")},
                        "videoModelKey": model_key,
                        "metadata": {},
                        "referenceImages": [
                            {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                            for mid in ref_media_ids
                        ],
                    }],
                    "useV2ModelConfig": True,
                }
                paid_attempted = True
                result = await _api(client, url=VIDEO_OMNI_URL, body=body, captcha_action=CAPTCHA_VIDEO)
            else:
                duration_seconds = payload.get("duration_seconds", 8)
                if job.job_type == "image_to_video":
                    model_key = resolve_video_model(
                        connection.paygate_tier or "PAYGATE_TIER_ONE",
                        _job_aspect_ratio(job.job_type, payload),
                        payload.get("quality"),
                    )
                    if not model_key:
                        self.runtime.projects.update_job_failed(
                            job.job_id, "Unsupported video quality for this account", claim_token,
                        )
                        return
                else:
                    model_key = {4: "abra_i2v_4s", 6: "abra_i2v_6s", 8: "abra_i2v_8s", 10: "abra_i2v_10s"}.get(
                        duration_seconds, "abra_i2v_8s"
                    )
                req_item = {
                    "aspectRatio": _job_aspect_ratio(job.job_type, payload),
                    "textInput": {"prompt": payload.get("prompt", "")},
                    "videoModelKey": model_key,
                    "startImage": {"mediaId": payload.get("start_media_id")},
                    "metadata": {"sceneId": str(uuid.uuid4())},
                }
                target_url = VIDEO_I2V_URL
                end_media_id = payload.get("end_media_id")
                if end_media_id:
                    req_item["endImage"] = {"mediaId": end_media_id}
                    target_url = VIDEO_I2V_FL_URL

                body = {
                    "clientContext": ctx,
                    "mediaGenerationContext": {"batchId": str(uuid.uuid4())},
                    "requests": [req_item],
                    "useV2ModelConfig": True,
                }
                paid_attempted = True
                result = await _api(client, url=target_url, body=body, captcha_action=CAPTCHA_VIDEO)

            status = result.get("status") if isinstance(result, dict) else None
            if not isinstance(status, int) or status >= 400 or result.get("error"):
                error_msg = str(result.get("error") or f"HTTP {status}")
                logger.warning("Job %s dispatch failed: %s", job.job_id, error_msg)
                self.runtime.projects.update_job_failed(job.job_id, error_msg, claim_token)
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
                # A job id is local to this provider and is not a Flow poll
                # identifier. Never persist it as an operation: that would
                # leave the paid job running forever because Flow cannot poll
                # the fabricated name. Keep the outcome explicit so callers
                # can reconcile it instead of blindly retrying a paid request.
                self.runtime.projects.update_job_failed(
                    job.job_id,
                    "Provider accepted a paid request but returned no poll identifier; the outcome is unknown. Reconcile before retrying.",
                    claim_token,
                )
                return

            _remember_project_on_success(self.runtime, connection, resolved_project_id, result)
            _remember_operations(self.runtime, connection, resolved_project_id, result)

            claimed_running = self.runtime.projects.update_job_running(
                job.job_id,
                operation_name=operation_name,
                installation_id=account_key,
                google_project_id=resolved_project_id,
                poll_name=poll_name or operation_name,
                claim_token=claim_token,
            )
            if not claimed_running:
                logger.warning("Job %s claim was superseded before dispatch completion", job.job_id)
                return
            # Also register job_id as operation alias so any lookup resolves
            self.runtime.projects.put_operation(
                job.job_id, account_key, resolved_project_id, "media", poll_name or operation_name
            )
            logger.info("Job %s dispatched successfully, operation=%s", job.job_id, operation_name)
        except Exception as exc:
            logger.exception("Exception while dispatching job %s", job.job_id)
            self.runtime.projects.update_job_failed(job.job_id, str(exc), claim_token)
        finally:
            if paid_attempted:
                # A paid request can be accepted even when the bridge reports
                # a timeout/error.  Invalidate the captured balance and
                # refresh it before another queued paid job can use it.
                try:
                    from app.api.generations import _refresh_paid_account
                    _refresh_paid_account(self.runtime, connection)
                except Exception:
                    logger.exception("Failed to schedule credit refresh for job %s", job.job_id)
            self.runtime.release_connection(connection.id, cost)

    async def poll_running_jobs(self) -> None:
        """Poll running jobs gently to update status and save completed video URLs."""
        running = self.runtime.projects.list_running_jobs()
        if not running:
            return

        now = time.time()
        poll_interval = float(getattr(self.runtime.settings, "worker_poll_seconds", 10.0))
        for job in running:
            # Poll each running job at most once every poll_interval seconds (default 10s)
            last_poll = self._last_poll_time.get(job.job_id, 0)
            if now - last_poll < poll_interval:
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
                    or c.installation_id == job.installation_id
                    or str(job.installation_id).startswith(f"{c.installation_id}\n")
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
                op_route = (
                    self.runtime.projects.get_operation(job.poll_name)
                    or self.runtime.projects.get_operation(job.operation_name)
                )
                if op_route and op_route.route_kind == "media":
                    body: dict[str, Any] = {
                        "media": [{
                            "name": op_route.poll_name,
                            "projectId": op_route.google_project_id or job.google_project_id,
                        }]
                    }
                else:
                    body = {
                        "operations": [{"operation": {"name": job.poll_name}}]
                    }
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
                        from app.api.generations import _remember_operations, _remember_generated_media
                        await _attach_video_urls(client, poll_result)
                        _remember_operations(self.runtime, conn, job.google_project_id, poll_result)
                        _remember_generated_media(self.runtime, conn, job.google_project_id, poll_result)
                        self.runtime.projects.update_job_completed(job.job_id, data)
                        self._last_poll_time.pop(job.job_id, None)
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
