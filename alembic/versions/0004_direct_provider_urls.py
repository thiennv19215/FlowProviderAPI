"""store generated provider URLs without copying media

Revision ID: 0004_direct_provider_urls
Revises: 0003_rate_limit_buckets
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_direct_provider_urls"
down_revision = "0003_rate_limit_buckets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("external_url", sa.Text(), nullable=True))
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.alter_column(
            "storage_key",
            existing_type=sa.String(length=512),
            nullable=True,
        )


def downgrade() -> None:
    # Keep the legacy non-null invariant valid. These placeholders deliberately
    # do not pretend that external provider media was copied into storage.
    op.execute(
        sa.text(
            "UPDATE media_assets "
            "SET storage_key = 'external/' || id "
            "WHERE storage_key IS NULL"
        )
    )
    with op.batch_alter_table("media_assets") as batch_op:
        batch_op.alter_column(
            "storage_key",
            existing_type=sa.String(length=512),
            nullable=False,
        )
        batch_op.drop_column("external_url")
