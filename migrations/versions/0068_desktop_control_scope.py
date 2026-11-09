"""Scope input ownership to the legacy desktop or one Bot desktop."""

import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

TABLE = "computer_desktop_leases"
PRIMARY_KEY = "computer_desktop_leases_pkey"


def upgrade() -> None:
    op.add_column(
        TABLE, sa.Column("desktop_id", sa.String(64), nullable=False, server_default="vm")
    )
    op.drop_constraint(PRIMARY_KEY, TABLE, type_="primary")
    op.create_primary_key(PRIMARY_KEY, TABLE, ["tenant_id", "user_id", "desktop_id"])


def downgrade() -> None:
    op.execute("DELETE FROM computer_desktop_leases WHERE desktop_id <> 'vm'")
    op.drop_constraint(PRIMARY_KEY, TABLE, type_="primary")
    op.create_primary_key(PRIMARY_KEY, TABLE, ["tenant_id", "user_id"])
    op.drop_column(TABLE, "desktop_id")
