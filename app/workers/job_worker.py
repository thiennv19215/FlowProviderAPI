from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from typing import Any

from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    CAPTCHA_IMAGE,
    CAPTCHA_VIDEO,
    FLOW_API_BASE,
    OMNI_FLASH_CREDIT_COST,
    VIDEO_I2V_FL_URL,
    VIDEO_I2V_URL,
    VIDEO_OMNI_URL,
    VIDEO_POLL_URL,
)
from app.providers.google_flow.sdk.helpers import (
    resolve_image_model,
    resolve_video_model,
)

logger = logging.getLogger(__name__)


def _job_credit_cost(generation_type: str, duration_seconds: int) -> int:
    if generation_type in {"image", "character_image"}:
        return 0
    if generation_type == "image_to_video":
        return 20
    return max(15, OMNI_FLASH_CREDIT_COST.get(duration_seconds, 20))


def _is_omni_generation(generation_type: str) -> bool:
    return generation_type in {"omni", "r2v", "omni_r2v", "reference_to_video", "ingredients", "references", "character_video"}


_is_omni_job_type = _is_omni_generation


def _is_frames_generation(generation_type: str) -> bool:
    return generation_type in {"image_to_video", "start_to_video", "frames_to_video", "frames", "i2v", "omni_i2v"}


def _job_aspect_ratio(generation_type: str, payload: dict) -> str:
    default = "VIDEO_ASPECT_RATIO_LANDSCAPE" if generation_type == "image_to_video" else "VIDEO_ASPECT_RATIO_PORTRAIT"
    value = payload.get("aspect_ratio", default)
    if generation_type == "character_video":
        return {
            "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
            "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
        }.get(value, value)
    return value


def _image_aspect_ratio(payload: dict) -> str:
    value = payload.get("aspect_ratio", "IMAGE_ASPECT_RATIO_PORTRAIT")
    return {
        "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
        "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
    }.get(value, value)


def _account_key(connection: Any) -> str:
    email = str(getattr(connection, "account_email", "") or "").strip().lower()
    return f"{connection.installation_id}\n{email}" if email else str(connection.installation_id)


def _poll_delay(settings: Any, error_count: int = 0) -> float:
    base = float(getattr(settings, "worker_poll_seconds", 10.0))
    if base <= 0:
        return 0.0
    maximum = float(getattr(settings, "worker_poll_max_backoff_seconds", 300))
    return min(maximum, base * (2 ** min(max(0, error_count), 8)))


