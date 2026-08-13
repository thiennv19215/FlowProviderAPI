"""store provider thumbnails in owned storage

Revision ID: 0009_thumbnail_storage
Revises: 0008_normalize_done_status
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_thumbnail_storage"
down_revision = "0008_normalize_done_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.add_column(sa.Column("thumbnail_storage_key", sa.String(length=512), nullable=True))
        batch.create_unique_constraint("uq_media_assets_thumbnail_storage_key", ["thumbnail_storage_key"])


def downgrade() -> None:
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_constraint("uq_media_assets_thumbnail_storage_key", type_="unique")
        batch.drop_column("thumbnail_storage_key")
