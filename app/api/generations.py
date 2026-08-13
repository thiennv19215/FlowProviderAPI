from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select

from app.api.deps import get_client, get_db
from app.api.errors import APIError
from app.api.schemas import (
    ImageGenerationRequest,
    ImageToVideoRequest,
    JobOutput,
    OmniVideoGenerationRequest,
    UnifiedGenerationRequest,
)
from app.providers.base import provider_capabilities
from app.api.serializers import job_dict
from app.db.models import MediaAsset
from app.jobs.repository import (
    IdempotencyConflict,
    assert_idempotent_submission,
    create_job,
    get_job_by_idempotency,
)

router = APIRouter(tags=["Generations"])

_IMAGE_MODELS = {
    "NANO_BANANA_PRO": "banana_pro",
    "banana_pro": "banana_pro",
    "NANO_BANANA_2": "banana_2",
    "banana_2": "banana_2",
}
_ASPECT_RATIOS = {
    "IMAGE_ASPECT_RATIO_LANDSCAPE": "16:9",
    "IMAGE_ASPECT_RATIO_PORTRAIT": "9:16",
    "IMAGE_ASPECT_RATIO_SQUARE": "1:1",
    "VIDEO_ASPECT_RATIO_LANDSCAPE": "16:9",
    "VIDEO_ASPECT_RATIO_PORTRAIT": "9:16",
    "16:9": "16:9",
    "9:16": "9:16",
    "1:1": "1:1",
}
_VIDEO_QUALITIES = {
    "720p": "lite",
    "1080p": "quality",
    "lite": "lite",
    "fast": "fast",
    "quality": "quality",
    "lite_relaxed": "lite_relaxed",
    "fast_relaxed": "fast_relaxed",
}
_OMNI_DURATIONS = {2, 4, 8, 10}


def _reference_media_ids(data: dict, kind: str) -> list[str]:
    if kind == "video":
        return [str(data["start_media_id"])]
    return [str(media_id) for media_id in data.get("reference_media_ids") or []]


def _validate_reference_assets(request: Request, db, client, data: dict, kind: str) -> None:
    ids = _reference_media_ids(data, kind)
    if not ids:
        return
    rows = list(
        db.scalars(
            select(MediaAsset).where(
                MediaAsset.id.in_(ids),
                MediaAsset.client_id == client.id,
            )
        )
    )
    by_id = {row.id: row for row in rows}
    limit = request.app.state.runtime.settings.max_reference_bytes
    for asset_id in ids:
        asset = by_id.get(asset_id)
        if not asset or asset.status != "done":
            raise APIError(
                422,
                "INVALID_MEDIA_REFERENCE",
                f"Reference media '{asset_id}' is missing or not done.",
                field="reference_media_ids",
            )
        if asset.type != "image" or not asset.mime_type.lower().startswith("image/"):
            raise APIError(
                422,
                "INVALID_MEDIA_TYPE",
                f"Reference media '{asset_id}' must be an image.",
                field="reference_media_ids",
            )
        if asset.size_bytes is not None and asset.size_bytes > limit:
            raise APIError(
                413,
                "REFERENCE_MEDIA_TOO_LARGE",
                f"Reference media '{asset_id}' exceeds the {limit} byte reference limit.",
                field="reference_media_ids",
            )


def _idempotency_conflict() -> APIError:
    return APIError(
        409,
        "IDEMPOTENCY_KEY_CONFLICT",
        "Idempotency-Key was already used for a different generation submission.",
        field="Idempotency-Key",
    )


