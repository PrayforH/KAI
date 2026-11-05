"""Encrypted specialist credentials; only references reach models or logs."""

import sqlalchemy as sa
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_specialist_credentials",
        sa.Column("credential_id", sa.String(128), primary_key=True),
        *(
            sa.Column(name, sa.String(128), nullable=False)
            for name in ("tenant_id", "user_id", "reference")
        ),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", "reference", name="uq_assistant_credential_ref"),
    )


def downgrade() -> None:
    op.drop_table("assistant_specialist_credentials")
