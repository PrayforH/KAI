"""Assistant group unread watermarks (payload.lastReadAt) and message reactions."""

import sqlalchemy as sa
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_group_reactions",
        sa.Column("group_id", sa.String(128), primary_key=True),
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("emoji", sa.String(16), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_group_reactions_message",
        "assistant_group_reactions",
        ["group_id", "message_id"],
    )
    # 旧群没有 lastReadAt 时，服务端以群的创建时间作为水位。


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_group_reactions_message",
        table_name="assistant_group_reactions",
    )
    op.drop_table("assistant_group_reactions")
