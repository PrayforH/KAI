"""Personal Weixin QR connection and durable text channel; preserve all chat data."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weixin_connections",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("generation", sa.String(64), nullable=False),
        sa.Column("bot_key", sa.String(64)),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("ciphertext", sa.String, nullable=False),
        sa.Column("cursor_ciphertext", sa.String, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_event_at", sa.DateTime(timezone=True)),
        sa.Column("last_reply_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("login_id", sa.String(64)),
        sa.Column("login_status", sa.String(32)),
        sa.Column("login_ciphertext", sa.String, nullable=False),
        sa.Column("login_expires_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "weixin_messages",
        sa.Column("message_id", sa.String(64), primary_key=True),
        sa.Column("message_key", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("generation", sa.String(64), nullable=False),
        sa.Column("text", sa.String, nullable=False),
        sa.Column("context_ciphertext", sa.String, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("run_id", sa.String(128)),
        sa.Column("reply", sa.String),
        sa.Column("error_code", sa.String(64)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("message_key", name="uq_weixin_message_key"),
    )
    op.create_index(
        "ix_weixin_message_work",
        "weixin_messages",
        ["tenant_id", "user_id", "generation", "status"],
    )


def downgrade() -> None:
    op.drop_table("weixin_messages")
    op.drop_table("weixin_connections")
