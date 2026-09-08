from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
    require_owned_session,
)
from harness.api.schemas import CreateSessionRequest
from harness.context.models import SessionContextDigest, SessionContextOverview
from harness.context.window import context_window_view
from harness.core.errors import ConflictError
from harness.core.models import Session
from harness.deployments.models import EnvironmentName

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Session:
    ensure_permission(identity, "tasks:write")
    resolved_owner = body.agent_owner_user_id or identity.user_id
    team_ids: tuple[str, ...] = ()
    connection_mode = "caller_owned"
    if body.space_id is not None:
        if body.agent_version is None:
            raise ConflictError("shared Agents require an immutable agent_version")
        release = await container.team_spaces.require_agent_access(
            identity.tenant_id,
            identity.user_id,
            body.space_id,
            resolved_owner,
            body.agent_name,
            body.agent_version,
        )
        team_ids = (body.space_id,)
        connection_mode = release.connection_mode.value
    elif resolved_owner != identity.user_id:
        raise ConflictError("agent_owner_user_id requires a team space grant")
    return await container.sessions.create(
        identity.tenant_id,
        identity.user_id,
        body.agent_name,
        body.agent_version,
        environment=(EnvironmentName(body.environment) if body.environment else None),
        team_ids=team_ids,
        agent_owner_user_id=resolved_owner,
        connection_mode=connection_mode,
    )


@router.get("/{session_id}/context", response_model=SessionContextOverview)
async def get_session_context(
    session_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    before_version: Annotated[int | None, Query(ge=2)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> SessionContextOverview:
    ensure_permission(identity, "tasks:read")
    session = await require_owned_session(container, identity, session_id)
    overview = await container.context.overview(
        identity.tenant_id,
        session.user_id,
        session.session_id,
        before_version=before_version,
        limit=limit,
    )
    window_event = await container.events.latest_for_session_types(
        identity.tenant_id,
        session.session_id,
        ("context.window.observed", "context.window.unavailable"),
    )
    window, window_status = context_window_view(window_event)
    return overview.model_copy(
        update={
            "window": window,
            "window_status": window_status,
        }
    )


@router.get(
    "/{session_id}/context/digests/{digest_id}",
    response_model=SessionContextDigest,
)
async def get_session_context_digest(
    session_id: str,
    digest_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> SessionContextDigest:
    ensure_permission(identity, "tasks:read")
    session = await require_owned_session(container, identity, session_id)
    return await container.context.digest(
        identity.tenant_id,
        session.user_id,
        session.session_id,
        digest_id,
    )
