"""Persist ordinary Feishu messages through the shared personal conversation."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feishu_incoming", sa.Column("binding_id", sa.String(128), nullable=True))
    op.add_column("feishu_incoming", sa.Column("run_id", sa.String(128), nullable=True))
    op.create_foreign_key(
        "fk_feishu_incoming_binding",
        "feishu_incoming",
        "feishu_bindings",
        ["binding_id"],
        ["binding_id"],
    )
    op.create_index(
        "ix_feishu_incoming_chat",
        "feishu_incoming",
        ["binding_id", "status", "created_at", "incoming_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_feishu_incoming_chat", table_name="feishu_incoming")
    op.drop_constraint("fk_feishu_incoming_binding", "feishu_incoming", type_="foreignkey")
    op.drop_column("feishu_incoming", "run_id")
    op.drop_column("feishu_incoming", "binding_id")
