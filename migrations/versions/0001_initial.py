"""Initial Harness persistence schema.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

from alembic import op

from harness.storage.models import Base

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Alembic imports Computer metadata for autogeneration. These tables belong
    # to 0051/0053; creating them here would make a fresh upgrade fail at that
    # step, and the desktop lease's foreign key needs the computer row table.
    tables = [
        table
        for name, table in Base.metadata.tables.items()
        if name
        not in {
            "assistant_computers",
            "computer_execution_leases",
            "computer_desktop_leases",
        }
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
