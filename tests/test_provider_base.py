from __future__ import annotations

import pytest
from app.api.schemas import (
    CreateProjectRequest,
    GeneratedMedia,
    ImageGenerationRequest,
    ImageUploadRequest,
    Job,
    JobMetadata,
    JobsResponse,
    VideoGenerationRequest,
)
from app.projects import ProjectStore
from app.providers.base import BaseProvider


class DummyProvider(BaseProvider):
    provider_name: str = "dummy"

    async def create_project(self, request: CreateProjectRequest, **kwargs):
        return {"project_id": "dummy_proj"}

    async def upload_media(self, request: ImageUploadRequest, **kwargs):
        return {"media_id": "dummy_media"}

    async def generate_image(self, request: ImageGenerationRequest, **kwargs) -> JobsResponse:
        return JobsResponse(
            jobs=[
                Job(
                    id="job_123",
                    provider="dummy",
                    type="image",
                    status="complete",
                    media=[GeneratedMedia(id="m1", type="image", url="https://example.com/img.png")],
                )
            ],
            metadata=JobMetadata(done=True),
        )

    async def generate_video(self, request: VideoGenerationRequest, **kwargs) -> JobsResponse:
        return JobsResponse(
            jobs=[Job(id="job_vid", provider="dummy", type="video", status="queued")],
            metadata=JobMetadata(done=False),
        )

    async def get_jobs_status(self, job_ids: list[str], **kwargs) -> JobsResponse:
        return JobsResponse(
            jobs=[Job(id=jid, provider="dummy", type="video", status="complete") for jid in job_ids],
            metadata=JobMetadata(done=True),
        )


@pytest.mark.asyncio
async def test_base_provider_interface():
    provider = DummyProvider()
    assert provider.provider_name == "dummy"

    res = await provider.generate_image(ImageGenerationRequest(prompt="test prompt"))
    assert len(res.jobs) == 1
    assert res.jobs[0].id == "job_123"
    assert res.jobs[0].provider == "dummy"
    assert res.jobs[0].status == "complete"
    assert res.jobs[0].media[0].url == "https://example.com/img.png"


def test_project_store_provider_support():
    from uuid import uuid4
    from pathlib import Path
    path = Path(f".test-run-provider-base-{uuid4().hex}.db")
    try:
        store = ProjectStore(str(path))
        job = store.enqueue_job(
            "job-flow-1",
            "image",
            {"prompt": "hello"},
            provider="google_flow",
        )
        assert job.job_id == "job-flow-1"
        assert job.provider == "google_flow"

        fetched = store.get_job("job-flow-1")
        assert fetched is not None
        assert fetched.provider == "google_flow"
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