class JobWorker:
    """Background worker for asynchronous job dispatch and result polling."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._running = False
        self._task: asyncio.Task | None = None
        self._wake_event = asyncio.Event()

    def wake(self) -> None:
        """Wake up the worker immediately to process queued jobs without waiting for poll_seconds."""
        if hasattr(self, "_wake_event") and self._wake_event is not None:
            try:
                loop = getattr(self._wake_event, "_loop", None)
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(self._wake_event.set)
                else:
                    self._wake_event.set()
            except Exception:
                pass

    async def start(self) -> None:
        if not getattr(self.runtime.settings, "worker_enabled", True):
            logger.info("JobWorker disabled by configuration.")
            return
        self._wake_event = asyncio.Event()
        abandoned = self.runtime.projects.fail_abandoned_dispatches(
            int(getattr(self.runtime.settings, "worker_dispatch_lease_seconds", 300))
        )
        if abandoned:
            logger.warning("Marked %s abandoned paid dispatch(es) as outcome unknown", abandoned)
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="flow-provider-job-worker")
        logger.info("JobWorker started successfully.")

    async def stop(self) -> None:
        self._running = False
        self.wake()
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        logger.info("JobWorker stopped.")

    async def _run_loop(self) -> None:
        poll_interval = float(getattr(self.runtime.settings, "worker_poll_seconds", 10.0))
        while self._running:
            try:
                self.runtime.projects.fail_abandoned_dispatches(
                    int(getattr(self.runtime.settings, "worker_dispatch_lease_seconds", 300))
                )
                expired = self.runtime.projects.fail_expired_jobs(
                    image_timeout_seconds=int(getattr(self.runtime.settings, "job_image_timeout_seconds", 120)),
                    video_queue_timeout_seconds=int(getattr(self.runtime.settings, "job_video_queue_timeout_seconds", 180)),
                    video_running_timeout_seconds=int(getattr(self.runtime.settings, "job_video_running_timeout_seconds", 600)),
                )
                if expired:
                    logger.warning("Marked %s job(s) failed after timeout limit", expired)
                await self.process_queued_jobs()
                await self.poll_running_jobs()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected error in JobWorker loop")
            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=poll_interval)
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    async def process_queued_jobs(self, max_concurrent: int = 20) -> None:
        """Pick queued jobs and dispatch them concurrently to available accounts until queue is empty or busy."""
        while True:
            tasks = []
            while len(tasks) < max_concurrent:
                job = self.runtime.projects.claim_next_queued_job()
                if not job or job.status != "dispatching":
                    break
                tasks.append(asyncio.create_task(self._dispatch_job(job)))
            if not tasks:
                break
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # If any job was released back to the queue because no account slot was available,
            # stop the loop immediately instead of busy-spinning on the exact same queued jobs.
            if any(r is False for r in results):
                break

    async def _dispatch_job(self, job: Any) -> None:
        claim_token = job.claim_token
        if not claim_token:
            logger.error("Queued job %s was claimed without an ownership token", job.job_id)
            return

        payload = job.request_payload
        duration = job.request_payload.get("duration_seconds", 8)
        cost = _job_credit_cost(job.generation_type, duration)

        target_installation_id = job.installation_id
        target_google_project_id = job.google_project_id

        has_inline_assets = bool(
            payload.get("input_image_hashes")
            or payload.get("input_images")
            or job.job_id in self.runtime.inline_images
            or payload.get("reference_asset_hashes")
        )
        if not has_inline_assets and (not target_google_project_id or not target_installation_id):
            referenced = []
            if payload.get("start_media_id"):
                referenced.append(payload["start_media_id"])
            if payload.get("end_media_id"):
                referenced.append(payload["end_media_id"])
            if payload.get("reference_media_ids"):
                referenced.extend(payload["reference_media_ids"])
            if referenced:
                from app.api.generations import _stored_media_route
                try:
                    inferred = _stored_media_route(self.runtime, referenced)
                except Exception:
                    inferred = None
                if inferred:
                    inferred_inst, inferred_proj = inferred
                    inferred_conn = next(
                        (c for c in self.runtime.bridge.ready_connections() if _account_key(c) == inferred_inst),
                        None,
                    )
                    if inferred_conn and self.runtime.can_reserve(inferred_conn, cost, job_type=job.media_type):
                        if not target_installation_id:
                            target_installation_id = inferred_inst
                        if not target_google_project_id:
                            target_google_project_id = inferred_proj

        project_owner = (
            self.runtime.projects.installation_for_project(target_google_project_id)
            if target_google_project_id
            else None
        )

        ready_conns = self.runtime.bridge.ready_connections()
        available = [
            conn
            for conn in ready_conns
            if (
                not target_installation_id
                or _account_key(conn) == target_installation_id
                or conn.installation_id == target_installation_id
            )
            and (
                not project_owner
                or _account_key(conn) == project_owner
                or conn.installation_id == project_owner
            )
            and self.runtime.can_reserve(conn, cost, job_type=job.media_type)
        ]
        if not available:
            fallback_conns = [
                c for c in ready_conns
                if self.runtime.can_reserve(c, cost, job_type=job.media_type)
            ]
            if fallback_conns:
                logger.warning(
                    "Assigned account '%s' cannot serve job %s (insufficient credits or offline). "
                    "Auto-failing over to a ready account with sufficient credits (%d candidate accounts).",
                    target_installation_id or project_owner,
                    job.job_id,
                    len(fallback_conns),
                )
                available = fallback_conns

        if not available:
            from datetime import datetime, timezone
            job_age_seconds = 0.0
            if getattr(job, "created_at", None):
                try:
                    dt = datetime.fromisoformat(job.created_at.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    job_age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
                except Exception:
                    job_age_seconds = 0.0

            if not ready_conns:
                self.runtime.projects.update_job_failed(
                    job.job_id,
                    "No Google Flow extension accounts are currently connected. Please ensure Chrome is open and the Google Flow extension is connected.",
                    claim_token,
                    error_code="NO_CONNECTED_ACCOUNTS",
                )
                return True
            else:
                accounts_with_credits = [
                    c for c in ready_conns
                    if (self.runtime.available_credits(c) is None or self.runtime.available_credits(c) >= cost)
                ]
                if not accounts_with_credits and cost > 0:
                    self.runtime.projects.update_job_failed(
                        job.job_id,
                        f"All {len(ready_conns)} connected accounts have insufficient credits for this job ({cost} credits required).",
                        claim_token,
                        error_code="INSUFFICIENT_CREDITS",
                    )
                    return True
                if job_age_seconds > 300:
                    self.runtime.projects.update_job_failed(
                        job.job_id,
                        "Job timed out waiting for an available account slot.",
                        claim_token,
                        error_code="QUEUE_TIMEOUT",
                    )
                    return True

            self.runtime.projects.release_job_claim(job.job_id, claim_token)
            return False

        connection = self.runtime.select_connection(available)
        if not self.runtime.reserve_connection(connection, cost, job_type=job.media_type):
            self.runtime.projects.release_job_claim(job.job_id, claim_token)
            return False

        client = BoundFlowClient(self.runtime.bridge, connection.id)
        account_key = _account_key(connection)

        paid_attempted = False
        try:
            from app.api.generations import (
                _api,
                _credit_exhaustion,
                _managed_project,
                _remember_operations,
                _remember_project_on_success,
            )
            from app.providers.google_flow.sdk.helpers import client_context

            project_owner = (
                self.runtime.projects.installation_for_project(job.google_project_id)
                if job.google_project_id
                else None
            )
            if not job.google_project_id or (project_owner and project_owner != account_key):
                resolved_project_id = await _managed_project(
                    self.runtime, connection, client
                )
            else:
                resolved_project_id = job.google_project_id
            tier = connection.paygate_tier or "PAYGATE_TIER_ONE"
            ctx = client_context(resolved_project_id, tier)

            reference_media_ids = list(payload.get("reference_media_ids") or [])
            if job.generation_type in {"character_image", "character_video"}:
                reference_media_ids = []
                missing_assets = []
                from app.api.schemas import InlineImageInput
                # Character references are snapshotted separately from
                # optional per-request references. Preserve Character order,
                # then append the caller's extra images without duplicates.
                reference_hashes = list(payload.get("reference_asset_hashes") or [])
                reference_hashes.extend(payload.get("additional_reference_asset_hashes") or [])
                seen_hashes: set[str] = set()
                for digest in reference_hashes:
                    digest = str(digest)
                    if digest in seen_hashes:
                        continue
                    seen_hashes.add(digest)
                    asset_mime, asset_file_name = self.runtime.projects.get_asset_info(digest)
                    stored_asset = self.runtime.projects.asset_store.read(
                        digest, asset_mime,
                    )
                    if stored_asset is None:
                        missing_assets.append(digest)
                        continue
                    raw_bytes, mime_type = stored_asset
                    reference_media_ids.append(
                        InlineImageInput(
                            image_base64=base64.b64encode(raw_bytes).decode("ascii"),
                            mime_type=mime_type,
                            file_name=asset_file_name or f"character-{digest[:12]}.png",
                        )
                    )
                if missing_assets:
                    self.runtime.projects.update_job_failed(
                        job.job_id,
                        "One or more Character reference assets are unavailable.",
                        claim_token,
                        error_code="CHARACTER_ASSET_MISSING",
                    )
                    return
                from app.api.generations import _upload_inline_images
                uploaded_ids, cached_digests, _hits = await _upload_inline_images(
                    self.runtime, connection, client, resolved_project_id, reference_media_ids,
                )
                reference_media_ids = uploaded_ids
                payload["_cached_digests"] = list(cached_digests)
                payload["reference_media_ids"] = uploaded_ids
            inline_images = self.runtime.inline_images.pop(job.job_id, None)
            if inline_images is None:
                inline_images = payload.get("input_images")
            if inline_images is None and payload.get("input_image_hashes"):
                inline_images = []
                missing_assets = []
                from app.api.schemas import InlineImageInput
                for digest in payload.get("input_image_hashes") or []:
                    digest = str(digest)
                    asset_mime, asset_file_name = self.runtime.projects.get_asset_info(digest)
                    stored_asset = self.runtime.projects.asset_store.read(digest, asset_mime)
                    if stored_asset is None:
                        missing_assets.append(digest)
                        continue
                    raw_bytes, mime_type = stored_asset
                    inline_images.append(
                        InlineImageInput(
                            image_base64=base64.b64encode(raw_bytes).decode("ascii"),
                            mime_type=mime_type,
                            file_name=asset_file_name or f"reference-{digest[:12]}.png",
                        )
                    )
                if missing_assets:
                    self.runtime.projects.update_job_failed(
                        job.job_id,
                        "One or more inline reference assets are unavailable.",
                        claim_token,
                        error_code="INPUT_IMAGE_ASSET_MISSING",
                    )
                    return
            if inline_images:
                from app.api.generations import _upload_inline_images
                from app.api.schemas import InlineImageInput
                raw_images = [
                    InlineImageInput(**img) if isinstance(img, dict) else img
                    for img in inline_images
                ]
                uploaded_ids, cached_digests, _hits = await _upload_inline_images(
                    self.runtime, connection, client, resolved_project_id, raw_images
                )
                payload["_cached_digests"] = list(
                    dict.fromkeys(
                        list(payload.get("_cached_digests") or []) + cached_digests
                    )
                )
                if job.media_type == "image":
                    reference_media_ids.extend(uploaded_ids)
                elif _is_omni_generation(job.generation_type):
                    payload["reference_media_ids"] = list(payload.get("reference_media_ids") or []) + uploaded_ids
                elif _is_frames_generation(job.generation_type) and uploaded_ids:
                    payload["start_media_id"] = uploaded_ids[0]
                    if len(uploaded_ids) > 1:
                        payload["end_media_id"] = uploaded_ids[1]

            from app.api.generations import _known_media, _rehydrate_media_ids

            all_referenced_mids: list[str] = []
            if payload.get("start_media_id"):
                all_referenced_mids.append(payload["start_media_id"])
            if payload.get("end_media_id"):
                all_referenced_mids.append(payload["end_media_id"])
            for mid in (payload.get("reference_media_ids") or []):
                if isinstance(mid, str) and mid not in all_referenced_mids:
                    all_referenced_mids.append(mid)
            if reference_media_ids:
                for mid in reference_media_ids:
                    if isinstance(mid, str) and mid not in all_referenced_mids:
                        all_referenced_mids.append(mid)

            if all_referenced_mids:
                known = _known_media(self.runtime, all_referenced_mids)
                mids_needing_transfer = [
                    mid for mid in all_referenced_mids
                    if mid in known and (
                        known[mid].installation_id != account_key
                        or known[mid].google_project_id != resolved_project_id
                    )
                ]
                if mids_needing_transfer:
                    logger.info(
                        "Rehydrating %d referenced media IDs across accounts to account %s, project %s",
                        len(mids_needing_transfer),
                        account_key,
                        resolved_project_id,
                    )
                    try:
                        rehydrated = await _rehydrate_media_ids(
                            self.runtime,
                            connection,
                            client,
                            mids_needing_transfer,
                            resolved_project_id,
                            known,
                        )
                        rehydrate_map = dict(zip(mids_needing_transfer, rehydrated, strict=True))
                        if payload.get("start_media_id") in rehydrate_map:
                            payload["start_media_id"] = rehydrate_map[payload["start_media_id"]]
                        if payload.get("end_media_id") in rehydrate_map:
                            payload["end_media_id"] = rehydrate_map[payload["end_media_id"]]
                        if payload.get("reference_media_ids"):
                            payload["reference_media_ids"] = [
                                rehydrate_map.get(mid, mid) for mid in payload["reference_media_ids"]
                            ]
                        if reference_media_ids:
                            reference_media_ids = [
                                rehydrate_map.get(mid, mid) for mid in reference_media_ids
                            ]
                    except Exception as exc:
                        logger.error("Failed to rehydrate cross-account media: %s", exc)
                        self.runtime.projects.update_job_failed(
                            job.job_id,
                            f"Cross-account media rehydration failed: {exc}",
                            claim_token,
                            error_code="MEDIA_REHYDRATION_FAILED",
                            retryable=True,
                        )
                        return

            if job.media_type == "image":
                requests = []
                for _ in range(int(payload.get("variant_count", 1))):
                    item = {
                        "clientContext": ctx,
                        "structuredPrompt": {"parts": [{"text": payload.get("prompt", "")}]},
                        "imageAspectRatio": _image_aspect_ratio(payload),
                        "imageModelName": resolve_image_model(payload.get("model", "pro")),
                    }
                    if reference_media_ids:
                        item["imageInputs"] = [
                            {
                                "name": media_id,
                                "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
                            }
                            for media_id in reference_media_ids
                        ]
                    requests.append(item)
                result = await _api(
                    client,
                    url=(
                        f"{FLOW_API_BASE}/v1/projects/{resolved_project_id}"
                        "/flowMedia:batchGenerateImages"
                    ),
                    body={
                        "clientContext": ctx,
                        "mediaGenerationContext": {"batchId": str(uuid.uuid4())},
                        "useNewMedia": True,
                        "requests": requests,
                    },
                    captcha_action=CAPTCHA_IMAGE,
                )
                status = result.get("status") if isinstance(result, dict) else None
                if status == 404:
                    for digest in payload.get("_cached_digests") or []:
                        self.runtime.projects.invalidate_media(
                            account_key, resolved_project_id, digest
                        )
                if not isinstance(status, int) or status >= 400 or result.get("error"):
                    unknown = not isinstance(status, int) or status in {
                        408, 425, 500, 502, 503, 504,
                    }
                    self.runtime.projects.update_job_failed(
                        job.job_id,
                        str(result.get("error") or f"HTTP {status}"),
                        claim_token,
                        error_code=(
                            "IMAGE_DISPATCH_OUTCOME_UNKNOWN"
                            if unknown
                            else "IMAGE_GENERATION_FAILED"
                        ),
                        retryable=False,
                        outcome_unknown=unknown,
                    )
                    return
                data = result.get("data") if isinstance(result.get("data"), dict) else {}
                _remember_project_on_success(
                    self.runtime, connection, resolved_project_id, result
                )
                from app.api.generations import _remember_generated_media
                _remember_generated_media(
                    self.runtime, connection, resolved_project_id, result
                )
                if not self.runtime.projects.update_job_completed(
                    job.job_id, data, claim_token
                ):
                    logger.warning(
                        "Image job %s claim was superseded before completion", job.job_id
                    )
                else:
                    logger.info("Image job %s completed successfully", job.job_id)
                return

            if _is_omni_generation(job.generation_type):
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
                        "aspectRatio": _job_aspect_ratio(job.generation_type, payload),
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
                if job.generation_type == "image_to_video":
                    model_key = resolve_video_model(
                        connection.paygate_tier or "PAYGATE_TIER_ONE",
                        _job_aspect_ratio(job.generation_type, payload),
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
                    "aspectRatio": _job_aspect_ratio(job.generation_type, payload),
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
            if status == 404:
                for digest in payload.get("_cached_digests") or []:
                    self.runtime.projects.invalidate_media(
                        account_key, resolved_project_id, digest
                    )
            if not isinstance(status, int) or status >= 400 or result.get("error"):
                error_msg = str(result.get("error") or f"HTTP {status}")
                if isinstance(status, int) and _credit_exhaustion(result):
                    logger.warning("Job %s was rejected for credits by Flow API", job.job_id)
                    self.runtime.projects.update_job_failed(
                        job.job_id,
                        f"Google Flow rejected request due to credit exhaustion: {error_msg}",
                        claim_token,
                        error_code="CREDIT_EXHAUSTED",
                        retryable=False,
                    )
                    return
                if paid_attempted and (
                    not isinstance(status, int) or status in {408, 425, 500, 502, 503, 504}
                ):
                    error_msg = (
                        "Paid dispatch failed or timed out; the outcome is unknown. "
                        "Reconcile before retrying. " + error_msg
                    )
                logger.warning("Job %s dispatch failed: %s", job.job_id, error_msg)
                unknown = paid_attempted and (
                    not isinstance(status, int) or status in {408, 425, 500, 502, 503, 504}
                )
                self.runtime.projects.update_job_failed(
                    job.job_id, error_msg, claim_token,
                    error_code="VIDEO_DISPATCH_OUTCOME_UNKNOWN" if unknown else "VIDEO_DISPATCH_FAILED",
                    outcome_unknown=unknown,
                )
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
                    error_code="VIDEO_DISPATCH_OUTCOME_UNKNOWN",
                    outcome_unknown=True,
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
                poll_delay_seconds=_poll_delay(self.runtime.settings),
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
            message = str(exc)
            if paid_attempted:
                message = (
                    "Paid dispatch failed or timed out; the outcome is unknown. "
                    "Reconcile before retrying. " + message
                )
            self.runtime.projects.update_job_failed(
                job.job_id,
                message,
                claim_token,
                error_code=(
                    "VIDEO_DISPATCH_OUTCOME_UNKNOWN"
                    if paid_attempted
                    else f"{job.media_type.upper()}_DISPATCH_FAILED"
                ),
                outcome_unknown=paid_attempted,
            )
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
            self.runtime.release_connection(connection.id, cost, job_type=job.media_type)

    async def poll_running_jobs(self) -> None:
        """Poll running jobs gently to update status and save completed video URLs."""
        running = self.runtime.projects.claim_due_running_jobs(
            lease_seconds=int(
                getattr(self.runtime.settings, "worker_poll_claim_lease_seconds", 120)
            )
        )
        if not running:
            return

        for job in running:
            if not job.installation_id or not job.poll_name:
                self.runtime.projects.update_job_failed(
                    job.job_id,
                    "Running video job has no owning account or poll identifier.",
                    error_code="VIDEO_POLL_ROUTE_MISSING",
                )
                continue

            if getattr(job, "poll_error_count", 0) >= 10:
                self.runtime.projects.update_job_failed(
                    job.job_id,
                    f"Video polling failed after 10 consecutive attempts: {job.last_poll_error or 'Owning extension account is unavailable.'}",
                    error_code="VIDEO_POLL_MAX_ERRORS",
                )
                logger.warning("Job %s marked failed after 10 consecutive poll errors", job.job_id)
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
                self.runtime.projects.schedule_job_poll(
                    job.job_id,
                    _poll_delay(self.runtime.settings, job.poll_error_count),
                    error_message="Owning extension account is unavailable.",
                    attempted=False,
                )
                continue

            if not self.runtime.can_reserve(conn, 0):
                self.runtime.projects.schedule_job_poll(
                    job.job_id,
                    _poll_delay(self.runtime.settings, job.poll_error_count),
                    error_message="Owning extension account is currently busy.",
                    attempted=False,
                )
                continue

            self.runtime.reserve_connection(conn, 0)
            try:
                from app.api.generations import (
                    _api,
                    _attach_video_urls,
                    _completed_video_media,
                    _video_status_failure,
                )

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

                self.runtime.projects.record_job_poll_attempt(job.job_id)

                if isinstance(data, dict):
                    failure = _video_status_failure(poll_result)
                    if failure:
                        self.runtime.projects.update_job_failed(
                            job.job_id, failure.message, error_code=failure.code,
                            retryable=failure.retryable,
                        )
                        logger.warning("Job %s failed: %s", job.job_id, failure.message)
                        continue
                    completed = _completed_video_media(data)
                    if completed:
                        from app.api.generations import (
                            _remember_generated_media,
                            _remember_operations,
                        )
                        await _attach_video_urls(client, poll_result)
                        _remember_operations(self.runtime, conn, job.google_project_id, poll_result)
                        _remember_generated_media(self.runtime, conn, job.google_project_id, poll_result)
                        self.runtime.projects.update_job_completed(job.job_id, data)
                        logger.info("Job %s completed successfully!", job.job_id)
                        continue

                    # Check for explicit failure
                    for item in data.get("operations") or []:
                        op = item.get("operation") if isinstance(item, dict) and isinstance(item.get("operation"), dict) else item
                        if isinstance(op, dict) and op.get("error"):
                            err = str(op["error"])
                            self.runtime.projects.update_job_failed(
                                job.job_id, err, error_code="VIDEO_OPERATION_FAILED",
                            )
                            logger.warning("Job %s failed with operation error: %s", job.job_id, err)
                            break
                    else:
                        self.runtime.projects.schedule_job_poll(
                            job.job_id,
                            _poll_delay(self.runtime.settings),
                            attempted=False,
                        )
                else:
                    error = poll_result.get("error") if isinstance(poll_result, dict) else None
                    self.runtime.projects.schedule_job_poll(
                        job.job_id,
                        _poll_delay(self.runtime.settings, job.poll_error_count),
                        error_message=str(error or "Google Flow returned no polling data."),
                        attempted=False,
                    )
            except Exception as exc:  # noqa: BLE001 - isolate one polling job from the worker loop
                logger.warning("Error polling job %s: %s", job.job_id, exc)
                self.runtime.projects.schedule_job_poll(
                    job.job_id,
                    _poll_delay(self.runtime.settings, job.poll_error_count),
                    error_message=str(exc),
                )
            finally:
                self.runtime.release_connection(conn.id, 0)
