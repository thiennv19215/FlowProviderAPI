from __future__ import annotations

import mimetypes
import uuid

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse

from app.api.errors import APIError
from app.api.generations import _decode_routing_scope, _image_digest, _job_response
from app.api.schemas import (
    ENTITY_TYPES,
    CharacterCreateRequest,
    CharacterImageGenerationRequest,
    CharacterResponse,
    CharacterUpdateRequest,
    CharacterVideoGenerationRequest,
    JobsResponse,
)

router = APIRouter(prefix="/v1/characters", tags=["Characters"])

def _as_response(character) -> dict:
    return {
        "id": character.character_id,
        "name": character.name,
        "entity_type": character.entity_type,
        "description": character.description,
        "image_prompt": character.image_prompt,
        "voice_description": character.voice_description,
        "image_model": character.image_model,
        "aspect_ratio": character.aspect_ratio,
        "reference_media_ids": character.reference_media_ids,
        "created_at": character.created_at,
        "updated_at": character.updated_at,
    }


def _validate_media(runtime, media_ids: list[str]) -> tuple[list[str], list[str]]:
    asset_hashes: list[str] = []
    resolved_media_ids: list[str] = []
    for media_id in media_ids:
        media = runtime.projects.get_media_by_google_id(media_id)
        if media is None:
            raise APIError(
                409,
                "CHARACTER_MEDIA_UNKNOWN",
                "Every reference_media_id must be an image uploaded through this Provider.",
                field="reference_media_ids",
            )
        if not str(media.mime_type).lower().startswith("image/"):
            raise APIError(422, "CHARACTER_MEDIA_NOT_IMAGE", "Character references must be images.", field="reference_media_ids")
        if (
            runtime.projects.asset_store.path_for(media.content_sha256) is None
            or runtime.projects.get_asset_mime(media.content_sha256) is None
        ):
            raise APIError(
                409,
                "CHARACTER_ASSET_MISSING",
                "The durable source file for this media is unavailable; upload the image again.",
                field="reference_media_ids",
            )
        if media.content_sha256 not in asset_hashes:
            asset_hashes.append(media.content_sha256)
            resolved_media_ids.append(media_id)
    return asset_hashes, resolved_media_ids


def _persist_inline_images(runtime, images) -> list[str]:
    """Persist caller-supplied one-off references in the durable asset store."""
    hashes: list[str] = []
    for image in images:
        image_base64 = image.image_base64
        digest = _image_digest(image_base64)
        try:
            stored_digest, _path, size = runtime.projects.asset_store.put_base64(
                image_base64, image.mime_type,
            )
        except ValueError as exc:
            raise APIError(422, "INVALID_IMAGE", str(exc), field="input_images") from exc
        if stored_digest != digest:
            raise APIError(422, "INVALID_IMAGE", "Image digest could not be verified.", field="input_images")
        runtime.projects.record_asset(digest, image.mime_type, size, image.file_name)
        if digest not in hashes:
            hashes.append(digest)
    runtime.projects.touch_assets(hashes)
    return hashes


def _idempotency_job(runtime, request: Request, payload: dict, *, character_id: str, generation_type: str, media_type: str):
    key = request.headers.get("Idempotency-Key")
    if key is None:
        return None, None
    key = key.strip()
    if not key or len(key) > 200:
        raise APIError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be non-empty and at most 200 characters.")
    existing = runtime.projects.get_job_by_idempotency_key(key)
    if existing:
        # Character references are an enqueue-time snapshot. They must not
        # make a retry with the same idempotency key conflict after the
        # catalog entry is edited; the original job remains authoritative.
        ignored = {"character_id", "reference_asset_hashes", "reference_media_ids"}
        stored = {
            k: v for k, v in existing.request_payload.items()
            if not k.startswith("_") and k not in ignored
        }
        current = {
            k: v for k, v in payload.items()
            if not k.startswith("_") and k not in ignored
        }
        if (
            existing.character_id != character_id
            or existing.media_type != media_type
            or existing.generation_type != generation_type
            or stored != current
        ):
            raise APIError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was already used with a different request payload.")
        return existing, key
    payload["_idempotency_key"] = key
    return None, key


