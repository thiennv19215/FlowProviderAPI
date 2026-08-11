from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceRef(BaseModel):
    key: str = Field(min_length=1, max_length=255)


class AssetRef(BaseModel):
    asset_id: str = Field(min_length=8, max_length=80)


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    provider: str = "google_flow"
    model: str | None = None
    aspect_ratio: Literal["1:1", "16:9", "9:16"] = "16:9"
    output_count: int = Field(default=1, ge=1, le=4)
    references: list[AssetRef] = Field(default_factory=list, max_length=8)
    workspace: WorkspaceRef


class VideoInput(BaseModel):
    start_asset_id: str


class VideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    provider: str = "google_flow"
    model: str | None = None
    quality: Literal["lite", "fast", "quality", "lite_relaxed", "fast_relaxed"] = "lite"
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    input: VideoInput
    workspace: WorkspaceRef


class OmniVideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=12000)
    provider: str = "google_flow"
    model: str | None = None
    duration: Literal[2, 4, 8, 10] = 8
    aspect_ratio: Literal["16:9", "9:16"] = "9:16"
    references: list[AssetRef] = Field(min_length=1, max_length=8)
    workspace: WorkspaceRef


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


class JobOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    object: Literal["generation_job"] = "generation_job"
    type: str
    provider: str
    model: str | None = None
    status: str
    stage: str
    workspace_key: str
    outputs: list[AssetOutput] = Field(default_factory=list)
    error: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ListResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[dict]
    has_more: bool = False
    next_cursor: str | None = None
