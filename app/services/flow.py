from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import quote

from app.api.errors import APIError
from app.api.schemas import InlineImageInput
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    API_HEADERS,
    TRPC_CREATE_PROJECT,
    TRPC_HEADERS,
    TRPC_SEARCH_PROJECTS,
    UPLOAD_IMAGE_URL,
)
from app.providers.google_flow.sdk.helpers import (
    extract_project_id,
    extract_upload_media_id,
)

ROUTING_SCOPE_HEADER = "X-Provider-Routing-Scope"
ROUTING_SCOPE_VERSION = "v2"


def _account_key(connection) -> str:
    email = str(getattr(connection, "account_email", "") or "").strip().lower()
    return f"{connection.installation_id}\n{email}" if email else str(connection.installation_id)


def _scope_secret(settings) -> bytes:
    # Routing scopes use the private connector credential, separate from the
    # business API key. Rotating the connector key invalidates existing scopes.
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


def _error(result: dict) -> APIError:
    reason = str(result.get("error") or "extension_request_failed")
    if "timeout" in reason.lower():
        return APIError(504, "EXTENSION_TIMEOUT", "The extension request timed out.", retryable=True)
    if "disconnect" in reason.lower():
        return APIError(503, "EXTENSION_DISCONNECTED", "The extension disconnected.", retryable=True)
    return APIError(502, "EXTENSION_REQUEST_FAILED", reason, retryable=True)


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


def _validate_project_route(runtime, project_id: str | None) -> None:
    """Reject project IDs that are not owned by a known Provider account.

    A queued job cannot safely discover the owner later: doing so would allow
    the worker to send a project-bound request through an arbitrary account.
    """
    if project_id and runtime.projects.installation_for_project(project_id) is None:
        raise APIError(
            409,
            "PROJECT_ROUTE_UNKNOWN",
            "No provider account route is stored for this Google Flow project.",
            field="project_id",
        )


def _generation_route(runtime, project_id, routing_scope, media_ids, has_inline):
    """Keep explicit routes sticky; inferred routes remain managed preferences."""
    _validate_project_route(runtime, project_id)
    account = _decode_routing_scope(runtime.settings, routing_scope) if routing_scope else None
    owner = runtime.projects.installation_for_project(project_id) if project_id else None
    if account and owner and account != owner:
        raise APIError(409, "PROJECT_ACCOUNT_MISMATCH", "The routing scope does not own this project.")
    if media_ids and not has_inline and not project_id:
        inferred = _stored_media_route(runtime, media_ids)
        if inferred:
            inferred_account, inferred_project = inferred
            if account and account != inferred_account:
                raise APIError(409, "MEDIA_ACCOUNT_MISMATCH", "The routing scope does not own the referenced media.")
            account = account or inferred_account
            project_id = inferred_project
    return account or owner, project_id


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
            if _account_key(item) == account_key
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

        stored_asset = runtime.projects.asset_store.read(media.content_sha256, media.mime_type)
        if stored_asset is not None:
            raw_bytes, mime_type = stored_asset
            content_sha256 = media.content_sha256
        else:
            if source_client is None:
                raise APIError(
                    503,
                    "MEDIA_REHYDRATION_FAILED",
                    "Source media is not available locally and owning account is offline.",
                    retryable=True,
                )
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
                mime_type = _transfer_mime_type(media, downloaded)

        cached = runtime.projects.get_media(target_account_key, project_id, content_sha256)
        if cached:
            return cached.google_media_id

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
            stored_asset = runtime.projects.asset_store.read(media.content_sha256, media.mime_type)
            if stored_asset is None:
                raise APIError(
                    503,
                    "MEDIA_SOURCE_UNAVAILABLE",
                    "The account that owns a referenced media is not currently available for transfer.",
                    retryable=True,
                )
            source_clients[media.installation_id] = None
        else:
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


def _persist_inline_assets(runtime, images: list[InlineImageInput]) -> list[str]:
    """Persist inline request images before enqueueing a durable job.

    The worker may run after an API restart, so raw inline bytes cannot live
    only in Runtime memory. Store the bytes in the content-addressed asset
    store and snapshot their hashes in the job payload instead.
    """
    digests: list[str] = []
    for image in images:
        try:
            digest, _path, _size = runtime.projects.persist_asset(
                image.image_base64, image.mime_type, image.file_name,
            )
        except ValueError as exc:
            raise APIError(422, "INVALID_IMAGE", str(exc), field="input_images") from exc
        digests.append(digest)
    runtime.projects.touch_assets(list(dict.fromkeys(digests)))
    return digests


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
            f"generated-media-route\0{media_id}".encode()
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
    # Keep only diagnostic fields, never serialize the complete upstream
    # response (which can contain signed URLs, headers, or input media).
    details: list[str] = []

    def collect(node: object, depth: int = 0) -> None:
        if depth > 6 or len(details) >= 8:
            return
        if isinstance(node, str) and node.strip():
            value = " ".join(node.split())[:400]
            if value not in details:
                details.append(value)
        elif isinstance(node, dict):
            for key in ("message", "localizedMessage", "reason", "status", "code"):
                value = node.get(key)
                if isinstance(value, str):
                    collect(value, depth + 1)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    collect(f"{key}={value}", depth + 1)
            for key in ("error", "details"):
                collect(node.get(key), depth + 1)
        elif isinstance(node, list):
            for item in node[:8]:
                collect(item, depth + 1)

    collect(error)
    if details:
        return f"{fallback} Upstream details: {'; '.join(details)}"[:1000]
    return (
        f"{fallback} Google Flow did not provide a detailed reason. "
        "Check the failed media in the Google Flow project before creating another video."
    )


def _extract_upstream_error_codes(node: object) -> tuple[str | None, str | None]:
    """Extract (upstream_code, upstream_status) from upstream error diagnostics."""
    code: str | None = None
    status: str | None = None

    def scan(item: object, depth: int = 0) -> None:
        nonlocal code, status
        if depth > 6 or (code is not None and status is not None):
            return
        if isinstance(item, dict):
            if code is None and item.get("code") is not None:
                code = str(item["code"])
            if status is None:
                for key in ("reason", "status"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        status = val.strip()
                        break
            for key in ("error", "details"):
                scan(item.get(key), depth + 1)
        elif isinstance(item, list):
            for sub in item[:8]:
                scan(sub, depth + 1)

    scan(node)
    return code, status


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
        u_code, u_status = _extract_upstream_error_codes(operation.get("error"))
        error_code = u_status or u_code or "VIDEO_OPERATION_FAILED"
        return APIError(
            502,
            error_code,
            _video_provider_error_message(
                operation.get("error"),
                "Google Flow video operation failed.",
            ),
            retryable=False,
            upstream_code=u_code,
            upstream_status=u_status,
        )

    def failed_media_status(node: object) -> tuple[str, list[dict]] | None:
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
            # Error fields may live on the status, metadata, or media item.
            # Restrict extraction to this failed item, not sibling media.
            return str(status), [media_status, metadata, node]
        for value in node.values():
            failure = failed_media_status(value)
            if failure:
                return failure
        return None

    failure_status = failed_media_status(data)
    if failure_status:
        status, diagnostics = failure_status
        u_code, u_status = _extract_upstream_error_codes(diagnostics)
        error_code = u_status or u_code or str(status)
        return APIError(
            502,
            error_code,
            _video_provider_error_message(
                diagnostics,
                f"Google Flow video generation failed with status {status}.",
            ),
            retryable=False,
            upstream_code=u_code,
            upstream_status=u_status or str(status),
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
