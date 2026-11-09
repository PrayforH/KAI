"""Identify existing Bot threads without moving their runs or sessions."""

from collections.abc import Sequence

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The stored contact or duty proves ownership. A prefix alone does not.
    op.execute("""
        WITH bot_threads AS (
            SELECT tenant_id, user_id, specialist_id,
                   COALESCE(payload->'config'->>'conversation_thread_id',
                            'assistant_' || specialist_id) AS thread_id
            FROM assistant_specialists
            UNION ALL
            SELECT tenant_id, user_id, payload->>'specialistId',
                   'assistant_' || objective_id
            FROM assistant_objectives
            WHERE payload->>'specialistId' IS NOT NULL
              AND payload->>'managedThreadId' IS NULL
        ), owned_threads AS (
            SELECT tenant_id, user_id, thread_id,
                   CASE WHEN COUNT(DISTINCT specialist_id) = 1
                        THEN MIN(specialist_id) END AS specialist_id
            FROM bot_threads
            GROUP BY tenant_id, user_id, thread_id
        )
        UPDATE agui_thread_bindings AS thread
        SET payload = (
            thread.payload::jsonb ||
            jsonb_build_object('kind', 'bot', 'specialist_id', bot.specialist_id)
        )::json
        FROM owned_threads AS bot
        WHERE thread.tenant_id = bot.tenant_id AND thread.user_id = bot.user_id
          AND thread.thread_id = bot.thread_id AND bot.specialist_id IS NOT NULL
    """)
    op.execute("""
        UPDATE agui_thread_bindings
        SET payload = (payload::jsonb || '{"kind":"bot"}'::jsonb)::json
        WHERE thread_id = 'kai-personal-assistant-v1'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE agui_thread_bindings
        SET payload = (payload::jsonb - 'kind' - 'specialist_id')::json
        WHERE payload::jsonb ? 'kind'
    """)
