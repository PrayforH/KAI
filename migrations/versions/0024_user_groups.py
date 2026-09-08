"""Add tenant-scoped user groups for batch Agent ACL grants.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import Base

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("user_groups", "group_members"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("group_members", "user_groups"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
