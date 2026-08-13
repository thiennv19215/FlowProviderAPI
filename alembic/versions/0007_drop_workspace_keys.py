"""drop obsolete provider workspace keys

Revision ID: 0007_drop_workspace_keys
Revises: 0006_numeric_media_ids
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_drop_workspace_keys"
down_revision = "0006_numeric_media_ids"
branch_labels = None
depends_on = None


def _deduplicate_client_projects() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    projects = sa.Table("workspace_projects", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(
            projects.c.id,
            projects.c.client_id,
            projects.c.provider,
            projects.c.provider_account_id,
            projects.c.created_at,
        ).order_by(projects.c.created_at.asc(), projects.c.id.asc())
    ).mappings()
    seen: set[tuple[str, str, str]] = set()
    duplicate_ids: list[str] = []
    for row in rows:
        key = (row["client_id"], row["provider"], row["provider_account_id"])
        if key in seen:
            duplicate_ids.append(row["id"])
        else:
            seen.add(key)
    if duplicate_ids:
        bind.execute(projects.delete().where(projects.c.id.in_(duplicate_ids)))


def upgrade() -> None:
    _deduplicate_client_projects()
    with op.batch_alter_table("workspace_projects") as batch:
        batch.drop_constraint("uq_workspace_provider_account", type_="unique")
        batch.drop_column("workspace_key")
        batch.create_unique_constraint(
            "uq_client_provider_account",
            ["client_id", "provider", "provider_account_id"],
        )
    with op.batch_alter_table("generation_jobs") as batch:
        batch.drop_column("workspace_key")


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs") as batch:
        batch.add_column(
            sa.Column(
                "workspace_key",
                sa.String(length=255),
                nullable=False,
                server_default="__api_client__",
            )
        )
        batch.alter_column("workspace_key", server_default=None)
    with op.batch_alter_table("workspace_projects") as batch:
        batch.drop_constraint("uq_client_provider_account", type_="unique")
        batch.add_column(
            sa.Column(
                "workspace_key",
                sa.String(length=255),
                nullable=False,
                server_default="__api_client__",
            )
        )
        batch.create_unique_constraint(
            "uq_workspace_provider_account",
            ["client_id", "workspace_key", "provider", "provider_account_id"],
        )
        batch.alter_column("workspace_key", server_default=None)
