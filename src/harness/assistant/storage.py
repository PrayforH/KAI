"""Assistant persistence, separate from the execution engine's state tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from harness.storage.models import Base


class ObjectiveRow(Base):
    __tablename__ = "assistant_objectives"
    __table_args__ = (
        Index("ix_assistant_objectives_owner", "tenant_id", "user_id", "created_at"),
        Index("ix_assistant_objectives_due", "status", "next_check_at"),
        Index(
            "uq_assistant_objective_idempotency",
            "tenant_id",
            "user_id",
            "idempotency_key",
            unique=True,
        ),
    )

    objective_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ObjectiveExecutionRow(Base):
    __tablename__ = "assistant_executions"
    __table_args__ = (
        UniqueConstraint("objective_id", "trigger_key", name="uq_assistant_execution_trigger"),
        Index("ix_assistant_execution_work", "status", "lease_until"),
        Index("ix_assistant_execution_objective", "objective_id", "created_at"),
    )

    execution_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    objective_id: Mapped[str] = mapped_column(ForeignKey("assistant_objectives.objective_id"))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    trigger_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    lease_token: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ObjectiveEventRow(Base):
    __tablename__ = "assistant_objective_events"
    __table_args__ = (Index("ix_assistant_events_objective", "objective_id", "created_at"),)

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    objective_id: Mapped[str] = mapped_column(ForeignKey("assistant_objectives.objective_id"))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssistantInboxRow(Base):
    __tablename__ = "assistant_inbox"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "dedup_key", name="uq_assistant_inbox_notice"),
        Index("ix_assistant_inbox_owner", "tenant_id", "user_id", "created_at"),
    )

    item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    objective_id: Mapped[str] = mapped_column(ForeignKey("assistant_objectives.objective_id"))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    dedup_key: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ManagedTaskRow(Base):
    """托管关联。一个 thread 至多一个主动执行托管者（部分唯一索引约束）。"""

    __tablename__ = "assistant_managed_tasks"
    __table_args__ = (
        Index(
            "uq_assistant_managed_execute",
            "tenant_id",
            "user_id",
            "thread_id",
            unique=True,
            postgresql_where=text("role = 'execute'"),
        ),
        Index("ix_assistant_managed_objective", "objective_id", "created_at"),
    )

    managed_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    objective_id: Mapped[str] = mapped_column(ForeignKey("assistant_objectives.objective_id"))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    thread_id: Mapped[str] = mapped_column(String(128))
    project_id: Mapped[str | None] = mapped_column(String(128))
    depends_on_objective_id: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ObjectiveCheckpointRow(Base):
    __tablename__ = "assistant_checkpoints"
    __table_args__ = (Index("ix_assistant_checkpoints_objective", "objective_id", "created_at"),)

    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    objective_id: Mapped[str] = mapped_column(ForeignKey("assistant_objectives.objective_id"))
    execution_id: Mapped[str] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SpecialistRow(Base):
    """专职子助理。slug 在用户内唯一，用于创建前查重。"""

    __tablename__ = "assistant_specialists"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "slug", name="uq_assistant_specialist_slug"),
        Index("ix_assistant_specialists_owner", "tenant_id", "user_id", "created_at"),
    )

    specialist_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    slug: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SpecialistHandoverRow(Base):
    __tablename__ = "assistant_handovers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "user_id", "message_id", name="uq_assistant_handover_message"
        ),
        Index("ix_assistant_handovers_specialist", "specialist_id", "created_at"),
    )

    handover_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    specialist_id: Mapped[str] = mapped_column(String(128))
    objective_id: Mapped[str | None] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    message_id: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
