"""Assistant vault items: masked metadata only, secrets stay in the credential store."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assistant_vault_items",
        sa.Column("item_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("reference", sa.String(128), nullable=False),
        sa.Column("last4", sa.String(8), nullable=True),
        sa.Column("has_value", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "title", name="uq_assistant_vault_title"
        ),
    )
    op.create_index(
        "ix_assistant_vault_owner",
        "assistant_vault_items",
        ["tenant_id", "user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_vault_owner", table_name="assistant_vault_items")
    op.drop_table("assistant_vault_items")
