"""Pinned projects sort above the rest of the sidebar.

Revision ID: 0041
Revises: 0040
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("projects")
    }
    if "pinned_at" not in columns:
        op.add_column(
            "projects",
            sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("projects")
    }
    if "pinned_at" in columns:
        op.drop_column("projects", "pinned_at")
