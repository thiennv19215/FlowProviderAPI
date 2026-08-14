import sqlite3
from pathlib import Path
from uuid import uuid4

from app.projects import ProjectStore


def test_operation_route_schema_migrates_existing_database():
    path = Path(f".test-run-project-store-migration-{uuid4().hex}.db")
    try:
        connection = sqlite3.connect(path)
        connection.execute(
            """
            CREATE TABLE provider_operations (
                operation_name TEXT PRIMARY KEY,
                installation_id TEXT NOT NULL,
                google_project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE provider_media (
                installation_id TEXT NOT NULL,
                google_project_id TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                google_media_id TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                file_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (installation_id, google_project_id, content_sha256)
            )
            """
        )
        connection.execute(
            "INSERT INTO provider_operations (operation_name, installation_id, google_project_id) VALUES (?, ?, ?)",
            ("operations/legacy", "installation-1", "projects/one"),
        )
        connection.commit()
        connection.close()

        store = ProjectStore(str(path))
        route = store.get_operation("operations/legacy")
        assert route is not None
        assert route.route_kind == "operation"
        assert route.poll_name == "operations/legacy"
        store.put_media(
            "installation-1", "projects/one", "sha", "media/one", "image/png", "one.png",
            {"media": {"name": "media/one"}}, 201, {"x-upload-id": "one"},
        )
        media = store.get_media("installation-1", "projects/one", "sha")
        assert media is not None
        assert media.response_status == 201
        assert media.response_headers == {"x-upload-id": "one"}
        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()


def test_project_mapping_survives_store_reopen():
    path = Path(f".test-run-project-store-{uuid4().hex}.db")
    try:
        first = ProjectStore(str(path))
        first.put("installation-1", "projects/one", "FlowProvider")
        first.close()

        second = ProjectStore(str(path))
        assert second.check() is True
        project = second.get("installation-1")
        assert project is not None
        assert project.google_project_id == "projects/one"
        assert second.installation_for_project("projects/one") == "installation-1"
        second.put_media(
            "installation-1", "projects/one", "sha-one", "media/one", "image/png", "one.png"
        )
        cached = second.get_media("installation-1", "projects/one", "sha-one")
        assert cached is not None
        assert cached.google_media_id == "media/one"
        assert second.get_media("installation-2", "projects/one", "sha-one") is None
        assert second.get_media("installation-1", "projects/two", "sha-one") is None
        second.invalidate_media("installation-1", "projects/one", "sha-one")
        assert second.get_media("installation-1", "projects/one", "sha-one") is None
        second.put_operation(
            "workflows/one", "installation-1", "projects/one", "media", "media/primary"
        )
        operation = second.get_operation("workflows/one")
        assert operation is not None
        assert operation.google_project_id == "projects/one"
        assert operation.route_kind == "media"
        assert operation.poll_name == "media/primary"
        second.invalidate("installation-1")
        assert second.get_operation("workflows/one") is None
        assert second.get("installation-1") is None
        second.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{path}{suffix}")
            if candidate.exists():
                candidate.unlink()
