"""Persist reusable Skill proposals and the user's enabled personal Skills."""

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_skill_proposals",
        sa.Column("proposal_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "fingerprint",
            name="uq_assistant_skill_proposal_fingerprint",
        ),
    )
    op.create_index(
        "ix_assistant_skill_proposal_owner",
        "assistant_skill_proposals",
        ["tenant_id", "user_id", "updated_at"],
    )

    op.create_table(
        "assistant_personal_skills",
        sa.Column("skill_id", sa.String(128), primary_key=True),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "name",
            name="uq_assistant_personal_skill_name",
        ),
    )
    op.create_index(
        "ix_assistant_personal_skill_owner",
        "assistant_personal_skills",
        ["tenant_id", "user_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assistant_personal_skill_owner", table_name="assistant_personal_skills"
    )
    op.drop_table("assistant_personal_skills")
    op.drop_index(
        "ix_assistant_skill_proposal_owner", table_name="assistant_skill_proposals"
    )
    op.drop_table("assistant_skill_proposals")
