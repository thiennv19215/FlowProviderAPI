from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ApiClient, GenerationJob, WorkspaceProject


def _upgrade(monkeypatch, database_url: str, revision: str) -> None:
    monkeypatch.setenv("FLOW_PROVIDER_DATABASE_URL", database_url)
    get_settings.cache_clear()
    command.upgrade(Config("alembic.ini"), revision)
    get_settings.cache_clear()


def test_workspace_key_migration_preserves_jobs_and_deduplicates_projects(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'workspace-migration.db'}"
    _upgrade(monkeypatch, database_url, "0006_numeric_media_ids")
    engine = create_engine(database_url)
    with Session(engine) as db:
        db.add(ApiClient(id="client_1", name="Test", key_prefix="fpa", key_hash="a" * 64))
        db.commit()

    metadata = MetaData()
    jobs = Table("generation_jobs", metadata, autoload_with=engine)
    projects = Table("workspace_projects", metadata, autoload_with=engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(projects.insert(), [
            {
                "id": "wsp_keep",
                "client_id": "client_1",
                "workspace_key": "legacy-a",
                "provider": "google_flow",
                "provider_account_id": "account_1",
                "provider_project_id": "project_keep",
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": "wsp_drop",
                "client_id": "client_1",
                "workspace_key": "legacy-b",
                "provider": "google_flow",
                "provider_account_id": "account_1",
                "provider_project_id": "project_duplicate",
                "created_at": now + timedelta(seconds=1),
                "updated_at": now + timedelta(seconds=1),
            },
        ])
        connection.execute(jobs.insert().values(
            id="job_legacy",
            client_id="client_1",
            kind="image",
            provider="google_flow",
            model=None,
            workspace_key="legacy-a",
            status="queued",
            stage="queued",
            priority=20,
            request_payload={"prompt": "cat"},
            result_payload=None,
            idempotency_key=None,
            provider_account_id=None,
            provider_project_id=None,
            provider_operation_id=None,
            next_run_at=now,
            lease_owner=None,
            lease_expires_at=None,
            attempt_count=0,
            cancel_requested=False,
            error_code=None,
            error_message=None,
            created_at=now,
            started_at=None,
            completed_at=None,
            updated_at=now,
        ))

    _upgrade(monkeypatch, database_url, "head")

    inspector = inspect(engine)
    assert "workspace_key" not in {column["name"] for column in inspector.get_columns("generation_jobs")}
    assert "workspace_key" not in {column["name"] for column in inspector.get_columns("workspace_projects")}
    with Session(engine) as db:
        job = db.get(GenerationJob, "job_legacy")
        assert job is not None
        projects_after = list(db.scalars(select(WorkspaceProject)))
        assert len(projects_after) == 1
        assert projects_after[0].id == "wsp_keep"
        assert projects_after[0].provider_project_id == "project_keep"
