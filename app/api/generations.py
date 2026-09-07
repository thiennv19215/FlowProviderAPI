from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, Request, Response

from app.api.errors import APIError
from app.api.schemas import (
    ImageGenerationRequest,
    ImageUploadRequest,
    JobsResponse,
    JobStatusRequest,
    VideoGenerationRequest,
)
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import UPLOAD_IMAGE_URL
from app.providers.google_flow.sdk.helpers import extract_upload_media_id

# Re-export existing helper names for import compatibility; implementations are
# shared application services, not HTTP router internals.
from app.services.flow import (
    _account_key,
    _scope_secret,
    _encode_routing_scope,
    _decode_routing_scope,
    _error,
    _stored_media_route,
    _known_media,
    _validate_project_route,
    _generation_route,
    _should_auto_transfer_media,
    _credit_exhaustion,
    _ready_connection_for_account,
    _transfer_mime_type,
    _copy_media_to_target,
    _rehydrate_media_ids,
    _flow_failure,
    _image_digest,
    _persist_inline_assets,
    _upload_inline_images,
    _project_items,
    _project_page_is_valid,
    _project_created_at,
    _latest_project,
    _project_cursor,
    _remember_operations,
    _remember_generated_media,
    _video_generation_succeeded,
    _video_media_generation_failed,
    _video_provider_error_message,
    _extract_upstream_error_codes,
    _video_status_failure,
    _completed_video_media,
    _attach_video_urls,
    _refresh_paid_account,
    _remember_project_on_success,
    _managed_project,
    _api,
)

router = APIRouter(tags=["Google Flow"])
ROUTING_SCOPE_HEADER = "X-Provider-Routing-Scope"
ROUTING_SCOPE_VERSION = "v2"


def _reserve(request: Request, connection, credit_cost: int = 0) -> bool:
    runtime = request.app.state.runtime
    if not runtime.reserve_connection(connection, credit_cost):
        return False
    reservations = getattr(request.state, "provider_reservations", None)
    if reservations is None:
        reservations = []
        request.state.provider_reservations = reservations
    reservations.append((connection.id, credit_cost))
    return True


