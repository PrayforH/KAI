"""Task dependencies inside managed delegation; the graph must stay acyclic."""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_managed_tasks",
        sa.Column("depends_on_objective_id", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assistant_managed_tasks", "depends_on_objective_id")
