"""Add scoped API integration keys.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 already creates current metadata during fresh installations.
    if sa.inspect(op.get_bind()).has_table("api_access_keys"):
        return
    op.create_table(
        "api_access_keys",
        sa.Column("key_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_access_keys_tenant_id", "api_access_keys", ["tenant_id"])
    op.create_index("ix_api_access_keys_user_id", "api_access_keys", ["user_id"])
    op.create_index("ix_api_access_keys_token_hash", "api_access_keys", ["token_hash"])
    op.create_index("ix_api_access_keys_revoked_at", "api_access_keys", ["revoked_at"])
    op.create_index(
        "ix_api_access_keys_tenant_created",
        "api_access_keys",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("api_access_keys")
