"""Studio routes for user-owned projects."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.core.errors import ConflictError, NotFoundError
from harness.projects.models import (
    CreateProjectRequest,
    Project,
    UpdateProjectRequest,
)
from harness.projects.service import ProjectService

router = APIRouter(prefix="/v1/studio/projects", tags=["projects"])


def get_project_service(request: Request) -> ProjectService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "projects", None)
    if not isinstance(service, ProjectService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "project_control_plane_not_configured",
                "message": "Project store is not configured",
            },
        )
    return service


def _not_found(error: NotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "project_not_found", "message": str(error)},
    )


def _conflict(error: ConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "project_conflict", "message": str(error)},
    )


@router.get("", response_model=list[Project])
async def list_projects(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
    include_archived: bool = False,
) -> list[Project]:
    ensure_permission(identity, "tasks:read")
    return await service.list(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        include_archived=include_archived,
    )


@router.post("", response_model=Project, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> Project:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.create(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            name=body.name,
        )
    except ConflictError as error:
        raise _conflict(error) from error


@router.patch("/{project_id}", response_model=Project)
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> Project:
    ensure_permission(identity, "tasks:write")
    if body.name is None and body.archived is None:
        raise ConflictError("No project update was requested")
    try:
        return await service.update(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
            name=body.name,
            archived=body.archived,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    ensure_permission(identity, "tasks:write")
    try:
        await service.delete(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            project_id=project_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
