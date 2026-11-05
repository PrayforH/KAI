"""Persistent assistant objectives and execution intents.

Revision ID: 0044
Revises: 0043

Freeze the original schema here: importing live ORM tables also pulled in
columns added by 0055 and 0063, breaking a fresh upgrade with duplicate columns.
Existing installations already at later revisions are unchanged.
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        """
        CREATE TABLE IF NOT EXISTS assistant_objectives (
        objective_id VARCHAR(128) NOT NULL,
        tenant_id VARCHAR(128) NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        status VARCHAR(32) NOT NULL,
        revision INTEGER NOT NULL,
        next_check_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        payload JSON NOT NULL,
        PRIMARY KEY (objective_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_assistant_objectives_due
            ON assistant_objectives (status, next_check_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_assistant_objectives_owner
            ON assistant_objectives (tenant_id, user_id, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS assistant_executions (
        execution_id VARCHAR(128) NOT NULL,
        objective_id VARCHAR(128) NOT NULL,
        tenant_id VARCHAR(128) NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        trigger_key VARCHAR(256) NOT NULL,
        status VARCHAR(32) NOT NULL,
        lease_token VARCHAR(128),
        lease_until TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        payload JSON NOT NULL,
        PRIMARY KEY (execution_id),
        CONSTRAINT uq_assistant_execution_trigger UNIQUE (objective_id, trigger_key),
        FOREIGN KEY(objective_id) REFERENCES assistant_objectives (objective_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_assistant_execution_objective
            ON assistant_executions (objective_id, created_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_assistant_execution_work
            ON assistant_executions (status, lease_until)
        """,
        """
        CREATE TABLE IF NOT EXISTS assistant_inbox (
        item_id VARCHAR(128) NOT NULL,
        objective_id VARCHAR(128) NOT NULL,
        tenant_id VARCHAR(128) NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        dedup_key VARCHAR(256) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        read_at TIMESTAMP WITH TIME ZONE,
        payload JSON NOT NULL,
        PRIMARY KEY (item_id),
        CONSTRAINT uq_assistant_inbox_notice UNIQUE (tenant_id, user_id, dedup_key),
        FOREIGN KEY(objective_id) REFERENCES assistant_objectives (objective_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_assistant_inbox_owner
            ON assistant_inbox (tenant_id, user_id, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS assistant_objective_events (
        event_id VARCHAR(128) NOT NULL,
        objective_id VARCHAR(128) NOT NULL,
        tenant_id VARCHAR(128) NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        kind VARCHAR(64) NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (event_id),
        FOREIGN KEY(objective_id) REFERENCES assistant_objectives (objective_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_assistant_events_objective
            ON assistant_objective_events (objective_id, created_at)
        """,
    ):
        op.execute(statement)


def downgrade() -> None:
    for name in ("assistant_inbox", "assistant_objective_events",
                 "assistant_executions", "assistant_objectives"):
        op.drop_table(name)
