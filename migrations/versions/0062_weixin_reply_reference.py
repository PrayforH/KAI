"""Quoted-message mapping for the WeChat channel."""

import sqlalchemy as sa
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weixin_messages", sa.Column("reply_to_key", sa.String(128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("weixin_messages", "reply_to_key")
