"""Persist assistant group chats: roster seats and the group message timeline."""

import sqlalchemy as sa
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_groups",
        sa.Column("group_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_assistant_groups_owner",
        "assistant_groups",
        ["tenant_id", "user_id", "updated_at"],
    )

    op.create_table(
        "assistant_group_members",
        sa.Column("group_id", sa.String(128), primary_key=True),
        sa.Column("member_id", sa.String(160), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("specialist_id", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "group_id", "member_id", name="uq_assistant_group_member"
        ),
    )
    op.create_index(
        "ix_assistant_group_members_group",
        "assistant_group_members",
        ["group_id", "status"],
    )

    op.create_table(
        "assistant_group_messages",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("author", sa.String(16), nullable=False),
        sa.Column("author_name", sa.String(80), nullable=False),
        sa.Column("specialist_id", sa.String(128)),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(128)),
        sa.Column("thread_id", sa.String(256)),
        sa.Column("reply_to_message_id", sa.String(128)),
        sa.Column("client_message_id", sa.String(128)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "group_id",
            "client_message_id",
            name="uq_assistant_group_client_message",
        ),
    )
    op.create_index(
        "ix_assistant_group_messages_timeline",
        "assistant_group_messages",
        ["group_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_group_messages_timeline",
        table_name="assistant_group_messages",
    )
    op.drop_table("assistant_group_messages")
    op.drop_index(
        "ix_assistant_group_members_group", table_name="assistant_group_members"
    )
    op.drop_table("assistant_group_members")
    op.drop_index("ix_assistant_groups_owner", table_name="assistant_groups")
    op.drop_table("assistant_groups")
