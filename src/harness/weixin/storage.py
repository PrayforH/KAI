"""Encrypted channel credentials and durable text inbox/outbox."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.storage.models import Base


class WeixinConnectionRow(Base):
    __tablename__ = "weixin_connections"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    generation: Mapped[str] = mapped_column(String(64))
    bot_key: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ciphertext: Mapped[str] = mapped_column(String, default="")
    cursor_ciphertext: Mapped[str] = mapped_column(String, default="")
    state: Mapped[str] = mapped_column(String(32), default="disconnected")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    login_id: Mapped[str | None] = mapped_column(String(64))
    login_status: Mapped[str | None] = mapped_column(String(32))
    login_ciphertext: Mapped[str] = mapped_column(String, default="")
    login_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WeixinMessageRow(Base):
    __tablename__ = "weixin_messages"
    __table_args__ = (
        UniqueConstraint("message_key", name="uq_weixin_message_key"),
        Index("ix_weixin_message_work", "tenant_id", "user_id", "generation", "status"),
    )
    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_key: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    generation: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String)
    media_ciphertext: Mapped[str] = mapped_column(String, default="")
    prepared: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    control: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    context_ciphertext: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    run_id: Mapped[str | None] = mapped_column(String(128))
    reply: Mapped[str | None] = mapped_column(String)
    # 引用回复：被引用平台消息换算成的内部 run 键（weixin-chat-<sha256>）。
    reply_to_key: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
