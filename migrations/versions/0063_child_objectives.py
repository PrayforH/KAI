"""Count coordinator-created child tasks against the authorization scope."""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_objectives",
        sa.Column("parent_objective_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_assistant_objectives_parent",
        "assistant_objectives",
        ["parent_objective_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_objectives_parent", table_name="assistant_objectives")
    op.drop_column("assistant_objectives", "parent_objective_id")
