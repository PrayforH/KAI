"""Authenticated UI snapshots, typed action admission and view state."""

from __future__ import annotations

import hashlib
from typing import Annotated, Any, cast

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
    require_owned_run,
)
from harness.core.errors import NotFoundError
from harness.core.models import ArtifactStatus
from harness.presentation import default_catalog
from harness.presentation.models import Publication
from harness.presentation.schema import MAX_HTML_BYTES, Presentation

router = APIRouter(prefix="/ui", tags=["intelligent-ui"])
Container = Annotated[ApiContainer, Depends(get_container)]
Actor = Annotated[Identity, Depends(require_identity)]

HTML_CONTENT_POLICY = (
    "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src https: data: blob:; font-src data:; "
    "media-src data: blob:; connect-src 'none'; frame-src 'none'; "
    "form-action 'none'; base-uri 'none'; object-src 'none'"
)


class ActionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    values: dict[str, list[str]] = Field(default_factory=dict, max_length=12)


class ViewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    expected_version: int = Field(ge=0)
    state: dict[str, Any]


async def owned(
    container: ApiContainer, actor: Identity, presentation_id: str, revision: int | None = None
) -> tuple[Publication, Presentation]:
    ensure_permission(actor, "tasks:read")
    owner, p = await container.presentations.repository.get(
        actor.tenant_id, presentation_id, revision
    )
    await require_owned_run(container, actor, owner.run_id)
    session = await container.sessions.get(actor.tenant_id, owner.session_id)
    if session.team_ids:
        await container.team_spaces.require_agent_access(
            actor.tenant_id,
            actor.user_id,
            session.team_ids[0],
            session.resolved_agent_owner_user_id,
            session.agent_name,
            session.agent_version,
        )
    return owner, p


@router.get("/capabilities")
async def get_ui_capabilities(
    actor: Actor, container: Container, renderers: str | None = Query(default=None, max_length=2000)
) -> dict[str, Any]:
    ensure_permission(actor, "tasks:read")
    result = default_catalog().describe()
    if renderers is not None:
        supported = set(renderers.split(","))
        renderers_list = cast(list[dict[str, Any]], result["renderers"])
        available = [r for r in renderers_list if r["renderer_version"] in supported]
        result["renderers"] = available
        result["generated_html"] = any(r["kind"] == "html" for r in available)
    return result


@router.get("/presentations/{presentation_id}")
async def get_presentation(
    presentation_id: str,
    actor: Actor,
    container: Container,
    revision: int | None = Query(default=None, ge=1),
    run_id: str | None = Query(default=None, min_length=1, max_length=160),
) -> dict[str, Any]:
    try:
        _, p = await owned(container, actor, presentation_id, revision)
    except NotFoundError:
        if run_id is None:
            raise
        # Older histories can contain a block before its publication was saved.
        # Rebuild only from a terminal run owned by this actor, then recheck the
        # exact requested identity/revision through the normal ownership path.
        run = await require_owned_run(container, actor, run_id)
        if not run.status.is_terminal:
            raise HTTPException(425, "交互工具正在准备，请稍后重试") from None
        await container.presentations.materialize(run)
        _, p = await owned(container, actor, presentation_id, revision)
    view = await container.presentations.repository.view(
        actor.tenant_id, actor.user_id, presentation_id, p.revision
    )
    requests = await container.presentations.repository.requests_for_presentation(
        actor.tenant_id, actor.user_id, presentation_id
    )
    return {
        **p.model_dump(mode="json"),
        "view_state": view.model_dump(),
        "action_requests": [
            {**await container.presentations.describe_request(r), "submitted_values": r.values}
            for r in requests
        ],
    }


@router.get("/presentations/{presentation_id}/html")
async def get_html_document(
    presentation_id: str,
    actor: Actor,
    container: Container,
    revision: int = Query(ge=1),
) -> Response:
    owner, p = await owned(container, actor, presentation_id, revision)
    if p.kind != "html":
        raise HTTPException(404, "HTML presentation not found")
    artifact = await container.artifacts.get(actor.tenant_id, p.data["artifact_id"])
    if artifact.run_id != owner.run_id or artifact.status is not ArtifactStatus.READY:
        raise HTTPException(404, "HTML artifact not found")
    if (
        artifact.media_type.split(";", 1)[0].strip().lower() != "text/html"
        or artifact.sha256 != p.data["sha256"]
        or artifact.size_bytes != p.data["size_bytes"]
        or artifact.size_bytes is None
        or not 0 < artifact.size_bytes <= MAX_HTML_BYTES
    ):
        raise HTTPException(409, "HTML artifact changed")
    _, content = await container.artifacts.download(actor.tenant_id, artifact.artifact_id)
    if (
        len(content) != artifact.size_bytes
        or hashlib.sha256(content).hexdigest() != p.data["sha256"]
    ):
        raise HTTPException(409, "HTML artifact content changed")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(422, "HTML artifact must use UTF-8") from error
    return Response(
        content,
        media_type="text/html",
        headers={
            "Content-Security-Policy": HTML_CONTENT_POLICY,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), clipboard-read=()",
        },
    )


@router.post("/presentations/{presentation_id}/actions", status_code=202)
async def submit_action(
    presentation_id: str,
    body: ActionInput,
    actor: Actor,
    container: Container,
    background_tasks: BackgroundTasks,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, Any]:
    ensure_permission(actor, "tasks:write")
    await owned(container, actor, presentation_id)
    try:
        request = await container.presentations.submit(
            tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            authentication_method=actor.authentication_method,
            presentation_id=presentation_id,
            action_id=body.action_id,
            expected_revision=body.expected_revision,
            idempotency_key=idempotency_key,
            values=body.values,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if container.auto_execute and request.run_id:
        background_tasks.add_task(container.worker.execute, actor.tenant_id, request.run_id)
    return await container.presentations.describe_request(request)


@router.get("/presentations/{presentation_id}/actions/{request_id}")
async def get_action(
    presentation_id: str, request_id: str, actor: Actor, container: Container
) -> dict[str, Any]:
    await owned(container, actor, presentation_id)
    request = await container.presentations.repository.request(actor.tenant_id, request_id)
    if request.actor_user_id != actor.user_id or request.presentation_id != presentation_id:
        raise HTTPException(404, "action request not found")
    return await container.presentations.describe_request(request)


@router.put("/presentations/{presentation_id}/view-state")
async def save_view(
    presentation_id: str, body: ViewInput, actor: Actor, container: Container
) -> dict[str, Any]:
    await owned(container, actor, presentation_id, body.revision)
    try:
        view = await container.presentations.save_view(
            tenant_id=actor.tenant_id,
            actor_user_id=actor.user_id,
            presentation_id=presentation_id,
            revision=body.revision,
            expected_version=body.expected_version,
            state=body.state,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return view.model_dump()
