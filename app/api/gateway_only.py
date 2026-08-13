"""Stateless caller-owned Google Flow gateway.

This route deliberately has no SQLAlchemy dependency and never instantiates an
asset storage adapter.  It is intended for FlowCanvas, which persists request
state and issues narrowly scoped signed object URLs.
"""
from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import time
import uuid
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Header, Request

from app.api.errors import APIError
from app.api.schemas import JobOutput, TaskMediaOutput, UnifiedGenerationRequest
from app.providers.google_flow.client import BoundFlowClient
from app.providers.google_flow.sdk import FlowSDK

router = APIRouter(tags=["Gateway"])
IMAGE_ASPECT = {"1:1": "IMAGE_ASPECT_RATIO_SQUARE", "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE", "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT"}
VIDEO_ASPECT = {"16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE", "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT"}
PUBLIC_IMAGE_MODELS = {"banana_pro": "NANO_BANANA_PRO", "banana_2": "NANO_BANANA_2"}
_GOOGLE_OUTPUT_SUFFIXES = (".googleusercontent.com", ".google.com", "storage.googleapis.com")


def _authorize(settings, authorization: str | None) -> None:
    expected = settings.bootstrap_api_key
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not expected:
        raise APIError(503, "GATEWAY_AUTH_UNAVAILABLE", "Gateway API key is not configured.")
    if not hmac.compare_digest(expected, supplied):
        raise APIError(401, "INVALID_API_KEY", "A valid Provider API key is required.")


def _caller_hosts(settings) -> set[str]:
    return {host.strip().lower() for host in settings.caller_owned_allowed_hosts.split(",") if host.strip()}


def _require_caller_url(url: str, allowed: set[str]) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed:
        raise APIError(422, "CALLER_STORAGE_HOST_FORBIDDEN", "A caller storage URL host is not allowlisted.")


def _google_output_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "storage.googleapis.com" or host.endswith(_GOOGLE_OUTPUT_SUFFIXES))


async def _read_caller_input(descriptor, allowed: set[str], limit: int) -> bytes:
    url = descriptor.download_url
    for _ in range(6):
        _require_caller_url(url, allowed)
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise APIError(502, "CALLER_INPUT_REDIRECT_INVALID", "Caller input redirect was invalid.")
                    url = str(httpx.URL(url).join(location))
                    continue
                if response.is_error:
                    raise APIError(502, "CALLER_INPUT_DOWNLOAD_FAILED", "Provider could not download caller input.", retryable=True)
                declared = response.headers.get("content-length")
                if declared and int(declared) > limit:
                    raise APIError(413, "CALLER_INPUT_TOO_LARGE", "Caller input exceeds the gateway size limit.")
                data = bytearray()
                async for chunk in response.aiter_bytes(1024 * 1024):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise APIError(413, "CALLER_INPUT_TOO_LARGE", "Caller input exceeds the gateway size limit.")
                value = bytes(data)
                break
    else:
        raise APIError(502, "CALLER_INPUT_TOO_MANY_REDIRECTS", "Caller input redirected too many times.")
    if descriptor.checksum_sha256 and hashlib.sha256(value).hexdigest().lower() != descriptor.checksum_sha256.lower():
        raise APIError(422, "CALLER_INPUT_CHECKSUM_MISMATCH", "Caller input checksum did not match.")
    return value


async def _read_google_output(url: str, limit: int) -> tuple[bytes, str]:
    for _ in range(6):
        if not _google_output_url(url):
            raise APIError(502, "GOOGLE_OUTPUT_URL_FORBIDDEN", "Google Flow returned an untrusted output URL.")
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise APIError(502, "GOOGLE_OUTPUT_REDIRECT_INVALID", "Google Flow output redirect was invalid.")
                    url = str(httpx.URL(url).join(location))
                    continue
                if response.is_error:
                    raise APIError(502, "GOOGLE_OUTPUT_DOWNLOAD_FAILED", "Provider could not fetch Google Flow output.", retryable=True)
                data = bytearray()
                async for chunk in response.aiter_bytes(1024 * 1024):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise APIError(413, "GOOGLE_OUTPUT_TOO_LARGE", "Google Flow output exceeds the gateway size limit.")
                return bytes(data), response.headers.get("content-type", "image/png").split(";", 1)[0]
    raise APIError(502, "GOOGLE_OUTPUT_TOO_MANY_REDIRECTS", "Google Flow output redirected too many times.")


