"""Retain verified previous images for recovery of delayed old instances."""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_computers",
        sa.Column("retired_images", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM assistant_computers "
            "WHERE json_array_length(retired_images) > 0)"
        )
    ):
        raise RuntimeError("computer image history must be preserved; refusing schema downgrade")
    op.drop_column("assistant_computers", "retired_images")
