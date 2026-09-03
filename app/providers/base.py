from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.api.schemas import (
    CreateProjectRequest,
    ImageGenerationRequest,
    ImageUploadRequest,
    JobsResponse,
    VideoGenerationRequest,
)


class BaseProvider(ABC):
    """Abstract base provider interface for AI generation services."""

    provider_name: str = "base"

    @abstractmethod
    async def create_project(self, request: CreateProjectRequest, **kwargs: Any) -> dict[str, Any]:
        """Create a workspace/project on the upstream provider."""
        pass

    @abstractmethod
    async def upload_media(self, request: ImageUploadRequest, **kwargs: Any) -> dict[str, Any]:
        """Upload source image/media to the upstream provider."""
        pass

    @abstractmethod
    async def generate_image(self, request: ImageGenerationRequest, **kwargs: Any) -> JobsResponse:
        """Create an image generation job or jobs."""
        pass

    @abstractmethod
    async def generate_video(self, request: VideoGenerationRequest, **kwargs: Any) -> JobsResponse:
        """Create an async video generation job."""
        pass

    @abstractmethod
    async def get_jobs_status(self, job_ids: list[str], **kwargs: Any) -> JobsResponse:
        """Query the status of one or more jobs."""
        pass
