"""Durable UI snapshots, revision CAS and action admission."""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None
_metadata = sa.MetaData()


def _key(name: str) -> sa.Column[str]:
    return sa.Column(name, sa.String(128), primary_key=True)


# Freeze the schema in this revision: future ORM changes must not change the
# meaning of a migration already deployed. 0001's legacy create_all may have
# created these tables on a fresh database, hence checkfirst below.
_TABLES = (
    sa.Table(
        "ui_publications",
        _metadata,
        _key("tenant_id"),
        _key("run_id"),
        sa.Column("payload", sa.JSON, nullable=False),
    ),
    sa.Table(
        "ui_presentations",
        _metadata,
        _key("tenant_id"),
        _key("presentation_id"),
        sa.Column("run_id", sa.String(128), nullable=False, index=True),
        sa.Column("current_revision", sa.Integer, nullable=False),
    ),
    sa.Table(
        "ui_presentation_revisions",
        _metadata,
        _key("tenant_id"),
        _key("presentation_id"),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    ),
    sa.Table(
        "ui_view_states",
        _metadata,
        _key("tenant_id"),
        _key("user_id"),
        _key("presentation_id"),
        sa.Column("revision", sa.Integer, primary_key=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
    ),
    sa.Table(
        "ui_action_requests",
        _metadata,
        _key("tenant_id"),
        _key("request_id"),
        sa.Column("actor_user_id", sa.String(128), nullable=False),
        sa.Column("presentation_id", sa.String(128), nullable=False),
        sa.Column("action_id", sa.String(160), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("fencing_token", sa.Integer, nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "presentation_id",
            "action_id",
            "idempotency_key",
            name="uq_ui_action_idempotency",
        ),
        sa.Index("ix_ui_action_pending", "status", "lease_until"),
    ),
)


def upgrade() -> None:
    for table in _TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    for table in reversed(_TABLES):
        table.drop(op.get_bind(), checkfirst=True)
