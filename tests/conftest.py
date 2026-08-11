from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.providers.base import ProviderDispatch, ProviderMedia, ProviderPollResult


class FakeProvider:
    name = "fake"
    requires_account_pool = False

    async def generate_image(self, *, job, db, account_id):
        return [ProviderMedia(bytes_data=b"fake-image-bytes", mime_type="image/png", width=1024, height=1024)]

    async def dispatch_video(self, *, job, db, account_id):
        return ProviderDispatch(operation_ids=["op_video_1"])

    async def dispatch_omni(self, *, job, db, account_id):
        return ProviderDispatch(operation_ids=["op_omni_1"])

    async def poll_video(self, *, job, db, account_id, dispatch):
        return ProviderPollResult(done=True, outputs=[ProviderMedia(bytes_data=b"fake-video-bytes", mime_type="video/mp4", duration=8.0)])


@pytest.fixture
def app(tmp_path: Path):
    settings = Settings(
        env="test",
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        bootstrap_api_key="fpa_test_key",
        public_base_url="http://testserver",
        worker_enabled=False,
        video_poll_seconds=0,
        local_storage_path=tmp_path / "assets",
    )
    return create_app(settings, extra_providers=[FakeProvider()])


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth():
    return {"Authorization": "Bearer fpa_test_key"}
