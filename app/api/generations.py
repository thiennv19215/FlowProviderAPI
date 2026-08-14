from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Request, Response

from app.api.errors import APIError
from app.api.schemas import (
    CreateProjectRequest, ImageGenerationRequest, ImageUploadRequest,
    ImageToVideoGenerationRequest, OmniVideoGenerationRequest, VideoGenerationRequest,
    VideoStatusRequest,
)
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    API_HEADERS, CAPTCHA_IMAGE, CAPTCHA_VIDEO, FLOW_API_BASE, TRPC_CREATE_PROJECT,
    TRPC_HEADERS, TRPC_SEARCH_PROJECTS, UPLOAD_IMAGE_URL, VIDEO_I2V_URL, VIDEO_OMNI_URL,
    VIDEO_POLL_URL,
)
from app.providers.google_flow.sdk.helpers import (
    client_context, extract_project_id, extract_upload_media_id,
    resolve_image_model, resolve_video_model,
)

router = APIRouter(tags=["Google Flow"])
ROUTING_SCOPE_HEADER = "X-Provider-Routing-Scope"
ROUTING_SCOPE_VERSION = "v1"


def _account_key(connection) -> str:
    email = str(getattr(connection, "account_email", "") or "").strip().lower()
    return f"{connection.installation_id}\n{email}" if email else connection.installation_id


def _authorize(settings, authorization: str | None) -> None:
    expected = settings.bootstrap_api_key
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected:
        raise APIError(503, "API_AUTH_UNAVAILABLE", "Provider API key is not configured.")
    if not hmac.compare_digest(expected, supplied):
        raise APIError(401, "INVALID_API_KEY", "A valid Provider API key is required.")


def _scope_secret(settings) -> bytes:
    # The bootstrap key is already required for every business endpoint. Reuse it
    # as the HMAC secret so routing scopes remain stateless and deployment needs no
    # second secret. Rotating the API key intentionally invalidates old scopes.
    return settings.bootstrap_api_key.encode("utf-8")


def _encode_routing_scope(settings, installation_id: str) -> str:
    payload = base64.urlsafe_b64encode(installation_id.encode("utf-8")).decode("ascii").rstrip("=")
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
        installation_id = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.") from exc
    if not installation_id:
        raise APIError(400, "ROUTING_SCOPE_INVALID", "Provider routing scope is invalid.")
    return installation_id


def _reserve(request: Request, connection) -> bool:
    runtime = request.app.state.runtime
    if not runtime.reserve_connection(connection):
        return False
    reservations = getattr(request.state, "provider_reservations", None)
    if reservations is None:
        reservations = []
        request.state.provider_reservations = reservations
    reservations.append(connection.id)
    return True


