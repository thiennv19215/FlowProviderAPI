from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Header, Request, Response

from app.api.errors import APIError
from app.api.schemas import (
    CreateProjectRequest, ImageGenerationRequest, ImageUploadRequest,
    ImageToVideoGenerationRequest, OmniVideoGenerationRequest, VideoGenerationRequest,
    VideoStatusRequest,
)
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk.constants import (
    API_HEADERS, CAPTCHA_IMAGE, CAPTCHA_VIDEO, FLOW_API_BASE, TRPC_CREATE_PROJECT,
    TRPC_HEADERS, UPLOAD_IMAGE_URL, VIDEO_I2V_URL, VIDEO_OMNI_URL, VIDEO_POLL_URL,
)
from app.providers.google_flow.sdk.helpers import client_context, resolve_image_model, resolve_video_model

router = APIRouter(tags=["Google Flow"])


def _authorize(settings, authorization: str | None) -> None:
    expected = settings.bootstrap_api_key
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected:
        raise APIError(503, "API_AUTH_UNAVAILABLE", "Provider API key is not configured.")
    if not hmac.compare_digest(expected, supplied):
        raise APIError(401, "INVALID_API_KEY", "A valid Provider API key is required.")


def _connection(request: Request, authorization: str | None):
    runtime = request.app.state.runtime
    _authorize(runtime.settings, authorization)
    available = [item for item in runtime.bridge.ready_connections() if runtime.bridge.pending_count(item.id) < item.max_slots]
    if not available:
        raise APIError(503, "PROVIDER_ACCOUNT_UNAVAILABLE", "No Google Flow extension is currently available.", retryable=True)
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


async def _api(client, *, url: str, body: dict, captcha_action: str | None = None) -> Response:
    return _response(await client.api_request(url=url, method="POST", headers=API_HEADERS, body=body, captcha_action=captcha_action))


@router.post("/v1/projects", response_model=None)
async def create_project(payload: CreateProjectRequest, request: Request, authorization: str | None = Header(default=None)) -> Response:
    _, client = _connection(request, authorization)
    result = await client.trpc_request(url=TRPC_CREATE_PROJECT, method="POST", headers=TRPC_HEADERS, body={"json": {"projectTitle": payload.title, "toolName": "PINHOLE"}})
    return _response(result)


@router.post("/v1/media", response_model=None)
async def upload_image(payload: ImageUploadRequest, request: Request, authorization: str | None = Header(default=None)) -> Response:
    _, client = _connection(request, authorization)
    body = {"clientContext": {"projectId": payload.project_id, "tool": "PINHOLE"}, "fileName": payload.file_name, "imageBytes": payload.image_base64, "isHidden": False, "isUserUploaded": True, "mimeType": payload.mime_type}
    return await _api(client, url=UPLOAD_IMAGE_URL, body=body)


@router.post("/v1/images/generations", response_model=None)
async def generate_image(payload: ImageGenerationRequest, request: Request, authorization: str | None = Header(default=None)) -> Response:
    connection, client = _connection(request, authorization)
    ctx = client_context(payload.project_id, connection.paygate_tier or "PAYGATE_TIER_ONE")
    batch_id = str(uuid.uuid4())
    requests = []
    for _ in range(payload.variant_count):
        item = {"clientContext": ctx, "structuredPrompt": {"parts": [{"text": payload.prompt}]}, "imageAspectRatio": payload.aspect_ratio, "imageModelName": resolve_image_model(payload.model)}
        if payload.reference_media_ids:
            item["imageInputs"] = [{"name": media_id, "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE"} for media_id in payload.reference_media_ids]
        requests.append(item)
    body = {"clientContext": ctx, "mediaGenerationContext": {"batchId": batch_id}, "useNewMedia": True, "requests": requests}
    return await _api(client, url=f"{FLOW_API_BASE}/v1/projects/{payload.project_id}/flowMedia:batchGenerateImages", body=body, captcha_action=CAPTCHA_IMAGE)


@router.post("/v1/videos/generations", response_model=None)
async def generate_video(payload: VideoGenerationRequest, request: Request, authorization: str | None = Header(default=None)) -> Response:
    connection, client = _connection(request, authorization)
    tier = connection.paygate_tier or "PAYGATE_TIER_ONE"
    ctx = client_context(payload.project_id, tier)
    if isinstance(payload, ImageToVideoGenerationRequest):
        model = resolve_video_model(tier, payload.aspect_ratio, payload.quality)
        if not model:
            raise APIError(422, "INVALID_VIDEO_QUALITY", "Unsupported video quality for this account.")
        body = {"clientContext": ctx, "mediaGenerationContext": {"batchId": str(uuid.uuid4())}, "requests": [{"aspectRatio": payload.aspect_ratio, "textInput": {"prompt": payload.prompt}, "videoModelKey": model, "startImage": {"mediaId": payload.start_media_id}, "metadata": {"sceneId": str(uuid.uuid4())}}], "useV2ModelConfig": True}
        return await _api(client, url=VIDEO_I2V_URL, body=body, captcha_action=CAPTCHA_VIDEO)
    body = {"mediaGenerationContext": {"batchId": str(uuid.uuid4()), "audioFailurePreference": "BLOCK_SILENCED_VIDEOS"}, "clientContext": ctx, "requests": [{"aspectRatio": payload.aspect_ratio, "textInput": {"prompt": payload.prompt}, "videoModelKey": payload.duration_model, "metadata": {}, "referenceImages": [{"mediaId": media_id, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"} for media_id in payload.reference_media_ids]}], "useV2ModelConfig": True}
    return await _api(client, url=VIDEO_OMNI_URL, body=body, captcha_action=CAPTCHA_VIDEO)


@router.post("/v1/videos/status", response_model=None)
async def check_video_operations(
    payload: VideoStatusRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    _, client = _connection(request, authorization)
    body = {"operations": [{"operation": {"name": operation_name}} for operation_name in payload.operation_names]}
    return await _api(client, url=VIDEO_POLL_URL, body=body)
