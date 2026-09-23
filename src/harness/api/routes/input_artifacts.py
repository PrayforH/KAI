from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response

from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
)
from harness.api.downloads import attachment_content_disposition
from harness.core.errors import NotFoundError
from harness.core.models import InputArtifact, ThreadFile

router = APIRouter(tags=["input-artifacts"])


@router.get("/input-artifacts/limits")
async def input_artifact_limits(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> dict[str, int]:
    """Authorize uploads before the BFF starts consuming a large multipart body."""
    ensure_permission(identity, "tasks:write")
    return {
        "max_file_bytes": container.input_artifacts.max_file_bytes,
        "max_files": container.input_artifacts.max_files_per_run,
        "max_total_bytes": container.input_artifacts.max_total_bytes,
    }


@router.get("/threads/{session_id}/files", response_model=list[ThreadFile])
async def list_thread_files(
    session_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> list[ThreadFile]:
    ensure_permission(identity, "tasks:read")
    session = await container.sessions.get(identity.tenant_id, session_id)
    if session.user_id != identity.user_id:
        raise NotFoundError(f"session not found: {session_id}")
    return await container.file_catalog.list_scope(
        identity.tenant_id, identity.user_id, session_id
    )


@router.post(
    "/input-artifacts",
    response_model=InputArtifact,
    status_code=status.HTTP_201_CREATED,
)
async def upload_input_artifact(
    file: Annotated[UploadFile, File()],
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> InputArtifact:
    ensure_permission(identity, "tasks:write")
    maximum = container.input_artifacts.max_file_bytes
    content = await file.read(maximum + 1)
    if len(content) > maximum:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "input_artifact_too_large",
                "message": f"单个附件不能超过 {maximum / (1024 * 1024):g} MB，请压缩或拆分后上传。",
            },
        )
    return await container.input_artifacts.upload(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        name=file.filename or "input",
        media_type=file.content_type or "application/octet-stream",
        content=content,
    )


@router.get("/input-artifacts/{input_artifact_id}/content")
async def download_input_artifact(
    input_artifact_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Response:
    ensure_permission(identity, "tasks:read")
    artifact, content = await container.input_artifacts.download(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        input_artifact_id=input_artifact_id,
    )
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": attachment_content_disposition(artifact.name)
        },
    )
