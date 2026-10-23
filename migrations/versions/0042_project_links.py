"""Project-scoped web resources.

Revision ID: 0042
Revises: 0041
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import ProjectLinkRow

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ProjectLinkRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    ProjectLinkRow.__table__.drop(op.get_bind(), checkfirst=True)
