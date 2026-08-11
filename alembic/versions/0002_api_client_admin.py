"""add api client admin flag

Revision ID: 0002_api_client_admin
Revises: 0001_initial
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_api_client_admin"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_clients", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute(sa.text("UPDATE api_clients SET is_admin = true WHERE name = 'Bootstrap client'"))


def downgrade() -> None:
    op.drop_column("api_clients", "is_admin")