@router.post("", response_model=CharacterResponse, status_code=201)
async def create_character(body: CharacterCreateRequest, request: Request):
    runtime = request.app.state.runtime
    asset_hashes, media_ids = _validate_media(runtime, body.reference_media_ids)
    runtime.projects.touch_assets(asset_hashes)
    character = runtime.projects.create_character(
        name=body.name,
        entity_type=body.entity_type,
        description=body.description,
        image_prompt=body.image_prompt,
        voice_description=body.voice_description,
        image_model=body.image_model,
        aspect_ratio=body.aspect_ratio,
        reference_asset_hashes=asset_hashes,
        reference_media_ids=media_ids,
    )
    return _as_response(character)


@router.get("", response_model=list[CharacterResponse])
async def list_characters(request: Request, entity_type: ENTITY_TYPES | None = None, limit: int = 50, offset: int = 0):
    if not 1 <= limit <= 100 or offset < 0:
        raise APIError(422, "INVALID_PAGINATION", "limit must be 1-100 and offset must be non-negative.")
    runtime = request.app.state.runtime
    return [_as_response(item) for item in runtime.projects.list_characters(entity_type=entity_type, limit=limit, offset=offset)]


@router.get("/{character_id}", response_model=CharacterResponse)
async def get_character(character_id: str, request: Request):
    character = request.app.state.runtime.projects.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    return _as_response(character)


@router.patch("/{character_id}", response_model=CharacterResponse)
async def update_character(character_id: str, body: CharacterUpdateRequest, request: Request):
    runtime = request.app.state.runtime
    updates = body.model_dump(exclude_unset=True)
    for field in ("name", "entity_type", "image_model"):
        if field in updates and updates[field] is None:
            raise APIError(422, "INVALID_VALUE", f"{field} cannot be null.", field=field)
    if "reference_media_ids" in updates and updates["reference_media_ids"] is not None:
        hashes, media_ids = _validate_media(runtime, updates.pop("reference_media_ids"))
        runtime.projects.touch_assets(hashes)
        updates["reference_asset_hashes"] = hashes
        updates["reference_media_ids"] = media_ids
    character = runtime.projects.update_character(character_id, **updates)
    if character is None:
        raise HTTPException(404, "Character not found")
    return _as_response(character)


@router.delete("/{character_id}", status_code=204)
async def delete_character(character_id: str, request: Request):
    if not request.app.state.runtime.projects.soft_delete_character(character_id):
        raise HTTPException(404, "Character not found")


