"""Durable sandbox leases.

Revision ID: 0033
Revises: 0032
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import SandboxLeaseRow

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    SandboxLeaseRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    SandboxLeaseRow.__table__.drop(op.get_bind(), checkfirst=True)
