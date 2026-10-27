"""Desktop control ownership is independent of execution leases."""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "computer_desktop_leases",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("computer_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("owner", sa.String(length=160), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["assistant_computers.tenant_id", "assistant_computers.user_id"],
        ),
        sa.PrimaryKeyConstraint("tenant_id", "user_id"),
        sa.CheckConstraint(
            "state IN ('observe','agent','human','takeover_pending')",
            name="ck_computer_desktop_state",
        ),
        sa.CheckConstraint("epoch >= 0 AND revision >= 1", name="ck_computer_desktop_version"),
        sa.CheckConstraint(
            "(state IN ('agent','human','takeover_pending')) = (expires_at IS NOT NULL)",
            name="ck_computer_desktop_ttl",
        ),
    )


def downgrade() -> None:
    op.drop_table("computer_desktop_leases")