@router.get("/{character_id}/reference-images/{index}")
async def get_reference_image(character_id: str, index: int, request: Request):
    runtime = request.app.state.runtime
    character = runtime.projects.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    if index < 0 or index >= len(character.reference_asset_hashes):
        raise HTTPException(404, "Reference image not found")
    path = runtime.projects.asset_store.path_for(character.reference_asset_hashes[index])
    if path is None:
        raise HTTPException(404, "Reference image is unavailable")
    digest = character.reference_asset_hashes[index]
    media_type = runtime.projects.get_asset_mime(digest)
    if media_type is None:
        stored = runtime.projects.asset_store.read(digest)
        media_type = stored[1] if stored else None
    return FileResponse(path, media_type=media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")


async def _enqueue_character_job(
    character_id: str,
    request: Request,
    body: dict,
    *,
    generation_type: str,
    media_type: str,
    routing_scope: str | None = None,
) -> object:
    runtime = request.app.state.runtime
    character = runtime.projects.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    if not character.reference_asset_hashes:
        raise APIError(409, "CHARACTER_REFERENCE_MISSING", "Character has no reference images.", field="character_id")

    payload = {
        **body,
        "character_id": character_id,
        "reference_asset_hashes": list(character.reference_asset_hashes),
        "reference_media_ids": list(character.reference_media_ids),
    }
    scoped_account_key = _decode_routing_scope(runtime.settings, routing_scope) if routing_scope else None
    project_id = body.get("project_id")
    project_owner = runtime.projects.installation_for_project(project_id) if project_id else None
    if scoped_account_key and project_owner and scoped_account_key != project_owner:
        raise APIError(
            409,
            "PROJECT_ACCOUNT_MISMATCH",
            "The selected Google account does not own this project.",
            field="project_id",
        )
    # Persist a known project owner on the job so the worker cannot dispatch
    # a project-bound Character request through an unrelated account.
    if not scoped_account_key and project_owner:
        scoped_account_key = project_owner
    existing, key = _idempotency_job(
        runtime, request, payload, character_id=character_id,
        generation_type=generation_type, media_type=media_type,
    )
    if existing is not None:
        return _job_response(request, [existing], status_code=202, include_route=True)
    job_id = f"job_{uuid.uuid4().hex}"
    job = runtime.projects.enqueue_job(
        job_id=job_id,
        generation_type=generation_type,
        media_type=media_type,
        request_payload=payload,
        idempotency_key=key,
        character_id=character_id,
        installation_id=scoped_account_key,
        google_project_id=body.get("project_id"),
    )
    return _job_response(request, [job], status_code=202, include_route=True)


@router.post("/{character_id}/images/generations", response_model=JobsResponse, status_code=202)
async def generate_character_image(
    character_id: str,
    body: CharacterImageGenerationRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias="X-Provider-Routing-Scope"),
):
    runtime = request.app.state.runtime
    character = runtime.projects.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    if not character.reference_asset_hashes:
        raise APIError(409, "CHARACTER_REFERENCE_MISSING", "Character has no reference images.", field="character_id")
    payload = body.model_dump(mode="json")
    external_hashes, external_media_ids = _validate_media(runtime, body.reference_media_ids)
    inline_hashes = _persist_inline_images(runtime, body.input_images)
    character_hashes = set(character.reference_asset_hashes)
    # The same asset supplied both through the Character and as an extra
    # reference is sent only once to Flow. Character references remain the
    # first items so their ordering is stable across retries.
    extra_hashes: list[str] = []
    extra_media_ids: list[str] = []
    for digest, media_id in zip(external_hashes, external_media_ids, strict=True):
        if digest not in character_hashes and digest not in extra_hashes:
            extra_hashes.append(digest)
            extra_media_ids.append(media_id)
    for digest in inline_hashes:
        if digest not in character_hashes and digest not in extra_hashes:
            extra_hashes.append(digest)
    if len(character_hashes) + len(extra_hashes) > 8:
        raise APIError(
            422,
            "TOO_MANY_REFERENCES",
            "Character references and extra references may contain at most 8 unique images in total.",
            field="reference_media_ids",
        )
    payload.pop("input_images", None)
    payload.pop("reference_media_ids", None)
    payload["additional_reference_asset_hashes"] = extra_hashes
    payload["additional_reference_media_ids"] = extra_media_ids
    payload["model"] = body.model or character.image_model
    aspect_ratio = body.aspect_ratio or character.aspect_ratio or "9:16"
    payload["aspect_ratio"] = aspect_ratio
    return await _enqueue_character_job(
        character_id, request, payload, generation_type="character_image", media_type="image",
        routing_scope=routing_scope,
    )


@router.post("/{character_id}/videos/generations", response_model=JobsResponse, status_code=202)
async def generate_character_video(
    character_id: str,
    body: CharacterVideoGenerationRequest,
    request: Request,
    routing_scope: str | None = Header(default=None, alias="X-Provider-Routing-Scope"),
):
    character = request.app.state.runtime.projects.get_character(character_id)
    if character is None:
        raise HTTPException(404, "Character not found")
    payload = body.model_dump(mode="json")
    payload["aspect_ratio"] = body.aspect_ratio
    if body.dialogue and character.voice_description:
        payload["prompt"] = (
            f"{body.prompt}\nCharacter voice: {character.name}: {character.voice_description}."
        )
    return await _enqueue_character_job(
        character_id, request, payload, generation_type="character_video", media_type="video",
        routing_scope=routing_scope,
    )
