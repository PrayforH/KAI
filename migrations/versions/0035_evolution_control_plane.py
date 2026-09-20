"""Owner-scoped evolution aggregates; additive and safe for older workers.

Revision ID: 0035
Revises: 0034
"""
from alembic import op

from harness.storage.models import EvolutionJobRow

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    EvolutionJobRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    EvolutionJobRow.__table__.drop(op.get_bind(), checkfirst=True)
