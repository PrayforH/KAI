"""Measured usage per checkpoint, so token/cost budget limits stay honest."""

import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_checkpoints", sa.Column("tokens", sa.Integer(), nullable=True))
    op.add_column("assistant_checkpoints", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column(
        "assistant_checkpoints",
        sa.Column("unknown_usage", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("assistant_checkpoints", "unknown_usage")
    op.drop_column("assistant_checkpoints", "cost_usd")
    op.drop_column("assistant_checkpoints", "tokens")
