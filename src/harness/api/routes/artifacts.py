import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
    require_owned_run,
)
from harness.api.downloads import attachment_content_disposition
from harness.core.models import Artifact, ArtifactStatus, Run, Session

router = APIRouter(tags=["artifacts"])


class UserArtifactIndexEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_id: str
    name: str
    media_type: str
    size_bytes: int | None = None
    run_id: str
    thread_id: str
    thread_title: str
    agent_name: str
    created_at: datetime
    task_archived: bool = False


@router.get("/artifacts", response_model=list[UserArtifactIndexEntry])
async def list_user_artifacts(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    thread_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> list[UserArtifactIndexEntry]:
    """Return generated files from every task owned by the current user."""

    ensure_permission(identity, "tasks:read")
    if thread_id is not None:
        binding = await container.agui.get_thread_record(
            tenant_id=identity.tenant_id, user_id=identity.user_id, thread_id=thread_id
        )
        bindings = [binding]
    else:
        active_bindings, archived_bindings = await asyncio.gather(
            container.agui.list_bindings(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                limit=1_000,
                archived=False,
            ),
            container.agui.list_bindings(
                tenant_id=identity.tenant_id,
                user_id=identity.user_id,
                limit=1_000,
                archived=True,
            ),
        )
        bindings = [*active_bindings, *archived_bindings]
    if not bindings:
        return []

    binding_by_session = {
        session_id: binding for binding in bindings for session_id in binding.session_ids
    }
    session_ids = list(binding_by_session)
    sessions, runs = await asyncio.gather(
        container.sessions.list_for_ids(identity.tenant_id, session_ids),
        container.runs.list_for_sessions(
            identity.tenant_id,
            session_ids,
            limit=max(2_000, limit * 20),
        ),
    )
    session_by_id: dict[str, Session] = {
        session.session_id: session for session in sessions if session.user_id == identity.user_id
    }
    owned_runs: dict[str, Run] = {
        run.run_id: run for run in runs if run.session_id in session_by_id
    }
    artifacts = await container.artifacts.list_for_runs(identity.tenant_id, list(owned_runs))
    entries: list[UserArtifactIndexEntry] = []
    for artifact in artifacts:
        if artifact.status is not ArtifactStatus.READY:
            continue
        run = owned_runs.get(artifact.run_id)
        if run is None:
            continue
        session = session_by_id[run.session_id]
        binding = binding_by_session[run.session_id]
        entries.append(
            UserArtifactIndexEntry(
                artifact_id=artifact.artifact_id,
                name=artifact.name,
                media_type=artifact.media_type,
                size_bytes=artifact.size_bytes,
                run_id=run.run_id,
                thread_id=binding.thread_id,
                thread_title=binding.title or "未命名任务",
                agent_name=session.agent_name,
                created_at=run.updated_at,
                task_archived=binding.archived_at is not None,
            )
        )
    return sorted(
        entries,
        key=lambda item: (item.created_at, item.artifact_id),
        reverse=True,
    )[:limit]


@router.post(
    "/runs/{run_id}/artifacts",
    response_model=Artifact,
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    run_id: str,
    file: Annotated[UploadFile, File()],
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Artifact:
    ensure_permission(identity, "tasks:write")
    await require_owned_run(container, identity, run_id)
    maximum = container.artifacts.max_file_bytes
    content = await file.read(maximum + 1)
    if len(content) > maximum:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "artifact_too_large",
                "message": f"artifact exceeds maximum size of {maximum} bytes",
            },
        )
    return await container.artifacts.upload(
        tenant_id=identity.tenant_id,
        run_id=run_id,
        name=file.filename or "artifact",
        media_type=file.content_type or "application/octet-stream",
        content=content,
    )


@router.get("/runs/{run_id}/artifacts", response_model=list[Artifact])
async def list_artifacts(
    run_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[Artifact]:
    ensure_permission(identity, "tasks:read")
    await require_owned_run(container, identity, run_id)
    return await container.artifacts.list_for_run(identity.tenant_id, run_id)


@router.get("/artifacts/{artifact_id}/content")
async def download_artifact(
    artifact_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    thread_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> Response:
    ensure_permission(identity, "tasks:read")
    artifact = await container.artifacts.get(identity.tenant_id, artifact_id)
    run = await require_owned_run(container, identity, artifact.run_id)
    if thread_id is not None:
        binding = await container.agui.get_binding(
            tenant_id=identity.tenant_id, user_id=identity.user_id, thread_id=thread_id
        )
        if run.session_id not in binding.session_ids:
            raise HTTPException(status_code=404, detail="Artifact not found in this task")
    artifact, content = await container.artifacts.download(identity.tenant_id, artifact_id)
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": attachment_content_disposition(artifact.name)},
    )
