"""Persistent specialist assistants and handover receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_specialists",
        sa.Column("specialist_id", sa.String(128), primary_key=True),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "user_id")
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "slug", name="uq_assistant_specialist_slug"),
    )
    op.create_index(
        "ix_assistant_specialists_owner",
        "assistant_specialists",
        ["tenant_id", "user_id", "created_at"],
    )
    op.create_table(
        "assistant_handovers",
        sa.Column("handover_id", sa.String(128), primary_key=True),
        sa.Column("specialist_id", sa.String(128), nullable=False),
        sa.Column("objective_id", sa.String(128)),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "user_id")
        ),
        sa.Column("message_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "message_id", name="uq_assistant_handover_message"),
    )
    op.create_index(
        "ix_assistant_handovers_specialist",
        "assistant_handovers",
        ["specialist_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_handovers_specialist", table_name="assistant_handovers")
    op.drop_table("assistant_handovers")
    op.drop_index("ix_assistant_specialists_owner", table_name="assistant_specialists")
    op.drop_table("assistant_specialists")
