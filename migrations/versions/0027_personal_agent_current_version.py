"""Backfill the current immutable version of personal Agents.

Revision ID: 0027
Revises: 0026

Migration 0023 introduced the stable personal Agent identity but deliberately
left ``current_version`` empty. The product now exposes version history and an
explicit rollback pointer, so existing identities need one deterministic
current Release. New publications maintain the pointer in application code.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH latest AS (
            SELECT DISTINCT ON (tenant_id, agent_id)
                tenant_id,
                agent_id,
                version,
                (payload::jsonb ->> 'created_at')::timestamptz AS created_at
            FROM agent_versions
            WHERE agent_id IS NOT NULL
            ORDER BY
                tenant_id,
                agent_id,
                (payload::jsonb ->> 'created_at')::timestamptz DESC,
                version DESC
        )
        UPDATE workspace_agents AS agent
        SET current_version = latest.version,
            updated_at = GREATEST(agent.updated_at, latest.created_at),
            payload = (
                agent.payload::jsonb
                || jsonb_build_object(
                    'currentVersion', latest.version,
                    'updatedAt', GREATEST(agent.updated_at, latest.created_at)
                )
            )::json
        FROM latest
        WHERE agent.tenant_id = latest.tenant_id
          AND agent.agent_id = latest.agent_id
          AND agent.scope = 'personal'
          AND agent.current_version IS NULL
        """
    )


def downgrade() -> None:
    # 0026 already supports current_version. Clearing it would destroy manual
    # rollback choices made after this migration, so the compatible data is
    # intentionally preserved.
    return
