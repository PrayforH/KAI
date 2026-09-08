"""Add workspace Agent identities, Releases and ACLs.

Revision ID: 0023
Revises: 0022

Introduces the stable workspace Agent model:

- ``workspace_agents``: stable ``agent_id`` identities owned by a user
  (personal) or a team space (workspace).
- ``agent_releases``: immutable versions released into a team space, replacing
  the grant semantics of ``shared_agent_versions`` (which is preserved as a
  legacy table and no longer written by the application).
- ``agent_acls``: explicit per-Agent permission rows on top of space-role
  baselines.
- ``agent_versions.agent_id`` / ``agent_drafts.agent_id``: backfilled identity
  links. Both columns are tightened to NOT NULL after backfill.

The migration is reversible: downgrade drops the new tables and columns and
keeps ``shared_agent_versions`` untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from harness.storage.models import Base

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_AGENT_ID = "concat('agent_', replace(gen_random_uuid()::text, '-', ''))"


def _columns(table: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _create_tables() -> None:
    bind = op.get_bind()
    for name in ("workspace_agents", "agent_releases", "agent_acls"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)


def _add_identity_columns() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("agent_versions") and "agent_id" not in _columns("agent_versions"):
        op.add_column(
            "agent_versions",
            sa.Column("agent_id", sa.String(128), nullable=True),
        )
    if inspector.has_table("agent_drafts"):
        if "agent_id" not in _columns("agent_drafts"):
            op.add_column(
                "agent_drafts",
                sa.Column("agent_id", sa.String(128), nullable=True),
            )
        if "space_id" not in _columns("agent_drafts"):
            op.add_column(
                "agent_drafts",
                sa.Column("space_id", sa.String(128), nullable=True),
            )


def _backfill_personal_agents() -> None:
    """Create one personal identity per (tenant, owner, name) aggregation."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("agent_versions") and not inspector.has_table("agent_drafts"):
        return
    if inspector.has_table("agent_versions"):
        connection.execute(
            sa.text(
                "WITH grouped AS MATERIALIZED ("
                "  SELECT v.tenant_id, v.owner_user_id, v.name "
                "  FROM agent_versions v "
                "  WHERE NOT EXISTS ("
                "    SELECT 1 FROM workspace_agents a "
                "    WHERE a.tenant_id = v.tenant_id AND a.scope = 'personal' "
                "      AND a.owner_user_id = v.owner_user_id AND a.name = v.name"
                "  ) "
                "  GROUP BY v.tenant_id, v.owner_user_id, v.name"
                "), new_agents AS ("
                "  SELECT g.tenant_id, g.owner_user_id, g.name, "
                f"    {_NEW_AGENT_ID} AS agent_id "
                "  FROM grouped g"
                ") "
                "INSERT INTO workspace_agents "
                "(tenant_id, agent_id, scope, owner_user_id, space_id, name, "
                " current_version, status, created_at, updated_at, payload) "
                "SELECT n.tenant_id, n.agent_id, 'personal', n.owner_user_id, NULL, n.name, "
                "       NULL, 'active', NOW(), NOW(), "
                "jsonb_build_object("
                "  'tenantId', n.tenant_id, "
                "  'agentId', n.agent_id, "
                "  'scope', 'personal', "
                "  'ownerUserId', n.owner_user_id, "
                "  'name', n.name, "
                "  'displayName', '', "
                "  'description', '', "
                "  'status', 'active', "
                "  'currentVersion', jsonb 'null', "
                "  'createdBy', n.owner_user_id, "
                "  'createdAt', NOW(), "
                "  'updatedAt', NOW()"
                ")::json "
                "FROM new_agents n"
            )
        )
    if inspector.has_table("agent_drafts"):
        connection.execute(
            sa.text(
                "WITH grouped AS MATERIALIZED ("
                "  SELECT d.tenant_id, d.owner_user_id, d.name "
                "  FROM agent_drafts d "
                "  WHERE NOT EXISTS ("
                "    SELECT 1 FROM workspace_agents a "
                "    WHERE a.tenant_id = d.tenant_id AND a.scope = 'personal' "
                "      AND a.owner_user_id = d.owner_user_id AND a.name = d.name"
                "  ) "
                "  GROUP BY d.tenant_id, d.owner_user_id, d.name"
                "), new_agents AS ("
                "  SELECT g.tenant_id, g.owner_user_id, g.name, "
                f"    {_NEW_AGENT_ID} AS agent_id "
                "  FROM grouped g"
                ") "
                "INSERT INTO workspace_agents "
                "(tenant_id, agent_id, scope, owner_user_id, space_id, name, "
                " current_version, status, created_at, updated_at, payload) "
                "SELECT n.tenant_id, n.agent_id, 'personal', n.owner_user_id, NULL, n.name, "
                "       NULL, 'active', NOW(), NOW(), "
                "jsonb_build_object("
                "  'tenantId', n.tenant_id, "
                "  'agentId', n.agent_id, "
                "  'scope', 'personal', "
                "  'ownerUserId', n.owner_user_id, "
                "  'name', n.name, "
                "  'displayName', '', "
                "  'description', '', "
                "  'status', 'active', "
                "  'currentVersion', jsonb 'null', "
                "  'createdBy', n.owner_user_id, "
                "  'createdAt', NOW(), "
                "  'updatedAt', NOW()"
                ")::json "
                "FROM new_agents n"
            )
        )


