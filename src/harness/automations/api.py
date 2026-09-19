"""Studio management routes for scheduled automation tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.automations.models import (
    AutomationRunRecord,
    AutomationTask,
    CreateAutomationTaskRequest,
    UpdateAutomationTaskRequest,
)
from harness.automations.service import AutomationService
from harness.core.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/v1/studio/automations", tags=["automations"])


def get_automation_service(request: Request) -> AutomationService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "automations", None)
    if not isinstance(service, AutomationService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "automation_control_plane_not_configured",
                "message": "Automation control plane is not configured",
            },
        )
    return service


def _not_found(error: NotFoundError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": "automation_task_not_found", "message": str(error)},
    )


def _conflict(error: ConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "automation_conflict", "message": str(error)},
    )


@router.get("", response_model=list[AutomationTask])
async def list_automations(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[AutomationTask]:
    ensure_permission(identity, "studio:read")
    return await service.list(identity.tenant_id, identity.user_id)


@router.post("", response_model=AutomationTask, status_code=status.HTTP_201_CREATED)
async def create_automation(
    body: CreateAutomationTaskRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationTask:
    ensure_permission(identity, "studio:write")
    try:
        return await service.create(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            request=body,
        )
    except ConflictError as error:
        raise _conflict(error) from error


@router.put("/{task_id}", response_model=AutomationTask)
async def update_automation(
    task_id: str,
    body: UpdateAutomationTaskRequest,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationTask:
    ensure_permission(identity, "studio:write")
    try:
        return await service.update(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            task_id=task_id,
            request=body,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.post("/{task_id}/enable", response_model=AutomationTask)
async def enable_automation(
    task_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationTask:
    ensure_permission(identity, "studio:write")
    try:
        current = await service.get(identity.tenant_id, identity.user_id, task_id)
        return await service.set_enabled(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            task_id=task_id,
            expected_revision=current.revision,
            enabled=True,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.post("/{task_id}/pause", response_model=AutomationTask)
async def pause_automation(
    task_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationTask:
    ensure_permission(identity, "studio:write")
    try:
        current = await service.get(identity.tenant_id, identity.user_id, task_id)
        return await service.set_enabled(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            task_id=task_id,
            expected_revision=current.revision,
            enabled=False,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.post("/{task_id}/run", response_model=AutomationRunRecord)
async def run_automation_now(
    task_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> AutomationRunRecord:
    ensure_permission(identity, "studio:write")
    try:
        return await service.run_now(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            task_id=task_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error
    except ConflictError as error:
        raise _conflict(error) from error


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_automation(
    task_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> None:
    ensure_permission(identity, "studio:write")
    try:
        await service.delete(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            task_id=task_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error


@router.get("/{task_id}/next-runs", response_model=list[datetime])
async def preview_automation_next_runs(
    task_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[datetime]:
    ensure_permission(identity, "studio:read")
    try:
        return await service.preview_next_runs(
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            task_id=task_id,
        )
    except NotFoundError as error:
        raise _not_found(error) from error


@router.get("/records", response_model=list[AutomationRunRecord])
async def list_automation_records(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AutomationService, Depends(get_automation_service)],
) -> list[AutomationRunRecord]:
    ensure_permission(identity, "studio:read")
    return await service.records(tenant_id=identity.tenant_id, user_id=identity.user_id)
