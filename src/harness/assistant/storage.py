"""Assistant persistence, separate from the execution engine's state tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
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
    parent_objective_id: Mapped[str | None] = mapped_column(String(128))
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
    tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    unknown_usage: Mapped[bool] = mapped_column(Boolean, default=False)
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


class SpecialistCredentialRow(Base):
    """加密凭据存储：值不回读给模型，接口只核对存在性。"""

    __tablename__ = "assistant_specialist_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "reference", name="uq_assistant_credential_ref"),
    )

    credential_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    reference: Mapped[str] = mapped_column(String(128))
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VaultItemRow(Base):
    """助理资料库条目：只存脱敏元数据，密文另存既有凭据表。

    条目归本人所有；按 Bot/用途授权时，才把 credential_ref 写进该 Bot 的 config。
    """

    __tablename__ = "assistant_vault_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "title", name="uq_assistant_vault_title"),
        Index("ix_assistant_vault_owner", "tenant_id", "user_id", "updated_at"),
    )

    item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(120))
    # 指向既有加密凭据表里的那一条；值本身不落在这张表。
    reference: Mapped[str] = mapped_column(String(128))
    last4: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 是否真的存了值：只有标题的条目不必让人以为里面有凭据。
    has_value: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DesktopTurnRow(Base):
    """共享桌面执行权；释放前其它职责只能排队。"""

    __tablename__ = "assistant_desktop_turns"
    __table_args__ = (
        Index(
            "uq_assistant_desktop_active",
            "tenant_id",
            "user_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
        Index("ix_assistant_desktop_turns_objective", "objective_id", "acquired_at"),
    )

    desktop_turn_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    objective_id: Mapped[str] = mapped_column(String(128))
    execution_id: Mapped[str] = mapped_column(String(128))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistantSkillProposalRow(Base):
    """A repeated-work suggestion; dismissal cools it down instead of deleting it."""

    __tablename__ = "assistant_skill_proposals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "fingerprint",
            name="uq_assistant_skill_proposal_fingerprint",
        ),
        Index(
            "ix_assistant_skill_proposal_owner",
            "tenant_id",
            "user_id",
            "updated_at",
        ),
    )

    proposal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class PersonalSkillRow(Base):
    """User-owned Skill content injected into that user's personal-assistant Runs."""

    __tablename__ = "assistant_personal_skills"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "name",
            name="uq_assistant_personal_skill_name",
        ),
        Index(
            "ix_assistant_personal_skill_owner",
            "tenant_id",
            "user_id",
            "updated_at",
        ),
    )

    skill_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AssistantGroupRow(Base):
    """共享会话（群）：名称与接话配置；成员名单单独存。"""

    __tablename__ = "assistant_groups"
    __table_args__ = (
        Index("ix_assistant_groups_owner", "tenant_id", "user_id", "updated_at"),
    )

    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AssistantGroupMemberRow(Base):
    """群成员席位；移除后保留 status=removed，名单查询只返回 active。"""

    __tablename__ = "assistant_group_members"
    __table_args__ = (
        UniqueConstraint("group_id", "member_id", name="uq_assistant_group_member"),
        Index("ix_assistant_group_members_group", "group_id", "status"),
    )

    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    specialist_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssistantGroupReactionRow(Base):
    """当前用户对一条群消息的表情；只在本群内可见。"""

    __tablename__ = "assistant_group_reactions"
    __table_args__ = (
        Index("ix_assistant_group_reactions_message", "group_id", "message_id"),
    )

    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(16), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssistantGroupMessageRow(Base):
    """群消息时间线；回复行先占位，正文在 Run 结束后从事件里补。"""

    __tablename__ = "assistant_group_messages"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "client_message_id", name="uq_assistant_group_client_message"
        ),
        Index("ix_assistant_group_messages_timeline", "group_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    author: Mapped[str] = mapped_column(String(16))
    author_name: Mapped[str] = mapped_column(String(80))
    specialist_id: Mapped[str | None] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str | None] = mapped_column(String(128))
    thread_id: Mapped[str | None] = mapped_column(String(256))
    reply_to_message_id: Mapped[str | None] = mapped_column(String(128))
    client_message_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
