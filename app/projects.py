from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.assets import AssetStore

# A dispatch claim is held only during one worker attempt. The lease is longer
# than the bridge's maximum paid-request timeout. An expired claim is failed as
# outcome-unknown for reconciliation; it is never automatically paid again.
JOB_CLAIM_LEASE_SECONDS = 15 * 60


@dataclass(frozen=True)
class ProviderProject:
    installation_id: str
    google_project_id: str
    project_title: str
    provider: str = "google_flow"


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
    provider: str = "google_flow"


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
    media_type: str
    generation_type: str
    status: str
    request_payload: dict
    provider: str = "google_flow"
    operation_name: str | None = None
    installation_id: str | None = None
    google_project_id: str | None = None
    poll_name: str | None = None
    result_data: dict | None = None
    error_message: str | None = None
    error_code: str | None = None
    error_retryable: bool = False
    outcome_unknown: bool = False
    running_at: str | None = None
    next_poll_at: str | None = None
    last_poll_at: str | None = None
    last_poll_error: str | None = None
    poll_attempts: int = 0
    poll_error_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    claim_token: str | None = None
    idempotency_key: str | None = None
    character_id: str | None = None


@dataclass(frozen=True)
class ProviderCharacter:
    character_id: str
    name: str
    entity_type: str
    description: str | None
    image_prompt: str | None
    voice_description: str | None
    image_model: str
    aspect_ratio: str | None
    reference_asset_hashes: list[str]
    reference_media_ids: list[str]
    deleted_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


