"""Durable channel identity, inbox, outbox and one-time card authorizations."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from harness.storage.models import Base


class BindingRow(Base):
    __tablename__ = "feishu_bindings"
    __table_args__ = (
        # Revoked bindings stay addressable by old outbox rows, but uniqueness only
        # applies to live identities. Rebinding always creates a new generation.
        Index(
            "uq_feishu_live_owner",
            "app_id",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index(
            "uq_feishu_live_actor",
            "app_id",
            "tenant_key",
            "open_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    binding_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(128))
    tenant_key: Mapped[str] = mapped_column(String(128))
    open_id: Mapped[str] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BindingCodeRow(Base):
    __tablename__ = "feishu_binding_codes"
    __table_args__ = (Index("ix_feishu_code_owner", "app_id", "tenant_id", "user_id"),)
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncomingRow(Base):
    __tablename__ = "feishu_incoming"
    __table_args__ = (
        UniqueConstraint("app_id", "event_key", name="uq_feishu_incoming_key"),
        Index("ix_feishu_incoming_work", "app_id", "status", "next_attempt_at"),
        Index("ix_feishu_incoming_actor", "app_id", "actor_key", "created_at"),
        Index("ix_feishu_incoming_chat", "binding_id", "status", "created_at", "incoming_id"),
    )
    incoming_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(128))
    actor_key: Mapped[str] = mapped_column(String(64))
    event_key: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    binding_id: Mapped[str | None] = mapped_column(ForeignKey("feishu_bindings.binding_id"))
    run_id: Mapped[str | None] = mapped_column(String(128))


class DeliveryRow(Base):
    __tablename__ = "feishu_deliveries"
    __table_args__ = (
        UniqueConstraint("app_id", "dedup_key", name="uq_feishu_delivery_key"),
        Index("ix_feishu_delivery_work", "app_id", "status", "next_attempt_at"),
    )
    delivery_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(128))
    binding_id: Mapped[str | None] = mapped_column(ForeignKey("feishu_bindings.binding_id"))
    # A non-bound reply can only contain generic help or binding failure, never user data.
    actor: Mapped[dict[str, Any]] = mapped_column(JSON)
    dedup_key: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    objective_id: Mapped[str | None] = mapped_column(
        ForeignKey("assistant_objectives.objective_id")
    )
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32), default="reply")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))


class CardActionRow(Base):
    __tablename__ = "feishu_card_actions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    binding_id: Mapped[str] = mapped_column(ForeignKey("feishu_bindings.binding_id"))
    objective_id: Mapped[str | None] = mapped_column(
        ForeignKey("assistant_objectives.objective_id")
    )
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReceiverRow(Base):
    __tablename__ = "feishu_receivers"
    app_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    configuration_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ConnectionRow(Base):
    __tablename__ = "feishu_connections"
    connection_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    app_id: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean)
    revision: Mapped[int] = mapped_column(Integer)
    ciphertext: Mapped[str] = mapped_column(String)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_by: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
