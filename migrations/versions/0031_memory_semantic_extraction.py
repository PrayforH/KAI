"""Scoped semantic memory, pgvector and durable extraction jobs.

Revision ID: 0031
Revises: 0030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from harness.storage.models import MemoryEmbeddingRow, MemoryExtractionJobRow

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("memory_entries")}
    if "agent_owner_user_id" not in columns:
        op.add_column("memory_entries", sa.Column("agent_owner_user_id", sa.String(128)))
    if "dedup_key" not in columns:
        op.add_column("memory_entries", sa.Column("dedup_key", sa.String(64)))
        op.create_unique_constraint(
            "uq_memory_live_content", "memory_entries", ["tenant_id", "user_id", "dedup_key"]
        )
    # Legacy rows keep NULL keys; the service also checks their canonical content.
    MemoryEmbeddingRow.__table__.create(bind, checkfirst=True)
    MemoryExtractionJobRow.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    op.drop_table("memory_extraction_jobs")
    op.drop_table("memory_embeddings")
    op.drop_constraint("uq_memory_live_content", "memory_entries", type_="unique")
    op.drop_column("memory_entries", "dedup_key")
    op.drop_column("memory_entries", "agent_owner_user_id")
    # Other applications may use vector: never drop the shared extension.
