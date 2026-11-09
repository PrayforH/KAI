"""Desktop sessions: one row per Bot desktop, leases keyed by desktop."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "computer_desktops",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("specialist_id", sa.String(128), primary_key=True),
        sa.Column("desktop_id", sa.String(64), nullable=False),
        sa.Column("computer_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("display", sa.String(16), nullable=True),
        sa.Column("unix_user", sa.String(32), nullable=True),
        sa.Column("vnc_socket", sa.String(200), nullable=True),
        sa.Column("profile_ref", sa.String(200), nullable=True),
        sa.Column("workspace_ref", sa.String(200), nullable=True),
        sa.Column("image", sa.String(512), nullable=True),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["assistant_computers.tenant_id", "assistant_computers.user_id"],
        ),
        sa.CheckConstraint(
            "state IN ('created','starting','idle','busy','stopped','failed')",
            name="ck_computer_desktops_state",
        ),
        sa.CheckConstraint(
            "epoch >= 0 AND revision >= 1", name="ck_computer_desktops_version"
        ),
    )
    op.create_index("uq_computer_desktops_id", "computer_desktops", ["desktop_id"], unique=True)
    op.create_index(
        "uq_computer_desktops_display",
        "computer_desktops",
        ["tenant_id", "user_id", "display"],
        unique=True,
        postgresql_where=sa.text("state IN ('starting','idle','busy')"),
    )

    op.add_column(
        "computer_execution_leases",
        sa.Column("desktop_id", sa.String(64), nullable=False, server_default="vm"),
    )
    op.drop_index("uq_computer_active_holder", table_name="computer_execution_leases")
    op.drop_index("uq_computer_lease_epoch", table_name="computer_execution_leases")
    op.create_index(
        "uq_computer_active_holder",
        "computer_execution_leases",
        ["tenant_id", "user_id", "desktop_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "uq_computer_lease_epoch",
        "computer_execution_leases",
        ["tenant_id", "user_id", "desktop_id", "epoch"],
        unique=True,
    )
    op.create_index(
        "fk_computer_lease_desktop",
        "computer_execution_leases",
        ["tenant_id", "user_id", "desktop_id"],
    )


def downgrade() -> None:
    op.drop_index("fk_computer_lease_desktop", table_name="computer_execution_leases")
    op.drop_index("uq_computer_lease_epoch", table_name="computer_execution_leases")
    op.drop_index("uq_computer_active_holder", table_name="computer_execution_leases")
    op.create_index(
        "uq_computer_active_holder",
        "computer_execution_leases",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "uq_computer_lease_epoch",
        "computer_execution_leases",
        ["tenant_id", "user_id", "epoch"],
        unique=True,
    )
    op.drop_column("computer_execution_leases", "desktop_id")
    op.drop_index("uq_computer_desktops_display", table_name="computer_desktops")
    op.drop_index("uq_computer_desktops_id", table_name="computer_desktops")
    op.drop_table("computer_desktops")
