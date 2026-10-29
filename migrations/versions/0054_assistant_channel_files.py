"""Independent durable file receipts for bound assistant channels."""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_channel_files",
        sa.Column("delivery_id", sa.String(64), primary_key=True),
        *(
            sa.Column(name, sa.String(size), nullable=False)
            for name, size in (
                ("channel", 16),
                ("tenant_id", 128),
                ("user_id", 128),
                ("source_id", 128),
                ("route", 256),
                ("run_id", 128),
                ("artifact_id", 128),
                ("status", 16),
            )
        ),
        sa.Column("ciphertext", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_channel_files_work",
        "assistant_channel_files",
        ["channel", "status", "next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_table("assistant_channel_files")
