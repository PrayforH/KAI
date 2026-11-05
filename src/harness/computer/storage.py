"""Registry and lease history share a locked computer row as their write gate."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from harness.storage.models import Base


class AssistantComputerRow(Base):
    __tablename__ = "assistant_computers"
    __table_args__ = (
        Index("uq_assistant_computer_id", "computer_id", unique=True),
        Index("uq_assistant_computer_volume", "provider", "volume_ref", unique=True),
        Index("uq_assistant_computer_provider_ref", "provider", "provider_ref", unique=True),
        CheckConstraint("epoch >= 0 AND revision >= 1", name="ck_assistant_computer_version"),
        CheckConstraint(
            "state IN ('registered','starting','idle','running','recovering','failed')",
            name="ck_assistant_computer_state",
        ),
        CheckConstraint(
            "state NOT IN ('idle','running') OR provider_ref IS NOT NULL",
            name="ck_assistant_computer_attached",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    computer_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    image: Mapped[str] = mapped_column(String(512))
    retired_images: Mapped[list[str]] = mapped_column(JSON, server_default=text("'[]'"))
    volume_ref: Mapped[str] = mapped_column(String(128))
    volume_ready: Mapped[bool] = mapped_column(Boolean)
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32))
    epoch: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ComputerLeaseRow(Base):
    __tablename__ = "computer_execution_leases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["assistant_computers.tenant_id", "assistant_computers.user_id"],
        ),
        Index("uq_computer_lease_epoch", "tenant_id", "user_id", "epoch", unique=True),
        Index(
            "uq_computer_active_holder",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_computer_lease_expiry", "state", "expires_at"),
        CheckConstraint("epoch >= 1", name="ck_computer_lease_epoch"),
        CheckConstraint(
            "run_fencing_token IS NULL OR (purpose = 'execute' AND run_fencing_token >= 0)",
            name="ck_computer_run_fence",
        ),
        CheckConstraint("state IN ('active','released','expired')", name="ck_computer_lease_state"),
        CheckConstraint(
            "purpose IN ('start','execute','recover')", name="ck_computer_lease_purpose"
        ),
        CheckConstraint(
            "(purpose = 'execute' AND run_id IS NOT NULL AND session_id IS NOT NULL) OR "
            "(purpose != 'execute' AND run_id IS NULL AND session_id IS NULL)",
            name="ck_computer_lease_run",
        ),
        CheckConstraint("expires_at > created_at", name="ck_computer_lease_lifetime"),
    )

    lease_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    computer_id: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(String(16))
    epoch: Mapped[int] = mapped_column(Integer)
    run_fencing_token: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String(128))
    session_id: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ComputerDesktopLeaseRow(Base):
    """One desktop control state per owner computer, separate from execution.

    Ownership of keyboard/pointer is a product decision between the owner and
    their assistant; it fences neither the work disk nor the execution lease.
    """

    __tablename__ = "computer_desktop_leases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["assistant_computers.tenant_id", "assistant_computers.user_id"],
        ),
        CheckConstraint(
            "state IN ('observe','agent','human','takeover_pending')",
            name="ck_computer_desktop_state",
        ),
        CheckConstraint("epoch >= 0 AND revision >= 1", name="ck_computer_desktop_version"),
        CheckConstraint(
            "(state IN ('agent','human','takeover_pending')) = (expires_at IS NOT NULL)",
            name="ck_computer_desktop_ttl",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    computer_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32))
    owner: Mapped[str | None] = mapped_column(String(160))
    epoch: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
