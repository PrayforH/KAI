"""Knowledge base member grants (phase 1: per-user viewer/editor).

Revision ID: 0032
Revises: 0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from harness.storage.models import KnowledgeBaseMemberRow

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    KnowledgeBaseMemberRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    KnowledgeBaseMemberRow.__table__.drop(op.get_bind(), checkfirst=True)
