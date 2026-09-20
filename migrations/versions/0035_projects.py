"""User-owned projects that group tasks.

Revision ID: 0035
Revises: 0034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from harness.storage.models import ProjectRow

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ProjectRow.__table__.create(op.get_bind(), checkfirst=True)
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agui_thread_bindings")}
    if "project_id" not in columns:
        op.add_column(
            "agui_thread_bindings",
            sa.Column("project_id", sa.String(length=128), nullable=True),
        )
        op.create_index(
            "ix_agui_thread_bindings_project_id",
            "agui_thread_bindings",
            ["project_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_agui_thread_bindings_project_id", table_name="agui_thread_bindings")
    op.drop_column("agui_thread_bindings", "project_id")
    ProjectRow.__table__.drop(op.get_bind(), checkfirst=True)