def _connection(
    request: Request,
    routing_scope: str | None = None,
    *,
    min_credits: int = 0,
    project_id: str | None = None,
    required_account_key: str | None = None,
    excluded_account_keys: set[str] | None = None,
):
    runtime = request.app.state.runtime
    available = runtime.bridge.ready_connections()
    if excluded_account_keys:
        available = [
            item for item in available if _account_key(item) not in excluded_account_keys
        ]

    if routing_scope:
        scoped_account_key = _decode_routing_scope(runtime.settings, routing_scope)
        if required_account_key and scoped_account_key != required_account_key:
            has_with_credits = any(runtime.can_reserve(item, min_credits) for item in available)
            if not has_with_credits:
                raise APIError(
                    409,
                    "MEDIA_ACCOUNT_MISMATCH",
                    "The routing scope does not own the referenced media.",
                )
        connection = next(
            (
                item for item in available
                if _account_key(item) == scoped_account_key
                and runtime.can_reserve(item, min_credits)
            ),
            None,
        )
        if connection is None:
            has_other_with_credits = any(runtime.can_reserve(item, min_credits) for item in available)
            if not has_other_with_credits:
                raise APIError(
                    503,
                    "ROUTING_SCOPE_UNAVAILABLE",
                    "The Google Flow account bound to this routing scope is not currently available.",
                    retryable=True,
                )
        else:
            if project_id:
                project_account_key = runtime.projects.installation_for_project(project_id)
                if project_account_key and project_account_key != _account_key(connection):
                    raise APIError(
                        409,
                        "PROJECT_ACCOUNT_MISMATCH",
                        "The selected Google account does not own this project.",
                    )
            if not _reserve(request, connection, min_credits):
                raise APIError(503, "ROUTING_SCOPE_UNAVAILABLE", "The routed account is at capacity.", retryable=True)
            return connection, BoundFlowClient(runtime.bridge, connection.id)
    if required_account_key:
        connection = next(
            (
                item for item in available
                if _account_key(item) == required_account_key
                and runtime.can_reserve(item, min_credits)
            ),
            None,
        )
        if connection is None:
            has_other_with_credits = any(runtime.can_reserve(item, min_credits) for item in available)
            if not has_other_with_credits:
                raise APIError(
                    503,
                    "MEDIA_ACCOUNT_UNAVAILABLE",
                    "The Google Flow account that owns the referenced media is unavailable.",
                    retryable=True,
                )
        else:
            if project_id:
                project_account_key = runtime.projects.installation_for_project(project_id)
                if project_account_key and project_account_key != required_account_key:
                    raise APIError(
                        409,
                        "MEDIA_PROJECT_MISMATCH",
                        "The referenced media do not belong to the requested Google Flow project.",
                        field="project_id",
                    )
            if not _reserve(request, connection, min_credits):
                raise APIError(503, "MEDIA_ACCOUNT_UNAVAILABLE", "The media account is at capacity.", retryable=True)
            return connection, BoundFlowClient(runtime.bridge, connection.id)
    if project_id:
        account_key = runtime.projects.installation_for_project(project_id)
        if account_key:
            connection = next(
                (
                    item for item in available
                    if _account_key(item) == account_key
                    and runtime.can_reserve(item, min_credits)
                ),
                None,
            )
            if connection is None:
                raise APIError(
                    503,
                    "VIDEO_ACCOUNT_UNAVAILABLE" if min_credits else "PROJECT_ACCOUNT_UNAVAILABLE",
                    (
                        f"The project account has fewer than {min_credits} available credits or is unavailable."
                        if min_credits else
                        "The Google Flow account that owns this project is not currently available."
                    ),
                    retryable=True,
                )
            if not _reserve(request, connection, min_credits):
                raise APIError(503, "PROJECT_ACCOUNT_UNAVAILABLE", "The project account is at capacity.", retryable=True)
            return connection, BoundFlowClient(runtime.bridge, connection.id)
        if not available:
            raise APIError(
                503,
                "VIDEO_ACCOUNT_UNAVAILABLE" if min_credits else "PROVIDER_ACCOUNT_UNAVAILABLE",
                "No Google Flow extension is currently available.",
                retryable=True,
            )
        raise APIError(
            409,
            "PROJECT_ROUTE_UNKNOWN",
            "No provider account route is stored for this Google Flow project.",
            field="project_id",
        )
    available = [item for item in available if runtime.can_reserve(item, min_credits)]
    if not available:
        if min_credits:
            raise APIError(
                503,
                "VIDEO_ACCOUNT_UNAVAILABLE",
                f"No Google Flow extension with at least {min_credits} credits is currently available.",
                retryable=True,
            )
        raise APIError(
            503,
            "PROVIDER_ACCOUNT_UNAVAILABLE",
            "No Google Flow extension is currently available.",
            retryable=True,
        )
    connection = runtime.select_connection(available)
    if not _reserve(request, connection, min_credits):
        raise APIError(503, "PROVIDER_ACCOUNT_UNAVAILABLE", "No provider account slot is available.", retryable=True)
    return connection, BoundFlowClient(runtime.bridge, connection.id)


def _response(result: dict) -> Response:
    from app.api.proxy_response import upstream_response
    return upstream_response(result, _error)


def _scoped_response(result: dict, settings, connection) -> Response:
    response = _response(result)
    account_key = _account_key(connection)
    if account_key:
        response.headers[ROUTING_SCOPE_HEADER] = _encode_routing_scope(settings, account_key)
    return response


def _paid_scoped_response(result: dict, settings, connection) -> Response:
    """Do not advertise an uncertain paid request as safe to repeat."""
    if result.get("error") and not isinstance(result.get("status"), int):
        exc = _error(result)
        if exc.code in {"EXTENSION_TIMEOUT", "EXTENSION_DISCONNECTED"}:
            exc.retryable = False
            exc.message = (
                "The paid generation outcome is unknown. Do not create it again "
                "without reconciling the original operation."
            )
        raise exc
    return _scoped_response(result, settings, connection)


