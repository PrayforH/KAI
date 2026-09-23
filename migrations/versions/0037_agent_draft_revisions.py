"""Preserve saved draft revisions atomically with subsequent edits."""
from alembic import op

from harness.storage.models import AgentDraftRevisionRow

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    AgentDraftRevisionRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    AgentDraftRevisionRow.__table__.drop(op.get_bind(), checkfirst=True)
