"""Index durable provider context observations by Session and event type.

Revision ID: 0026
Revises: 0025
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_run_events_session_type_timestamp"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON run_events (
            tenant_id,
            (payload ->> 'session_id'),
            (payload ->> 'type'),
            timestamp DESC
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
