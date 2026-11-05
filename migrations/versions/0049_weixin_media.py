"""Add encrypted Weixin media references and idempotent prepared inputs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "weixin_messages",
        sa.Column("media_ciphertext", sa.String, nullable=False, server_default=""),
    )
    op.add_column("weixin_messages", sa.Column("prepared", postgresql.JSONB))


def downgrade() -> None:
    op.drop_column("weixin_messages", "prepared")
    op.drop_column("weixin_messages", "media_ciphertext")
