"""Persist the obligation to execute every accepted Run.

Revision ID: 0038
Revises: 0037

The table is defined inline rather than by importing the ORM model: revision
0001 already calls the live ``Base.metadata``, so a migration that also read
the current model would make this revision's meaning change whenever the model
does. The ``has_table`` guard matches revision 0006, because on a brand-new
database 0001's legacy ``create_all`` can already have created this table.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("run_execution_commands"):
        return
    op.create_table(
        "run_execution_commands",
        sa.Column("command_id", sa.String(length=191), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.PrimaryKeyConstraint("command_id"),
        sa.UniqueConstraint(
            "tenant_id", "run_id", name="uq_run_execution_command"
        ),
    )
    op.create_index(
        "ix_run_execution_commands_tenant_id",
        "run_execution_commands",
        ["tenant_id"],
    )
    op.create_index(
        "ix_run_execution_commands_run_id",
        "run_execution_commands",
        ["run_id"],
    )
    op.create_index(
        "ix_run_execution_commands_status",
        "run_execution_commands",
        ["status"],
    )
    op.create_index(
        "ix_run_execution_commands_claimable",
        "run_execution_commands",
        ["status", "available_at", "command_id"],
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("run_execution_commands"):
        op.drop_table("run_execution_commands")