def _link_identity_columns() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if inspector.has_table("agent_versions"):
        connection.execute(
            sa.text(
                "UPDATE agent_versions v "
                "SET agent_id = a.agent_id, "
                "    payload = (v.payload::jsonb || "
                "               jsonb_build_object('agent_id', a.agent_id))::json "
                "FROM workspace_agents a "
                "WHERE a.tenant_id = v.tenant_id AND a.scope = 'personal' "
                "  AND a.owner_user_id = v.owner_user_id AND a.name = v.name"
            )
        )
    if inspector.has_table("agent_drafts"):
        connection.execute(
            sa.text(
                "UPDATE agent_drafts d "
                "SET agent_id = a.agent_id, "
                "    payload = (d.payload::jsonb || "
                "               jsonb_build_object('agentId', a.agent_id))::json "
                "FROM workspace_agents a "
                "WHERE a.tenant_id = d.tenant_id AND a.scope = 'personal' "
                "  AND a.owner_user_id = d.owner_user_id AND a.name = d.name"
            )
        )


def _migrate_shared_versions() -> None:
    """Turn legacy shared grants into workspace Agents and Releases."""
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("shared_agent_versions"):
        return
    connection.execute(
        sa.text(
            "WITH new_agents AS ("
            "  SELECT s.tenant_id, "
            f"    {_NEW_AGENT_ID} AS agent_id, "
            "    s.space_id, s.agent_name AS name, MIN(s.created_at) AS first_at, "
            "    MAX(s.created_at) AS last_at, "
            "    (ARRAY_AGG(s.payload->>'sharedBy' ORDER BY s.created_at DESC))[1] "
            "      AS latest_sharer "
            "  FROM shared_agent_versions s "
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM workspace_agents a "
            "    WHERE a.tenant_id = s.tenant_id AND a.scope = 'workspace' "
            "      AND a.space_id = s.space_id AND a.name = s.agent_name"
            "  ) "
            "  GROUP BY s.tenant_id, s.space_id, s.agent_name"
            ") "
            "INSERT INTO workspace_agents "
            "(tenant_id, agent_id, scope, owner_user_id, space_id, name, "
            " current_version, status, created_at, updated_at, payload) "
            "SELECT n.tenant_id, n.agent_id, 'workspace', NULL, n.space_id, n.name, "
            "       NULL, 'active', n.first_at, n.last_at, "
            "jsonb_build_object("
            "  'tenantId', n.tenant_id, "
            "  'agentId', n.agent_id, "
            "  'scope', 'workspace', "
            "  'ownerUserId', jsonb 'null', "
            "  'spaceId', n.space_id, "
            "  'name', n.name, "
            "  'displayName', '', "
            "  'description', '', "
            "  'status', 'active', "
            "  'currentVersion', jsonb 'null', "
            "  'createdBy', n.latest_sharer, "
            "  'createdAt', n.first_at, "
            "  'updatedAt', n.last_at"
            ")::json "
            "FROM new_agents n"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO agent_releases "
            "(tenant_id, space_id, agent_id, version, source_owner_user_id, "
            " source_name, promoted_by, created_at, payload) "
            "SELECT s.tenant_id, s.space_id, a.agent_id, s.agent_version, "
            "       s.agent_owner_user_id, s.agent_name, "
            "       COALESCE(s.payload->>'sharedBy', s.agent_owner_user_id), s.created_at, "
            "       jsonb_build_object("
            "         'tenantId', s.tenant_id, "
            "         'spaceId', s.space_id, "
            "         'agentId', a.agent_id, "
            "         'version', s.agent_version, "
            "         'sourceOwnerUserId', s.agent_owner_user_id, "
            "         'sourceName', s.agent_name, "
            "         'promotedBy', COALESCE(s.payload->>'sharedBy', s.agent_owner_user_id), "
            "         'runnableByViewer', "
            "         COALESCE((s.payload->>'runnableByViewer')::boolean, TRUE), "
            "         'connectionMode', 'caller_owned', "
            "         'createdAt', s.created_at"
            "       )::json "
            "FROM shared_agent_versions s "
            "JOIN workspace_agents a "
            "  ON a.tenant_id = s.tenant_id AND a.scope = 'workspace' "
            " AND a.space_id = s.space_id AND a.name = s.agent_name "
            "ON CONFLICT DO NOTHING"
        )
    )
    # Promote the newest Release as the current published version.
    connection.execute(
        sa.text(
            "UPDATE workspace_agents a "
            "SET current_version = r.version, "
            "    updated_at = r.created_at, "
            "    payload = (a.payload::jsonb || "
            "               jsonb_build_object('currentVersion', r.version))::json "
            "FROM agent_releases r "
            "WHERE r.tenant_id = a.tenant_id AND r.agent_id = a.agent_id "
            "  AND a.scope = 'workspace' "
            "  AND r.created_at = ("
            "    SELECT MAX(r2.created_at) FROM agent_releases r2 "
            "    WHERE r2.tenant_id = a.tenant_id AND r2.agent_id = a.agent_id"
            "  )"
        )
    )


def _tighten_constraints() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column in (("agent_versions", "agent_id"), ("agent_drafts", "agent_id")):
        if inspector.has_table(table) and column in _columns(table):
            op.alter_column(table, column, existing_type=sa.String(128), nullable=False)


def upgrade() -> None:
    _create_tables()
    _add_identity_columns()
    _backfill_personal_agents()
    _link_identity_columns()
    _migrate_shared_versions()
    _tighten_constraints()


def downgrade() -> None:
    bind = op.get_bind()
    for name in ("agent_acls", "agent_releases", "workspace_agents"):
        if sa.inspect(bind).has_table(name):
            Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
    inspector = sa.inspect(bind)
    for table, column in (
        ("agent_versions", "agent_id"),
        ("agent_drafts", "agent_id"),
        ("agent_drafts", "space_id"),
    ):
        if inspector.has_table(table) and column in _columns(table):
            op.drop_column(table, column)
