"""normalize completed media and job statuses

Revision ID: 0008_normalize_done_status
Revises: 0007_drop_workspace_keys
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_normalize_done_status"
down_revision = "0007_drop_workspace_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE generation_jobs SET status = 'done' WHERE status = 'succeeded'")
    )
    bind.execute(
        sa.text("UPDATE media_assets SET status = 'done' WHERE status = 'ready'")
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE generation_jobs SET status = 'succeeded' WHERE status = 'done'")
    )
    bind.execute(
        sa.text("UPDATE media_assets SET status = 'ready' WHERE status = 'done'")
    )