@router.post("/v1/media", response_model=None)
async def upload_image(
    payload: ImageUploadRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    # Validate and persist the caller-owned bytes before routing to Flow. This
    # makes the local asset store the durable source of truth even when Flow is
    # temporarily unavailable, and avoids reporting account availability for a
    # malformed image payload.
    digest = _image_digest(payload.image_base64)
    try:
        stored_digest, _asset_path, _asset_size = runtime.projects.persist_asset(
            payload.image_base64, payload.mime_type, payload.file_name,
        )
    except ValueError as exc:
        raise APIError(422, "INVALID_IMAGE", str(exc)) from exc
    if stored_digest != digest:
        raise APIError(422, "INVALID_IMAGE", "Image digest could not be verified.")
    excluded_account_keys = {
        account_key
        for project_id in payload.excluded_project_ids
        if (account_key := runtime.projects.installation_for_project(project_id))
    }
    if routing_scope and not payload.project_id:
        try:
            scoped_account_key = _decode_routing_scope(runtime.settings, routing_scope)
            scoped_conn = _ready_connection_for_account(runtime, scoped_account_key)
            if not scoped_conn or not runtime.can_reserve(scoped_conn, payload.required_credits or 0):
                routing_scope = None
        except Exception:
            routing_scope = None
    connection, client = _connection(
        request,
        routing_scope,
        min_credits=payload.required_credits,
        project_id=payload.project_id,
        excluded_account_keys=excluded_account_keys,
    )
    resolved_project_id = payload.project_id or await _managed_project(runtime, connection, client)
    account_key = _account_key(connection)
    async with runtime.media_lock(account_key, resolved_project_id, digest):
        cached = runtime.projects.get_media(account_key, resolved_project_id, digest)
        if cached:
            cached_data = cached.response_data or {
                "media": {
                    "name": cached.google_media_id,
                    "projectId": resolved_project_id,
                }
            }
            response = _scoped_response(
                {
                    "status": cached.response_status or 200,
                    "headers": cached.response_headers or {},
                    "data": cached_data,
                },
                runtime.settings,
                connection,
            )
            response.headers["X-Flow-Project-Id"] = resolved_project_id
            response.headers["X-Flow-Media-Cache-Hits"] = "1"
            return response
        body = {
            "clientContext": {"projectId": resolved_project_id, "tool": "PINHOLE"},
            "fileName": payload.file_name,
            "imageBytes": payload.image_base64,
            "isHidden": False,
            "isUserUploaded": True,
            "mimeType": payload.mime_type,
        }
        result = await _api(client, url=UPLOAD_IMAGE_URL, body=body)
        _remember_project_on_success(runtime, connection, resolved_project_id, result)
        media_id = extract_upload_media_id(result)
        if media_id:
            runtime.projects.put_media(
                account_key,
                resolved_project_id,
                digest,
                media_id,
                payload.mime_type,
                payload.file_name,
                result.get("data") if isinstance(result.get("data"), dict) else None,
                result.get("status") if isinstance(result.get("status"), int) else None,
                result.get("headers") if isinstance(result.get("headers"), dict) else None,
            )
    response = _scoped_response(result, runtime.settings, connection)
    response.headers["X-Flow-Project-Id"] = resolved_project_id
    response.headers["X-Flow-Media-Cache-Hits"] = "0"
    return response


def _normalize_job_media(job: Any) -> list[dict]:
    media_list = []
    if job and getattr(job, "result_data", None) and isinstance(job.result_data, dict):
        raw_media = list(job.result_data.get("media") or [])
        for op in job.result_data.get("operations") or []:
            inner = op.get("operation") if isinstance(op, dict) and isinstance(op.get("operation"), dict) else op
            if isinstance(inner, dict):
                resp = inner.get("response")
                if isinstance(resp, dict) and isinstance(resp.get("media"), list):
                    raw_media.extend(resp["media"])
        for m in raw_media:
            if isinstance(m, dict) and m.get("name"):
                mid = m["name"]
                img = m.get("image") if isinstance(m.get("image"), dict) else {}
                vid = m.get("video") if isinstance(m.get("video"), dict) else {}
                gen_img = img.get("generatedImage") if isinstance(img.get("generatedImage"), dict) else {}
                gen_vid = vid.get("generatedVideo") if isinstance(vid.get("generatedVideo"), dict) else {}
                url = m.get("downloadUrl") or gen_img.get("fifeUrl") or gen_vid.get("fifeUrl") or gen_vid.get("url")
                thumb = m.get("thumbnailUrl") or gen_img.get("thumbnailUrl") or gen_vid.get("thumbnailUrl")
                dims = img.get("dimensions") or vid.get("dimensions") or {}
                media_type = "image" if img or getattr(job, "media_type", "") == "image" else "video"

                item = {
                    "id": mid,
                    "type": media_type,
                    "url": url,
                }
                if media_type == "image":
                    if dims.get("width") is not None:
                        item["width"] = dims.get("width")
                    if dims.get("height") is not None:
                        item["height"] = dims.get("height")
                else:
                    item["thumbnail_url"] = thumb
                    item["width"] = dims.get("width")
                    item["height"] = dims.get("height")
                    if vid.get("durationSeconds") is not None:
                        item["duration_seconds"] = vid.get("durationSeconds")
                media_list.append(item)
    return media_list


def _normalize_generation_type(gen_type: str | None, media_type: str | None) -> str:
    if gen_type in {"character_image", "character_video"}:
        return gen_type
    if gen_type in {"r2v", "omni_r2v", "omni", "reference_to_video", "ingredients", "references"}:
        return "reference_to_video"
    if gen_type in {"i2v", "omni_i2v", "image_to_video", "start_to_video", "frames_to_video", "frames"}:
        return "frames_to_video"
    if media_type == "video":
        return "frames_to_video"
    return "image"


def _job_to_dict(job: Any) -> dict:
    status_map = {
        "queued": "queued",
        "dispatching": "running",
        "running": "running",
        "completed": "complete",
        "complete": "complete",
        "failed": "failed",
    }
    public_status = status_map.get(getattr(job, "status", "queued"), "queued")
    err = None
    if public_status == "failed" and getattr(job, "error_message", None):
        err = {
            "code": getattr(job, "error_code", None) or "JOB_FAILED",
            "message": job.error_message,
            "retryable": bool(getattr(job, "error_retryable", False)),
            "outcome_unknown": bool(getattr(job, "outcome_unknown", False)),
            "upstream_code": getattr(job, "upstream_code", None),
            "upstream_status": getattr(job, "upstream_status", None),
        }
    return {
        "id": getattr(job, "job_id", ""),
        "type": getattr(job, "media_type", "image"),
        "generation_type": _normalize_generation_type(
            getattr(job, "generation_type", None),
            getattr(job, "media_type", "image"),
        ),
        "status": public_status,
        "media": _normalize_job_media(job),
        "error": err,
    }


def _job_response(
    request: Request,
    jobs: list[Any],
    status_code: int = 200,
    include_route: bool = False,
) -> Response:
    runtime = request.app.state.runtime
    job_dicts = [_job_to_dict(j) for j in jobs]
    routes = []
    for job, item in zip(jobs, job_dicts, strict=True):
        account = getattr(job, "installation_id", None)
        project = getattr(job, "google_project_id", None)
        scope = _encode_routing_scope(runtime.settings, account) if account else None
        item["project_id"] = project
        item["routing_scope"] = scope
        routes.append((project, scope))
    counts = {"queued": 0, "running": 0, "complete": 0, "failed": 0}
    for jd in job_dicts:
        if jd["status"] in counts:
            counts[jd["status"]] += 1
    done = all(jd["status"] in {"complete", "failed"} for jd in job_dicts)
    # A batch can span accounts. Only advertise shared routing in headers.
    project_id, routing_scope = (
        routes[0] if routes and len(set(routes)) == 1 else (None, None)
    )

    resp_data = {
        "jobs": job_dicts,
        "metadata": {
            "request_id": getattr(request.state, "request_id", None),
            "project_id": project_id,
            "routing_scope": routing_scope,
            "poll_after_seconds": None if done else 10,
            "counts": counts,
            "done": done,
        },
    }
    response = Response(
        content=json.dumps(resp_data, ensure_ascii=False),
        status_code=status_code,
        media_type="application/json",
    )
    if include_route and routing_scope:
        response.headers[ROUTING_SCOPE_HEADER] = routing_scope
    if include_route and project_id:
        response.headers["X-Flow-Project-Id"] = project_id
    return response


@router.post("/v1/images/generations", response_model=JobsResponse, status_code=202)
async def generate_image(
    payload: ImageGenerationRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    runtime = request.app.state.runtime
    request_data = payload.model_dump(mode="json")
    _validate_project_route(runtime, payload.project_id)
    inline_images = payload.input_images
    if inline_images:
        request_data["input_image_hashes"] = [
            _image_digest(image.image_base64) for image in inline_images
        ]
        request_data.pop("input_images", None)
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise APIError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must not be blank.")
        if len(idempotency_key) > 200:
            raise APIError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must contain at most 200 characters.")
        existing_job = runtime.projects.get_job_by_idempotency_key(idempotency_key)
        if existing_job:
            clean_stored = {k: v for k, v in existing_job.request_payload.items() if not k.startswith("_") and k != "input_images"}
            clean_current = {k: v for k, v in request_data.items() if not k.startswith("_") and k != "input_images"}
            if (
                clean_stored != clean_current
                or existing_job.request_payload.get("_routing_scope") != routing_scope
            ):
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was already used with a different request payload.")
            return _job_response(request, [existing_job], status_code=202, include_route=True)

    job_id = f"job_{uuid.uuid4().hex}"
    if idempotency_key:
        request_data["_idempotency_key"] = idempotency_key

    scoped_account_key, resolved_project_id = _generation_route(
        runtime, payload.project_id, routing_scope, payload.reference_media_ids, bool(inline_images)
    )
    request_data["_routing_locked"] = bool(routing_scope or payload.project_id)
    request_data["_routing_scope"] = routing_scope
    if inline_images:
        _persist_inline_assets(runtime, inline_images)

    job = runtime.projects.enqueue_job(
        job_id=job_id,
        generation_type="image",
        media_type="image",
        request_payload=request_data,
        installation_id=scoped_account_key,
        google_project_id=resolved_project_id,
        idempotency_key=idempotency_key,
    )
    runtime.wake_worker()
    return _job_response(request, [job], status_code=202, include_route=True)


@router.post("/v1/videos/generations", response_model=JobsResponse, status_code=202)
async def generate_video(
    payload: VideoGenerationRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    runtime = request.app.state.runtime
    request_data = payload.model_dump(mode="json")
    _validate_project_route(runtime, payload.project_id)
    inline_images = payload.input_images
    if inline_images:
        request_data["input_image_hashes"] = [
            _image_digest(image.image_base64) for image in inline_images
        ]
        request_data.pop("input_images", None)
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key:
            raise APIError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must not be blank.")
        if len(idempotency_key) > 200:
            raise APIError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must contain at most 200 characters.")
        existing_job = runtime.projects.get_job_by_idempotency_key(idempotency_key)
        if existing_job:
            clean_stored = {k: v for k, v in existing_job.request_payload.items() if not k.startswith("_") and k != "input_images"}
            clean_current = {k: v for k, v in request_data.items() if not k.startswith("_") and k != "input_images"}
            if (
                clean_stored != clean_current
                or existing_job.request_payload.get("_routing_scope") != routing_scope
            ):
                raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was already used with a different request payload.")
            return _job_response(request, [existing_job], status_code=202, include_route=True)

    job_id = f"job_{uuid.uuid4().hex}"
    if idempotency_key:
        request_data["_idempotency_key"] = idempotency_key

    scoped_account_key, resolved_project_id = _generation_route(
        runtime, payload.project_id, routing_scope, [], bool(inline_images)
    )
    request_data["_routing_locked"] = bool(routing_scope or payload.project_id)
    request_data["_routing_scope"] = routing_scope
    if inline_images:
        _persist_inline_assets(runtime, inline_images)

    job = runtime.projects.enqueue_job(
        job_id=job_id,
        generation_type=payload.type,
        media_type="video",
        request_payload=request_data,
        installation_id=scoped_account_key,
        google_project_id=resolved_project_id,
        idempotency_key=idempotency_key,
    )
    runtime.wake_worker()
    return _job_response(request, [job], status_code=202, include_route=True)


@router.post("/v1/jobs/status", response_model=JobsResponse, status_code=200)
async def get_job_status(
    payload: JobStatusRequest,
    request: Request,
) -> Response:
    runtime = request.app.state.runtime
    from types import SimpleNamespace
    jobs = []
    for jid in payload.job_ids:
        job = runtime.projects.get_job(
            jid,
            image_timeout_seconds=int(getattr(runtime.settings, "job_image_timeout_seconds", 120)),
            video_queue_timeout_seconds=int(getattr(runtime.settings, "job_video_queue_timeout_seconds", 180)),
            video_running_timeout_seconds=int(getattr(runtime.settings, "job_video_running_timeout_seconds", 600)),
        )
        if job:
            jobs.append(job)
        else:
            jobs.append(SimpleNamespace(
                job_id=jid,
                provider="google_flow",
                media_type="image",
                status="failed",
                result_data=None,
                error_message="Job not found.",
                error_code="JOB_NOT_FOUND",
                error_retryable=False,
                outcome_unknown=False,
                google_project_id=None,
                installation_id=None,
            ))
    return _job_response(request, jobs, status_code=200, include_route=True)
