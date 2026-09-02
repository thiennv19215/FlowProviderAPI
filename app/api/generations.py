from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import uuid
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Request, Response

from app.api.errors import APIError
from app.api.schemas import (
    CreateProjectRequest,
    ImageGenerationRequest,
    ImageToVideoGenerationRequest,
    ImageUploadRequest,
    InlineImageInput,
    OmniVideoGenerationRequest,
    VideoGenerationRequest,
    VideoStatusRequest,
)
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    API_HEADERS,
    CAPTCHA_IMAGE,
    CAPTCHA_VIDEO,
    FLOW_API_BASE,
    OMNI_FLASH_CREDIT_COST,
    TRPC_CREATE_PROJECT,
    TRPC_HEADERS,
    TRPC_SEARCH_PROJECTS,
    UPLOAD_IMAGE_URL,
    VIDEO_I2V_URL,
    VIDEO_OMNI_URL,
    VIDEO_POLL_URL,
)
from app.providers.google_flow.sdk.helpers import (
    client_context,
    extract_project_id,
    extract_upload_media_id,
    resolve_image_model,
    resolve_video_model,
)

router = APIRouter(tags=["Google Flow"])
ROUTING_SCOPE_HEADER = "X-Provider-Routing-Scope"
ROUTING_SCOPE_VERSION = "v2"


def _account_key(connection) -> str:
    email = str(getattr(connection, "account_email", "") or "").strip().lower()
    return f"{connection.installation_id}\n{email}" if email else connection.installation_id


def _scope_secret(settings) -> bytes:
    # Routing scopes are signed with the private connector credential; public
    # business endpoints intentionally do not require an API key.
    return (settings.extension_api_key or "flow-provider-development-scope-secret").encode("utf-8")


