"""Durable group broadcast/delegation admission and summary state."""

import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_group_deliveries",
        sa.Column("batch_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("trigger_message_id", sa.String(128), nullable=False),
        sa.Column("summary_state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_group_deliveries_source",
        "assistant_group_deliveries",
        ["tenant_id", "user_id", "group_id", "trigger_message_id"],
    )
    op.create_index(
        "ix_group_deliveries_due", "assistant_group_deliveries", ["summary_state", "updated_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_group_deliveries_due", table_name="assistant_group_deliveries")
    op.drop_index("ix_group_deliveries_source", table_name="assistant_group_deliveries")
    op.drop_table("assistant_group_deliveries")