def _submit(
    request: Request,
    db,
    client,
    payload,
    kind: str,
    *,
    idempotency_key: str | None = None,
):
    data = payload.model_dump(mode="json")
    if kind == "video":
        data["start_media_id"] = str(data["start_media_id"])
    else:
        data["reference_media_ids"] = [
            str(media_id) for media_id in data.get("reference_media_ids") or []
        ]
    provider = payload.provider
    model = getattr(payload, "model", None)
    runtime = request.app.state.runtime
    configured_provider = runtime.providers.get(provider)
    capabilities = provider_capabilities(configured_provider)
    if not capabilities.supports(kind):
        raise APIError(
            400,
            "UNSUPPORTED_PROVIDER_CAPABILITY",
            f"Provider '{provider}' does not support {kind} generation.",
            field="provider",
        )
    clean_key = idempotency_key.strip() if isinstance(idempotency_key, str) else None

    # Lookup first: a retry after an ambiguous network failure must recover the
    # already-created task even if account capacity or reference availability
    # has changed since the original accepted submission.
    if clean_key:
        existing = get_job_by_idempotency(
            db,
            client_id=client.id,
            idempotency_key=clean_key,
        )
        if existing is not None:
            try:
                assert_idempotent_submission(
                    existing,
                    kind=kind,
                    provider=provider,
                    model=model,
                    payload=data,
                )
            except IdempotencyConflict as exc:
                raise _idempotency_conflict() from exc
            return job_dict(runtime, db, existing)

    _validate_reference_assets(request, db, client, data, kind)
    has_online_account = getattr(configured_provider, "has_online_account", None)
    if (
        capabilities.account_pool
        and callable(has_online_account)
        and not has_online_account()
    ):
        raise APIError(
            503,
            "PROVIDER_ACCOUNT_UNAVAILABLE",
            getattr(configured_provider,"unavailable_message",f"No account for provider '{provider}' is currently online."),
            retryable=True,
        )

    try:
        job = create_job(
            db,
            client=client,
            kind=kind,
            provider=provider,
            model=model,
            payload=data,
            request_id=request.state.request_id,
            idempotency_key=clean_key,
        )
    except IdempotencyConflict as exc:
        raise _idempotency_conflict() from exc
    return job_dict(runtime, db, job)


def _unified_payload(payload: UnifiedGenerationRequest):
    """Translate the small orchestrator contract into the provider-native V1 model."""
    options = payload.options or {}
    aspect_value = str(options.get("aspect_ratio") or "")
    aspect_ratio = _ASPECT_RATIOS.get(aspect_value)

    if payload.kind == "image":
        model_value = str(options.get("model") or "")
        output_count = options.get("output_count", options.get("count", 1))
        try:
            output_count = int(output_count)
        except (TypeError, ValueError):
            output_count = 1
        return ImageGenerationRequest(
            provider=payload.provider,
            prompt=payload.prompt,
            model=_IMAGE_MODELS.get(model_value, "banana_pro"),
            aspect_ratio=aspect_ratio or "9:16",
            output_count=max(1, min(output_count, 4)),
            reference_media_ids=payload.media_ids,
        )

    if payload.kind == "video":
        if not payload.media_ids:
            raise APIError(
                422,
                "INVALID_MEDIA_REFERENCE",
                "Video generation requires one start image.",
                field="media_ids",
            )
        quality_value = str(options.get("quality") or "lite").lower()
        return ImageToVideoRequest(
            provider=payload.provider,
            prompt=payload.prompt,
            start_media_id=payload.media_ids[0],
            quality=_VIDEO_QUALITIES.get(quality_value, "lite"),
            aspect_ratio=(
                aspect_ratio if aspect_ratio in {"16:9", "9:16"} else "16:9"
            ),
        )

    if not payload.media_ids:
        raise APIError(
            422,
            "INVALID_MEDIA_REFERENCE",
            "Omni generation requires at least one reference image.",
            field="media_ids",
        )
    duration = options.get("duration", options.get("duration_s", 8))
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 8
    if duration not in _OMNI_DURATIONS:
        duration = 8
    return OmniVideoGenerationRequest(
        provider=payload.provider,
        prompt=payload.prompt,
        reference_media_ids=payload.media_ids,
        duration=duration,
        aspect_ratio=aspect_ratio if aspect_ratio in {"16:9", "9:16"} else "9:16",
    )


@router.post("/v1/generations", status_code=202, response_model=JobOutput)
def create_generation(
    payload: UnifiedGenerationRequest,
    request: Request,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
    ),
    db=Depends(get_db),
    client=Depends(get_client),
):
    provider_payload = _unified_payload(payload)
    return _submit(
        request,
        db,
        client,
        provider_payload,
        payload.kind,
        idempotency_key=idempotency_key,
    )


@router.post("/v1/images/generations", status_code=202, response_model=JobOutput)
def create_image_generation(
    payload: ImageGenerationRequest,
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
):
    return _submit(request, db, client, payload, "image")


@router.post("/v1/videos/image-to-video", status_code=202, response_model=JobOutput)
def create_image_to_video(
    payload: ImageToVideoRequest,
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
):
    return _submit(request, db, client, payload, "video")


@router.post("/v1/videos/omni-generations", status_code=202, response_model=JobOutput)
def create_omni_generation(
    payload: OmniVideoGenerationRequest,
    request: Request,
    db=Depends(get_db),
    client=Depends(get_client),
):
    return _submit(request, db, client, payload, "omni")
