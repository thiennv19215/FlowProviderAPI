from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.json_schema import JsonSchemaValue


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
    provider: str = Field(default="google_flow",exclude=True)

    @classmethod
    def __get_pydantic_json_schema__(cls,core_schema,handler)->JsonSchemaValue:
        schema=handler(core_schema);properties=schema.get("properties",{})
        properties.pop("provider",None)
        return schema


class ImageGenerationRequest(FlowGenerationRequest):
    prompt: str = Field(min_length=1, max_length=12000)
    model: Literal["banana_pro", "banana_2"] = "banana_pro"
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "9:16"
    output_count: int = Field(default=1, ge=1, le=4)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_references(cls,data):
        if isinstance(data,dict) and "reference_asset_ids" not in data and isinstance(data.get("references"),list):
            data={**data,"reference_asset_ids":[item.get("asset_id") for item in data["references"] if isinstance(item,dict)]}
        return data


class ImageToVideoRequest(FlowGenerationRequest):
    prompt: str = Field(min_length=1, max_length=12000)
    start_asset_id: str = Field(min_length=8,max_length=80)
    quality: Literal["lite", "fast", "quality", "lite_relaxed", "fast_relaxed"] = "lite"
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_input(cls,data):
        if isinstance(data,dict) and "start_asset_id" not in data and isinstance(data.get("input"),dict):
            data={**data,"start_asset_id":data["input"].get("start_asset_id")}
        return data


class OmniVideoGenerationRequest(FlowGenerationRequest):
    prompt: str = Field(min_length=1, max_length=12000)
    duration: Literal[2, 4, 8, 10] = 8
    aspect_ratio: Literal["16:9", "9:16"] = "9:16"
    reference_asset_ids: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_references(cls,data):
        if isinstance(data,dict) and "reference_asset_ids" not in data and isinstance(data.get("references"),list):
            data={**data,"reference_asset_ids":[item.get("asset_id") for item in data["references"] if isinstance(item,dict)]}
        return data


class AssetUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=120)
    size_bytes: int | None = Field(default=None, ge=0)
    type: Literal["image", "video"] = "image"


class AssetOutput(BaseModel):
    id: str
    object: Literal["asset"] = "asset"
    type: str
    status: str = "ready"
    mime_type: str
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    content_url: str | None = None
    created_at: datetime


class TaskAssetOutput(BaseModel):
    asset_id: str
    type: str
    url: str | None = None


class JobOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    task_id: str
    status: str
    outputs: list[TaskAssetOutput] = Field(default_factory=list)
    error: ErrorObject | None = None


class JobListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[JobOutput]
    has_more: bool = False
    next_cursor: str | None = None


class AssetUploadDescriptor(BaseModel):
    method: Literal["PUT"] = "PUT"
    url: str
    headers: dict[str, str]
    expires_in: int | None = None


class AssetUploadResponse(BaseModel):
    asset: AssetOutput
    upload: AssetUploadDescriptor
