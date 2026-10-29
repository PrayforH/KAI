"""Idempotent objective creation for delegated background tasks."""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_objectives",
        sa.Column("idempotency_key", sa.String(128), nullable=True),
    )
    op.create_index(
        "uq_assistant_objective_idempotency",
        "assistant_objectives",
        ["tenant_id", "user_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_assistant_objective_idempotency", table_name="assistant_objectives")
    op.drop_column("assistant_objectives", "idempotency_key")
