"""Remove obsolete unviewed schedule badges without changing Bot state."""

from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE assistant_specialists
        SET payload = (payload::jsonb - 'routinePausedReason')::json
        WHERE payload::jsonb->'routinePausedReason'->>'reason' = 'unviewed'
    """)


def downgrade() -> None:
    pass
