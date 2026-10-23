"""Project-scoped memories and documents.

Revision ID: 0040
Revises: 0039
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import ProjectDocumentRow, ProjectMemoryRow

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ProjectMemoryRow.__table__.create(op.get_bind(), checkfirst=True)
    ProjectDocumentRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    ProjectDocumentRow.__table__.drop(op.get_bind(), checkfirst=True)
    ProjectMemoryRow.__table__.drop(op.get_bind(), checkfirst=True)