async def _put_output(destination, data: bytes, mime_type: str, allowed: set[str]) -> None:
    _require_caller_url(destination.upload_url, allowed)
    headers = {key: value for key, value in destination.headers.items()}
    headers.setdefault("content-type", mime_type)
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=15), follow_redirects=False) as client:
        response = await client.put(destination.upload_url, content=data, headers=headers)
    if response.is_error:
        raise APIError(502, "CALLER_OUTPUT_UPLOAD_FAILED", "Provider could not upload to caller storage.", retryable=True)


def _validate_request(payload: UnifiedGenerationRequest) -> None:
    if payload.kind == "image":
        if len(payload.output_destinations) > 4:
            raise APIError(422, "OUTPUT_COUNT_UNSUPPORTED", "Google Flow image generation supports at most four outputs.")
        return
    if len(payload.output_destinations) != 1:
        raise APIError(422, "OUTPUT_COUNT_UNSUPPORTED", "Video generation requires exactly one output destination.")
    if payload.kind == "video" and len(payload.inputs) != 1:
        raise APIError(422, "VIDEO_INPUT_COUNT_INVALID", "Image-to-video requires exactly one input image.")


def _task_id(idempotency_key: str) -> str:
    # FlowCanvas owns the durable idempotency record. A deterministic gateway
    # identifier lets it reconcile a lost HTTP response without gateway state.
    return f"gw_{uuid.uuid5(uuid.NAMESPACE_URL, 'flowprovider:' + idempotency_key).hex}"


async def _upload_references(payload, sdk, project_id: str, allowed: set[str], limit: int) -> list[str]:
    references = []
    for index, item in enumerate(payload.inputs):
        if not item.mime_type.startswith("image/"):
            raise APIError(422, "CALLER_INPUT_NOT_IMAGE", "Google Flow generation requires image inputs.")
        data = await _read_caller_input(item, allowed, limit)
        uploaded = await sdk.upload_image(
            base64.b64encode(data).decode("ascii"), item.mime_type, project_id, f"reference-{index + 1}.png"
        )
        if not uploaded.get("media_id"):
            raise APIError(502, "FLOW_REFERENCE_UPLOAD_FAILED", "Google Flow could not accept caller input.", retryable=True)
        references.append(uploaded["media_id"])
    return references


async def _poll_video(sdk, dispatch: dict, project_id: str, settings) -> list[dict]:
    deadline = time.monotonic() + settings.max_provider_operation_seconds
    operation_names = dispatch.get("operation_names") or []
    if not operation_names:
        raise APIError(502, "GOOGLE_FLOW_DISPATCH_FAILED", "Google Flow did not return a video operation.", retryable=True)
    while time.monotonic() < deadline:
        result = await sdk.check_async(
            operation_names=operation_names,
            project_id=project_id,
            workflows_data=dispatch.get("workflows") or [],
        )
        if result.get("error"):
            raise APIError(502, "GOOGLE_FLOW_POLL_FAILED", "Google Flow video status was unavailable.", retryable=True)
        operations = result.get("operations") or []
        failure = next((item.get("error") for item in operations if item.get("error")), None)
        if failure:
            raise APIError(502, "GOOGLE_FLOW_GENERATION_FAILED", "Google Flow video generation failed.")
        if operations and all(item.get("done") for item in operations):
            return [entry for item in operations for entry in item.get("media_entries") or []]
        await asyncio.sleep(max(0, settings.video_poll_seconds))
    raise APIError(504, "GOOGLE_FLOW_OPERATION_TIMEOUT", "Google Flow video generation did not finish in time.", retryable=True)


