"""Persist fixed targets for private-channel execution controls."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("weixin_messages", sa.Column("control", postgresql.JSONB(none_as_null=True)))


def downgrade() -> None:
    op.drop_column("weixin_messages", "control")
