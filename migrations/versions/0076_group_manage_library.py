"""Group management fields and the per-group library.

Adds an optional group description, per-message attachments, the shared group
file library and knowledge-base bindings. Only additive: existing rows keep
their bytes and get an empty attachment list.
"""

import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistant_groups",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "assistant_group_messages",
        sa.Column(
            "attachments",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.create_table(
        "assistant_group_documents",
        sa.Column("document_id", sa.String(128), primary_key=True),
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_group_documents_group",
        "assistant_group_documents",
        ["group_id", "created_at"],
    )
    op.create_table(
        "assistant_group_knowledge_bases",
        sa.Column("group_id", sa.String(128), primary_key=True),
        sa.Column("knowledge_base_reference", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("added_by", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_group_knowledge_bases_group",
        "assistant_group_knowledge_bases",
        ["group_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_group_knowledge_bases_group",
        table_name="assistant_group_knowledge_bases",
    )
    op.drop_table("assistant_group_knowledge_bases")
    op.drop_index(
        "ix_assistant_group_documents_group",
        table_name="assistant_group_documents",
    )
    op.drop_table("assistant_group_documents")
    op.drop_column("assistant_group_messages", "attachments")
    op.drop_column("assistant_groups", "description")