async def _generate_entries(payload, sdk, project_id: str, references: list[str], connection, settings) -> list[dict]:
    options = payload.options or {}
    if payload.kind == "image":
        model = payload.model or options.get("model") or "banana_pro"
        if model not in PUBLIC_IMAGE_MODELS:
            raise APIError(422, "INVALID_IMAGE_MODEL", "Unsupported Google Flow image model.")
        ratio = options.get("aspect_ratio") or "9:16"
        if ratio not in IMAGE_ASPECT:
            raise APIError(422, "INVALID_ASPECT_RATIO", "Unsupported image aspect ratio.")
        result = await sdk.gen_image(
            prompt=payload.prompt, project_id=project_id,
            paygate_tier=connection.paygate_tier or "PAYGATE_TIER_ONE",
            aspect_ratio=IMAGE_ASPECT[ratio], ref_media_ids=references,
            variant_count=len(payload.output_destinations), image_model=PUBLIC_IMAGE_MODELS[model],
        )
        if result.get("error"):
            raise APIError(502, "GOOGLE_FLOW_GENERATION_FAILED", "Google Flow did not complete generation.", retryable=True)
        return result.get("media_entries") or []

    ratio = options.get("aspect_ratio") or ("16:9" if payload.kind == "video" else "9:16")
    if ratio not in VIDEO_ASPECT:
        raise APIError(422, "INVALID_ASPECT_RATIO", "Unsupported video aspect ratio.")
    if payload.kind == "video":
        quality = options.get("quality") or "lite"
        if quality not in {"lite", "fast", "quality", "lite_relaxed", "fast_relaxed"}:
            raise APIError(422, "INVALID_VIDEO_QUALITY", "Unsupported Google Flow video quality.")
        dispatch = await sdk.gen_video(
            prompt=payload.prompt, project_id=project_id, start_media_id=references[0],
            aspect_ratio=VIDEO_ASPECT[ratio], paygate_tier=connection.paygate_tier or "PAYGATE_TIER_ONE",
            video_quality=quality,
        )
    else:
        duration = options.get("duration", 8)
        if duration not in {2, 4, 8, 10}:
            raise APIError(422, "INVALID_VIDEO_DURATION", "Unsupported Omni video duration.")
        dispatch = await sdk.gen_video_omni(
            prompt=payload.prompt, project_id=project_id, ref_media_ids=references,
            duration_s=duration, aspect_ratio=VIDEO_ASPECT[ratio],
            paygate_tier=connection.paygate_tier or "PAYGATE_TIER_ONE",
        )
    if dispatch.get("error"):
        raise APIError(502, "GOOGLE_FLOW_GENERATION_FAILED", "Google Flow did not accept video generation.", retryable=True)
    return await _poll_video(sdk, dispatch, project_id, settings)


@router.post("/v1/gateway/generations", response_model=JobOutput)
async def generate(
    payload: UnifiedGenerationRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JobOutput:
    runtime = request.app.state.runtime
    settings = runtime.settings
    _authorize(settings, authorization)
    if not idempotency_key or not 1 <= len(idempotency_key) <= 255:
        raise APIError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required and must not exceed 255 characters.")
    if payload.storage_mode != "caller_owned":
        raise APIError(422, "CALLER_OWNED_REQUIRED", "Gateway-only requests must use caller-owned storage.")
    _validate_request(payload)
    allowed = _caller_hosts(settings)
    if not allowed:
        raise APIError(503, "CALLER_STORAGE_POLICY_MISSING", "Caller storage hostname policy is not configured.")
    for item in payload.inputs:
        _require_caller_url(item.download_url, allowed)
    for item in payload.output_destinations:
        _require_caller_url(item.upload_url, allowed)
    connections = [item for item in runtime.bridge.ready_connections()
        if runtime.bridge.pending_count(item.id) < item.max_slots]
    if not connections:
        raise APIError(503, "PROVIDER_ACCOUNT_UNAVAILABLE", "No Google Flow account is currently online.", retryable=True)
    connection = min(connections, key=lambda item: runtime.bridge.pending_count(item.id))
    sdk = FlowSDK(BoundFlowClient(runtime.bridge, connection.id))
    created = await sdk.create_project("FlowCanvas gateway")
    project_id = created.get("project_id")
    if not project_id:
        raise APIError(502, "FLOW_PROJECT_CREATE_FAILED", "Google Flow did not create a project.", retryable=True)
    references = await _upload_references(payload, sdk, project_id, allowed, settings.max_reference_in_memory_bytes)
    entries = await _generate_entries(payload, sdk, project_id, references, connection, settings)
    if len(entries) != len(payload.output_destinations):
        raise APIError(502, "GOOGLE_FLOW_OUTPUT_COUNT_MISMATCH", "Google Flow returned an unexpected output count.", retryable=True)
    outputs = []
    for destination, entry in zip(payload.output_destinations, entries, strict=True):
        data = entry.get("bytes_data")
        mime_type = "image/png" if payload.kind == "image" else "video/mp4"
        if not data:
            url = entry.get("url")
            if not isinstance(url, str):
                raise APIError(502, "GOOGLE_FLOW_OUTPUT_MISSING", "Google Flow did not return an output.", retryable=True)
            data, mime_type = await _read_google_output(url, settings.max_provider_output_bytes)
        await _put_output(destination, data, mime_type, allowed)
        outputs.append(TaskMediaOutput(output_index=destination.output_index, type="image" if payload.kind == "image" else "video", mime_type=mime_type, size_bytes=len(data), checksum_sha256=hashlib.sha256(data).hexdigest(), uploaded=True))
    connection.success_count += 1
    return JobOutput(task_id=_task_id(idempotency_key), status="done", outputs=outputs)
