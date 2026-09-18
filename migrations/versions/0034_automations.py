"""Durable automation tasks and run records.

Revision ID: 0034
Revises: 0033
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import AutomationRecordRow, AutomationTaskRow

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    AutomationTaskRow.__table__.create(op.get_bind(), checkfirst=True)
    AutomationRecordRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    AutomationRecordRow.__table__.drop(op.get_bind(), checkfirst=True)
    AutomationTaskRow.__table__.drop(op.get_bind(), checkfirst=True)
