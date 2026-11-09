"""Persist a scheduled Automation occurrence before advancing its plan."""

import sqlalchemy as sa
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "automation_dispatches",
        sa.Column("dispatch_id", sa.String(128), primary_key=True),
        sa.Column("task_id", sa.String(128), unique=True, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.String(64)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("payload", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("automation_dispatches")
