from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr
from pydantic.json_schema import JsonSchemaValue

MediaId = Annotated[StrictStr, Field(pattern=r"^[1-9][0-9]{14}$")]


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


class FlowGenerationRequest(BaseModel):
    provider: str = Field(default="google_flow", exclude=True)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler) -> JsonSchemaValue:
        schema = handler(core_schema)
        properties = schema.get("properties", {})
        properties.pop("provider", None)
        return schema


class UnifiedGenerationRequest(FlowGenerationRequest):
    """Stable backend-to-backend contract used by application orchestrators.

    Provider-specific defaults and validation stay inside FlowProviderAPI.  A
    caller only selects the media job kind, supplies its prompt/localized media
    references, and forwards optional generation settings.
    """

    kind: Literal["image", "video", "omni"]
    prompt: str = Field(min_length=1, max_length=12000)
    media_ids: list[MediaId] = Field(default_factory=list, max_length=8)
    options: dict[str, Any] = Field(default_factory=dict)


class ImageGenerationRequest(FlowGenerationRequest):
    prompt: str = Field(min_length=1, max_length=12000)
    model: Literal["banana_pro", "banana_2"] = "banana_pro"
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "9:16"
    output_count: int = Field(default=1, ge=1, le=4)
    reference_media_ids: list[MediaId] = Field(default_factory=list, max_length=8)


class ImageToVideoRequest(FlowGenerationRequest):
    prompt: str = Field(min_length=1, max_length=12000)
    start_media_id: MediaId
    quality: Literal["lite", "fast", "quality", "lite_relaxed", "fast_relaxed"] = "lite"
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"


class OmniVideoGenerationRequest(FlowGenerationRequest):
    prompt: str = Field(min_length=1, max_length=12000)
    duration: Literal[2, 4, 8, 10] = 8
    aspect_ratio: Literal["16:9", "9:16"] = "9:16"
    reference_media_ids: list[MediaId] = Field(min_length=1, max_length=8)


class MediaOutput(BaseModel):
    media_id: MediaId
    object: Literal["media"] = "media"
    type: str
    status: str = "ready"
    mime_type: str
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    url: str | None = None
    created_at: datetime


class TaskMediaOutput(BaseModel):
    media_id: MediaId
    type: str
    url: str | None = None
    thumbnail_url: str | None = None


class JobOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: str
    status: str
    outputs: list[TaskMediaOutput] = Field(default_factory=list)
    error: ErrorObject | None = None


class JobListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[JobOutput]
    has_more: bool = False
    next_cursor: str | None = None
