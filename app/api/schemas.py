from __future__ import annotations

import ipaddress
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class CallerOwnedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_key: str = Field(min_length=1, max_length=255)
    checksum_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    mime_type: str = Field(min_length=3, max_length=120)
    size_bytes: int = Field(ge=1, le=2 * 1024 * 1024 * 1024)
    download_url: str = Field(min_length=12, max_length=4096)

    @field_validator("download_url")
    @classmethod
    def safe_download_url(cls, value: str) -> str:
        return _safe_https_url(value)


class CallerOwnedOutputDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_index: int = Field(ge=0, le=7)
    upload_url: str = Field(min_length=12, max_length=4096)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("upload_url")
    @classmethod
    def safe_upload_url(cls, value: str) -> str:
        return _safe_https_url(value)

    @field_validator("headers")
    @classmethod
    def safe_headers(cls, value: dict[str, str]) -> dict[str, str]:
        normalized = {str(key).lower(): str(item) for key, item in value.items()}
        if set(normalized) - {"content-type"}:
            raise ValueError("only the content-type destination header is supported")
        return normalized


class UnifiedGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["image", "video", "omni"]
    prompt: str = Field(min_length=1, max_length=12000)
    storage_mode: Literal["caller_owned"]
    model: str | None = Field(default=None, min_length=1, max_length=120)
    inputs: list[CallerOwnedInput] = Field(default_factory=list, max_length=8)
    output_destinations: list[CallerOwnedOutputDestination] = Field(min_length=1, max_length=8)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_shape(self):
        indexes = [item.output_index for item in self.output_destinations]
        if sorted(indexes) != list(range(len(indexes))):
            raise ValueError("output_destinations must use contiguous output_index values from zero")
        if self.kind in {"video", "omni"} and not self.inputs:
            raise ValueError(f"caller_owned {self.kind} requests require an input")
        return self


class TaskMediaOutput(BaseModel):
    output_index: int
    type: Literal["image", "video"]
    mime_type: str
    size_bytes: int
    checksum_sha256: str
    uploaded: Literal[True]


class JobOutput(BaseModel):
    task_id: str
    status: Literal["done"]
    outputs: list[TaskMediaOutput] = Field(default_factory=list)
    error: ErrorObject | None = None
