"""Add a lightweight immutable Agent catalog projection.

Revision ID: 0028
Revises: 0027

Agent version payloads contain reproducible package files and can be tens of
megabytes. Task navigation only needs release metadata and the manifest. These
columns let catalog reads avoid fetching or parsing the full JSON payload.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 creates current metadata on a fresh database; existing deployments
    # still need each missing projection column before the backfill.
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("agent_versions")
    }
    for column in (
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("manifest_hash", sa.String(64), nullable=True),
        sa.Column("package_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("catalog_manifest", sa.JSON(), nullable=True),
    ):
        if column.name not in existing:
            op.add_column("agent_versions", column)
    op.execute(
        """
        UPDATE agent_versions
        SET status = payload::jsonb ->> 'status',
            manifest_hash = payload::jsonb ->> 'manifest_hash',
            package_hash = payload::jsonb ->> 'package_hash',
            created_at = (payload::jsonb ->> 'created_at')::timestamptz,
            catalog_manifest = COALESCE(
                payload::jsonb -> 'snapshot' -> 'manifest',
                '{}'::jsonb
            )::json
        """
    )
    op.alter_column("agent_versions", "status", nullable=False)
    op.alter_column("agent_versions", "manifest_hash", nullable=False)
    op.alter_column("agent_versions", "created_at", nullable=False)
    op.alter_column("agent_versions", "catalog_manifest", nullable=False)


def downgrade() -> None:
    op.drop_column("agent_versions", "catalog_manifest")
    op.drop_column("agent_versions", "created_at")
    op.drop_column("agent_versions", "package_hash")
    op.drop_column("agent_versions", "manifest_hash")
    op.drop_column("agent_versions", "status")
