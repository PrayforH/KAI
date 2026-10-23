"""Explicitly saved project deliverables.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

from harness.storage.models import ProjectResultRow

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    ProjectResultRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ProjectResultRow.__table__.drop(op.get_bind(), checkfirst=True)
    # Older Project DTOs reject unknown payload fields after an API rollback.
    op.execute("UPDATE projects SET payload = (payload::jsonb - 'mcpReferences')::json")
