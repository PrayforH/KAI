"""Authenticated team-space collaboration API."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from pydantic import BaseModel, Field

from harness.api.dependencies import ApiContainer, Identity, get_container, require_identity
from harness.api.schemas import AgentCatalogItem
from harness.auth.models import TenantMember
from harness.core.agent_dependencies import dependencies_from_snapshot
from harness.core.errors import ConflictError, PermissionDeniedError
from harness.sharing.models import (
    AgentAcl,
    AgentPermission,
    AgentRelease,
    ConnectionMode,
    GranteeType,
    SharedKnowledgeBase,
    SpaceRole,
    TeamSpace,
    TeamSpaceMember,
    WorkspaceAgent,
)
from harness.studio.mcp_credential_store import (
    ConfigureMcpCredentialRequest,
    McpCredentialStatus,
    space_credential_owner,
)

router = APIRouter(prefix="/spaces", tags=["team spaces"])


class CreateSpaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)


class SpaceSummary(BaseModel):
    space: TeamSpace
    membership: TeamSpaceMember


class PutSpaceMemberRequest(BaseModel):
    user_id: str = Field(min_length=1)
    role: SpaceRole


class ShareAgentRequest(BaseModel):
    owner_user_id: str | None = None
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    runnable_by_viewer: bool = True
    connection_mode: ConnectionMode = ConnectionMode.CALLER_OWNED
    share_knowledge_references: tuple[str, ...] = ()


class WorkspaceAgentItem(BaseModel):
    agent: WorkspaceAgent
    permissions: tuple[AgentPermission, ...] = ()
    can_view: bool = True
    can_chat: bool = True
    can_edit: bool = False
    can_publish: bool = False
    can_manage: bool = False


class SharedAgentItem(BaseModel):
    release: AgentRelease
    agent: AgentCatalogItem


class SpaceWorkspace(BaseModel):
    """One consistent payload for switching the collaboration workspace UI."""

    summary: SpaceSummary
    members: tuple[TeamSpaceMember, ...]
    directory: tuple[TenantMember, ...]
    agents: tuple[WorkspaceAgentItem, ...]
    releases_by_agent: dict[str, tuple[SharedAgentItem, ...]]
    acls_by_agent: dict[str, tuple[AgentAcl, ...]]
    knowledge: tuple[SharedKnowledgeBase, ...]
    mcp_credentials: tuple[McpCredentialStatus, ...] = ()


class PutAgentAclRequest(BaseModel):
    grantee_type: GranteeType
    grantee_id: str = Field(min_length=1)
    permission: AgentPermission


class ShareKnowledgeRequest(BaseModel):
    reference: str = Field(min_length=1, max_length=128)


_MCP_REFERENCE_PATTERN = r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$"


@router.get(
    "/{space_id}/mcp/credentials",
    response_model=tuple[McpCredentialStatus, ...],
)
async def list_space_mcp_credentials(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> tuple[McpCredentialStatus, ...]:
    await container.team_spaces.require_manage(identity.tenant_id, identity.user_id, space_id)
    return await container.mcp_credentials.list(
        identity.tenant_id, space_credential_owner(space_id)
    )


@router.put(
    "/{space_id}/mcp/{reference}/credentials",
    response_model=McpCredentialStatus,
)
async def configure_space_mcp_credential(
    space_id: str,
    reference: Annotated[str, Path(pattern=_MCP_REFERENCE_PATTERN)],
    body: ConfigureMcpCredentialRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> McpCredentialStatus:
    """Configure workspace-provided (service_owned) MCP credentials.

    Members running a service_owned shared Agent resolve credentials by the
    space, never by their personal store.
    """
    await container.team_spaces.require_manage(identity.tenant_id, identity.user_id, space_id)
    return await container.mcp_credentials.configure(
        identity.tenant_id,
        space_credential_owner(space_id),
        reference,
        body,
    )


@router.delete(
    "/{space_id}/mcp/{reference}/credentials",
    response_model=McpCredentialStatus,
)
async def delete_space_mcp_credential(
    space_id: str,
    reference: Annotated[str, Path(pattern=_MCP_REFERENCE_PATTERN)],
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> McpCredentialStatus:
    await container.team_spaces.require_manage(identity.tenant_id, identity.user_id, space_id)
    await container.mcp_credentials.delete(
        identity.tenant_id,
        space_credential_owner(space_id),
        reference,
    )
    return McpCredentialStatus(reference=reference, configured=False)


def _workspace_item(
    agent: WorkspaceAgent,
    permissions: frozenset[AgentPermission],
) -> WorkspaceAgentItem:
    return WorkspaceAgentItem(
        agent=agent,
        permissions=tuple(sorted(permissions, key=lambda item: item.value)),
        can_view=AgentPermission.VIEW in permissions,
        can_chat=AgentPermission.CHAT in permissions,
        can_edit=AgentPermission.EDIT in permissions,
        can_publish=AgentPermission.PUBLISH in permissions,
        can_manage=AgentPermission.MANAGE in permissions,
    )


@router.get("", response_model=list[SpaceSummary])
async def list_spaces(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[SpaceSummary]:
    spaces = await container.team_spaces.list_for_user(identity.tenant_id, identity.user_id)
    memberships = await asyncio.gather(
        *(
            container.team_spaces.get_for_user(
                identity.tenant_id, identity.user_id, space.space_id
            )
            for space in spaces
        )
    )
    return [
        SpaceSummary(space=space, membership=resolved[1])
        for space, resolved in zip(spaces, memberships, strict=True)
    ]


@router.post("", response_model=SpaceSummary, status_code=status.HTTP_201_CREATED)
async def create_space(
    body: CreateSpaceRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> SpaceSummary:
    space = await container.team_spaces.create(
        identity.tenant_id, identity.user_id, body.name, body.description
    )
    _, membership = await container.team_spaces.get_for_user(
        identity.tenant_id, identity.user_id, space.space_id
    )
    return SpaceSummary(space=space, membership=membership)


@router.get("/{space_id}", response_model=SpaceSummary)
async def get_space(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> SpaceSummary:
    space, membership = await container.team_spaces.get_for_user(
        identity.tenant_id, identity.user_id, space_id
    )
    return SpaceSummary(space=space, membership=membership)


@router.get("/{space_id}/workspace", response_model=SpaceWorkspace)
async def get_space_workspace(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> SpaceWorkspace:
    """Load the visible space in one response instead of issuing 4+2N browser requests."""
    tenant_id = identity.tenant_id
    user_id = identity.user_id
    (space, membership), members, agents, knowledge = await asyncio.gather(
        container.team_spaces.get_for_user(tenant_id, user_id, space_id),
        container.team_spaces.list_members(tenant_id, user_id, space_id),
        container.team_spaces.list_agents(tenant_id, user_id, space_id),
        container.team_spaces.list_knowledge(tenant_id, user_id, space_id),
    )
    can_manage = membership.role in {SpaceRole.OWNER, SpaceRole.ADMIN}
    directory, mcp_credentials = await asyncio.gather(
        container.auth.list_members(tenant_id) if can_manage else asyncio.sleep(0, result=[]),
        container.mcp_credentials.list(tenant_id, space_credential_owner(space_id))
        if can_manage
        else asyncio.sleep(0, result=()),
    )

    async def load_agent(
        agent: WorkspaceAgent,
    ) -> tuple[WorkspaceAgentItem, tuple[SharedAgentItem, ...], tuple[AgentAcl, ...]]:
        permissions, release_rows, acls = await asyncio.gather(
            container.team_spaces.effective_permissions(
                tenant_id, user_id, space_id, agent.agent_id
            ),
            container.team_spaces.list_releases(tenant_id, user_id, space_id, agent.agent_id),
            container.team_spaces.list_acls(tenant_id, user_id, space_id, agent.agent_id),
        )
        releases = tuple(
            SharedAgentItem(
                release=release,
                agent=AgentCatalogItem.from_version(
                    version,
                    scope="team",
                    space_id=space_id,
                    agent_id=agent.agent_id,
                    current_version=agent.current_version,
                    connection_mode=release.connection_mode.value,
                ),
            )
            for release, version in release_rows
        )
        return _workspace_item(agent, permissions), releases, tuple(acls)

    agent_rows = await asyncio.gather(*(load_agent(agent) for agent in agents))
    return SpaceWorkspace(
        summary=SpaceSummary(space=space, membership=membership),
        members=tuple(members),
        directory=tuple(directory),
        agents=tuple(row[0] for row in agent_rows),
        releases_by_agent={
            agent.agent_id: row[1] for agent, row in zip(agents, agent_rows, strict=True)
        },
        acls_by_agent={
            agent.agent_id: row[2] for agent, row in zip(agents, agent_rows, strict=True)
        },
        knowledge=tuple(knowledge),
        mcp_credentials=tuple(mcp_credentials),
    )


@router.get("/{space_id}/members", response_model=list[TeamSpaceMember])
async def list_space_members(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[TeamSpaceMember]:
    return await container.team_spaces.list_members(identity.tenant_id, identity.user_id, space_id)


@router.get("/{space_id}/member-directory", response_model=list[TenantMember])
async def space_member_directory(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[TenantMember]:
    await container.team_spaces.require_manage(identity.tenant_id, identity.user_id, space_id)
    return await container.auth.list_members(identity.tenant_id)


@router.put("/{space_id}/members", response_model=TeamSpaceMember)
async def put_space_member(
    space_id: str,
    body: PutSpaceMemberRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> TeamSpaceMember:
    # A team space can only contain an existing member of the authenticated tenant.
    await container.auth.profile(identity.tenant_id, body.user_id)
    return await container.team_spaces.put_member(
        identity.tenant_id,
        identity.user_id,
        space_id,
        body.user_id,
        body.role,
    )


@router.delete("/{space_id}/members/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_space_member(
    space_id: str,
    target_user_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    await container.team_spaces.remove_member(
        identity.tenant_id, identity.user_id, space_id, target_user_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{space_id}/agents", response_model=list[WorkspaceAgentItem])
async def list_workspace_agents(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[WorkspaceAgentItem]:
    agents = await container.team_spaces.list_agents(identity.tenant_id, identity.user_id, space_id)
    return [
        _workspace_item(
            agent,
            await container.team_spaces.effective_permissions(
                identity.tenant_id, identity.user_id, space_id, agent.agent_id
            ),
        )
        for agent in agents
    ]


@router.post(
    "/{space_id}/agents",
    response_model=SharedAgentItem,
    status_code=status.HTTP_201_CREATED,
)
async def share_agent(
    space_id: str,
    body: ShareAgentRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> SharedAgentItem:
    owner_user_id = body.owner_user_id or identity.user_id
    if owner_user_id != identity.user_id:
        raise PermissionDeniedError("users can only share their own Agents")

    source = await container.agents.get_published(
        identity.tenant_id,
        owner_user_id,
        body.name,
        body.version,
    )
    dependencies = dependencies_from_snapshot(source.snapshot)
    requested_knowledge = set(body.share_knowledge_references)
    unexpected_knowledge = requested_knowledge.difference(dependencies.knowledge_references)
    if unexpected_knowledge:
        raise ConflictError(
            "knowledge dependency is not declared by this Agent: "
            + ", ".join(sorted(unexpected_knowledge))
        )

    catalog = await container.capability_catalogs.get_for_user(
        identity.tenant_id, identity.user_id
    )
    mcp_by_reference = {item.reference: item for item in catalog.catalog.mcp_servers}
    unavailable_mcp = [
        reference
        for reference in dependencies.mcp_references
        if reference not in mcp_by_reference or not mcp_by_reference[reference].enabled
    ]
    if unavailable_mcp:
        raise ConflictError(
            "Agent MCP dependency is unavailable: " + ", ".join(unavailable_mcp)
        )

    shared_knowledge = await container.team_spaces.list_knowledge(
        identity.tenant_id, identity.user_id, space_id
    )
    existing_knowledge = {item.knowledge_base_reference for item in shared_knowledge}
    missing_knowledge = set(dependencies.knowledge_references).difference(
        existing_knowledge, requested_knowledge
    )
    if missing_knowledge:
        raise ConflictError(
            "Agent knowledge dependency is not shared with this space: "
            + ", ".join(sorted(missing_knowledge))
        )

    knowledge_to_share = requested_knowledge.difference(existing_knowledge)
    if knowledge_to_share:
        await container.team_spaces.require_manage(
            identity.tenant_id, identity.user_id, space_id
        )
        await asyncio.gather(
            *(
                container.knowledge.get_base(
                    identity.tenant_id, reference, identity.user_id
                )
                for reference in knowledge_to_share
            )
        )

    if body.connection_mode is ConnectionMode.SERVICE_OWNED:
        credential_statuses = await container.mcp_credentials.list(
            identity.tenant_id, space_credential_owner(space_id)
        )
        configured = {item.reference for item in credential_statuses if item.configured}
        missing_credentials = [
            reference
            for reference in dependencies.mcp_references
            if mcp_by_reference[reference].auth_mode != "none" and reference not in configured
        ]
        if missing_credentials:
            raise ConflictError(
                "workspace MCP credential is not configured: "
                + ", ".join(missing_credentials)
            )

    agent, release = await container.team_spaces.share_agent(
        identity.tenant_id,
        identity.user_id,
        space_id,
        owner_user_id,
        body.name,
        body.version,
        runnable_by_viewer=body.runnable_by_viewer,
        connection_mode=body.connection_mode,
    )
    await container.studio.ensure_workspace_draft_from_source(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        space_id=space_id,
        agent_id=agent.agent_id,
        source_owner_user_id=owner_user_id,
        source_name=body.name,
        source_version=body.version,
    )
    if knowledge_to_share:
        await asyncio.gather(
            *(
                container.team_spaces.share_knowledge(
                    identity.tenant_id, identity.user_id, space_id, reference
                )
                for reference in knowledge_to_share
            )
        )
    _, version = await container.team_spaces.get_release_version(
        identity.tenant_id,
        identity.user_id,
        space_id,
        agent.agent_id,
        release.version,
    )
    return SharedAgentItem(
        release=release,
        agent=AgentCatalogItem.from_version(
            version,
            scope="team",
            space_id=space_id,
            agent_id=agent.agent_id,
            current_version=agent.current_version,
            connection_mode=release.connection_mode.value,
        ),
    )


@router.get("/{space_id}/agents/{agent_id}/releases", response_model=list[SharedAgentItem])
async def list_agent_releases(
    space_id: str,
    agent_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[SharedAgentItem]:
    result: list[SharedAgentItem] = []
    for release, version in await container.team_spaces.list_releases(
        identity.tenant_id, identity.user_id, space_id, agent_id
    ):
        result.append(
            SharedAgentItem(
                release=release,
                agent=AgentCatalogItem.from_version(
                    version,
                    scope="team",
                    space_id=space_id,
                    agent_id=agent_id,
                    current_version=release.version,
                    connection_mode=release.connection_mode.value,
                ),
            )
        )
    return result


@router.post(
    "/{space_id}/agents/{agent_id}/releases/{version}/promote",
    response_model=WorkspaceAgentItem,
)
async def promote_agent_release(
    space_id: str,
    agent_id: str,
    version: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> WorkspaceAgentItem:
    agent = await container.team_spaces.promote_release(
        identity.tenant_id, identity.user_id, space_id, agent_id, version
    )
    permissions = await container.team_spaces.effective_permissions(
        identity.tenant_id, identity.user_id, space_id, agent_id
    )
    return _workspace_item(agent, permissions)


@router.delete(
    "/{space_id}/agents/{agent_id}/releases/{version}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unshare_agent(
    space_id: str,
    agent_id: str,
    version: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    await container.team_spaces.unshare_agent(
        identity.tenant_id,
        identity.user_id,
        space_id,
        agent_id,
        version,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{space_id}/agents/{agent_id}/fork",
    response_model=AgentCatalogItem,
    status_code=status.HTTP_201_CREATED,
)
async def fork_workspace_agent(
    space_id: str,
    agent_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AgentCatalogItem:
    fork = await container.team_spaces.fork_agent(
        identity.tenant_id,
        identity.user_id,
        space_id,
        agent_id,
    )
    return AgentCatalogItem.from_version(fork, scope="personal", can_edit=True)


@router.get("/{space_id}/agents/{agent_id}/acl", response_model=list[AgentAcl])
async def list_agent_acls(
    space_id: str,
    agent_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[AgentAcl]:
    return await container.team_spaces.list_acls(
        identity.tenant_id, identity.user_id, space_id, agent_id
    )


@router.put(
    "/{space_id}/agents/{agent_id}/acl",
    response_model=AgentAcl,
    status_code=status.HTTP_201_CREATED,
)
async def put_agent_acl(
    space_id: str,
    agent_id: str,
    body: PutAgentAclRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AgentAcl:
    return await container.team_spaces.put_acl(
        identity.tenant_id,
        identity.user_id,
        space_id,
        agent_id,
        body.grantee_type,
        body.grantee_id,
        body.permission,
    )


@router.delete(
    "/{space_id}/agents/{agent_id}/acl/{grantee_type}/{grantee_id}/{permission}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agent_acl(
    space_id: str,
    agent_id: str,
    grantee_type: GranteeType,
    grantee_id: str,
    permission: AgentPermission,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    await container.team_spaces.delete_acl(
        identity.tenant_id,
        identity.user_id,
        space_id,
        agent_id,
        grantee_type,
        grantee_id,
        permission,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{space_id}/knowledge", response_model=list[SharedKnowledgeBase])
async def list_shared_knowledge(
    space_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[SharedKnowledgeBase]:
    return await container.team_spaces.list_knowledge(
        identity.tenant_id, identity.user_id, space_id
    )


@router.post(
    "/{space_id}/knowledge",
    response_model=SharedKnowledgeBase,
    status_code=status.HTTP_201_CREATED,
)
async def share_knowledge(
    space_id: str,
    body: ShareKnowledgeRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> SharedKnowledgeBase:
    await container.knowledge.get_base(
        identity.tenant_id,
        body.reference,
        identity.user_id,
    )
    return await container.team_spaces.share_knowledge(
        identity.tenant_id, identity.user_id, space_id, body.reference
    )


@router.delete(
    "/{space_id}/knowledge/{reference}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unshare_knowledge(
    space_id: str,
    reference: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    await container.team_spaces.unshare_knowledge(
        identity.tenant_id, identity.user_id, space_id, reference
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
