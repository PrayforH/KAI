"""Authoritative relational persistence model."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthIdentityRow(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_oauth_identity_subject"),)

    identity_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    subject: Mapped[str] = mapped_column(String(256))
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    provider_email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TenantMembershipRow(Base):
    __tablename__ = "tenant_memberships"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RefreshTokenRow(Base):
    __tablename__ = "refresh_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_hash: Mapped[str | None] = mapped_column(String(64))


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(160), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(256))
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    details: Mapped[dict[str, Any]] = mapped_column(JSON)


class ApiAccessKeyRow(Base):
    __tablename__ = "api_access_keys"
    __table_args__ = (
        Index("ix_api_access_keys_tenant_created", "tenant_id", "created_at"),
    )

    key_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(160))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    permissions: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AgentVersionRow(Base):
    __tablename__ = "agent_versions"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    package_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    catalog_manifest: Mapped[dict[str, Any]] = mapped_column(JSON)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class TeamSpaceRow(Base):
    __tablename__ = "team_spaces"
    __table_args__ = (Index("ix_team_spaces_tenant_created", "tenant_id", "created_at"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class TeamSpaceMemberRow(Base):
    __tablename__ = "team_space_members"
    __table_args__ = (
        Index("ix_team_space_members_user", "tenant_id", "user_id", "space_id"),
        Index("ix_team_space_members_role", "tenant_id", "space_id", "role"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SharedAgentVersionRow(Base):
    __tablename__ = "shared_agent_versions"
    __table_args__ = (
        Index("ix_shared_agent_versions_space", "tenant_id", "space_id", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_owner_user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class WorkspaceAgentRow(Base):
    __tablename__ = "workspace_agents"
    __table_args__ = (
        Index("ix_workspace_agents_tenant_scope_owner", "tenant_id", "scope", "owner_user_id"),
        Index("ix_workspace_agents_tenant_space_name", "tenant_id", "space_id", "name"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16))
    owner_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    space_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    current_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AgentReleaseRow(Base):
    __tablename__ = "agent_releases"
    __table_args__ = (
        Index("ix_agent_releases_space_agent", "tenant_id", "space_id", "agent_id"),
        Index(
            "ix_agent_releases_space_source",
            "tenant_id",
            "space_id",
            "source_owner_user_id",
            "source_name",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_owner_user_id: Mapped[str] = mapped_column(String(128))
    source_name: Mapped[str] = mapped_column(String(128))
    promoted_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AgentAclRow(Base):
    __tablename__ = "agent_acls"
    __table_args__ = (
        Index("ix_agent_acls_agent", "tenant_id", "agent_id"),
        Index("ix_agent_acls_grantee", "tenant_id", "grantee_type", "grantee_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    grantee_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    grantee_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    permission: Mapped[str] = mapped_column(String(16), primary_key=True)
    granted_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class UserGroupRow(Base):
    __tablename__ = "user_groups"
    __table_args__ = (Index("ix_user_groups_tenant_name", "tenant_id", "name"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class GroupMemberRow(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        Index("ix_group_members_group", "tenant_id", "group_id", "user_id"),
        Index("ix_group_members_user", "tenant_id", "user_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SharedKnowledgeBaseRow(Base):
    __tablename__ = "shared_knowledge_bases"
    __table_args__ = (
        Index("ix_shared_knowledge_bases_space", "tenant_id", "space_id", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    space_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    knowledge_base_reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AgentDraftRow(Base):
    __tablename__ = "agent_drafts"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_agent_drafts_revision_positive"),
        CheckConstraint(
            "schema_version >= 1",
            name="ck_agent_drafts_schema_version_positive",
        ),
        Index(
            "ix_agent_drafts_tenant_owner_name",
            "tenant_id",
            "owner_user_id",
            "name",
        ),
        Index(
            "ix_agent_drafts_tenant_owner_updated",
            "tenant_id",
            "owner_user_id",
            "updated_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    draft_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    space_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    name: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class CapabilityCatalogRow(Base):
    __tablename__ = "capability_catalogs"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_capability_catalogs_revision_positive"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    updated_by: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class McpCredentialRow(Base):
    __tablename__ = "mcp_credentials"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_mcp_credentials_revision_positive"),
        Index(
            "ix_mcp_credentials_tenant_owner_updated",
            "tenant_id",
            "owner_user_id",
            "updated_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    key_names: Mapped[list[str]] = mapped_column(JSON)
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PreviewDeploymentRow(Base):
    __tablename__ = "preview_deployments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "requested_by",
            "idempotency_key",
            name="uq_preview_deployment_idempotency",
        ),
        Index(
            "ix_preview_deployments_tenant_created",
            "tenant_id",
            "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    preview_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(128), index=True)
    draft_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EvalDatasetVersionRow(Base):
    __tablename__ = "eval_dataset_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_eval_dataset_version_positive"),
        Index("ix_eval_datasets_tenant_created", "tenant_id", "created_at"),
        Index("ix_eval_datasets_tenant_agent", "tenant_id", "agent_name"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(128), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    required: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EvalRunRow(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "requested_by",
            "idempotency_key",
            name="uq_eval_run_idempotency",
        ),
        CheckConstraint("dataset_version >= 1", name="ck_eval_run_dataset_version_positive"),
        Index("ix_eval_runs_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_eval_runs_agent_version",
            "tenant_id",
            "agent_name",
            "agent_version",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(128), index=True)
    dataset_id: Mapped[str] = mapped_column(String(128), index=True)
    dataset_version: Mapped[int] = mapped_column(Integer)
    agent_name: Mapped[str] = mapped_column(String(128))
    agent_version: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EvalCaseResultRow(Base):
    __tablename__ = "eval_case_results"
    __table_args__ = (Index("ix_eval_case_results_run", "tenant_id", "eval_run_id"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EnvironmentRow(Base):
    __tablename__ = "deployment_environments"
    __table_args__ = (CheckConstraint("revision >= 0", name="ck_environment_revision"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DeploymentSnapshotRow(Base):
    __tablename__ = "deployment_snapshots"
    __table_args__ = (
        Index(
            "ix_deployment_snapshots_agent_created",
            "tenant_id",
            "created_by",
            "agent_name",
            "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    agent_version: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DeploymentRow(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "requested_by",
            "idempotency_key",
            name="uq_deployment_idempotency",
        ),
        Index(
            "ix_deployments_agent_created",
            "tenant_id",
            "requested_by",
            "agent_name",
            "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    requested_by: Mapped[str] = mapped_column(String(128), index=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QualityScoreRow(Base):
    __tablename__ = "quality_scores"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    score_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_name: Mapped[str] = mapped_column(String(128), index=True)
    agent_version: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QualityRuleRow(Base):
    __tablename__ = "quality_alert_rules"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QualityIncidentRow(Base):
    __tablename__ = "quality_alert_incidents"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QualitySyncRow(Base):
    __tablename__ = "quality_sync_jobs"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sync_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QualityDatasetRow(Base):
    __tablename__ = "quality_dataset_projections"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    projection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QuotaPolicyRow(Base):
    __tablename__ = "quota_policies"
    __table_args__ = (Index("ix_quota_policies_tenant_scope", "tenant_id", "scope_key"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(384))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class QuotaCounterRow(Base):
    __tablename__ = "quota_counters"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(384), primary_key=True)
    resource: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    reserved: Mapped[int] = mapped_column(BigInteger)
    committed: Mapped[int] = mapped_column(BigInteger)
    limit_value: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuotaReservationRow(Base):
    __tablename__ = "quota_reservations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_quota_reservation_idempotency",
        ),
        Index("ix_quota_reservations_tenant_state", "tenant_id", "state"),
        Index("ix_quota_reservations_expires", "expires_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reservation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    resource: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class UsageLedgerRow(Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        Index("ix_usage_ledger_tenant_resource", "tenant_id", "resource"),
        Index("ix_usage_ledger_tenant_occurred", "tenant_id", "occurred_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reservation_id: Mapped[str | None] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int | None] = mapped_column(BigInteger)
    cost_state: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class RetentionPolicyRow(Base):
    __tablename__ = "retention_policies"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class LegalHoldRow(Base):
    __tablename__ = "legal_holds"
    __table_args__ = (Index("ix_legal_holds_tenant_active", "tenant_id", "active"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    hold_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean)
    scope_kind: Mapped[str] = mapped_column(String(32))
    subject_id: Mapped[str] = mapped_column(String(256))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DataLifecycleJobRow(Base):
    __tablename__ = "data_lifecycle_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_data_lifecycle_job_idempotency",
        ),
        Index("ix_data_lifecycle_jobs_status", "status", "created_at"),
        Index("ix_data_lifecycle_jobs_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    fencing_token: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReliabilityIncidentRow(Base):
    __tablename__ = "reliability_incidents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "fingerprint",
            name="uq_reliability_incident_fingerprint",
        ),
        Index("ix_reliability_incidents_status", "tenant_id", "status", "updated_at"),
        Index("ix_reliability_incidents_recovery", "kind", "status", "updated_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ReaperActionRow(Base):
    __tablename__ = "reaper_actions"
    __table_args__ = (Index("ix_reaper_actions_tenant_occurred", "tenant_id", "occurred_at"),)

    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128))
    reaper: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[str] = mapped_column(String(256))
    outcome: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class CapacitySnapshotRow(Base):
    __tablename__ = "capacity_snapshots"
    __table_args__ = (Index("ix_capacity_snapshots_captured", "tenant_id", "captured_at"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AgentTriggerRow(Base):
    __tablename__ = "agent_triggers"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_agent_triggers_revision_positive"),
        Index(
            "ix_agent_triggers_tenant_agent",
            "tenant_id",
            "agent_name",
            "created_at",
        ),
    )

    trigger_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True, default="webhook")
    enabled: Mapped[bool] = mapped_column(Boolean, index=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeBaseRow(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_knowledge_bases_revision_positive"),
        Index("ix_knowledge_bases_tenant_updated", "tenant_id", "updated_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeBaseMemberRow(Base):
    __tablename__ = "knowledge_base_members"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_reference",
            "subject_type",
            "subject_id",
            name="uq_knowledge_base_members_subject",
        ),
        Index(
            "ix_knowledge_base_members_tenant_user",
            "tenant_id",
            "subject_id",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    knowledge_base_reference: Mapped[str] = mapped_column(String(128), index=True)
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeSourceRow(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="ck_knowledge_sources_revision_positive"),
        Index("ix_knowledge_sources_tenant_health", "tenant_id", "health"),
        Index(
            "ix_knowledge_sources_tenant_updated",
            "tenant_id",
            "updated_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reference: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    health: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    active_snapshot_id: Mapped[str | None] = mapped_column(String(128), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeSnapshotRow(Base):
    __tablename__ = "knowledge_snapshots"
    __table_args__ = (
        Index(
            "ix_knowledge_snapshots_source_created",
            "tenant_id",
            "source_reference",
            "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_reference: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index(
            "ix_knowledge_chunks_snapshot",
            "tenant_id",
            "snapshot_id",
            "document_id",
            "ordinal",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_reference: Mapped[str] = mapped_column(String(128))
    document_id: Mapped[str] = mapped_column(String(256))
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class KnowledgeSyncRunRow(Base):
    __tablename__ = "knowledge_sync_runs"
    __table_args__ = (
        Index(
            "ix_knowledge_sync_runs_source_created",
            "tenant_id",
            "source_reference",
            "created_at",
        ),
        Index("ix_knowledge_sync_runs_status", "status", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    sync_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_reference: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class CredentialConnectionRow(Base):
    __tablename__ = "credential_connections"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_credential_connections_revision_positive",
        ),
        Index(
            "ix_credential_connections_resource",
            "tenant_id",
            "resource_kind",
            "resource_reference",
        ),
        Index(
            "ix_credential_connections_principal",
            "tenant_id",
            "scope",
            "principal_id",
            "status",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resource_kind: Mapped[str] = mapped_column(String(32))
    resource_reference: Mapped[str] = mapped_column(String(256))
    scope: Mapped[str] = mapped_column(String(32))
    principal_id: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class GovernedPolicyRow(Base):
    __tablename__ = "governed_policies"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_governed_policies_revision_positive",
        ),
        Index("ix_governed_policies_tenant_updated", "tenant_id", "updated_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer)
    published_revision: Mapped[int | None] = mapped_column(Integer)
    published_hash: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class GovernedPolicyPublicationRow(Base):
    __tablename__ = "governed_policy_publications"
    __table_args__ = (
        Index(
            "ix_governed_policy_publications_published",
            "tenant_id",
            "policy_id",
            "published_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SessionRow(Base):
    __tablename__ = "sessions"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SessionContextStateRow(Base):
    __tablename__ = "session_context_state"
    __table_args__ = (
        Index(
            "ix_session_context_state_owner",
            "tenant_id",
            "owner_user_id",
            "updated_at",
        ),
        CheckConstraint(
            "trust_high_watermark IN ('safe', 'sensitive', 'untrusted')",
            name="ck_session_context_state_trust",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    trust_high_watermark: Mapped[str] = mapped_column(String(32))
    latest_digest_id: Mapped[str | None] = mapped_column(String(128))
    latest_digest_version: Mapped[int] = mapped_column(Integer, default=0)
    transcript_checkpoint_hash: Mapped[str | None] = mapped_column(String(71))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class SessionContextDigestRow(Base):
    __tablename__ = "session_context_digests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "digest_id",
            name="uq_session_context_digest_id",
        ),
        Index(
            "ix_session_context_digests_owner",
            "tenant_id",
            "owner_user_id",
            "session_id",
            "version",
        ),
        Index(
            "ix_session_context_digests_checkpoint",
            "tenant_id",
            "session_id",
            "transcript_checkpoint_hash",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    digest_id: Mapped[str] = mapped_column(String(128))
    owner_user_id: Mapped[str] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(71))
    transcript_checkpoint_hash: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "session_id", "idempotency_key", name="uq_run_idempotency"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class EventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "sequence", name="uq_event_sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class OutboxRow(Base):
    __tablename__ = "outbox"

    outbox_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(128), index=True)
    aggregate_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SdkSessionEntryRow(Base):
    __tablename__ = "sdk_session_entries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "session_id",
            "subpath",
            "sequence",
            name="uq_sdk_entry_sequence",
        ),
    )

    entry_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    project_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    subpath: Mapped[str] = mapped_column(String(512), default="")
    sequence: Mapped[int] = mapped_column(Integer)
    entry_uuid: Mapped[str | None] = mapped_column(String(128), index=True)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ApprovalRow(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", "tool_call_id", name="uq_approval_tool_call"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    tool_call_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ArtifactRow(Base):
    __tablename__ = "artifacts"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class InputArtifactRow(Base):
    __tablename__ = "input_artifacts"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    input_artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class UserMemoryRow(Base):
    __tablename__ = "user_memories"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class MemoryEntryRow(Base):
    __tablename__ = "memory_entries"
    __table_args__ = (
        Index(
            "ix_memory_entries_scope_status",
            "tenant_id",
            "user_id",
            "agent_name",
            "status",
            "updated_at",
        ),
        Index("ix_memory_entries_expiry", "status", "expires_at"),
        UniqueConstraint("tenant_id", "user_id", "dedup_key", name="uq_memory_live_content"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    agent_owner_user_id: Mapped[str | None] = mapped_column(String(128))
    dedup_key: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class MemoryEmbeddingRow(Base):
    __tablename__ = "memory_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "user_id", "entry_id"],
            ["memory_entries.tenant_id", "memory_entries.user_id", "memory_entries.entry_id"],
            ondelete="CASCADE",
        ),
    )
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    entry_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(128))
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))


class MemoryExtractionJobRow(Base):
    __tablename__ = "memory_extraction_jobs"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    agent_name: Mapped[str] = mapped_column(String(128))
    agent_owner_user_id: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryConsentRow(Base):
    __tablename__ = "memory_consents"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class MemoryRetentionRow(Base):
    __tablename__ = "memory_retentions"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ThreadFileRow(Base):
    __tablename__ = "thread_files"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    file_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    parent_file_id: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class WorkspaceSnapshotRow(Base):
    __tablename__ = "workspace_snapshots"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AguiThreadBindingRow(Base):
    __tablename__ = "agui_thread_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "session_id", name="uq_agui_binding_session"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
