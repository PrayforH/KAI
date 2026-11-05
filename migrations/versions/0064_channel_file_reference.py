"""Index confirmed file receipts for owner-bound quoted follow-ups."""

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_channel_files", sa.Column("message_id", sa.String(128), nullable=True))
    op.create_index(
        "ix_assistant_channel_files_reference",
        "assistant_channel_files",
        ["channel", "tenant_id", "user_id", "message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_channel_files_reference", table_name="assistant_channel_files")
    op.drop_column("assistant_channel_files", "message_id")
