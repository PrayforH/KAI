import asyncio
from datetime import datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from harness.agent_package import MAX_AGENT_BUNDLE_UPLOAD_BYTES
from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
)
from harness.api.schemas import AgentCatalogItem, PublishAgentRequest
from harness.core.models import AgentVersion
from harness.sharing.models import WorkspaceAgent, WorkspaceAgentStatus

router = APIRouter(prefix="/agents", tags=["agents"])


class TransferAgentRequest(BaseModel):
    to_user_id: str | None = Field(default=None, min_length=1)
    to_space_id: str | None = Field(default=None, min_length=1)


class PersonalAgentVersionItem(BaseModel):
    agent_id: str
    name: str
    version: str
    display_name: str
    manifest_hash: str
    package_hash: str | None
    created_at: datetime
    current_version: str | None


def _manifest_mapping(version: AgentVersion) -> dict[str, object]:
    manifest = version.snapshot.get("manifest")
    return cast(dict[str, object], manifest) if isinstance(manifest, dict) else {}


def _is_internal(version: AgentVersion) -> bool:
    metadata = _manifest_mapping(version).get("metadata")
    labels = cast(dict[str, object], metadata).get("labels") if isinstance(metadata, dict) else None
    if not isinstance(labels, dict):
        return False
    return cast(dict[str, object], labels).get("visibility") == "internal"


@router.get("", response_model=list[AgentCatalogItem])
async def list_agents(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[AgentCatalogItem]:
    ensure_permission(identity, "tasks:read")
    versions, personal_agents, shared_agents = await asyncio.gather(
        container.agents.list_published_catalog(identity.tenant_id, identity.user_id),
        container.workspace_agents.list_personal_agents(identity.tenant_id, identity.user_id),
        container.team_spaces.list_accessible_agents(identity.tenant_id, identity.user_id),
    )
    drafts = await container.agent_drafts.list_summaries(identity.tenant_id, identity.user_id)
    internal_names = {draft.name for draft in drafts if draft.parent_draft_id is not None}
    personal_by_name = {agent.name: agent for agent in personal_agents}
    personal: list[AgentCatalogItem] = []
    for version in versions:
        if version.name in internal_names or _is_internal(version):
            continue
        workspace_agent = personal_by_name.get(version.name)
        if workspace_agent is not None and workspace_agent.status is WorkspaceAgentStatus.ARCHIVED:
            continue
        personal.append(
            AgentCatalogItem.from_version(
                version,
                can_edit=True,
                agent_id=(
                    workspace_agent.agent_id if workspace_agent is not None else version.agent_id
                ),
                current_version=(
                    workspace_agent.current_version
                    if workspace_agent is not None
                    else version.version
                ),
            )
        )
    shared: list[AgentCatalogItem] = []
    for entry in shared_agents:
        space = entry.space
        agent = entry.agent
        release = entry.release
        version = entry.version
        if _is_internal(version):
            continue
        shared.append(
            AgentCatalogItem.from_version(
                version,
                scope="team",
                space_id=space.space_id,
                space_name=space.name,
                runnable_by_viewer=release.runnable_by_viewer,
                agent_id=agent.agent_id,
                current_version=agent.current_version,
                connection_mode=release.connection_mode.value,
                can_chat=entry.can_chat,
            )
        )
    items: list[AgentCatalogItem] = [*personal, *shared]
    catalog = await container.capability_catalogs.get_for_user(identity.tenant_id, identity.user_id)
    routes = {route.route_id: route for route in catalog.catalog.model_routes}
    bindings = catalog.catalog.agent_model_bindings
    projected: list[AgentCatalogItem] = []
    for item in items:
        route = routes.get(bindings.get(item.name, ""))
        if route is None or not route.enabled or route.model_type not in {"chat", "vision"}:
            projected.append(item)
            continue
        projected.append(
            item.model_copy(
                update={
                    "model_route": route.route_id,
                    "model": route.models[0],
                    "model_capabilities": route.capabilities,
                }
            )
        )
    return projected


@router.get(
    "/{agent_id}/versions",
    response_model=list[PersonalAgentVersionItem],
)
async def list_personal_agent_versions(
    agent_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[PersonalAgentVersionItem]:
    ensure_permission(identity, "tasks:read")
    agent, versions = await container.team_spaces.list_personal_releases(
        identity.tenant_id, identity.user_id, agent_id
    )
    return [
        PersonalAgentVersionItem(
            agent_id=agent.agent_id,
            name=version.name,
            version=version.version,
            display_name=AgentCatalogItem.from_version(version).display_name,
            manifest_hash=version.manifest_hash,
            package_hash=version.package_hash,
            created_at=version.created_at,
            current_version=agent.current_version,
        )
        for version in versions
    ]


@router.post(
    "/{agent_id}/versions/{version}/promote",
    response_model=PersonalAgentVersionItem,
)
async def promote_personal_agent_version(
    agent_id: str,
    version: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> PersonalAgentVersionItem:
    ensure_permission(identity, "agents:publish")
    agent = await container.team_spaces.promote_personal_release(
        identity.tenant_id, identity.user_id, agent_id, version
    )
    _, versions = await container.team_spaces.list_personal_releases(
        identity.tenant_id, identity.user_id, agent_id
    )
    selected = next(item for item in versions if item.version == version)
    return PersonalAgentVersionItem(
        agent_id=agent.agent_id,
        name=selected.name,
        version=selected.version,
        display_name=AgentCatalogItem.from_version(selected).display_name,
        manifest_hash=selected.manifest_hash,
        package_hash=selected.package_hash,
        created_at=selected.created_at,
        current_version=agent.current_version,
    )


@router.post("", response_model=AgentVersion, status_code=status.HTTP_201_CREATED)
async def publish_agent(
    body: PublishAgentRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AgentVersion:
    ensure_permission(identity, "agents:publish")
    if not container.agents.path_publication_enabled:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "local_path_publication_disabled",
                "message": "production accepts reproducible Agent bundles, not server-local paths",
            },
        )
    return await container.agents.publish(identity.tenant_id, identity.user_id, body.path)


@router.post(
    "/{agent_id}/transfer",
    response_model=WorkspaceAgent,
)
async def transfer_agent(
    agent_id: str,
    body: TransferAgentRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> WorkspaceAgent:
    return await container.team_spaces.transfer_agent(
        identity.tenant_id,
        identity.user_id,
        agent_id,
        to_user_id=body.to_user_id,
        to_space_id=body.to_space_id,
    )


@router.post(
    "/bundles",
    response_model=AgentVersion,
    status_code=status.HTTP_201_CREATED,
)
async def publish_agent_bundle(
    request: Request,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AgentVersion:
    ensure_permission(identity, "agents:publish")
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/zip":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "code": "agent_bundle_media_type_invalid",
                "message": "Agent bundles must use Content-Type application/zip",
            },
        )
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError:
            content_length = -1
        if content_length < 0 or content_length > MAX_AGENT_BUNDLE_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "agent_bundle_too_large",
                    "message": (
                        "Agent bundle exceeds maximum size of "
                        f"{MAX_AGENT_BUNDLE_UPLOAD_BYTES} bytes"
                    ),
                },
            )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_AGENT_BUNDLE_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={
                    "code": "agent_bundle_too_large",
                    "message": (
                        "Agent bundle exceeds maximum size of "
                        f"{MAX_AGENT_BUNDLE_UPLOAD_BYTES} bytes"
                    ),
                },
            )
    return await container.agents.publish_bundle(
        identity.tenant_id, identity.user_id, bytes(content)
    )
