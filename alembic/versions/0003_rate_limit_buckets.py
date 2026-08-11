from alembic import op
import sqlalchemy as sa

revision = "0003_rate_limit_buckets"
down_revision = "0002_api_client_admin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "rate_limit_buckets",
        sa.Column("client_id",sa.String(length=80),nullable=False),
        sa.Column("window_started_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("request_count",sa.Integer(),nullable=False,server_default="0"),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["client_id"],["api_clients.id"],ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("client_id"),
    )


def downgrade():
    op.drop_table("rate_limit_buckets")
