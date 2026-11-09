"""Keep generated duty execution threads out of the ordinary task list."""

from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE agui_thread_bindings AS binding
        SET payload = (binding.payload::jsonb || '{"kind":"duty"}'::jsonb)::json
        FROM assistant_objectives AS objective
        WHERE binding.tenant_id = objective.tenant_id
          AND binding.user_id = objective.user_id
          AND binding.thread_id = 'assistant_' || objective.objective_id
          AND objective.payload->>'managedThreadId' IS NULL
          AND EXISTS (
              SELECT 1 FROM assistant_executions AS execution
              WHERE execution.tenant_id = binding.tenant_id
                AND execution.user_id = binding.user_id
                AND execution.objective_id = objective.objective_id
                AND execution.payload->>'threadId' = binding.thread_id
          )
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE agui_thread_bindings
        SET payload = (payload::jsonb || jsonb_build_object(
            'kind', CASE WHEN payload->>'specialist_id' IS NULL THEN 'task' ELSE 'bot' END
        ))::json
        WHERE payload->>'kind' = 'duty'
    """)
