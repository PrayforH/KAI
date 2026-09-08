"""Immutable team-space collaboration models."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SharingModel(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)


class SpaceRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    CONTRIBUTOR = "contributor"
    VIEWER = "viewer"


class AgentScope(StrEnum):
    PERSONAL = "personal"
    WORKSPACE = "workspace"


class WorkspaceAgentStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class AgentPermission(StrEnum):
    VIEW = "view"
    CHAT = "chat"
    EDIT = "edit"
    PUBLISH = "publish"
    MANAGE = "manage"


class GranteeType(StrEnum):
    USER = "user"
    GROUP = "group"
    SPACE_ROLE = "space_role"


class ConnectionMode(StrEnum):
    """Credential resolution for a shared Agent.

    caller_owned resolves MCP credentials by the running user (the platform
    default); service_owned uses workspace-provided shared credentials.
    """

    CALLER_OWNED = "caller_owned"
    SERVICE_OWNED = "service_owned"


class TeamSpace(SharingModel):
    tenant_id: str = Field(alias="tenantId")
    space_id: str = Field(alias="spaceId")
    name: str
    description: str = ""
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")


class TeamSpaceMember(SharingModel):
    tenant_id: str = Field(alias="tenantId")
    space_id: str = Field(alias="spaceId")
    user_id: str = Field(alias="userId")
    role: SpaceRole
    created_at: datetime = Field(alias="createdAt")


class WorkspaceAgent(SharingModel):
    """Stable Agent identity owned by a user (personal) or a team space."""

    tenant_id: str = Field(alias="tenantId")
    agent_id: str = Field(alias="agentId", min_length=1)
    scope: AgentScope
    owner_user_id: str | None = Field(default=None, alias="ownerUserId")
    space_id: str | None = Field(default=None, alias="spaceId")
    name: str = Field(min_length=1)
    display_name: str = Field(alias="displayName", default="")
    description: str = ""
    status: WorkspaceAgentStatus = WorkspaceAgentStatus.ACTIVE
    # The immutable AgentVersion currently released for this Agent. Switching
    # this pointer never changes agent_id.
    current_version: str | None = Field(default=None, alias="currentVersion")
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AgentRelease(SharingModel):
    """A published immutable AgentVersion released into a team space.

    Replaces the legacy shared_agent_versions grant. The underlying
    agent_versions row keeps its (tenant, owner, name, version) identity;
    source_owner_user_id/source_name resolve it for runtime and catalog use.
    """

    tenant_id: str = Field(alias="tenantId")
    space_id: str = Field(alias="spaceId")
    agent_id: str = Field(alias="agentId")
    version: str
    source_owner_user_id: str = Field(alias="sourceOwnerUserId")
    source_name: str = Field(alias="sourceName")
    promoted_by: str = Field(alias="promotedBy")
    runnable_by_viewer: bool = Field(default=True, alias="runnableByViewer")
    connection_mode: ConnectionMode = Field(
        default=ConnectionMode.CALLER_OWNED, alias="connectionMode"
    )
    created_at: datetime = Field(alias="createdAt")


class AgentAcl(SharingModel):
    tenant_id: str = Field(alias="tenantId")
    agent_id: str = Field(alias="agentId")
    grantee_type: GranteeType = Field(alias="granteeType")
    grantee_id: str = Field(alias="granteeId")
    permission: AgentPermission
    granted_by: str = Field(alias="grantedBy")
    created_at: datetime = Field(alias="createdAt")


class UserGroup(SharingModel):
    """Tenant-scoped group used for batch Agent ACL grants."""

    tenant_id: str = Field(alias="tenantId")
    group_id: str = Field(alias="groupId")
    name: str = Field(min_length=1)
    description: str = ""
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")


class GroupMember(SharingModel):
    tenant_id: str = Field(alias="tenantId")
    group_id: str = Field(alias="groupId")
    user_id: str = Field(alias="userId")
    created_at: datetime = Field(alias="createdAt")


class SharedKnowledgeBase(SharingModel):
    tenant_id: str = Field(alias="tenantId")
    space_id: str = Field(alias="spaceId")
    knowledge_base_reference: str = Field(alias="knowledgeBaseReference")
    shared_by: str = Field(alias="sharedBy")
    created_at: datetime = Field(alias="createdAt")
