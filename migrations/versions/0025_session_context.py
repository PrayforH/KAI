"""Add monotonic Session context state and immutable Digests.

Revision ID: 0025
Revises: 0024
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import Base

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in ("session_context_state", "session_context_digests"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("session_context_digests", "session_context_state"):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
