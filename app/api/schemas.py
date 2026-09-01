from __future__ import annotations

import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    model_config = ConfigDict(extra="forbid")
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)
    model: Literal["NANO_BANANA_PRO", "NANO_BANANA_2"] = "NANO_BANANA_PRO"
    aspect_ratio: Literal["IMAGE_ASPECT_RATIO_SQUARE", "IMAGE_ASPECT_RATIO_LANDSCAPE", "IMAGE_ASPECT_RATIO_PORTRAIT"] = "IMAGE_ASPECT_RATIO_PORTRAIT"
    reference_media_ids: list[str] = Field(default_factory=list, max_length=8)
    input_images: list[InlineImageInput] = Field(default_factory=list, max_length=8)
    variant_count: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def validate_references(self):
        if len(self.reference_media_ids) + len(self.input_images) > 8:
            raise ValueError("reference_media_ids and input_images may contain at most 8 images in total")
        if sum(len(image.image_base64) for image in self.input_images) > MAX_BASE64_TOTAL_CHARS:
            raise ValueError("input_images may contain at most 64 MiB of Base64 data in total")
        return self


class ImageToVideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image_to_video"]
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)
    start_media_id: str | None = Field(default=None, min_length=1, max_length=500)
    input_images: list[InlineImageInput] = Field(default_factory=list, max_length=1)
    aspect_ratio: Literal["VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"] = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    quality: Literal["lite", "fast", "quality", "lite_relaxed", "fast_relaxed"] = "lite"

    @model_validator(mode="after")
    def validate_start_image(self):
        if int(self.start_media_id is not None) + len(self.input_images) != 1:
            raise ValueError("provide exactly one start_media_id or input_images item")
        return self


class OmniVideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["omni"]
    project_id: str | None = Field(default=None, min_length=1, max_length=500)
    prompt: str = Field(min_length=1, max_length=12000)
    reference_media_ids: list[str] = Field(default_factory=list, max_length=8)
    input_images: list[InlineImageInput] = Field(default_factory=list, max_length=8)
    aspect_ratio: Literal["VIDEO_ASPECT_RATIO_LANDSCAPE", "VIDEO_ASPECT_RATIO_PORTRAIT"] = "VIDEO_ASPECT_RATIO_PORTRAIT"
    duration_seconds: Literal[4, 6, 8, 10] = 8

    @property
    def duration_model(self) -> str:
        return {4: "abra_r2v_4s", 6: "abra_r2v_6s", 8: "abra_r2v_8s", 10: "abra_r2v_10s"}[self.duration_seconds]

    @model_validator(mode="after")
    def validate_references(self):
        count = len(self.reference_media_ids) + len(self.input_images)
        if not 1 <= count <= 8:
            raise ValueError("reference_media_ids and input_images must contain 1 to 8 images in total")
        if sum(len(image.image_base64) for image in self.input_images) > MAX_BASE64_TOTAL_CHARS:
            raise ValueError("input_images may contain at most 64 MiB of Base64 data in total")
        return self


VideoGenerationRequest = Annotated[
    ImageToVideoGenerationRequest | OmniVideoGenerationRequest,
    Field(discriminator="type"),
]


class VideoStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_names: list[str] = Field(min_length=1, max_length=20)