def _row_to_job(row: sqlite3.Row) -> ProviderJob:
    columns = set(row.keys())
    payload = json.loads(row["request_payload_json"]) if row["request_payload_json"] else {}
    result = json.loads(row["result_json"]) if row["result_json"] else None
    return ProviderJob(
        job_id=row["job_id"],
        provider=(
            row["provider"]
            if "provider" in columns and row["provider"]
            else "google_flow"
        ),
        media_type=(
            row["media_type"]
            if "media_type" in columns and row["media_type"]
            else ("image" if row["generation_type"] in {"image", "character_image"} else "video")
        ),
        generation_type=row["generation_type"],
        status=row["status"],
        request_payload=payload,
        operation_name=row["operation_name"],
        installation_id=row["installation_id"],
        google_project_id=row["google_project_id"],
        poll_name=row["poll_name"],
        result_data=result,
        error_message=row["error_message"],
        error_code=row["error_code"] if "error_code" in columns else None,
        error_retryable=bool(row["error_retryable"]) if "error_retryable" in columns else False,
        outcome_unknown=bool(row["outcome_unknown"]) if "outcome_unknown" in columns else False,
        running_at=row["running_at"] if "running_at" in columns else None,
        next_poll_at=row["next_poll_at"] if "next_poll_at" in columns else None,
        last_poll_at=row["last_poll_at"] if "last_poll_at" in columns else None,
        last_poll_error=row["last_poll_error"] if "last_poll_error" in columns else None,
        poll_attempts=int(row["poll_attempts"] or 0) if "poll_attempts" in columns else 0,
        poll_error_count=int(row["poll_error_count"] or 0) if "poll_error_count" in columns else 0,
        created_at=str(row["created_at"]) if row["created_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
        completed_at=str(row["completed_at"]) if row["completed_at"] else None,
        claim_token=row["claim_token"] if "claim_token" in columns else None,
        idempotency_key=row["idempotency_key"] if "idempotency_key" in columns else None,
        character_id=row["character_id"] if "character_id" in columns else None,
    )


class ProjectStore:
    """Small durable mapping between a Chrome installation and its Flow project."""

    def __init__(self, path: str, *, asset_store_path: str | None = None):
        self.path = path
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self.asset_store = AssetStore(asset_store_path or ".data/assets")

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
                if "provider" not in media_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_media ADD COLUMN provider TEXT NOT NULL DEFAULT 'google_flow'"
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
                        media_type TEXT NOT NULL DEFAULT 'video',
                        generation_type TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'queued',
                        request_payload_json TEXT NOT NULL,
                        claimed_at TEXT,
                        claim_token TEXT,
                        operation_name TEXT,
                        installation_id TEXT,
                        google_project_id TEXT,
                        poll_name TEXT,
                        result_json TEXT,
                        error_message TEXT,
                        error_code TEXT,
                        error_retryable INTEGER NOT NULL DEFAULT 0,
                        outcome_unknown INTEGER NOT NULL DEFAULT 0,
                        idempotency_key TEXT,
                        character_id TEXT,
                        running_at TEXT,
                        next_poll_at TEXT,
                        last_poll_at TEXT,
                        last_poll_error TEXT,
                        poll_attempts INTEGER NOT NULL DEFAULT 0,
                        poll_error_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT
                    )
                    """
                )
                job_columns = {
                    row["name"]
                    for row in self._connection.execute("PRAGMA table_info(provider_jobs)")
                }
                if "generation_type" not in job_columns and "job_type" in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs RENAME COLUMN job_type TO generation_type"
                    )
                    job_columns.remove("job_type")
                    job_columns.add("generation_type")
                if "claimed_at" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN claimed_at TEXT"
                    )
                if "media_type" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN media_type TEXT NOT NULL DEFAULT 'video'"
                    )
                    self._connection.execute(
                        "UPDATE provider_jobs SET media_type = 'image' WHERE generation_type = 'image'"
                    )
                if "claim_token" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN claim_token TEXT"
                    )
                if "idempotency_key" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN idempotency_key TEXT"
                    )
                if "error_code" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN error_code TEXT"
                    )
                if "error_retryable" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN error_retryable INTEGER NOT NULL DEFAULT 0"
                    )
                if "outcome_unknown" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN outcome_unknown INTEGER NOT NULL DEFAULT 0"
                    )
                if "running_at" not in job_columns:
                    self._connection.execute("ALTER TABLE provider_jobs ADD COLUMN running_at TEXT")
                if "next_poll_at" not in job_columns:
                    self._connection.execute("ALTER TABLE provider_jobs ADD COLUMN next_poll_at TEXT")
                if "last_poll_at" not in job_columns:
                    self._connection.execute("ALTER TABLE provider_jobs ADD COLUMN last_poll_at TEXT")
                if "last_poll_error" not in job_columns:
                    self._connection.execute("ALTER TABLE provider_jobs ADD COLUMN last_poll_error TEXT")
                if "poll_attempts" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN poll_attempts INTEGER NOT NULL DEFAULT 0"
                    )
                if "poll_error_count" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN poll_error_count INTEGER NOT NULL DEFAULT 0"
                    )
                if "provider" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN provider TEXT NOT NULL DEFAULT 'google_flow'"
                    )
                if "character_id" not in job_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_jobs ADD COLUMN character_id TEXT"
                    )
                self._connection.execute(
                    """
                    UPDATE provider_jobs
                    SET outcome_unknown = 1,
                        error_code = COALESCE(error_code, 'VIDEO_DISPATCH_OUTCOME_UNKNOWN')
                    WHERE status = 'failed'
                      AND outcome_unknown = 0
                      AND lower(COALESCE(error_message, '')) LIKE '%outcome is unknown%'
                    """
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_status ON provider_jobs (status, created_at)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_poll_due ON provider_jobs (status, next_poll_at)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_operation ON provider_jobs (operation_name)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_character_status ON provider_jobs (character_id, status, created_at)"
                )
                self._connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency_key "
                    "ON provider_jobs (idempotency_key) WHERE idempotency_key IS NOT NULL"
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_assets (
                        content_sha256 TEXT PRIMARY KEY,
                        mime_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL,
                        file_name TEXT,
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        last_referenced_at TEXT,
                        orphaned_at TEXT
                    )
                    """
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_assets_reference ON provider_assets (status, last_referenced_at)"
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS provider_characters (
                        character_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        entity_type TEXT NOT NULL DEFAULT 'character',
                        description TEXT,
                        image_prompt TEXT,
                        voice_description TEXT,
                        image_model TEXT NOT NULL DEFAULT 'pro',
                        aspect_ratio TEXT,
                        reference_asset_hashes_json TEXT NOT NULL DEFAULT '[]',
                        reference_media_ids_json TEXT NOT NULL DEFAULT '[]',
                        deleted_at TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_characters_active ON provider_characters (deleted_at, created_at)"
                )
                character_columns = {
                    row["name"]
                    for row in self._connection.execute("PRAGMA table_info(provider_characters)")
                }
                if "image_model" not in character_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_characters ADD COLUMN image_model TEXT NOT NULL DEFAULT 'pro'"
                    )
                if "aspect_ratio" not in character_columns:
                    self._connection.execute(
                        "ALTER TABLE provider_characters ADD COLUMN aspect_ratio TEXT"
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

    def prune(self, *, operation_days: int = 30, media_days: int = 90, asset_retention_days: int = 30) -> None:
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
            # Keep source bytes while any live Character or job still references them.
            rows = self._db().execute(
                "SELECT content_sha256, orphaned_at FROM provider_assets WHERE status = 'active'"
            ).fetchall()
            referenced: set[str] = set()
            for row in self._db().execute(
                "SELECT reference_asset_hashes_json, deleted_at FROM provider_characters"
            ).fetchall():
                try:
                    values = json.loads(row["reference_asset_hashes_json"] or "[]")
                    hashes = [str(value) for value in values if isinstance(value, str)]
                    if not row["deleted_at"]:
                        referenced.update(hashes)
                except (TypeError, ValueError):
                    continue
            for row in self._db().execute(
                "SELECT request_payload_json FROM provider_jobs WHERE status IN ('queued', 'dispatching', 'running')"
            ).fetchall():
                try:
                    payload = json.loads(row["request_payload_json"] or "{}")
                    for key in (
                        "reference_asset_hashes",
                        "additional_reference_asset_hashes",
                        "input_image_hashes",
                    ):
                        values = payload.get(key, [])
                        referenced.update(str(value) for value in values if isinstance(value, str))
                except (TypeError, ValueError):
                    continue
            for row in rows:
                digest = row["content_sha256"]
                if digest in referenced:
                    self._db().execute(
                        "UPDATE provider_assets SET last_referenced_at = CURRENT_TIMESTAMP, orphaned_at = NULL WHERE content_sha256 = ?",
                        (digest,),
                    )
                elif row["orphaned_at"] is None:
                    self._db().execute(
                        "UPDATE provider_assets SET orphaned_at = CURRENT_TIMESTAMP WHERE content_sha256 = ?",
                        (digest,),
                    )
            old_assets = self._db().execute(
                "SELECT content_sha256 FROM provider_assets WHERE status = 'active' AND orphaned_at IS NOT NULL AND orphaned_at <= datetime('now', ?)",
                (f"-{max(1, asset_retention_days)} days",),
            ).fetchall()
            for row in old_assets:
                self.asset_store.delete(row["content_sha256"])
                self._db().execute(
                    "UPDATE provider_assets SET status = 'deleted', updated_at = CURRENT_TIMESTAMP WHERE content_sha256 = ?",
                    (row["content_sha256"],),
                )
            self._db().commit()

    def enqueue_job(
        self,
        job_id: str,
        generation_type: str | None = None,
        request_payload: dict | None = None,
        *,
        job_type: str | None = None,
        provider: str = "google_flow",
        media_type: str | None = None,
        installation_id: str | None = None,
        google_project_id: str | None = None,
        idempotency_key: str | None = None,
        character_id: str | None = None,
    ) -> ProviderJob:
        gen_type = generation_type or job_type or "image"
        payload = request_payload or {}
        media_type = media_type or ("image" if gen_type in {"image", "character_image"} else "video")
        if media_type not in {"image", "video"}:
            raise ValueError("media_type must be 'image' or 'video'")
        clean_payload = {k: v for k, v in payload.items() if k != "input_images"}
        payload_json = json.dumps(clean_payload, ensure_ascii=False)
        with self._lock:
            try:
                self._db().execute(
                    """
                    INSERT INTO provider_jobs (
                        job_id, provider, media_type, generation_type, status, request_payload_json,
                        installation_id, google_project_id, idempotency_key, character_id
                    )
                    VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        provider,
                        media_type,
                        gen_type,
                        payload_json,
                        installation_id,
                        google_project_id,
                        idempotency_key,
                        character_id,
                    ),
                )
                self._db().commit()
            except sqlite3.IntegrityError:
                self._db().rollback()
                if idempotency_key:
                    existing = self.get_job_by_idempotency_key(idempotency_key)
                    if existing is not None:
                        return existing
                raise
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError(f"failed to enqueue job {job_id}")
        return job

    def get_job_by_idempotency_key(self, idempotency_key: str) -> ProviderJob | None:
        with self._lock:
            row = self._db().execute(
                "SELECT * FROM provider_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    @staticmethod
    def _row_to_character(row: sqlite3.Row) -> ProviderCharacter:
        columns = set(row.keys())

        def decode(name: str) -> list[str]:
            try:
                value = json.loads(row[name] or "[]")
            except (TypeError, ValueError):
                value = []
            return [str(item) for item in value] if isinstance(value, list) else []

        return ProviderCharacter(
            character_id=row["character_id"],
            name=row["name"],
            entity_type=row["entity_type"],
            description=row["description"],
            image_prompt=row["image_prompt"],
            voice_description=row["voice_description"],
            image_model=row["image_model"] if "image_model" in columns else "pro",
            aspect_ratio=row["aspect_ratio"] if "aspect_ratio" in columns else None,
            reference_asset_hashes=decode("reference_asset_hashes_json"),
            reference_media_ids=decode("reference_media_ids_json"),
            deleted_at=row["deleted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_asset(self, digest: str, mime_type: str, size_bytes: int, file_name: str) -> None:
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_assets (content_sha256, mime_type, size_bytes, file_name, last_referenced_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(content_sha256) DO UPDATE SET
                    mime_type = excluded.mime_type,
                    size_bytes = excluded.size_bytes,
                    file_name = excluded.file_name,
                    status = 'active',
                    last_referenced_at = CURRENT_TIMESTAMP,
                    orphaned_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (digest, mime_type, size_bytes, file_name),
            )
            self._db().commit()

    def touch_assets(self, digests: list[str]) -> None:
        if not digests:
            return
        with self._lock:
            placeholders = ",".join("?" for _ in digests)
            self._db().execute(
                f"UPDATE provider_assets SET last_referenced_at = CURRENT_TIMESTAMP, orphaned_at = NULL, status = 'active' WHERE content_sha256 IN ({placeholders})",
                digests,
            )
            self._db().commit()

    def get_asset_mime(self, digest: str) -> str | None:
        with self._lock:
            row = self._db().execute(
                "SELECT mime_type FROM provider_assets WHERE content_sha256 = ? AND status = 'active'",
                (digest,),
            ).fetchone()
        return str(row["mime_type"]) if row is not None else None

    def get_asset_info(self, digest: str) -> tuple[str | None, str | None]:
        with self._lock:
            row = self._db().execute(
                "SELECT mime_type, file_name FROM provider_assets WHERE content_sha256 = ? AND status = 'active'",
                (digest,),
            ).fetchone()
        if row is None:
            return None, None
        return str(row["mime_type"] or "") or None, str(row["file_name"] or "") or None

    def get_character(self, character_id: str, *, include_deleted: bool = False) -> ProviderCharacter | None:
        with self._lock:
            sql = "SELECT * FROM provider_characters WHERE character_id = ?"
            params: tuple[object, ...] = (character_id,)
            if not include_deleted:
                sql += " AND deleted_at IS NULL"
            row = self._db().execute(sql, params).fetchone()
        return self._row_to_character(row) if row is not None else None

    def list_characters(self, *, entity_type: str | None = None, limit: int = 50, offset: int = 0) -> list[ProviderCharacter]:
        with self._lock:
            if entity_type:
                rows = self._db().execute(
                    "SELECT * FROM provider_characters WHERE deleted_at IS NULL AND entity_type = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (entity_type, limit, offset),
                ).fetchall()
            else:
                rows = self._db().execute(
                    "SELECT * FROM provider_characters WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [self._row_to_character(row) for row in rows]

    def create_character(
        self,
        *,
        name: str,
        entity_type: str = "character",
        description: str | None = None,
        image_prompt: str | None = None,
        voice_description: str | None = None,
        image_model: str = "pro",
        aspect_ratio: str | None = None,
        reference_asset_hashes: list[str] | None = None,
        reference_media_ids: list[str] | None = None,
    ) -> ProviderCharacter:
        if len(reference_asset_hashes or []) > 3 or len(reference_media_ids or []) > 3:
            raise ValueError("A Character accepts at most three reference images")
        if len(reference_asset_hashes or []) != len(reference_media_ids or []):
            raise ValueError("Character asset and media references must be paired")
        character_id = f"char_{uuid.uuid4().hex}"
        with self._lock:
            self._db().execute(
                """
                INSERT INTO provider_characters (
                    character_id, name, entity_type, description, image_prompt, voice_description,
                    image_model, aspect_ratio, reference_asset_hashes_json, reference_media_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    character_id, name, entity_type, description, image_prompt, voice_description,
                    image_model, aspect_ratio,
                    json.dumps(reference_asset_hashes or [], ensure_ascii=False),
                    json.dumps(reference_media_ids or [], ensure_ascii=False),
                ),
            )
            self._db().commit()
        return self.get_character(character_id, include_deleted=True)  # type: ignore[return-value]

    def update_character(self, character_id: str, **updates: object) -> ProviderCharacter | None:
        allowed = {
            "name", "entity_type", "description", "image_prompt", "voice_description",
            "image_model", "aspect_ratio",
            "reference_asset_hashes", "reference_media_ids",
        }
        updates = {key: value for key, value in updates.items() if key in allowed}
        if "reference_asset_hashes" in updates:
            hashes = updates["reference_asset_hashes"] or []
            if not isinstance(hashes, list) or len(hashes) > 3:
                raise ValueError("A Character accepts at most three reference images")
        if "reference_media_ids" in updates:
            media_ids = updates["reference_media_ids"] or []
            if not isinstance(media_ids, list) or len(media_ids) > 3:
                raise ValueError("A Character accepts at most three reference images")
        if (
            "reference_asset_hashes" in updates
            and "reference_media_ids" in updates
            and len(updates["reference_asset_hashes"] or [])
            != len(updates["reference_media_ids"] or [])
        ):
            raise ValueError("Character asset and media references must be paired")
        if not updates:
            return self.get_character(character_id)
        assignments: list[str] = []
        values: list[object] = []
        for key, value in updates.items():
            column = {
                "reference_asset_hashes": "reference_asset_hashes_json",
                "reference_media_ids": "reference_media_ids_json",
            }.get(key, key)
            if key in {"reference_asset_hashes", "reference_media_ids"}:
                value = json.dumps(value or [], ensure_ascii=False)
            assignments.append(f"{column} = ?")
            values.append(value)
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.append(character_id)
        with self._lock:
            updated = self._db().execute(
                f"UPDATE provider_characters SET {', '.join(assignments)} WHERE character_id = ? AND deleted_at IS NULL",
                values,
            ).rowcount
            self._db().commit()
        return self.get_character(character_id, include_deleted=True) if updated else None

    def soft_delete_character(self, character_id: str) -> bool:
        with self._lock:
            updated = self._db().execute(
                "UPDATE provider_characters SET deleted_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE character_id = ? AND deleted_at IS NULL",
                (character_id,),
            ).rowcount
            self._db().commit()
        return updated == 1

    def get_active_character_job(
        self, character_id: str, generation_types: tuple[str, ...] | None = None,
    ) -> ProviderJob | None:
        with self._lock:
            sql = "SELECT * FROM provider_jobs WHERE character_id = ? AND status IN ('queued', 'dispatching', 'running')"
            params: list[object] = [character_id]
            if generation_types:
                placeholders = ",".join("?" for _ in generation_types)
                sql += f" AND generation_type IN ({placeholders})"
                params.extend(generation_types)
            sql += " ORDER BY created_at DESC LIMIT 1"
            row = self._db().execute(sql, params).fetchone()
        return _row_to_job(row) if row is not None else None

    def fail_abandoned_dispatches(self, lease_seconds: int = JOB_CLAIM_LEASE_SECONDS) -> int:
        """Fail only dispatch claims old enough that their owning worker is gone."""
        with self._lock:
            db = self._db()
            updated = db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    claimed_at = NULL,
                    claim_token = NULL,
                    error_message = CASE media_type
                        WHEN 'image' THEN 'Provider restarted during image dispatch; the outcome is unknown.'
                        ELSE ?
                    END,
                    error_code = CASE media_type
                        WHEN 'image' THEN 'IMAGE_DISPATCH_OUTCOME_UNKNOWN'
                        ELSE 'VIDEO_DISPATCH_OUTCOME_UNKNOWN'
                    END,
                    error_retryable = 0,
                    outcome_unknown = 1,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE status = 'dispatching'
                  AND claimed_at IS NOT NULL
                  AND claimed_at <= datetime('now', ?)
                """,
                (
                    (
                        "Provider restarted during paid dispatch; the outcome is unknown. "
                        "Reconcile before retrying."
                    ),
                    f"-{max(1, lease_seconds)} seconds",
                ),
            ).rowcount
            db.commit()
        return updated

    def get_job(
        self,
        job_id: str,
        *,
        image_timeout_seconds: int = 120,
        video_queue_timeout_seconds: int = 180,
        video_running_timeout_seconds: int = 600,
    ) -> ProviderJob | None:
        with self._lock:
            db = self._db()
            row = db.execute(
                "SELECT * FROM provider_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is not None and row["status"] in ("queued", "dispatching", "running") and row["created_at"]:
                status = row["status"]
                media_type = row["media_type"]
                created_at = row["created_at"]
                running_at = row["running_at"]
                try:
                    from datetime import datetime, timezone
                    clean_ts = created_at.replace("Z", "").replace("T", " ")
                    dt = datetime.fromisoformat(clean_ts).replace(tzinfo=timezone.utc)
                    total_age = (datetime.now(timezone.utc) - dt).total_seconds()
                    if running_at:
                        clean_run_ts = running_at.replace("Z", "").replace("T", " ")
                        run_dt = datetime.fromisoformat(clean_run_ts).replace(tzinfo=timezone.utc)
                        run_age = (datetime.now(timezone.utc) - run_dt).total_seconds()
                    else:
                        run_age = total_age
                except Exception:
                    total_age = 0.0
                    run_age = 0.0

                timed_out = False
                err_code = "JOB_TIMEOUT"
                err_msg = "Job exceeded processing time limit."
                outcome_unknown = False

                if media_type == "image" and total_age > image_timeout_seconds:
                    timed_out = True
                    err_code = "IMAGE_TIMEOUT"
                    err_msg = f"Image generation timed out after {int(total_age)}s limit."
                elif media_type == "video":
                    if status == "queued" and total_age > video_queue_timeout_seconds:
                        timed_out = True
                        err_code = "QUEUE_TIMEOUT"
                        err_msg = f"Video job timed out waiting in queue after {int(total_age)}s limit."
                    elif status in ("dispatching", "running") and run_age > video_running_timeout_seconds:
                        timed_out = True
                        err_code = "VIDEO_POLL_TIMEOUT"
                        err_msg = f"Video generation timed out after {int(run_age)}s limit."
                        outcome_unknown = True

                if timed_out:
                    db.execute(
                        """
                        UPDATE provider_jobs
                        SET status = 'failed',
                            error_message = ?,
                            error_code = ?,
                            error_retryable = 0,
                            outcome_unknown = ?,
                            updated_at = CURRENT_TIMESTAMP,
                            completed_at = CURRENT_TIMESTAMP
                        WHERE job_id = ? AND status IN ('queued', 'dispatching', 'running')
                        """,
                        (err_msg, err_code, int(outcome_unknown), job_id),
                    )
                    db.commit()
                    row = db.execute(
                        "SELECT * FROM provider_jobs WHERE job_id = ?", (job_id,)
                    ).fetchone()

        return _row_to_job(row) if row is not None else None

    def get_job_by_operation(self, operation_name: str) -> ProviderJob | None:
        with self._lock:
            # 1. Direct match by operation_name, poll_name, or job_id
            row = self._db().execute(
                "SELECT * FROM provider_jobs WHERE operation_name = ? OR poll_name = ? OR job_id = ?",
                (operation_name, operation_name, operation_name),
            ).fetchone()
            if row is not None:
                return _row_to_job(row)

            # 2. Match via provider_operations mapping (e.g. if operation_name is a media ID or workflow alias)
            op_row = self._db().execute(
                "SELECT operation_name, poll_name FROM provider_operations WHERE operation_name = ? OR poll_name = ?",
                (operation_name, operation_name),
            ).fetchone()
            if op_row is not None:
                row = self._db().execute(
                    "SELECT * FROM provider_jobs WHERE operation_name = ? OR poll_name = ? OR operation_name = ? OR poll_name = ?",
                    (op_row["operation_name"], op_row["poll_name"], op_row["poll_name"], op_row["operation_name"]),
                ).fetchone()
                if row is not None:
                    return _row_to_job(row)

            # 3. Match completed media ID inside result_json
            row = self._db().execute(
                "SELECT * FROM provider_jobs WHERE status = 'completed' AND result_json LIKE ? ORDER BY created_at DESC LIMIT 1",
                (f"%{operation_name}%",),
            ).fetchone()
            if row is not None:
                return _row_to_job(row)

        return None

    def claim_next_queued_job(self) -> ProviderJob | None:
        with self._lock:
            db = self._db()
            # Claiming must be one SQLite transaction.  The worker performs
            # network I/O after this method returns, so a plain SELECT would
            # let two worker processes dispatch the same paid job.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT * FROM provider_jobs
                WHERE status = 'queued'
                ORDER BY CASE media_type WHEN 'image' THEN 0 ELSE 1 END,
                         created_at ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                db.commit()
                return None
            claim_token = uuid.uuid4().hex
            updated = db.execute(
                """
                UPDATE provider_jobs
                SET status = 'dispatching',
                    claimed_at = CURRENT_TIMESTAMP,
                    claim_token = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'queued'
                """,
                (claim_token, row["job_id"]),
            ).rowcount
            if updated != 1:
                db.rollback()
                return None
            db.commit()
            claimed_row = db.execute(
                "SELECT * FROM provider_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            return _row_to_job(claimed_row) if claimed_row is not None else None

    def release_job_claim(self, job_id: str, claim_token: str) -> bool:
        """Return a claimed queued job to the queue when no account was available."""
        with self._lock:
            updated = self._db().execute(
                """
                UPDATE provider_jobs
                SET status = 'queued', claimed_at = NULL, claim_token = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'dispatching' AND claim_token = ?
                """,
                (job_id, claim_token),
            ).rowcount
            self._db().commit()
        return updated == 1

    def update_job_running(
        self,
        job_id: str,
        *,
        operation_name: str,
        installation_id: str,
        google_project_id: str,
        poll_name: str | None = None,
        claim_token: str | None = None,
    ) -> bool:
        poll_name = poll_name or operation_name
        with self._lock:
            db = self._db()
            condition = "job_id = ? AND status = 'dispatching'"
            params: list[object] = [operation_name, installation_id, google_project_id, poll_name, job_id]
            if claim_token is not None:
                condition += " AND claim_token = ?"
                params.append(claim_token)
            updated = db.execute(
                f"""
                UPDATE provider_jobs
                SET status = 'running',
                    claimed_at = NULL,
                    operation_name = ?,
                    installation_id = ?,
                    google_project_id = ?,
                    poll_name = ?,
                    running_at = CURRENT_TIMESTAMP,
                    next_poll_at = CURRENT_TIMESTAMP,
                    last_poll_at = NULL,
                    last_poll_error = NULL,
                    poll_attempts = 0,
                    poll_error_count = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE {condition}
                """,
                params,
            ).rowcount
            db.commit()
        return updated == 1

    def update_job_completed(
        self, job_id: str, result_data: dict, claim_token: str | None = None,
    ) -> bool:
        result_json = json.dumps(result_data, ensure_ascii=False)
        with self._lock:
            db = self._db()
            condition = "job_id = ? AND status IN ('dispatching', 'running')"
            params: list[object] = [result_json, job_id]
            if claim_token is not None:
                condition += " AND claim_token = ?"
                params.append(claim_token)
            updated = db.execute(
                f"""
                UPDATE provider_jobs
                SET status = 'completed',
                    claimed_at = NULL,
                    claim_token = NULL,
                    result_json = ?,
                    next_poll_at = NULL,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE {condition}
                """,
                params,
            ).rowcount
            db.commit()
        return updated == 1

    def update_job_failed(
        self, job_id: str, error_message: str, claim_token: str | None = None,
        *, error_code: str = "VIDEO_GENERATION_FAILED", retryable: bool = False,
        outcome_unknown: bool = False,
    ) -> bool:
        with self._lock:
            db = self._db()
            condition = "job_id = ? AND status IN ('queued', 'dispatching', 'running')"
            params: list[object] = [
                error_message[:1000], error_code, int(retryable), int(outcome_unknown), job_id,
            ]
            if claim_token is not None:
                condition += " AND claim_token = ?"
                params.append(claim_token)
            updated = db.execute(
                f"""
                UPDATE provider_jobs
                SET status = 'failed',
                    claimed_at = NULL,
                    claim_token = NULL,
                    error_message = ?,
                    error_code = ?,
                    error_retryable = ?,
                    outcome_unknown = ?,
                    next_poll_at = NULL,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE {condition}
                """,
                params,
            ).rowcount
            db.commit()
        return updated == 1

    def fail_expired_running_jobs(self, max_age_seconds: int) -> int:
        """Move irrecoverably stale polling jobs to an explicit reconciliation failure."""
        with self._lock:
            updated = self._db().execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'VIDEO_POLL_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 1,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE status = 'running' AND media_type = 'video'
                  AND COALESCE(running_at, updated_at) <= datetime('now', ?)
                """,
                (
                    "Video polling exceeded its maximum age; reconcile the existing Flow operation before retrying.",
                    f"-{max(1, max_age_seconds)} seconds",
                ),
            ).rowcount
            self._db().commit()
        return updated

    def fail_expired_jobs(
        self,
        *,
        image_timeout_seconds: int = 120,
        video_queue_timeout_seconds: int = 180,
        video_running_timeout_seconds: int = 600,
    ) -> int:
        """Move any stale queued, dispatching, or running jobs to an explicit terminal failure."""
        total = 0
        with self._lock:
            db = self._db()
            # 1. Expired image jobs (queued, dispatching, running)
            img_count = db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'IMAGE_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 0,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE media_type = 'image'
                  AND status IN ('queued', 'dispatching', 'running')
                  AND created_at <= datetime('now', ?)
                """,
                (
                    f"Image generation timed out after exceeding {image_timeout_seconds}s limit.",
                    f"-{max(1, image_timeout_seconds)} seconds",
                ),
            ).rowcount
            total += img_count

            # 2. Expired queued video jobs
            vid_q_count = db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'QUEUE_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 0,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE media_type = 'video'
                  AND status = 'queued'
                  AND created_at <= datetime('now', ?)
                """,
                (
                    f"Video job timed out waiting in queue after exceeding {video_queue_timeout_seconds}s limit.",
                    f"-{max(1, video_queue_timeout_seconds)} seconds",
                ),
            ).rowcount
            total += vid_q_count

            # 3. Expired running video jobs
            vid_r_count = db.execute(
                """
                UPDATE provider_jobs
                SET status = 'failed',
                    error_message = ?,
                    error_code = 'VIDEO_POLL_TIMEOUT',
                    error_retryable = 0,
                    outcome_unknown = 1,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = CURRENT_TIMESTAMP
                WHERE media_type = 'video'
                  AND status IN ('dispatching', 'running')
                  AND COALESCE(running_at, created_at) <= datetime('now', ?)
                """,
                (
                    f"Video generation timed out after exceeding {video_running_timeout_seconds}s limit.",
                    f"-{max(1, video_running_timeout_seconds)} seconds",
                ),
            ).rowcount
            total += vid_r_count

            if total > 0:
                db.commit()
        return total

    def schedule_job_poll(
        self,
        job_id: str,
        delay_seconds: float,
        *,
        error_message: str | None = None,
        attempted: bool = True,
    ) -> bool:
        """Persist the next poll time and consecutive-error state for a running job."""
        modifier = f"+{max(1, int(delay_seconds))} seconds"
        with self._lock:
            if error_message is None:
                poll_error_sql = "0"
                last_error: str | None = None
            else:
                poll_error_sql = "poll_error_count + 1"
                last_error = error_message[:1000]
            updated = self._db().execute(
                f"""
                UPDATE provider_jobs
                SET next_poll_at = datetime('now', ?),
                    last_poll_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_poll_at END,
                    last_poll_error = ?,
                    poll_attempts = poll_attempts + CASE WHEN ? THEN 1 ELSE 0 END,
                    poll_error_count = {poll_error_sql},
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'running'
                """,
                (modifier, int(attempted), last_error, int(attempted), job_id),
            ).rowcount
            self._db().commit()
        return updated == 1

    def record_job_poll_attempt(self, job_id: str) -> bool:
        """Record one completed provider poll cycle before interpreting its result."""
        with self._lock:
            updated = self._db().execute(
                """
                UPDATE provider_jobs
                SET last_poll_at = CURRENT_TIMESTAMP,
                    poll_attempts = poll_attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'running'
                """,
                (job_id,),
            ).rowcount
            self._db().commit()
        return updated == 1

    def list_running_jobs(self, limit: int = 100) -> list[ProviderJob]:
        with self._lock:
            rows = self._db().execute(
                """
                SELECT * FROM provider_jobs
                WHERE status = 'running' AND media_type = 'video'
                  AND (next_poll_at IS NULL OR next_poll_at <= CURRENT_TIMESTAMP)
                ORDER BY COALESCE(next_poll_at, running_at, created_at) ASC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        return [_row_to_job(r) for r in rows]

    def claim_due_running_jobs(
        self, *, limit: int = 100, lease_seconds: int = 120,
    ) -> list[ProviderJob]:
        """Atomically lease due polling work so multiple workers do not duplicate polls."""
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT job_id FROM provider_jobs
                WHERE status = 'running' AND media_type = 'video'
                  AND (next_poll_at IS NULL OR next_poll_at <= CURRENT_TIMESTAMP)
                ORDER BY COALESCE(next_poll_at, running_at, created_at) ASC
                LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
            job_ids = [str(row["job_id"]) for row in rows]
            if not job_ids:
                db.commit()
                return []
            placeholders = ",".join("?" for _ in job_ids)
            db.execute(
                f"""
                UPDATE provider_jobs
                SET next_poll_at = datetime('now', ?)
                WHERE status = 'running' AND job_id IN ({placeholders})
                """,
                (f"+{max(1, lease_seconds)} seconds", *job_ids),
            )
            claimed = db.execute(
                f"SELECT * FROM provider_jobs WHERE status = 'running' AND job_id IN ({placeholders})",
                job_ids,
            ).fetchall()
            db.commit()
        by_id = {str(row["job_id"]): _row_to_job(row) for row in claimed}
        return [by_id[job_id] for job_id in job_ids if job_id in by_id]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