def _connection(
    request: Request,
    authorization: str | None,
    routing_scope: str | None = None,
    *,
    min_credits: int = 0,
    project_id: str | None = None,
):
    runtime = request.app.state.runtime
    _authorize(runtime.settings, authorization)
    available = runtime.bridge.ready_connections(min_credits=min_credits)

    if routing_scope:
        installation_id = _decode_routing_scope(runtime.settings, routing_scope)
        connection = next(
            (
                item for item in available
                if getattr(item, "installation_id", None) == installation_id
                and runtime.connection_load(item) < item.max_slots
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
        if not _reserve(request, connection):
            raise APIError(503, "ROUTING_SCOPE_UNAVAILABLE", "The routed account is at capacity.", retryable=True)
        return connection, BoundFlowClient(runtime.bridge, connection.id)
    if project_id:
        account_key = runtime.projects.installation_for_project(project_id)
        if account_key:
            connection = next(
                (
                    item for item in available
                    if _account_key(item) == account_key
                    and runtime.connection_load(item) < item.max_slots
                ),
                None,
            )
            if connection is None:
                raise APIError(
                    503,
                    "PROJECT_ACCOUNT_UNAVAILABLE",
                    "The Google Flow account that owns this project is not currently available.",
                    retryable=True,
                )
            if not _reserve(request, connection):
                raise APIError(503, "PROJECT_ACCOUNT_UNAVAILABLE", "The project account is at capacity.", retryable=True)
            return connection, BoundFlowClient(runtime.bridge, connection.id)
    available = [item for item in available if runtime.connection_load(item) < item.max_slots]
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
    if not _reserve(request, connection):
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
    installation_id = getattr(connection, "installation_id", None)
    if installation_id:
        response.headers[ROUTING_SCOPE_HEADER] = _encode_routing_scope(settings, installation_id)
    return response


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


def _project_items(result: dict) -> list[dict]:
    try:
        projects = result["data"]["result"]["data"]["json"]["result"]["projects"]
    except (KeyError, TypeError):
        return []
    return [item for item in projects if isinstance(item, dict) and item.get("projectId")]


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
        primary_media_id = metadata.get("primaryMediaId")
        if isinstance(primary_media_id, str) and primary_media_id:
            runtime.projects.put_operation(name, account_key, project_id, "media", primary_media_id)
            remembered.add(name)
    if remembered:
        return
    for media in data.get("media") or []:
        name = media.get("name") if isinstance(media, dict) else None
        if isinstance(name, str) and name:
            runtime.projects.put_operation(name, account_key, project_id, "media", name)


def _refresh_paid_account(runtime, connection, result: dict) -> None:
    status = result.get("status")
    if not isinstance(status, int) or status >= 400:
        return
    # Do not schedule another paid job against a balance captured before this
    # operation. Images remain eligible while the fresh balance is requested.
    connection.credits = None
    asyncio.create_task(runtime.bridge.refresh_account(connection.id))


async def _managed_project(runtime, connection, client) -> str:
    account_key = _account_key(connection)
    stored = runtime.projects.get(account_key)
    if stored:
        runtime.projects.touch(account_key)
        return stored.google_project_id
    async with runtime.project_lock(account_key):
        stored = runtime.projects.get(account_key)
        if stored:
            runtime.projects.touch(account_key)
            return stored.google_project_id
        title = "FlowProvider"
        cursor = None
        seen_cursors: set[str] = set()
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
            projects = _project_items(search_result)
            for item in projects:
                info = item.get("projectInfo") if isinstance(item.get("projectInfo"), dict) else {}
                runtime.projects.remember_project(
                    account_key, item["projectId"], str(info.get("projectTitle") or "Untitled"),
                )
            existing = next(
                (
                    item for item in projects
                    if isinstance(item.get("projectInfo"), dict)
                    and item["projectInfo"].get("projectTitle") == title
                ),
                None,
            )
            if existing:
                runtime.projects.put(account_key, existing["projectId"], title)
                return existing["projectId"]
            next_cursor = _project_cursor(search_result)
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
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
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, authorization, routing_scope)
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
    for item in _project_items(result):
        info = item.get("projectInfo") if isinstance(item.get("projectInfo"), dict) else {}
        runtime.projects.remember_project(
            _account_key(connection),
            item["projectId"],
            str(info.get("projectTitle") or "Untitled"),
        )
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/projects", response_model=None)
async def create_project(
    payload: CreateProjectRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, authorization)
    result = await client.trpc_request(
        url=TRPC_CREATE_PROJECT,
        method="POST",
        headers=TRPC_HEADERS,
        body={"json": {"projectTitle": payload.title, "toolName": "PINHOLE"}},
    )
    project_id = extract_project_id(result)
    if project_id:
        runtime.projects.remember_project(_account_key(connection), project_id, payload.title)
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/media", response_model=None)
async def upload_image(
    payload: ImageUploadRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(
        request, authorization, routing_scope, project_id=payload.project_id,
    )
    digest = _image_digest(payload.image_base64)
    account_key = _account_key(connection)
    async with runtime.media_lock(account_key, payload.project_id, digest):
        cached = runtime.projects.get_media(account_key, payload.project_id, digest)
        if cached:
            cached_data = cached.response_data or {
                "media": {
                    "name": cached.google_media_id,
                    "projectId": payload.project_id,
                }
            }
            response = _scoped_response(
                {"status": 200, "data": cached_data},
                runtime.settings,
                connection,
            )
            response.headers["X-Flow-Project-Id"] = payload.project_id
            response.headers["X-Flow-Media-Cache-Hits"] = "1"
            return response
        body = {
            "clientContext": {"projectId": payload.project_id, "tool": "PINHOLE"},
            "fileName": payload.file_name,
            "imageBytes": payload.image_base64,
            "isHidden": False,
            "isUserUploaded": True,
            "mimeType": payload.mime_type,
        }
        result = await _api(client, url=UPLOAD_IMAGE_URL, body=body)
        media_id = extract_upload_media_id(result)
        if media_id:
            runtime.projects.put_media(
                account_key,
                payload.project_id,
                digest,
                media_id,
                payload.mime_type,
                payload.file_name,
                result.get("data") if isinstance(result.get("data"), dict) else None,
            )
    response = _scoped_response(result, runtime.settings, connection)
    response.headers["X-Flow-Project-Id"] = payload.project_id
    response.headers["X-Flow-Media-Cache-Hits"] = "0"
    return response


@router.post("/v1/images/generations", response_model=None)
async def generate_image(
    payload: ImageGenerationRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(
        request, authorization, routing_scope, project_id=payload.project_id,
    )
    managed = payload.project_id is None
    account_key = _account_key(connection)
    force_upload: set[str] = set()
    project_recovered = False
    for attempt in range(3):
        project_id = payload.project_id or await _managed_project(runtime, connection, client)
        reference_media_ids = list(payload.reference_media_ids)
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
        response = _scoped_response(result, runtime.settings, connection)
        response.headers["X-Flow-Project-Id"] = project_id
        response.headers["X-Flow-Media-Cache-Hits"] = str(cache_hits)
        return response
    raise APIError(502, "PROJECT_RECOVERY_FAILED", "Google Flow project recovery failed.", retryable=True)


@router.post("/v1/videos/generations", response_model=None)
async def generate_video(
    payload: VideoGenerationRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(
        request,
        authorization,
        routing_scope,
        min_credits=20,
        project_id=payload.project_id,
    )
    tier = connection.paygate_tier or "PAYGATE_TIER_ONE"
    ctx = client_context(payload.project_id, tier)
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
                "startImage": {"mediaId": payload.start_media_id},
                "metadata": {"sceneId": str(uuid.uuid4())},
            }],
            "useV2ModelConfig": True,
        }
        result = await _api(client, url=VIDEO_I2V_URL, body=body, captcha_action=CAPTCHA_VIDEO)
        _remember_operations(runtime, connection, payload.project_id, result)
        _refresh_paid_account(runtime, connection, result)
        return _scoped_response(result, runtime.settings, connection)
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
                for media_id in payload.reference_media_ids
            ],
        }],
        "useV2ModelConfig": True,
    }
    result = await _api(client, url=VIDEO_OMNI_URL, body=body, captcha_action=CAPTCHA_VIDEO)
    _remember_operations(runtime, connection, payload.project_id, result)
    _refresh_paid_account(runtime, connection, result)
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/videos/status", response_model=None)
async def check_video_operations(
    payload: VideoStatusRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    _authorize(runtime.settings, authorization)
    scoped_installation = (
        _decode_routing_scope(runtime.settings, routing_scope)
        if routing_scope else None
    )
    route_rows = [
        (operation_name, runtime.projects.get_operation(operation_name))
        for operation_name in payload.operation_names
    ]
    unknown = [name for name, route in route_rows if route is None]
    if unknown and not scoped_installation:
        raise APIError(
            409,
            "OPERATION_ROUTE_UNKNOWN",
            f"No stored provider account route exists for {len(unknown)} video operation(s).",
            field="operation_names",
        )

    available = runtime.bridge.ready_connections()
    scoped_connection = None
    if scoped_installation:
        scoped_connection = next(
            (
                item for item in available
                if item.installation_id == scoped_installation
                and runtime.connection_load(item) < item.max_slots
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

    group_results: list[tuple[object, dict]] = []
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
            result = await _api(client, url=VIDEO_POLL_URL, body=body)
            status = result.get("status")
            if not isinstance(status, int) or status >= 400:
                return _response(result)
            group_results.append((connection, result))

    if len(group_results) == 1:
        connection, result = group_results[0]
        return _scoped_response(result, runtime.settings, connection)

    merged: dict = {}
    for _connection_item, result in group_results:
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
    return response
