from __future__ import annotations

import base64
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
from app.providers.google_flow.sdk.helpers import client_context, resolve_image_model, resolve_video_model

router = APIRouter(tags=["Google Flow"])
ROUTING_SCOPE_HEADER = "X-Provider-Routing-Scope"
ROUTING_SCOPE_VERSION = "v1"


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


def _connection(request: Request, authorization: str | None, routing_scope: str | None = None):
    runtime = request.app.state.runtime
    _authorize(runtime.settings, authorization)
    available = [
        item
        for item in runtime.bridge.ready_connections()
        if runtime.bridge.pending_count(item.id) < item.max_slots
    ]
    if routing_scope:
        installation_id = _decode_routing_scope(runtime.settings, routing_scope)
        connection = next(
            (item for item in available if getattr(item, "installation_id", None) == installation_id),
            None,
        )
        if connection is None:
            raise APIError(
                503,
                "ROUTING_SCOPE_UNAVAILABLE",
                "The Google Flow account bound to this routing scope is not currently available.",
                retryable=True,
            )
        return connection, BoundFlowClient(runtime.bridge, connection.id)
    if not available:
        raise APIError(
            503,
            "PROVIDER_ACCOUNT_UNAVAILABLE",
            "No Google Flow extension is currently available.",
            retryable=True,
        )
    connection = min(available, key=lambda item: runtime.bridge.pending_count(item.id))
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
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/media", response_model=None)
async def upload_image(
    payload: ImageUploadRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, authorization, routing_scope)
    body = {
        "clientContext": {"projectId": payload.project_id, "tool": "PINHOLE"},
        "fileName": payload.file_name,
        "imageBytes": payload.image_base64,
        "isHidden": False,
        "isUserUploaded": True,
        "mimeType": payload.mime_type,
    }
    result = await _api(client, url=UPLOAD_IMAGE_URL, body=body)
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/images/generations", response_model=None)
async def generate_image(
    payload: ImageGenerationRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, authorization, routing_scope)
    ctx = client_context(payload.project_id, connection.paygate_tier or "PAYGATE_TIER_ONE")
    batch_id = str(uuid.uuid4())
    requests = []
    for _ in range(payload.variant_count):
        item = {
            "clientContext": ctx,
            "structuredPrompt": {"parts": [{"text": payload.prompt}]},
            "imageAspectRatio": payload.aspect_ratio,
            "imageModelName": resolve_image_model(payload.model),
        }
        if payload.reference_media_ids:
            item["imageInputs"] = [
                {"name": media_id, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"}
                for media_id in payload.reference_media_ids
            ]
        requests.append(item)
    body = {
        "clientContext": ctx,
        "mediaGenerationContext": {"batchId": batch_id},
        "useNewMedia": True,
        "requests": requests,
    }
    result = await _api(
        client,
        url=f"{FLOW_API_BASE}/v1/projects/{payload.project_id}/flowMedia:batchGenerateImages",
        body=body,
        captcha_action=CAPTCHA_IMAGE,
    )
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/videos/generations", response_model=None)
async def generate_video(
    payload: VideoGenerationRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, authorization, routing_scope)
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
    return _scoped_response(result, runtime.settings, connection)


@router.post("/v1/videos/status", response_model=None)
async def check_video_operations(
    payload: VideoStatusRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    routing_scope: str | None = Header(default=None, alias=ROUTING_SCOPE_HEADER),
) -> Response:
    runtime = request.app.state.runtime
    connection, client = _connection(request, authorization, routing_scope)
    body = {"operations": [{"operation": {"name": operation_name}} for operation_name in payload.operation_names]}
    result = await _api(client, url=VIDEO_POLL_URL, body=body)
    return _scoped_response(result, runtime.settings, connection)
