from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProviderProject:
    installation_id: str
    google_project_id: str
    project_title: str


@dataclass(frozen=True)
class ProviderMedia:
    installation_id: str
    google_project_id: str
    content_sha256: str
    google_media_id: str
    mime_type: str
    file_name: str
    response_data: dict | None
    response_status: int | None
    response_headers: dict | None


@dataclass(frozen=True)
class ProviderOperation:
    operation_name: str
    installation_id: str
    google_project_id: str
    route_kind: str
    poll_name: str


@dataclass(frozen=True)
class ProviderJob:
    job_id: str
    job_type: str
    status: str
    request_payload: dict
    operation_name: str | None = None
    installation_id: str | None = None
    google_project_id: str | None = None
    poll_name: str | None = None
    result_data: dict | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


def _row_to_job(row: sqlite3.Row) -> ProviderJob:
    payload = json.loads(row["request_payload_json"]) if row["request_payload_json"] else {}
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return ProviderJob(
        job_id=row["job_id"],
        job_type=row["job_type"],
        status=row["status"],
        request_payload=payload,
        operation_name=row["operation_name"],
        installation_id=row["installation_id"],
        google_project_id=row["google_project_id"],
        poll_name=row["poll_name"],
        result_data=result,
        error_message=row["error_message"],
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
    )


