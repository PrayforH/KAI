"""Managed-task bindings and structured checkpoints for delegated work."""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_managed_tasks",
        sa.Column("managed_id", sa.String(128), primary_key=True),
        sa.Column(
            "objective_id",
            sa.String(128),
            sa.ForeignKey("assistant_objectives.objective_id"),
            nullable=False,
        ),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "user_id", "thread_id")
        ),
        sa.Column("project_id", sa.String(128)),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_assistant_managed_objective",
        "assistant_managed_tasks",
        ["objective_id", "created_at"],
    )
    op.create_index(
        "uq_assistant_managed_execute",
        "assistant_managed_tasks",
        ["tenant_id", "user_id", "thread_id"],
        unique=True,
        postgresql_where=sa.text("role = 'execute'"),
    )
    op.create_table(
        "assistant_checkpoints",
        sa.Column("checkpoint_id", sa.String(128), primary_key=True),
        sa.Column(
            "objective_id",
            sa.String(128),
            sa.ForeignKey("assistant_objectives.objective_id"),
            nullable=False,
        ),
        sa.Column("execution_id", sa.String(128), nullable=False),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "user_id")
        ),
        sa.Column("run_id", sa.String(128)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_checkpoints_objective",
        "assistant_checkpoints",
        ["objective_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_checkpoints_objective", table_name="assistant_checkpoints"
    )
    op.drop_table("assistant_checkpoints")
    op.drop_index("uq_assistant_managed_execute", table_name="assistant_managed_tasks")
    op.drop_index("ix_assistant_managed_objective", table_name="assistant_managed_tasks")
    op.drop_table("assistant_managed_tasks")
