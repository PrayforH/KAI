"""Manage the deployment's Feishu connection without changing channel identities."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feishu_connections",
        sa.Column("connection_id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("app_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("ciphertext", sa.String, nullable=False),
        sa.Column("options", sa.JSON, nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "feishu_receivers",
        sa.Column("configuration_revision", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("feishu_receivers", "configuration_revision")
    op.drop_table("feishu_connections")
