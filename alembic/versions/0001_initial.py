"""initial provider schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-11
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_clients",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("key_prefix", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_jobs", sa.Integer(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_api_clients_key_prefix", "api_clients", ["key_prefix"], unique=False)

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("workspace_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("provider_account_id", sa.String(length=120), nullable=True),
        sa.Column("provider_project_id", sa.String(length=255), nullable=True),
        sa.Column("provider_operation_id", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["api_clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_job_client_idempotency"),
    )
    op.create_index("ix_generation_jobs_client_id", "generation_jobs", ["client_id"], unique=False)
    op.create_index("ix_generation_jobs_kind", "generation_jobs", ["kind"], unique=False)
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"], unique=False)
    op.create_index("ix_generation_jobs_provider_account_id", "generation_jobs", ["provider_account_id"], unique=False)
    op.create_index("ix_jobs_runnable", "generation_jobs", ["status", "next_run_at", "priority", "created_at"], unique=False)
    op.create_index("ix_jobs_provider_account", "generation_jobs", ["provider_account_id", "status"], unique=False)

    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("source_provider", sa.String(length=64), nullable=True),
        sa.Column("source_job_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["api_clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_media_assets_client_id", "media_assets", ["client_id"], unique=False)
    op.create_index("ix_media_assets_source_job_id", "media_assets", ["source_job_id"], unique=False)

    op.create_table(
        "workspace_projects",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("client_id", sa.String(length=80), nullable=False),
        sa.Column("workspace_key", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_account_id", sa.String(length=120), nullable=False),
        sa.Column("provider_project_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["api_clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "workspace_key", "provider", "provider_account_id", name="uq_workspace_provider_account"),
    )
    op.create_index("ix_workspace_projects_client_id", "workspace_projects", ["client_id"], unique=False)

    op.create_table(
        "project_media_mappings",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("asset_id", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_project_id", sa.String(length=255), nullable=False),
        sa.Column("provider_media_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["media_assets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "provider_project_id", name="uq_asset_provider_project"),
    )
    op.create_index("ix_project_media_mappings_asset_id", "project_media_mappings", ["asset_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_project_media_mappings_asset_id", table_name="project_media_mappings")
    op.drop_table("project_media_mappings")
    op.drop_index("ix_workspace_projects_client_id", table_name="workspace_projects")
    op.drop_table("workspace_projects")
    op.drop_index("ix_media_assets_source_job_id", table_name="media_assets")
    op.drop_index("ix_media_assets_client_id", table_name="media_assets")
    op.drop_table("media_assets")
    op.drop_index("ix_jobs_provider_account", table_name="generation_jobs")
    op.drop_index("ix_jobs_runnable", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_provider_account_id", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_status", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_kind", table_name="generation_jobs")
    op.drop_index("ix_generation_jobs_client_id", table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_index("ix_api_clients_key_prefix", table_name="api_clients")
    op.drop_table("api_clients")
