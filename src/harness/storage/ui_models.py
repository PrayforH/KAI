"""UI aggregates share the platform metadata and migration lifecycle."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from harness.storage.models import Base


class UiPublicationRow(Base):
    __tablename__ = "ui_publications"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class UiPresentationRow(Base):
    __tablename__ = "ui_presentations"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    presentation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    current_revision: Mapped[int] = mapped_column(Integer)


class UiRevisionRow(Base):
    __tablename__ = "ui_presentation_revisions"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    presentation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class UiViewStateRow(Base):
    __tablename__ = "ui_view_states"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    presentation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class UiActionRequestRow(Base):
    __tablename__ = "ui_action_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "presentation_id",
            "action_id",
            "idempotency_key",
            name="uq_ui_action_idempotency",
        ),
        Index("ix_ui_action_pending", "status", "lease_until"),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_user_id: Mapped[str] = mapped_column(String(128))
    presentation_id: Mapped[str] = mapped_column(String(128))
    action_id: Mapped[str] = mapped_column(String(160))
    revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    fencing_token: Mapped[int] = mapped_column(Integer)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
