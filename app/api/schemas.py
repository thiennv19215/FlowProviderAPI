from __future__ import annotations

import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_BASE64_TOTAL_CHARS = 64 * 1024 * 1024


class ErrorDetail(BaseModel):
    field: str | None = None
    code: str
    message: str


class ErrorObject(BaseModel):
    status_code: int
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorObject


def _safe_https_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must be an absolute HTTPS URL")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return value
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        raise ValueError("URL host must not be a private, loopback, link-local, or reserved address")
    return value


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


class ImageUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    excluded_project_ids: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    required_credits: int = Field(default=0, ge=0, le=1000)
    file_name: str = Field(default="upload.png", min_length=1, max_length=255)
    mime_type: str = Field(min_length=3, max_length=120, pattern=r"^image/")
    image_base64: str = Field(min_length=1, max_length=MAX_BASE64_TOTAL_CHARS)


class InlineImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=1, max_length=MAX_BASE64_TOTAL_CHARS)
    mime_type: str = Field(default="image/png", min_length=3, max_length=120, pattern=r"^image/")
    file_name: str = Field(default="reference.png", min_length=1, max_length=255)


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)
    model: Literal["pro", "v2", "NANO_BANANA_PRO", "NANO_BANANA_2"] = Field(
        default="pro", json_schema_extra={"enum": ["pro", "v2"]},
    )
    aspect_ratio: Literal[
        "1:1", "16:9", "9:16",
        "IMAGE_ASPECT_RATIO_SQUARE",
        "IMAGE_ASPECT_RATIO_LANDSCAPE",
        "IMAGE_ASPECT_RATIO_PORTRAIT",
    ] = Field(default="9:16", json_schema_extra={"enum": ["1:1", "16:9", "9:16"]})
    reference_media_ids: list[str] = Field(default_factory=list, max_length=8)
    input_images: list[InlineImageInput] = Field(default_factory=list, max_length=8)
    variant_count: int = Field(default=1, ge=1, le=4)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str) -> str:
        return {"pro": "NANO_BANANA_PRO", "v2": "NANO_BANANA_2"}.get(value, value)

    @field_validator("aspect_ratio")
    @classmethod
    def normalize_aspect_ratio(cls, value: str) -> str:
        return {
            "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
            "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE",
            "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT",
        }.get(value, value)

    @model_validator(mode="after")
    def validate_references(self):
        if len(self.reference_media_ids) + len(self.input_images) > 8:
            raise ValueError("reference_media_ids and input_images may contain at most 8 images in total")
        if sum(len(image.image_base64) for image in self.input_images) > MAX_BASE64_TOTAL_CHARS:
            raise ValueError("input_images may contain at most 64 MiB of Base64 data in total")
        return self


ENTITY_TYPES = Literal[
    "character", "location", "creature", "visual_asset", "generic_troop", "faction",
]


class CharacterCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    entity_type: ENTITY_TYPES = "character"
    description: str | None = Field(default=None, max_length=5000)
    image_prompt: str | None = Field(default=None, max_length=12000)
    voice_description: str | None = Field(default=None, max_length=200)
    image_model: Literal["pro", "v2"] = "pro"
    aspect_ratio: Literal["1:1", "16:9", "9:16"] | None = None
    reference_media_ids: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=3,
    )


class CharacterUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    entity_type: ENTITY_TYPES | None = None
    description: str | None = Field(default=None, max_length=5000)
    image_prompt: str | None = Field(default=None, max_length=12000)
    voice_description: str | None = Field(default=None, max_length=200)
    image_model: Literal["pro", "v2"] | None = None
    aspect_ratio: Literal["1:1", "16:9", "9:16"] | None = None
    reference_media_ids: list[Annotated[str, Field(min_length=1, max_length=500)]] | None = Field(
        default=None, max_length=3,
    )


class CharacterResponse(BaseModel):
    id: str
    name: str
    entity_type: ENTITY_TYPES
    description: str | None = None
    image_prompt: str | None = None
    voice_description: str | None = None
    image_model: Literal["pro", "v2"] = "pro"
    aspect_ratio: Literal["1:1", "16:9", "9:16"] | None = None
    reference_media_ids: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None


class CharacterImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    prompt: str = Field(min_length=1, max_length=12000)
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    model: Literal["pro", "v2"] | None = None
    aspect_ratio: Literal["1:1", "16:9", "9:16"] | None = None
    variant_count: int = Field(default=1, ge=1, le=4)
    # Character references are added by the Provider automatically. These
    # optional inputs let a caller add scene/style references for this one
    # generation without changing the Character catalog.
    reference_media_ids: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=8,
    )
    input_images: list[InlineImageInput] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_references(self):
        if len(self.reference_media_ids) + len(self.input_images) > 8:
            raise ValueError("reference_media_ids and input_images may contain at most 8 images in total")
        if sum(len(image.image_base64) for image in self.input_images) > MAX_BASE64_TOTAL_CHARS:
            raise ValueError("input_images may contain at most 64 MiB of Base64 data in total")
        return self


class CharacterVideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    prompt: str = Field(min_length=1, max_length=12000)
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    aspect_ratio: Literal["16:9", "9:16"] = "9:16"
    duration_seconds: Literal[4, 6, 8, 10] = 8
    dialogue: bool = False


class ImageToVideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    type: Literal["frames_to_video", "frames", "start_to_video", "image_to_video", "i2v", "omni_i2v"]
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)
    start_media_id: str | None = Field(default=None, min_length=1, max_length=500)
    end_media_id: str | None = Field(default=None, min_length=1, max_length=500)
    input_images: list[InlineImageInput] = Field(min_length=1, max_length=2)
    aspect_ratio: Literal[
        "16:9", "9:16", "VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"
    ] = Field(default="9:16", json_schema_extra={"enum": ["16:9", "9:16"]})
    duration_seconds: Literal[4, 6, 8, 10] = 8
    quality: Literal["lite", "fast", "quality", "lite_relaxed", "fast_relaxed"] | None = None

    @field_validator("aspect_ratio")
    @classmethod
    def normalize_aspect_ratio(cls, value: str) -> str:
        return {
            "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
            "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
        }.get(value, value)

    @property
    def duration_model(self) -> str:
        return {4: "abra_i2v_4s", 6: "abra_i2v_6s", 8: "abra_i2v_8s", 10: "abra_i2v_10s"}[self.duration_seconds]

    @model_validator(mode="after")
    def validate_start_image(self):
        if self.type == "image_to_video" and "aspect_ratio" not in self.model_fields_set:
            self.aspect_ratio = "VIDEO_ASPECT_RATIO_LANDSCAPE"
        if self.type not in {"image_to_video", "start_to_video", "frames_to_video", "frames"} and self.quality is not None:
            raise ValueError("quality is only valid for legacy image_to_video")
        if not self.input_images:
            raise ValueError("input_images (Base64 encoded image) is required for video generation to support multi-account load balancing")
        if self.start_media_id is not None:
            raise ValueError("start_media_id is not allowed; provide Base64 input_images for automatic multi-account balancing")
        if self.end_media_id is not None and len(self.input_images) > 1:
            raise ValueError("provide at most one inline start image when end_media_id is set")
        if sum(len(image.image_base64) for image in self.input_images) > MAX_BASE64_TOTAL_CHARS:
            raise ValueError("input_images may contain at most 64 MiB of Base64 data in total")
        return self


I2VGenerationRequest = ImageToVideoGenerationRequest


class OmniVideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)
    type: Literal["reference_to_video", "ingredients", "references", "omni", "r2v", "omni_r2v"]
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)
    reference_media_ids: list[str] = Field(default_factory=list, max_length=8)
    input_images: list[InlineImageInput] = Field(min_length=1, max_length=8)
    aspect_ratio: Literal[
        "16:9", "9:16", "VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"
    ] = Field(default="9:16", json_schema_extra={"enum": ["16:9", "9:16"]})
    duration_seconds: Literal[4, 6, 8, 10] = 8

    @field_validator("aspect_ratio")
    @classmethod
    def normalize_aspect_ratio(cls, value: str) -> str:
        return {
            "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
            "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
        }.get(value, value)

    @property
    def duration_model(self) -> str:
        return {4: "abra_r2v_4s", 6: "abra_r2v_6s", 8: "abra_r2v_8s", 10: "abra_r2v_10s"}[self.duration_seconds]

    @model_validator(mode="after")
    def validate_references(self):
        if not self.input_images or len(self.input_images) < 1:
            raise ValueError("input_images (Base64 encoded images) is required for reference_to_video generation")
        if self.reference_media_ids:
            raise ValueError("reference_media_ids is not allowed; provide Base64 input_images for automatic multi-account balancing")
        if not 1 <= len(self.input_images) <= 8:
            raise ValueError("input_images must contain 1 to 8 images")
        if sum(len(image.image_base64) for image in self.input_images) > MAX_BASE64_TOTAL_CHARS:
            raise ValueError("input_images may contain at most 64 MiB of Base64 data in total")
        return self


R2VGenerationRequest = OmniVideoGenerationRequest


VideoGenerationRequest = Annotated[
    ImageToVideoGenerationRequest | OmniVideoGenerationRequest,
    Field(discriminator="type"),
]


class JobStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list,
        max_length=20,
    )
    operation_names: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_status_ids(self):
        if not self.job_ids and not self.operation_names:
            raise ValueError("Either job_ids or operation_names must be provided")
        return self


class GeneratedMedia(BaseModel):
    id: str
    type: Literal["image", "video"]
    url: str | None = None
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: int | None = None


class JobError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    outcome_unknown: bool = False


class Job(BaseModel):
    id: str
    provider: str = "google_flow"
    type: Literal["image", "video"]
    generation_type: Literal[
        "image", "frames_to_video", "reference_to_video", "character_image", "character_video",
    ] | str = "image"
    status: Literal["queued", "running", "complete", "failed"]
    media: list[GeneratedMedia] = Field(default_factory=list)
    error: JobError | None = None


class JobMetadata(BaseModel):
    request_id: str | None = None
    project_id: str | None = None
    routing_scope: str | None = None
    poll_after_seconds: int | None = None
    counts: dict[Literal["queued", "running", "complete", "failed"], int] = Field(
        default_factory=dict,
    )
    done: bool = False


class JobsResponse(BaseModel):
    jobs: list[Job]
    metadata: JobMetadata


# Backward-compatible name used by the existing /v1/videos/status contract.
VideoStatusRequest = JobStatusRequest
VideoJobError = JobError
VideoJob = Job
VideoJobMetadata = JobMetadata
VideoJobsResponse = JobsResponse
