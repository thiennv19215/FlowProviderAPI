from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApiClient, GenerationJob, MediaAsset, ProjectMediaMapping


def _upgrade(monkeypatch, database_url: str, revision: str) -> None:
    monkeypatch.setenv("FLOW_PROVIDER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)
    get_settings.cache_clear()


def test_numeric_media_id_migration_rewrites_foreign_keys_and_payloads(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    _upgrade(monkeypatch, database_url, "0005_video_thumbnails")
    engine = create_engine(database_url)
    with Session(engine) as db:
        db.add(ApiClient(id="client_1", name="Test", key_prefix="fpa", key_hash="a" * 64))
        db.add(MediaAsset(
            id="media_legacy", client_id="client_1", status="ready", type="image",
            storage_key="clients/client_1/media_legacy.png", mime_type="image/png",
        ))
        db.add(ProjectMediaMapping(
            id="map_legacy", asset_id="media_legacy", provider="google_flow",
            provider_project_id="project_1", provider_media_id="provider_1",
        ))
        db.add(GenerationJob(
            id="job_legacy", client_id="client_1", kind="image", provider="google_flow",
            workspace_key="workspace", request_payload={"reference_media_ids": ["media_legacy"]},
            result_payload={"asset_ids": ["media_legacy"]},
        ))
        db.commit()
    _upgrade(monkeypatch, database_url, "head")
    with Session(engine) as db:
        asset = db.scalar(select(MediaAsset))
        assert asset is not None
        assert asset.id.isdigit() and len(asset.id) == 15
        assert db.get(MediaAsset, "media_legacy") is None
        mapping = db.scalar(select(ProjectMediaMapping))
        assert mapping.asset_id == asset.id
        job = db.get(GenerationJob, "job_legacy")
        assert job.request_payload["reference_media_ids"] == [asset.id]
        assert job.result_payload["asset_ids"] == [asset.id]
