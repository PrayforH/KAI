"""Durable Feishu bindings, events, deliveries and one-time card actions."""

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE feishu_bindings (
    binding_id VARCHAR(128) NOT NULL,
    app_id VARCHAR(128) NOT NULL,
    tenant_key VARCHAR(128) NOT NULL,
    open_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    notifications BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (binding_id)
)
""")
    op.execute("""
CREATE UNIQUE INDEX uq_feishu_live_actor ON feishu_bindings (app_id, tenant_key, open_id)
WHERE revoked_at IS NULL
""")
    op.execute("""
CREATE UNIQUE INDEX uq_feishu_live_owner ON feishu_bindings (app_id, tenant_id, user_id)
WHERE revoked_at IS NULL
""")
    op.execute("""
CREATE TABLE feishu_binding_codes (
    digest VARCHAR(64) NOT NULL,
    app_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (digest)
)
""")
    op.execute("""
CREATE INDEX ix_feishu_code_owner ON feishu_binding_codes (app_id, tenant_id, user_id)
""")
    op.execute("""
CREATE TABLE feishu_incoming (
    incoming_id VARCHAR(128) NOT NULL,
    app_id VARCHAR(128) NOT NULL,
    actor_key VARCHAR(64) NOT NULL,
    event_key VARCHAR(256) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempts INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
    finished_at TIMESTAMP WITH TIME ZONE,
    error_code VARCHAR(64),
    PRIMARY KEY (incoming_id),
    CONSTRAINT uq_feishu_incoming_key UNIQUE (app_id, event_key)
)
""")
    op.execute("""
CREATE INDEX ix_feishu_incoming_actor ON feishu_incoming (app_id, actor_key, created_at)
""")
    op.execute("""
CREATE INDEX ix_feishu_incoming_work ON feishu_incoming (app_id, status, next_attempt_at)
""")
    op.execute("""
CREATE TABLE feishu_deliveries (
    delivery_id VARCHAR(128) NOT NULL,
    app_id VARCHAR(128) NOT NULL,
    binding_id VARCHAR(128),
    actor JSON NOT NULL,
    dedup_key VARCHAR(256) NOT NULL,
    payload JSON NOT NULL,
    objective_id VARCHAR(128),
    fingerprint VARCHAR(64),
    category VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    attempts INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
    lease_token VARCHAR(128),
    lease_until TIMESTAMP WITH TIME ZONE,
    first_attempt_at TIMESTAMP WITH TIME ZONE,
    message_id VARCHAR(128),
    error_code VARCHAR(64),
    PRIMARY KEY (delivery_id),
    CONSTRAINT uq_feishu_delivery_key UNIQUE (app_id, dedup_key),
    FOREIGN KEY(binding_id) REFERENCES feishu_bindings (binding_id),
    FOREIGN KEY(objective_id) REFERENCES assistant_objectives (objective_id)
)
""")
    op.execute("""
CREATE INDEX ix_feishu_delivery_work ON feishu_deliveries (app_id, status, next_attempt_at)
""")
    op.execute("""
CREATE TABLE feishu_card_actions (
    digest VARCHAR(64) NOT NULL,
    binding_id VARCHAR(128) NOT NULL,
    objective_id VARCHAR(128),
    expected_revision INTEGER,
    action VARCHAR(32) NOT NULL,
    payload JSON NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (digest),
    FOREIGN KEY(binding_id) REFERENCES feishu_bindings (binding_id),
    FOREIGN KEY(objective_id) REFERENCES assistant_objectives (objective_id)
)
""")
    op.execute("""
CREATE TABLE feishu_receivers (
    app_id VARCHAR(128) NOT NULL,
    instance_id VARCHAR(128) NOT NULL,
    state VARCHAR(32) NOT NULL,
    heartbeat_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_event_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (app_id)
)
""")


def downgrade() -> None:
    op.drop_table("feishu_receivers")
    op.drop_table("feishu_card_actions")
    op.drop_table("feishu_deliveries")
    op.drop_table("feishu_incoming")
    op.drop_table("feishu_binding_codes")
    op.drop_table("feishu_bindings")
