"""store upstream video thumbnail URLs

Revision ID: 0005_video_thumbnails
Revises: 0004_direct_provider_urls
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_video_thumbnails"
down_revision = "0004_direct_provider_urls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("media_assets", sa.Column("thumbnail_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media_assets", "thumbnail_url")
