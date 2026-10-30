"""Single-executor turns for the shared visible desktop."""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_desktop_turns",
        sa.Column("desktop_turn_id", sa.String(128), primary_key=True),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "user_id", "objective_id", "execution_id")
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_assistant_desktop_turns_objective",
        "assistant_desktop_turns",
        ["objective_id", "acquired_at"],
    )
    op.create_index(
        "uq_assistant_desktop_active",
        "assistant_desktop_turns",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_assistant_desktop_active", table_name="assistant_desktop_turns")
    op.drop_index(
        "ix_assistant_desktop_turns_objective", table_name="assistant_desktop_turns"
    )
    op.drop_table("assistant_desktop_turns")