def _encode_routing_scope(settings, account_key: str) -> str:
    payload = base64.urlsafe_b64encode(account_key.encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(_scope_secret(settings), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{ROUTING_SCOPE_VERSION}.{payload}.{signature}"


def _decode_routing_scope(settings, scope: str) -> str:
    try:
        version, payload, signature = scope.split(".", 2)
    except ValueError as exc:
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.") from exc
    if version != ROUTING_SCOPE_VERSION or not payload or not signature:
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.")
    expected = hmac.new(_scope_secret(settings), payload.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.")
    try:
        padded = payload + "=" * (-len(payload) % 4)
        account_key = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.") from exc
    if not account_key:
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.")
    return account_key


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
    # Never route a business request to a simulated connector.  Production only
    # serves responses produced by a real Google Flow account.
    available = [item for item in available if not getattr(item, "simulation_mode", False)]
    if excluded_account_keys:
        available = [
            item for item in available if _account_key(item) not in excluded_account_keys
        ]

    if routing_scope:
        scoped_account_key = _decode_routing_scope(runtime.settings, routing_scope)
        if required_account_key and scoped_account_key != required_account_key:
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
            raise APIError(
                503,
                "ROUTING_SCOPE_UNAVAILABLE",
                "The Google Flow account bound to this routing scope is not currently available.",
                retryable=True,
            )
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
            raise APIError(
                503,
                "MEDIA_ACCOUNT_UNAVAILABLE",
                "The Google Flow account that owns the referenced media is unavailable.",
                retryable=True,
            )
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


def _error(result: dict) -> APIError:
    reason = str(result.get("error") or "extension_request_failed")
    if "timeout" in reason.lower():
        return APIError(504, "EXTENSION_TIMEOUT", "The extension request timed out.", retryable=True)
    if "disconnect" in reason.lower():
        return APIError(503, "EXTENSION_DISCONNECTED", "The extension disconnected.", retryable=True)
    return APIError(502, "EXTENSION_REQUEST_FAILED", reason, retryable=True)


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


def _stored_media_route(runtime, media_ids: list[str]) -> tuple[str, str] | None:
    """Resolve known media IDs to their single owning account and project."""
    routes = {
        (media.installation_id, media.google_project_id)
        for media_id in media_ids
        if (media := runtime.projects.get_media_by_google_id(media_id)) is not None
    }
    if len(routes) > 1:
        raise APIError(
            409,
            "MEDIA_ROUTE_MISMATCH",
            "Referenced media belong to different Google Flow accounts or projects.",
            field="reference_media_ids",
        )
    return next(iter(routes), None)


def _known_media(runtime, media_ids: list[str]) -> dict[str, object]:
    return {
        media_id: media
        for media_id in media_ids
        if (media := runtime.projects.get_media_by_google_id(media_id)) is not None
    }


def _should_auto_transfer_media(runtime, project_id: str | None, routing_scope: str | None, known_media: dict[str, object]) -> bool:
    if routing_scope or not known_media:
        return False
    if not project_id:
        return True
    target_account_key = runtime.projects.installation_for_project(project_id)
    return bool(
        target_account_key
        and any(
            (media.installation_id, media.google_project_id)
            != (target_account_key, project_id)
            for media in known_media.values()
        )
    )


def _credit_exhaustion(result: dict) -> bool:
    """Return true only for a deterministic no-credit/quota rejection."""
    status = result.get("status") if isinstance(result, dict) else None
    if status == 402:
        return True
    if status not in {400, 403, 429}:
        return False
    try:
        text = json.dumps(result, ensure_ascii=False, default=str).lower()
    except (TypeError, ValueError):
        text = str(result).lower()
    credit_failure = (
        "insufficient credit" in text
        or "not enough credit" in text
        or "no credit" in text
        or "out of credit" in text
        or "credit exhausted" in text
        or "credits exhausted" in text
        or "insufficient_quota" in text
        or "resource_exhausted" in text
    )
    quota_failure = "quota" in text and any(
        marker in text for marker in ("exceed", "exhaust", "insufficient", "not enough", "unavailable")
    )
    return credit_failure or quota_failure


def _ready_connection_for_account(runtime, account_key: str):
    return next(
        (
            item
            for item in runtime.bridge.ready_connections()
            if not getattr(item, "simulation_mode", False)
            and _account_key(item) == account_key
        ),
        None,
    )


def _transfer_mime_type(media, downloaded: dict) -> str:
    downloaded_type = str(downloaded.get("mime_type") or "").split(";", 1)[0].strip().lower()
    if downloaded_type.startswith("image/") and downloaded_type != "image/generated":
        return downloaded_type
    stored_type = str(getattr(media, "mime_type", "") or "").split(";", 1)[0].strip().lower()
    if stored_type.startswith("image/") and stored_type != "image/generated":
        return stored_type
    return "image/png"


async def _copy_media_to_target(
    runtime,
    target_account_key: str,
    project_id: str,
    target_client: BoundFlowClient,
    source_client: BoundFlowClient,
    media,
    media_id: str,
) -> str:
    """Download and cache one source media while serializing duplicate copies."""
    async with runtime.media_lock(target_account_key, project_id, media.content_sha256):
        cached = runtime.projects.get_media(
            target_account_key, project_id, media.content_sha256,
        )
        if cached:
            return cached.google_media_id

        async with runtime.media_transfer_slots:
            downloaded = await source_client.download_media(media_id)
            if downloaded.get("error"):
                raise APIError(
                    502,
                    "MEDIA_REHYDRATION_FAILED",
                    f"Referenced media could not be downloaded from its owning account: {downloaded['error']}",
                    retryable=True,
                )
            raw_bytes = downloaded.get("bytes")
            if not isinstance(raw_bytes, (bytes, bytearray)) or not raw_bytes:
                raise APIError(
                    502,
                    "MEDIA_REHYDRATION_FAILED",
                    "Referenced media download returned no image data.",
                    retryable=True,
                )
            raw_bytes = bytes(raw_bytes)
            content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
            cached = runtime.projects.get_media(target_account_key, project_id, content_sha256)
            if cached:
                return cached.google_media_id

            mime_type = _transfer_mime_type(media, downloaded)
            upload_result = await _api(
                target_client,
                url=UPLOAD_IMAGE_URL,
                body={
                    "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
                    "fileName": media.file_name or "reference.png",
                    "imageBytes": base64.b64encode(raw_bytes).decode("ascii"),
                    "isHidden": False,
                    "isUserUploaded": True,
                    "mimeType": mime_type,
                },
            )
            new_media_id = extract_upload_media_id(upload_result)
            if not new_media_id:
                raise _flow_failure(
                    upload_result,
                    "MEDIA_REHYDRATION_UPLOAD_FAILED",
                    "Referenced media could not be uploaded to the selected account.",
                )
            response_data = upload_result.get("data") if isinstance(upload_result.get("data"), dict) else None
            response_status = upload_result.get("status") if isinstance(upload_result.get("status"), int) else None
            response_headers = upload_result.get("headers") if isinstance(upload_result.get("headers"), dict) else None
            runtime.projects.put_media(
                target_account_key,
                project_id,
                content_sha256,
                new_media_id,
                mime_type,
                media.file_name or "reference.png",
                response_data,
                response_status,
                response_headers,
            )
            # Generated media uses a synthetic source key. Keep an alias so a
            # repeated transfer can reuse the copied ID without downloading again.
            if media.content_sha256 != content_sha256:
                runtime.projects.put_media(
                    target_account_key,
                    project_id,
                    media.content_sha256,
                    new_media_id,
                    mime_type,
                    media.file_name or "reference.png",
                    response_data,
                    response_status,
                    response_headers,
                )
            return new_media_id


async def _rehydrate_media_ids(
    runtime,
    connection,
    target_client: BoundFlowClient,
    media_ids: list[str],
    project_id: str,
    known_media: dict[str, object],
) -> list[str]:
    """Copy known image media into the selected account when managed routing changes."""
    target_account_key = _account_key(connection)
    source_clients: dict[str, BoundFlowClient] = {}
    for media_id in media_ids:
        media = known_media.get(media_id)
        if media is None or (
            media.installation_id == target_account_key
            and media.google_project_id == project_id
        ):
            continue
        source_connection = _ready_connection_for_account(runtime, media.installation_id)
        if source_connection is None:
            raise APIError(
                503,
                "MEDIA_SOURCE_UNAVAILABLE",
                "The account that owns a referenced media is not currently available for transfer.",
                retryable=True,
            )
        # Resolving a signed URL is a read-only operation and does not consume a
        # generation slot. The target job already owns the reservation.
        source_clients.setdefault(
            media.installation_id,
            BoundFlowClient(runtime.bridge, source_connection.id),
        )

    transferred: dict[str, str] = {}
    output: list[str] = []
    for media_id in media_ids:
        media = known_media.get(media_id)
        if media is None or (
            media.installation_id == target_account_key
            and media.google_project_id == project_id
        ):
            output.append(media_id)
            continue
        if media_id in transferred:
            output.append(transferred[media_id])
            continue

        new_media_id = await _copy_media_to_target(
            runtime,
            target_account_key,
            project_id,
            target_client,
            source_clients[media.installation_id],
            media,
            media_id,
        )
        transferred[media_id] = new_media_id
        output.append(new_media_id)
    return output


def _flow_failure(result: dict, code: str, message: str) -> APIError:
    status = result.get("status")
    retryable = status in {408, 425, 429, 500, 502, 503, 504}
    if result.get("error") and not isinstance(status, int):
        return _error(result)
    return APIError(
        status if isinstance(status, int) and 400 <= status <= 599 else 502,
        code,
        message,
        retryable=retryable,
    )


def _image_digest(image_base64: str) -> str:
    try:
        image_bytes = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise APIError(422, "INVALID_IMAGE_BASE64", "input_images contains invalid Base64 data.") from exc
    return hashlib.sha256(image_bytes).hexdigest()


async def _upload_inline_images(
    runtime,
    connection,
    client,
    project_id: str,
    images: list[InlineImageInput],
    *,
    force_upload: set[str] | None = None,
) -> tuple[list[str], list[str], int]:
    """Resolve caller-owned image bytes to provider-owned, project-scoped media IDs."""
    account_key = _account_key(connection)
    forced = force_upload or set()
    media_ids: list[str] = []
    cached_digests: list[str] = []
    cache_hits = 0
    resolved_media_ids: dict[str, str] = {}
    for image in images:
        digest = _image_digest(image.image_base64)
        if digest in resolved_media_ids:
            media_ids.append(resolved_media_ids[digest])
            continue
        cached = None if digest in forced else runtime.projects.get_media(
            account_key, project_id, digest,
        )
        if cached:
            media_ids.append(cached.google_media_id)
            resolved_media_ids[digest] = cached.google_media_id
            cached_digests.append(digest)
            cache_hits += 1
            continue
        async with runtime.media_lock(account_key, project_id, digest):
            cached = None if digest in forced else runtime.projects.get_media(
                account_key, project_id, digest,
            )
            if cached:
                media_ids.append(cached.google_media_id)
                resolved_media_ids[digest] = cached.google_media_id
                cached_digests.append(digest)
                cache_hits += 1
                continue
            upload_result = await _api(
                client,
                url=UPLOAD_IMAGE_URL,
                body={
                    "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
                    "fileName": image.file_name,
                    "imageBytes": image.image_base64,
                    "isHidden": False,
                    "isUserUploaded": True,
                    "mimeType": image.mime_type,
                },
            )
            media_id = extract_upload_media_id(upload_result)
            if not media_id:
                raise _flow_failure(
                    upload_result, "IMAGE_UPLOAD_FAILED", "Reference image upload failed."
                )
            runtime.projects.put_media(
                account_key,
                project_id,
                digest,
                media_id,
                image.mime_type,
                image.file_name,
                upload_result.get("data") if isinstance(upload_result.get("data"), dict) else None,
                upload_result.get("status") if isinstance(upload_result.get("status"), int) else None,
                upload_result.get("headers") if isinstance(upload_result.get("headers"), dict) else None,
            )
        resolved_media_ids[digest] = media_id
        media_ids.append(media_id)
    return media_ids, cached_digests, cache_hits


def _project_items(result: dict) -> list[dict]:
    try:
        projects = result["data"]["result"]["data"]["json"]["result"]["projects"]
    except (KeyError, TypeError):
        return []
    return [item for item in projects if isinstance(item, dict) and item.get("projectId")]


def _project_page_is_valid(result: dict) -> bool:
    try:
        projects = result["data"]["result"]["data"]["json"]["result"]["projects"]
    except (KeyError, TypeError):
        return False
    return isinstance(projects, list)


def _project_created_at(item: dict) -> float | None:
    info = item.get("projectInfo") if isinstance(item.get("projectInfo"), dict) else {}
    for source in (info, item):
        for key in ("createTime", "creationTime", "createdAt"):
            value = source.get(key)
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value:
                try:
                    return datetime.fromisoformat(value).timestamp()
                except ValueError:
                    continue
    return None


def _latest_project(projects: list[dict]) -> dict | None:
    if not projects:
        return None
    timestamps = [_project_created_at(item) for item in projects]
    if all(value is not None for value in timestamps):
        return max(
            zip(projects, timestamps, strict=True),
            key=lambda pair: pair[1],
        )[0]
    # Google Flow currently returns newest projects first. Preserve that order
    # when a response omits creation timestamps instead of guessing from IDs.
    return projects[0]


def _project_cursor(result: dict) -> str | None:
    try:
        page = result["data"]["result"]["data"]["json"]["result"]
    except (KeyError, TypeError):
        return None
    if not isinstance(page, dict):
        return None
    for key in ("nextCursor", "nextPageCursor", "cursor"):
        value = page.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _remember_operations(runtime, connection, project_id: str, result: dict) -> None:
    account_key = _account_key(connection)
    data = result.get("data")
    if not isinstance(data, dict):
        return
    remembered: set[str] = set()
    media_by_workflow: dict[str, str] = {}
    for media in data.get("media") or []:
        if not isinstance(media, dict):
            continue
        media_name = media.get("name")
        workflow_name = media.get("workflowId") or media.get("workflow_id")
        if isinstance(media_name, str) and media_name and isinstance(workflow_name, str) and workflow_name:
            media_by_workflow[workflow_name] = media_name
    for item in data.get("operations") or []:
        if not isinstance(item, dict):
            continue
        operation = item.get("operation") if isinstance(item.get("operation"), dict) else item
        name = operation.get("name") if isinstance(operation, dict) else None
        if isinstance(name, str) and name:
            runtime.projects.put_operation(name, account_key, project_id, "operation", name)
            remembered.add(name)
    for workflow in data.get("workflows") or []:
        if not isinstance(workflow, dict) or not isinstance(workflow.get("name"), str):
            continue
        name = workflow["name"]
        metadata = workflow.get("metadata") if isinstance(workflow.get("metadata"), dict) else {}
        primary_media_id = metadata.get("primaryMediaId") or media_by_workflow.get(name)
        if isinstance(primary_media_id, str) and primary_media_id:
            runtime.projects.put_operation(name, account_key, project_id, "media", primary_media_id)
            remembered.add(name)
    if remembered:
        return
    for media in data.get("media") or []:
        name = media.get("name") if isinstance(media, dict) else None
        if isinstance(name, str) and name:
            runtime.projects.put_operation(name, account_key, project_id, "media", name)


def _remember_generated_media(runtime, connection, project_id: str, result: dict) -> None:
    """Keep generated image media sticky for later image/video requests."""
    status = result.get("status")
    data = result.get("data")
    if not isinstance(status, int) or status >= 400 or not isinstance(data, dict):
        return
    account_key = _account_key(connection)
    for media in data.get("media") or []:
        media_id = media.get("name") if isinstance(media, dict) else None
        if not isinstance(media_id, str) or not media_id:
            continue
        route_digest = hashlib.sha256(
            f"generated-media-route\0{media_id}".encode("utf-8")
        ).hexdigest()
        runtime.projects.put_media(
            account_key,
            project_id,
            route_digest,
            media_id,
            "image/generated",
            "generated-image",
        )


def _video_generation_succeeded(media: dict) -> bool:
    metadata = media.get("mediaMetadata")
    if not isinstance(metadata, dict):
        return False
    media_status = metadata.get("mediaStatus")
    if not isinstance(media_status, dict):
        return False
    status = media_status.get("mediaGenerationStatus")
    if not isinstance(status, str):
        return False
    normalized = status.upper()
    terminal = normalized.rsplit("_", 1)[-1]
    return terminal in {"SUCCESS", "SUCCESSFUL", "SUCCEEDED", "COMPLETE", "COMPLETED", "DONE"}


_VIDEO_FAILURE_TERMINALS = {
    "ABORTED",
    "BLOCKED",
    "CANCELED",
    "CANCELLED",
    "ERROR",
    "EXPIRED",
    "FAILED",
    "FAILURE",
    "REJECTED",
    "TIMEOUT",
    "UNSUCCESSFUL",
}


def _video_media_generation_failed(status: object) -> bool:
    if not isinstance(status, str) or not status:
        return False
    return status.upper().rsplit("_", 1)[-1] in _VIDEO_FAILURE_TERMINALS


def _video_provider_error_message(error: object, fallback: str) -> str:
    if isinstance(error, dict):
        for key in ("message", "localizedMessage", "status", "code"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return f"{fallback}: {value.strip()}"
            if isinstance(value, (int, float)):
                return f"{fallback}: {value}"
    elif isinstance(error, str) and error.strip():
        return f"{fallback}: {error.strip()}"
    return fallback


def _video_status_failure(result: dict) -> APIError | None:
    """Convert Flow's HTTP-200 task failures into provider errors."""
    data = result.get("data")
    if not isinstance(data, dict):
        return None

    for item in data.get("operations") or []:
        if not isinstance(item, dict):
            continue
        operation = item.get("operation") if isinstance(item.get("operation"), dict) else item
        if not isinstance(operation, dict) or "error" not in operation:
            continue
        return APIError(
            502,
            "VIDEO_OPERATION_FAILED",
            _video_provider_error_message(
                operation.get("error"),
                "Google Flow video operation failed.",
            ),
            retryable=False,
        )

    def failed_media_status(node: object) -> str | None:
        if isinstance(node, list):
            for item in node:
                failure = failed_media_status(item)
                if failure:
                    return failure
            return None
        if not isinstance(node, dict):
            return None
        metadata = node.get("mediaMetadata")
        media_status = metadata.get("mediaStatus") if isinstance(metadata, dict) else None
        status = media_status.get("mediaGenerationStatus") if isinstance(media_status, dict) else None
        if _video_media_generation_failed(status):
            return str(status)
        for value in node.values():
            failure = failed_media_status(value)
            if failure:
                return failure
        return None

    failure_status = failed_media_status(data)
    if failure_status:
        return APIError(
            502,
            "VIDEO_MEDIA_FAILED",
            f"Google Flow video generation failed with status {failure_status}.",
            retryable=False,
        )
    return None


def _completed_video_media(node: object, completed: bool = False) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, list):
        for item in node:
            found.extend(_completed_video_media(item, completed))
        return found
    if not isinstance(node, dict):
        return found
    node_completed = completed or _video_generation_succeeded(node)
    if node.get("done") is True and not node.get("error"):
        node_completed = True
    video = node.get("video")
    generated = video.get("generatedVideo") if isinstance(video, dict) else None
    if node_completed and isinstance(node.get("name"), str) and isinstance(generated, dict):
        found.append(node)
    for value in node.values():
        if isinstance(value, (dict, list)):
            found.extend(_completed_video_media(value, node_completed))
    return found


async def _attach_video_urls(client: BoundFlowClient, result: dict) -> tuple[int, int]:
    """Resolve video and thumbnail redirects only for completed video media."""
    data = result.get("data")
    if not isinstance(data, dict):
        return 0, 0
    video_candidates: dict[str, list[dict]] = {}
    thumbnail_candidates: dict[str, list[dict]] = {}
    available_videos = 0
    available_thumbnails = 0
    for media in _completed_video_media(data):
        video = media.get("video")
        generated = video.get("generatedVideo") if isinstance(video, dict) else None
        if not isinstance(generated, dict):
            continue
        media_id = media.get("name")
        existing_video = generated.get("fifeUrl") or media.get("downloadUrl")
        if isinstance(existing_video, str) and existing_video.startswith("https://"):
            media["downloadUrl"] = existing_video
            available_videos += 1
        elif isinstance(media_id, str) and media_id:
            video_candidates.setdefault(media_id, []).append(media)

        existing_thumbnail = generated.get("thumbnailUrl") or media.get("thumbnailUrl")
        if isinstance(existing_thumbnail, str) and existing_thumbnail.startswith("https://"):
            media["thumbnailUrl"] = existing_thumbnail
            available_thumbnails += 1
        elif isinstance(media_id, str) and media_id:
            thumbnail_candidates.setdefault(media_id, []).append(media)

    jobs = [
        ("video", media_id, client.resolve_media_url(media_id))
        for media_id in video_candidates
    ] + [
        ("thumbnail", media_id, client.resolve_media_url(media_id, thumbnail=True))
        for media_id in thumbnail_candidates
    ]
    if not jobs:
        return available_videos, available_thumbnails
    resolved = await asyncio.gather(
        *(job for _kind, _media_id, job in jobs),
        return_exceptions=True,
    )
    for (kind, media_id, _job), value in zip(jobs, resolved, strict=True):
        if not isinstance(value, str) or not value.startswith("https://"):
            continue
        candidates = video_candidates if kind == "video" else thumbnail_candidates
        for media in candidates[media_id]:
            if kind == "video":
                media["downloadUrl"] = value
                available_videos += 1
            else:
                media["thumbnailUrl"] = value
                available_thumbnails += 1
    return available_videos, available_thumbnails


def _refresh_paid_account(runtime, connection) -> None:
    # A timeout/error can still follow an accepted paid operation, so every
    # attempted paid request invalidates the captured balance until refreshed.
    connection.credits = None
    runtime.bridge.schedule_account_refresh(connection.id, initial_delay=2)


def _remember_project_on_success(runtime, connection, project_id: str, result: dict) -> None:
    status = result.get("status")
    if isinstance(status, int) and status < 400:
        runtime.projects.remember_project(_account_key(connection), project_id, "External")


async def _managed_project(runtime, connection, client) -> str:
    account_key = _account_key(connection)
    stored = runtime.projects.get(account_key)
    if stored and runtime.project_is_synced(connection, account_key):
        runtime.projects.touch(account_key)
        return stored.google_project_id
    async with runtime.project_lock(account_key):
        stored = runtime.projects.get(account_key)
        if stored and runtime.project_is_synced(connection, account_key):
            runtime.projects.touch(account_key)
            return stored.google_project_id
        title = "FlowProvider"
        cursor = None
        seen_cursors: set[str] = set()
        discovered_projects: list[dict] = []
        lookup_complete = False
        for _page_number in range(20):
            search_payload = {
                "json": {"pageSize": 10, "toolName": "PINHOLE", "cursor": cursor},
            }
            if cursor is None:
                search_payload["meta"] = {"values": {"cursor": ["undefined"]}}
            search_result = await client.trpc_request(
                url=f"{TRPC_SEARCH_PROJECTS}?input={quote(json.dumps(search_payload, separators=(',', ':')), safe='')}",
                method="GET",
                headers=TRPC_HEADERS,
            )
            if search_result.get("error") or (
                isinstance(search_result.get("status"), int) and search_result["status"] >= 400
            ):
                raise _flow_failure(search_result, "PROJECT_LIST_FAILED", "Google Flow project lookup failed.")
            if not _project_page_is_valid(search_result):
                raise APIError(
                    502,
                    "PROJECT_LIST_INVALID",
                    "Google Flow project lookup returned an invalid response.",
                    retryable=True,
                )
            projects = _project_items(search_result)
            discovered_projects.extend(projects)
            for item in projects:
                info = item.get("projectInfo") if isinstance(item.get("projectInfo"), dict) else {}
                runtime.projects.remember_project(
                    account_key, item["projectId"], str(info.get("projectTitle") or "Untitled"),
                )
            next_cursor = _project_cursor(search_result)
            if not next_cursor or next_cursor in seen_cursors:
                lookup_complete = True
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        existing = _latest_project(discovered_projects)
        if existing:
            info = (
                existing.get("projectInfo")
                if isinstance(existing.get("projectInfo"), dict)
                else {}
            )
            existing_title = str(info.get("projectTitle") or "Untitled")
            runtime.projects.put(account_key, existing["projectId"], existing_title)
            runtime.mark_project_synced(connection, account_key)
            return existing["projectId"]
        if not lookup_complete:
            raise APIError(
                503,
                "PROJECT_LIST_INCOMPLETE",
                "Google Flow project lookup did not finish; refusing to create a duplicate project.",
                retryable=True,
            )
        result = await client.trpc_request(
            url=TRPC_CREATE_PROJECT,
            method="POST",
            headers=TRPC_HEADERS,
            body={"json": {"projectTitle": title, "toolName": "PINHOLE"}},
        )
        project_id = extract_project_id(result)
        if not project_id:
            raise _flow_failure(result, "PROJECT_CREATE_FAILED", "Google Flow project creation failed.")
        runtime.projects.put(account_key, project_id, title)
        runtime.mark_project_synced(connection, account_key)
        return project_id


async def _api(client, *, url: str, body: dict, captcha_action: str | None = None) -> dict:
    return await client.api_request(
        url=url,
        method="POST",
        headers=API_HEADERS,
        body=body,
        captcha_action=captcha_action,
    )


@router.get("/v1/projects", response_model=None)
async def list_projects(
    request: Request,
    page_size: int = Query(default=10, ge=1, le=100),
    cursor: str | None = Query(default=None, min_length=1, max_length=2000),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, routing_scope)
    payload: dict = {
        "json": {"pageSize": page_size, "toolName": "PINHOLE", "cursor": cursor},
    }
    if cursor is None:
        payload["meta"] = {"values": {"cursor": ["undefined"]}}
    encoded_input = quote(json.dumps(payload, separators=(",", ":")), safe="")
    result = await client.trpc_request(
        url=f"{TRPC_SEARCH_PROJECTS}?input={encoded_input}",
        method="GET",
        headers=TRPC_HEADERS,
    )
    project_items = _project_items(result)
    for item in project_items:
        info = item.get("projectInfo") if isinstance(item.get("projectInfo"), dict) else {}
        runtime.projects.remember_project(
            _account_key(connection),
            item["projectId"],
            str(info.get("projectTitle") or "Untitled"),
        )
    if cursor is None:
        newest_project = _latest_project(project_items)
        if newest_project:
            account_key = _account_key(connection)
            info = (
                newest_project.get("projectInfo")
                if isinstance(newest_project.get("projectInfo"), dict)
                else {}
            )
            runtime.projects.put(
                account_key,
                newest_project["projectId"],
                str(info.get("projectTitle") or "Untitled"),
            )
            runtime.mark_project_synced(connection, account_key)
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/projects", response_model=None)
async def create_project(
    payload: CreateProjectRequest,
    request: Request,
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request)
    result = await client.trpc_request(
        url=TRPC_CREATE_PROJECT,
        method="POST",
        headers=TRPC_HEADERS,
        body={"json": {"projectTitle": payload.title, "toolName": "PINHOLE"}},
    )
    project_id = extract_project_id(result)
    if project_id:
        account_key = _account_key(connection)
        runtime.projects.remember_project(account_key, project_id, payload.title)
        if payload.title == "FlowProvider":
            runtime.projects.put(account_key, project_id, payload.title)
            runtime.mark_project_synced(connection, account_key)
    return _scoped_response(result, runtime.settings, connection)




@router.post("/v1/media", response_model=None)
async def upload_image(
    payload: ImageUploadRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    excluded_account_keys = {
        account_key
        for project_id in payload.excluded_project_ids
        if (account_key := runtime.projects.installation_for_project(project_id))
    }
    connection, client = _connection(
        request,
        routing_scope,
        min_credits=payload.required_credits,
        project_id=payload.project_id,
        excluded_account_keys=excluded_account_keys,
    )
    resolved_project_id = payload.project_id or await _managed_project(runtime, connection, client)
    digest = _image_digest(payload.image_base64)
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


@router.post("/v1/images/generations", response_model=None)
async def generate_image(
    payload: ImageGenerationRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    known_media = _known_media(runtime, list(payload.reference_media_ids))
    auto_transfer = _should_auto_transfer_media(
        runtime, payload.project_id, routing_scope, known_media,
    )
    stored_route = (
        None
        if auto_transfer
        else _stored_media_route(runtime, list(payload.reference_media_ids))
    )
    stored_account_key, stored_project_id = stored_route or (None, None)
    if payload.project_id and stored_project_id and payload.project_id != stored_project_id:
        raise APIError(
            409,
            "MEDIA_PROJECT_MISMATCH",
            "Referenced media do not belong to the requested Google Flow project.",
            field="project_id",
        )
    effective_project_id = payload.project_id or (None if auto_transfer else stored_project_id)
    connection, client = _connection(
        request,
        routing_scope,
        project_id=effective_project_id,
        required_account_key=None if auto_transfer else stored_account_key,
    )
    managed = effective_project_id is None
    account_key = _account_key(connection)
    force_upload: set[str] = set()
    project_recovered = False
    for attempt in range(3):
        project_id = effective_project_id or await _managed_project(runtime, connection, client)
        reference_media_ids = await _rehydrate_media_ids(
            runtime,
            connection,
            client,
            list(payload.reference_media_ids),
            project_id,
            known_media,
        )
        cached_digests: list[str] = []
        cache_hits = 0
        stale_project = False
        for image in payload.input_images:
            digest = _image_digest(image.image_base64)
            cached = None if digest in force_upload else runtime.projects.get_media(
                account_key, project_id, digest,
            )
            if cached:
                reference_media_ids.append(cached.google_media_id)
                cached_digests.append(digest)
                cache_hits += 1
                continue
            async with runtime.media_lock(account_key, project_id, digest):
                cached = None if digest in force_upload else runtime.projects.get_media(
                    account_key, project_id, digest,
                )
                if cached:
                    reference_media_ids.append(cached.google_media_id)
                    cached_digests.append(digest)
                    cache_hits += 1
                    continue
                upload_result = await _api(
                    client,
                    url=UPLOAD_IMAGE_URL,
                    body={
                        "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
                        "fileName": image.file_name,
                        "imageBytes": image.image_base64,
                        "isHidden": False,
                        "isUserUploaded": True,
                        "mimeType": image.mime_type,
                    },
                )
                if managed and upload_result.get("status") == 404 and not project_recovered:
                    runtime.projects.invalidate(account_key)
                    project_recovered = True
                    force_upload.clear()
                    stale_project = True
                    break
                media_id = extract_upload_media_id(upload_result)
                if not media_id:
                    raise _flow_failure(upload_result, "IMAGE_UPLOAD_FAILED", "Reference image upload failed.")
                runtime.projects.put_media(
                    account_key,
                    project_id,
                    digest,
                    media_id,
                    image.mime_type,
                    image.file_name,
                    upload_result.get("data") if isinstance(upload_result.get("data"), dict) else None,
                    upload_result.get("status") if isinstance(upload_result.get("status"), int) else None,
                    upload_result.get("headers") if isinstance(upload_result.get("headers"), dict) else None,
                )
            reference_media_ids.append(media_id)
        if stale_project:
            continue

        ctx = client_context(project_id, connection.paygate_tier or "PAYGATE_TIER_ONE")
        requests = []
        for _ in range(payload.variant_count):
            item = {
                "clientContext": ctx,
                "structuredPrompt": {"parts": [{"text": payload.prompt}]},
                "imageAspectRatio": payload.aspect_ratio,
                "imageModelName": resolve_image_model(payload.model),
            }
            if reference_media_ids:
                item["imageInputs"] = [
                    {"name": media_id, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
                    for media_id in reference_media_ids
                ]
            requests.append(item)
        result = await _api(
            client,
            url=f"{FLOW_API_BASE}/v1/projects/{project_id}/flowMedia:batchGenerateImages",
            body={
                "clientContext": ctx,
                "mediaGenerationContext": {"batchId": str(uuid.uuid4())},
                "useNewMedia": True,
                "requests": requests,
            },
            captcha_action=CAPTCHA_IMAGE,
        )
        if result.get("status") == 404 and cached_digests and attempt < 2:
            for digest in cached_digests:
                runtime.projects.invalidate_media(account_key, project_id, digest)
            force_upload.update(cached_digests)
            continue
        if managed and result.get("status") == 404 and not project_recovered and attempt < 2:
            runtime.projects.invalidate(account_key)
            project_recovered = True
            force_upload.clear()
            continue
        _remember_project_on_success(runtime, connection, project_id, result)
        _remember_generated_media(runtime, connection, project_id, result)
        response = _scoped_response(result, runtime.settings, connection)
        response.headers["X-Flow-Project-Id"] = project_id
        response.headers["X-Flow-Media-Cache-Hits"] = str(cache_hits)
        return response
    raise APIError(502, "PROJECT_RECOVERY_FAILED", "Google Flow project recovery failed.", retryable=True)




@router.post("/v1/videos/generations", response_model=None)
async def generate_video(
    payload: VideoGenerationRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    credit_cost = (
        max(20, OMNI_FLASH_CREDIT_COST[payload.duration_seconds])
        if isinstance(payload, OmniVideoGenerationRequest) else 20
    )
    inline_images = list(payload.input_images)
    if isinstance(payload, ImageToVideoGenerationRequest):
        media_ids = [payload.start_media_id] if payload.start_media_id else []
    else:
        media_ids = list(payload.reference_media_ids)
    requested_media_ids = list(media_ids)
    known_media = _known_media(runtime, media_ids)
    auto_transfer = _should_auto_transfer_media(
        runtime, payload.project_id, routing_scope, known_media,
    )
    stored_route = (
        None
        if auto_transfer
        else _stored_media_route(request.app.state.runtime, media_ids)
    )
    stored_account_key, stored_project_id = stored_route or (None, None)
    if payload.project_id and stored_project_id and payload.project_id != stored_project_id:
        raise APIError(
            409,
            "MEDIA_PROJECT_MISMATCH",
            "Referenced media do not belong to the requested Google Flow project.",
            field="project_id",
        )
    effective_project_id = payload.project_id or (None if auto_transfer else stored_project_id)
    can_failover = bool(
        not routing_scope
        and (not requested_media_ids or all(
            media_id in known_media for media_id in requested_media_ids
        ))
        and (inline_images or known_media)
    )
    preferred_account_key = (
        runtime.projects.installation_for_project(payload.project_id)
        if payload.project_id else stored_account_key
    )
    try:
        connection, client = _connection(
            request,
            routing_scope,
            min_credits=credit_cost,
            project_id=effective_project_id,
            required_account_key=None if auto_transfer else stored_account_key,
        )
    except APIError as exc:
        recovered = False
        if can_failover and exc.code in {
            "VIDEO_ACCOUNT_UNAVAILABLE",
            "MEDIA_ACCOUNT_UNAVAILABLE",
            "PROJECT_ACCOUNT_UNAVAILABLE",
        }:
            try:
                connection, client = _connection(
                    request,
                    min_credits=credit_cost,
                    excluded_account_keys={preferred_account_key} if preferred_account_key else None,
                )
                effective_project_id = None
                auto_transfer = True
                recovered = True
            except APIError:
                pass
        if not recovered:
            if (
                getattr(runtime.settings, "worker_enabled", True)
                and payload.project_id is None
                and exc.code in {
                    "PROVIDER_ACCOUNT_UNAVAILABLE",
                    "VIDEO_ACCOUNT_UNAVAILABLE",
                }
            ):
                job_id = f"job_{uuid.uuid4().hex}"
                runtime.projects.enqueue_job(
                    job_id=job_id,
                    job_type=payload.type,
                    request_payload=payload.model_dump(mode="json"),
                )
                queued_resp = _response({
                    "status": 200,
                    "data": {
                        "status": "queued",
                        "job_id": job_id,
                        "workflows": [{"name": job_id}],
                        "message": "All provider accounts are currently busy. Request is queued and will execute automatically.",
                        "remainingCredits": None,
                    }
                })
                queued_resp.headers["X-Flow-Job-Id"] = job_id
                queued_resp.headers["X-Flow-Job-Status"] = "queued"
                return queued_resp
            raise
    if not auto_transfer and stored_account_key and _account_key(connection) != stored_account_key:
        raise APIError(
            409,
            "MEDIA_ACCOUNT_MISMATCH",
            "Referenced media do not belong to the selected Google Flow account.",
        )
    failover_attempted = False
    stale_inline_retried = False
    project_recovered = False
    force_upload: set[str] = set()
    while True:
        resolved_project_id = effective_project_id or await _managed_project(runtime, connection, client)
        media_ids = await _rehydrate_media_ids(
            runtime,
            connection,
            client,
            requested_media_ids,
            resolved_project_id,
            known_media,
        )
        try:
            inline_media_ids, cached_digests, cache_hits = await _upload_inline_images(
                runtime,
                connection,
                client,
                resolved_project_id,
                inline_images,
                force_upload=force_upload,
            )
        except APIError as exc:
            if effective_project_id is None and exc.status_code == 404 and not project_recovered:
                runtime.projects.invalidate(_account_key(connection))
                project_recovered = True
                force_upload.clear()
                continue
            raise
        media_ids.extend(inline_media_ids)
        tier = connection.paygate_tier or "PAYGATE_TIER_ONE"
        ctx = client_context(resolved_project_id, tier)
        if isinstance(payload, ImageToVideoGenerationRequest):
            model = resolve_video_model(tier, payload.aspect_ratio, payload.quality)
            if not model:
                raise APIError(422, "INVALID_VIDEO_QUALITY", "Unsupported video quality for this account.")
            body = {
                "clientContext": ctx,
                "mediaGenerationContext": {"batchId": str(uuid.uuid4())},
                "requests": [{
                    "aspectRatio": payload.aspect_ratio,
                    "textInput": {"prompt": payload.prompt},
                    "videoModelKey": model,
                    "startImage": {"mediaId": media_ids[0]},
                    "metadata": {"sceneId": str(uuid.uuid4())},
                }],
                "useV2ModelConfig": True,
            }
            result = await _api(client, url=VIDEO_I2V_URL, body=body, captcha_action=CAPTCHA_VIDEO)
        else:
            body = {
                "mediaGenerationContext": {
                    "batchId": str(uuid.uuid4()),
                    "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
                },
                "clientContext": ctx,
                "requests": [{
                    "aspectRatio": payload.aspect_ratio,
                    "textInput": {"prompt": payload.prompt},
                    "videoModelKey": payload.duration_model,
                    "metadata": {},
                    "referenceImages": [
                        {"mediaId": media_id, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                        for media_id in media_ids
                    ],
                }],
                "useV2ModelConfig": True,
            }
            result = await _api(client, url=VIDEO_OMNI_URL, body=body, captcha_action=CAPTCHA_VIDEO)
        _refresh_paid_account(runtime, connection)
        if _credit_exhaustion(result) and can_failover and not failover_attempted:
            try:
                connection, client = _connection(
                    request,
                    min_credits=credit_cost,
                    excluded_account_keys={_account_key(connection)},
                )
            except APIError:
                pass
            else:
                effective_project_id = None
                auto_transfer = True
                failover_attempted = True
                force_upload.clear()
                stale_inline_retried = False
                project_recovered = False
                continue
        if result.get("status") == 404 and cached_digests and not stale_inline_retried:
            account_key = _account_key(connection)
            for digest in cached_digests:
                runtime.projects.invalidate_media(account_key, resolved_project_id, digest)
            force_upload.update(cached_digests)
            stale_inline_retried = True
            continue
        if result.get("status") == 404 and effective_project_id is None and not project_recovered:
            runtime.projects.invalidate(_account_key(connection))
            project_recovered = True
            force_upload.clear()
            continue
        _remember_project_on_success(runtime, connection, resolved_project_id, result)
        _remember_operations(runtime, connection, resolved_project_id, result)

        job_id = f"job_{uuid.uuid4().hex}"
        op_name = None
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        for wf in data.get("workflows") or []:
            if isinstance(wf, dict) and wf.get("name"):
                op_name = wf["name"]
                break
        if not op_name:
            for op in data.get("operations") or []:
                inner = op.get("operation") if isinstance(op, dict) and isinstance(op.get("operation"), dict) else op
                if isinstance(inner, dict) and inner.get("name"):
                    op_name = inner["name"]
                    break
        if op_name and payload.project_id is None:
            try:
                runtime.projects.enqueue_job(job_id, payload.type, payload.model_dump(mode="json"))
                runtime.projects.update_job_running(
                    job_id,
                    operation_name=op_name,
                    installation_id=_account_key(connection),
                    google_project_id=resolved_project_id,
                    poll_name=op_name,
                )
            except Exception:
                pass

        response = _paid_scoped_response(result, runtime.settings, connection)
        response.headers["X-Flow-Project-Id"] = resolved_project_id
        response.headers["X-Flow-Media-Cache-Hits"] = str(cache_hits)
        return response


@router.post("/v1/videos/status", response_model=None)
async def check_video_operations(
    payload: VideoStatusRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    scoped_account_key = (
        _decode_routing_scope(runtime.settings, routing_scope)
        if routing_scope else None
    )

    cached_jobs = [runtime.projects.get_job_by_operation(name) for name in payload.operation_names]
    if all(j is not None and j.status == "completed" and j.result_data for j in cached_jobs):
        if len(cached_jobs) == 1:
            return _response({"status": 200, "data": cached_jobs[0].result_data})
        merged: dict = {}
        for j in cached_jobs:
            for k, v in (j.result_data or {}).items():
                if isinstance(v, list):
                    merged.setdefault(k, []).extend(v)
                elif k not in merged:
                    merged[k] = v
        return _response({"status": 200, "data": merged})

    failed_jobs = [j for j in cached_jobs if j is not None and j.status == "failed"]
    if failed_jobs:
        first_failed = failed_jobs[0]
        return _response({
            "status": 200,
            "data": {
                "status": "failed",
                "error": first_failed.error_message or "Video generation failed.",
                "operations": [
                    {
                        "name": first_failed.operation_name or first_failed.job_id,
                        "done": True,
                        "error": first_failed.error_message or "Video generation failed.",
                    }
                ],
            }
        })

    if all(j is not None and j.status in {"queued", "running"} for j in cached_jobs):
        statuses = [j.status for j in cached_jobs if j is not None]
        overall = "running" if "running" in statuses else "queued"
        return _response({
            "status": 200,
            "data": {
                "status": overall,
                "message": (
                    "Video is currently rendering on Google Flow."
                    if overall == "running"
                    else "Job is queued waiting for an available account slot."
                ),
                "operations": [
                    {
                        "name": j.operation_name or j.poll_name or j.job_id,
                        "done": False,
                        "status": j.status,
                    }
                    for j in cached_jobs if j is not None
                ],
                "workflows": [
                    {"name": j.operation_name or j.job_id}
                    for j in cached_jobs if j is not None
                ],
            }
        })

    route_rows = [
        (operation_name, runtime.projects.get_operation(operation_name))
        for operation_name in payload.operation_names
    ]
    unknown = [name for name, route in route_rows if route is None]
    if unknown and not scoped_account_key:
        raise APIError(
            409,
            "OPERATION_ROUTE_UNKNOWN",
            f"No stored provider account route exists for {len(unknown)} video operation(s).",
            field="operation_names",
        )

    available = runtime.bridge.ready_connections()
    available = [item for item in available if not getattr(item, "simulation_mode", False)]
    scoped_connection = None
    if scoped_account_key:
        scoped_connection = next(
            (
                item for item in available
                if _account_key(item) == scoped_account_key
                and runtime.can_reserve(item)
            ),
            None,
        )
        if scoped_connection is None:
            raise APIError(503, "ROUTING_SCOPE_UNAVAILABLE", "The routed account is unavailable.", retryable=True)

    grouped: dict[str, list[dict]] = {}
    for operation_name, route in route_rows:
        if route is None:
            account_key = _account_key(scoped_connection)
            item = {
                "original": operation_name, "kind": "operation",
                "poll_name": operation_name, "project_id": None,
            }
        else:
            account_key = route.installation_id
            if scoped_connection and account_key != _account_key(scoped_connection):
                raise APIError(409, "OPERATION_ACCOUNT_MISMATCH", "The routing scope does not own this operation.")
            item = {
                "original": operation_name, "kind": route.route_kind,
                "poll_name": route.poll_name, "project_id": route.google_project_id,
            }
        grouped.setdefault(account_key, []).append(item)

    group_results: list[tuple[object, dict, tuple[int, int]]] = []
    poll_jobs: list[tuple[object, BoundFlowClient, dict]] = []
    order: dict[str, int] = {}
    for index, (operation_name, route) in enumerate(route_rows):
        order.setdefault(operation_name, index)
        if route is not None:
            order.setdefault(route.poll_name, index)

    for account_key, route_items in grouped.items():
        connection = next(
            (item for item in available if _account_key(item) == account_key),
            None,
        )
        if connection is None:
            raise APIError(
                503,
                "OPERATION_ACCOUNT_UNAVAILABLE",
                "The Google Flow account that owns a requested video operation is unavailable.",
                retryable=True,
            )
        if not _reserve(request, connection):
            raise APIError(503, "OPERATION_ACCOUNT_UNAVAILABLE", "The operation account is at capacity.", retryable=True)
        client = BoundFlowClient(runtime.bridge, connection.id)
        operations = [item for item in route_items if item["kind"] == "operation"]
        media = [item for item in route_items if item["kind"] == "media"]
        bodies = []
        if operations:
            bodies.append({
                "operations": [
                    {"operation": {"name": item["poll_name"]}}
                    for item in operations
                ]
            })
        if media:
            bodies.append({
                "media": [
                    {"name": item["poll_name"], "projectId": item["project_id"]}
                    for item in media
                ]
            })
        for body in bodies:
            poll_jobs.append((connection, client, body))

    results = await asyncio.gather(*(
        _api(client, url=VIDEO_POLL_URL, body=body)
        for _connection, client, body in poll_jobs
    ))
    for result in results:
        status = result.get("status")
        if not isinstance(status, int) or status >= 400:
            return _response(result)
        failure = _video_status_failure(result)
        if failure:
            raise failure
    url_counts = await asyncio.gather(*(
        _attach_video_urls(client, result)
        for (_connection, client, _body), result in zip(poll_jobs, results, strict=True)
    ))
    for (connection, _client, _body), result, url_counts_for_result in zip(
        poll_jobs, results, url_counts, strict=True,
    ):
        group_results.append((connection, result, url_counts_for_result))

    if len(group_results) == 1:
        connection, result, (video_url_count, thumbnail_url_count) = group_results[0]
        data = result.get("data")
        if isinstance(data, dict) and _completed_video_media(data):
            for op_name in payload.operation_names:
                existing_job = runtime.projects.get_job_by_operation(op_name)
                if existing_job and existing_job.status != "completed":
                    runtime.projects.update_job_completed(existing_job.job_id, data)
        response = _scoped_response(result, runtime.settings, connection)
        response.headers["X-Flow-Video-Urls"] = str(video_url_count)
        response.headers["X-Flow-Thumbnail-Urls"] = str(thumbnail_url_count)
        return response

    merged: dict = {}
    for _connection_item, result, _url_counts in group_results:
        data = result.get("data")
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
            elif key not in merged:
                merged[key] = value
    def result_name(item: object) -> str | None:
        if not isinstance(item, dict):
            return None
        inner = item.get("operation") if isinstance(item.get("operation"), dict) else item
        return inner.get("name") if isinstance(inner, dict) and isinstance(inner.get("name"), str) else None

    for value in merged.values():
        if isinstance(value, list):
            value.sort(key=lambda item: order.get(result_name(item) or "", len(order)))
    response = _response({"status": 200, "data": merged})
    response.headers["X-Flow-Operation-Groups"] = str(len(grouped))
    response.headers["X-Flow-Video-Urls"] = str(sum(item[2][0] for item in group_results))
    response.headers["X-Flow-Thumbnail-Urls"] = str(sum(item[2][1] for item in group_results))
    return response