class ProjectStore:
    """Small durable mapping between a Chrome installation and its Flow project."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                if self.path != ":memory:":
                    Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
                self._connection = sqlite3.connect(self.path, check_same_thread=False)
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA busy_timeout=5000")
                self._connection.execute("PRAGMA journal_mode=WAL")
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_projects (
                        installation_id TEXT PRIMARY KEY,
                        google_project_id TEXT NOT NULL,
                        project_title TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_media (
                        installation_id TEXT NOT NULL,
                        google_project_id TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL,
                        google_media_id TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        file_name TEXT NOT NULL,
                        response_json TEXT,
                        response_status INTEGER,
                        response_headers_json TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (installation_id, google_project_id, content_sha256)
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_project_routes (
                        google_project_id TEXT PRIMARY KEY,
                        installation_id TEXT NOT NULL,
                        project_title TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                media_columns = {
                    row["name"]
                    for row in self._connection.execute("PRAGMA table_info(provider_media)")
                }
                if "response_json" not in media_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_media ADD COLUMN response_json TEXT"
                    )
                if "response_status" not in media_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_media ADD COLUMN response_status INTEGER"
                    )
                if "response_headers_json" not in media_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_media ADD COLUMN response_headers_json TEXT"
                    )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_operations (
                        operation_name TEXT PRIMARY KEY,
                        installation_id TEXT NOT NULL,
                        google_project_id TEXT NOT NULL,
                        route_kind TEXT NOT NULL DEFAULT 'operation',
                        poll_name TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                operation_columns = {
                    row["name"]
                    for row in self._connection.execute("PRAGMA table_info(provider_operations)")
                }
                if "route_kind" not in operation_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_operations ADD COLUMN route_kind TEXT NOT NULL DEFAULT 'operation'"
                    )
                if "poll_name" not in operation_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_operations ADD COLUMN poll_name TEXT"
                    )
                self._connection.execute(
                    "UPDATE provider_operations SET poll_name = operation_name WHERE poll_name IS NULL"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_project_routes_account ON provider_project_routes (installation_id, status)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_account_project ON provider_media (installation_id, google_project_id, status)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_last_used ON provider_media (last_used_at)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_media_google_id ON provider_media (google_media_id, status)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_operations_account_project ON provider_operations (installation_id, google_project_id, status)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_operations_last_used ON provider_operations (last_used_at)"
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_jobs (
                        job_id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        request_payload_json TEXT NOT NULL,
                        operation_name TEXT,
                        installation_id TEXT,
                        google_project_id TEXT,
                        poll_name TEXT,
                        result_json TEXT,
                        error_message TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT
                    )
                    """
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON provider_jobs (status, created_at)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_operation ON provider_jobs (operation_name)"
                )
                self._connection.commit()
            return self._connection

    def get(self, installation_id: str) -> ProviderProject | None:
        with self._lock:
            row = self._db().execute(
                """
                SELECT installation_id, google_project_id, project_title
                FROM provider_projects
                WHERE installation_id = ? AND status = 'active'
                """,
                (installation_id,),
            ).fetchone()
        if row is None:
            return None
        return ProviderProject(row["installation_id"], row["google_project_id"], row["project_title"])

    def installation_for_project(self, google_project_id: str) -> str | None:
        with self._lock:
            row = self._db().execute(
                """
                SELECT installation_id FROM provider_project_routes
                WHERE google_project_id = ? AND status = 'active'
                """,
                (google_project_id,),
            ).fetchone()
        return row["installation_id"] if row is not None else None

    def remember_project(
        self,
        installation_id: str,
        google_project_id: str,
        project_title: str,
    ) -> None:
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_project_routes (
                    google_project_id, installation_id, project_title
                ) VALUES (?, ?, ?)
                ON CONFLICT(google_project_id) DO UPDATE SET
                    installation_id = excluded.installation_id,
                    project_title = excluded.project_title,
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (google_project_id, installation_id, project_title),
            )
            self._db().commit()

    def check(self) -> bool:
        with self._lock:
            return self._db().execute("SELECT 1").fetchone()[0] == 1

    def put(self, installation_id: str, google_project_id: str, project_title: str) -> ProviderProject:
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_projects (installation_id, google_project_id, project_title)
                VALUES (?, ?, ?)
                ON CONFLICT(installation_id) DO UPDATE SET
                    google_project_id = excluded.google_project_id,
                    project_title = excluded.project_title,
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (installation_id, google_project_id, project_title),
            )
            self._db().commit()
        self.remember_project(installation_id, google_project_id, project_title)
        return ProviderProject(installation_id, google_project_id, project_title)

    def touch(self, installation_id: str) -> None:
        with self._lock:
            self._db().execute(
                "UPDATE provider_projects SET last_used_at = CURRENT_TIMESTAMP WHERE installation_id = ?",
                (installation_id,),
            )
            self._db().commit()

    def invalidate(self, installation_id: str) -> None:
        with self._lock:
            row = self._db().execute(
                "SELECT google_project_id FROM provider_projects WHERE installation_id = ?",
                (installation_id,),
            ).fetchone()
            self._db().execute(
                """
                UPDATE provider_projects
                SET status = 'invalid', updated_at = CURRENT_TIMESTAMP
                WHERE installation_id = ?
                """,
                (installation_id,),
            )
            if row is not None:
                self._db().execute(
                    """
                    UPDATE provider_project_routes
                    SET status = 'invalid', updated_at = CURRENT_TIMESTAMP
                    WHERE installation_id = ? AND google_project_id = ?
                    """,
                    (installation_id, row["google_project_id"]),
                )
                self._db().execute(
                    """
                    UPDATE provider_media
                    SET status = 'invalid', updated_at = CURRENT_TIMESTAMP
                    WHERE installation_id = ? AND google_project_id = ?
                    """,
                    (installation_id, row["google_project_id"]),
                )
                self._db().execute(
                    """
                    UPDATE provider_operations
                    SET status = 'invalid', updated_at = CURRENT_TIMESTAMP
                    WHERE installation_id = ? AND google_project_id = ?
                    """,
                    (installation_id, row["google_project_id"]),
                )
            self._db().commit()

    def get_media(
        self,
        installation_id: str,
        google_project_id: str,
        content_sha256: str,
    ) -> ProviderMedia | None:
        with self._lock:
            row = self._db().execute(
                """
                SELECT installation_id, google_project_id, content_sha256,
                       google_media_id, mime_type, file_name, response_json,
                       response_status, response_headers_json
                FROM provider_media
                WHERE installation_id = ? AND google_project_id = ?
                  AND content_sha256 = ? AND status = 'active'
                """,
                (installation_id, google_project_id, content_sha256),
            ).fetchone()
            if row is not None:
                self._db().execute(
                    """
                    UPDATE provider_media SET last_used_at = CURRENT_TIMESTAMP
                    WHERE installation_id = ? AND google_project_id = ? AND content_sha256 = ?
                    """,
                    (installation_id, google_project_id, content_sha256),
                )
                self._db().commit()
        if row is None:
            return None
        return ProviderMedia(
            row["installation_id"], row["google_project_id"], row["content_sha256"],
            row["google_media_id"], row["mime_type"], row["file_name"],
            json.loads(row["response_json"]) if row["response_json"] else None,
            row["response_status"],
            json.loads(row["response_headers_json"]) if row["response_headers_json"] else None,
        )

    def get_media_by_google_id(self, google_media_id: str) -> ProviderMedia | None:
        """Return the active account/project route that owns a Google media ID."""
        with self._lock:
            row = self._db().execute(
                """
                SELECT installation_id, google_project_id, content_sha256,
                       google_media_id, mime_type, file_name, response_json,
                       response_status, response_headers_json
                FROM provider_media
                WHERE google_media_id = ? AND status = 'active'
                ORDER BY last_used_at DESC
                LIMIT 1
                """,
                (google_media_id,),
            ).fetchone()
            if row is not None:
                self._db().execute(
                    """
                    UPDATE provider_media SET last_used_at = CURRENT_TIMESTAMP
                    WHERE installation_id = ? AND google_project_id = ?
                      AND content_sha256 = ?
                    """,
                    (
                        row["installation_id"],
                        row["google_project_id"],
                        row["content_sha256"],
                    ),
                )
                self._db().commit()
        if row is None:
            return None
        return ProviderMedia(
            row["installation_id"], row["google_project_id"], row["content_sha256"],
            row["google_media_id"], row["mime_type"], row["file_name"],
            json.loads(row["response_json"]) if row["response_json"] else None,
            row["response_status"],
            json.loads(row["response_headers_json"])
            if row["response_headers_json"] else None,
        )

    def put_media(
        self,
        installation_id: str,
        google_project_id: str,
        content_sha256: str,
        google_media_id: str,
        mime_type: str,
        file_name: str,
        response_data: dict | None = None,
        response_status: int | None = None,
        response_headers: dict | None = None,
    ) -> ProviderMedia:
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_media (
                    installation_id, google_project_id, content_sha256,
                    google_media_id, mime_type, file_name, response_json,
                    response_status, response_headers_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(installation_id, google_project_id, content_sha256) DO UPDATE SET
                    google_media_id = excluded.google_media_id,
                    mime_type = excluded.mime_type,
                    file_name = excluded.file_name,
                    response_json = COALESCE(excluded.response_json, provider_media.response_json),
                    response_status = COALESCE(excluded.response_status, provider_media.response_status),
                    response_headers_json = COALESCE(
                        excluded.response_headers_json, provider_media.response_headers_json
                    ),
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (
                    installation_id, google_project_id, content_sha256,
                    google_media_id, mime_type, file_name,
                    json.dumps(response_data, separators=(",", ":")) if response_data is not None else None,
                    response_status,
                    json.dumps(response_headers, separators=(",", ":")) if response_headers is not None else None,
                ),
            )
            self._db().commit()
        return ProviderMedia(
            installation_id, google_project_id, content_sha256,
            google_media_id, mime_type, file_name, response_data,
            response_status, response_headers,
        )

    def invalidate_media(
        self,
        installation_id: str,
        google_project_id: str,
        content_sha256: str,
    ) -> None:
        with self._lock:
            self._db().execute(
                """
                UPDATE provider_media
                SET status = 'invalid', updated_at = CURRENT_TIMESTAMP
                WHERE installation_id = ? AND google_project_id = ? AND content_sha256 = ?
                """,
                (installation_id, google_project_id, content_sha256),
            )
            self._db().commit()

    def put_operation(
        self,
        operation_name: str,
        installation_id: str,
        google_project_id: str,
        route_kind: str = "operation",
        poll_name: str | None = None,
    ) -> ProviderOperation:
        poll_name = poll_name or operation_name
        if route_kind not in {"operation", "media"}:
            raise ValueError("invalid operation route kind")
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_operations (
                    operation_name, installation_id, google_project_id, route_kind, poll_name
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(operation_name) DO UPDATE SET
                    installation_id = excluded.installation_id,
                    google_project_id = excluded.google_project_id,
                    route_kind = excluded.route_kind,
                    poll_name = excluded.poll_name,
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP,
                    last_used_at = CURRENT_TIMESTAMP
                """,
                (operation_name, installation_id, google_project_id, route_kind, poll_name),
            )
            self._db().commit()
        return ProviderOperation(operation_name, installation_id, google_project_id, route_kind, poll_name)

    def get_operation(self, operation_name: str) -> ProviderOperation | None:
        with self._lock:
            row = self._db().execute(
                """
                SELECT operation_name, installation_id, google_project_id, route_kind, poll_name
                FROM provider_operations
                WHERE operation_name = ? AND status = 'active'
                """,
                (operation_name,),
            ).fetchone()
            if row is not None:
                self._db().execute(
                    """
                    UPDATE provider_operations SET last_used_at = CURRENT_TIMESTAMP
                    WHERE operation_name = ?
                    """,
                    (operation_name,),
                )
                self._db().commit()
        if row is None:
            return None
        return ProviderOperation(
            row["operation_name"], row["installation_id"], row["google_project_id"],
            row["route_kind"], row["poll_name"],
        )

    def prune(self, *, operation_days: int = 30, media_days: int = 90) -> None:
        """Bound durable cache growth without touching active project ownership."""
        with self._lock:
            self._db().execute(
                """
                DELETE FROM provider_operations
                WHERE status != 'active'
                   OR last_used_at < datetime('now', ?)
                """,
                (f"-{operation_days} days",),
            )
            self._db().execute(
                """
                DELETE FROM provider_media
                WHERE status != 'active'
                   OR last_used_at < datetime('now', ?)
                """,
                (f"-{media_days} days",),
            )
            self._db().commit()

    def enqueue_job(self, job_id: str, job_type: str, request_payload: dict) -> ProviderJob:
        payload_json = json.dumps(request_payload, ensure_ascii=False)
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_jobs (job_id, job_type, status, request_payload_json)
                VALUES (?, ?, 'queued', ?)
                """,
                (job_id, job_type, payload_json),
            )
            self._db().commit()
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError(f"failed to enqueue job {job_id}")
        return job

    def get_job(self, job_id: str) -> ProviderJob | None:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM provider_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def get_job_by_operation(self, operation_name: str) -> ProviderJob | None:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM provider_jobs WHERE operation_name = ? OR poll_name = ? OR job_id = ?",
                (operation_name, operation_name, operation_name),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def claim_next_queued_job(self) -> ProviderJob | None:
        with self._lock:
            row = self._db().execute(
                """
                SELECT * FROM provider_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            return _row_to_job(row)

    def update_job_running(
        self,
        job_id: str,
        *,
        operation_name: str,
        installation_id: str,
        google_project_id: str,
        poll_name: str | None = None,
    ) -> None:
        poll_name = poll_name or operation_name
        with self._lock:
            self._db().execute(
                """
                UPDATE provider_jobs
                SET status = 'running',
                    operation_name = ?,
                    installation_id = ?,
                    google_project_id = ?,
                    poll_name = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (operation_name, installation_id, google_project_id, poll_name, job_id),
            )
            self._db().commit()

    def update_job_completed(self, job_id: str, result_data: dict) -> None:
        result_json = json.dumps(result_data, ensure_ascii=False)
        with self._lock:
            self._db().execute(
                """
                UPDATE provider_jobs
                SET status = 'completed',
                    result_json = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (result_json, job_id),
            )
            self._db().commit()

    def update_job_failed(self, job_id: str, error_message: str) -> None:
        with self._lock:
            self._db().execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (error_message[:1000], job_id),
            )
            self._db().commit()

    def list_running_jobs(self) -> list[ProviderJob]:
        with self._lock:
            rows = self._db().execute(
                "SELECT * FROM provider_jobs WHERE status = 'running' ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
