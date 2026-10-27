"""Persist owner computers separately from disposable Run sandbox leases."""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_computers",
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(128), primary_key=True),
        sa.Column("computer_id", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("image", sa.String(512), nullable=False),
        sa.Column("volume_ref", sa.String(128), nullable=False),
        sa.Column("volume_ready", sa.Boolean(), nullable=False),
        sa.Column("provider_ref", sa.String(128)),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_active_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("epoch >= 0 AND revision >= 1", name="ck_assistant_computer_version"),
        sa.CheckConstraint(
            "state IN ('registered','starting','idle','running','recovering','failed')",
            name="ck_assistant_computer_state",
        ),
        sa.CheckConstraint(
            "state NOT IN ('idle','running') OR provider_ref IS NOT NULL",
            name="ck_assistant_computer_attached",
        ),
    )
    op.create_index("uq_assistant_computer_id", "assistant_computers", ["computer_id"], unique=True)
    op.create_index(
        "uq_assistant_computer_volume",
        "assistant_computers",
        ["provider", "volume_ref"],
        unique=True,
    )
    op.create_index(
        "uq_assistant_computer_provider_ref",
        "assistant_computers",
        ["provider", "provider_ref"],
        unique=True,
    )
    op.create_table(
        "computer_execution_leases",
        sa.Column("lease_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("computer_id", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(160), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(128)),
        sa.Column("session_id", sa.String(128)),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["assistant_computers.tenant_id", "assistant_computers.user_id"],
        ),
        sa.CheckConstraint("epoch >= 1", name="ck_computer_lease_epoch"),
        sa.CheckConstraint(
            "state IN ('active','released','expired')", name="ck_computer_lease_state"
        ),
        sa.CheckConstraint(
            "purpose IN ('start','execute','recover')", name="ck_computer_lease_purpose"
        ),
        sa.CheckConstraint(
            "(purpose = 'execute' AND run_id IS NOT NULL AND session_id IS NOT NULL) OR "
            "(purpose != 'execute' AND run_id IS NULL AND session_id IS NULL)",
            name="ck_computer_lease_run",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_computer_lease_lifetime"),
    )
    op.create_index(
        "uq_computer_lease_epoch",
        "computer_execution_leases",
        ["tenant_id", "user_id", "epoch"],
        unique=True,
    )
    op.create_index(
        "ix_computer_lease_expiry", "computer_execution_leases", ["state", "expires_at"]
    )
    op.create_index(
        "uq_computer_active_holder",
        "computer_execution_leases",
        ["tenant_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("computer_execution_leases")
    op.drop_table("assistant_computers")
