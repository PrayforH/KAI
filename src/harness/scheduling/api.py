"""One catalog route for every schedule owned by the caller."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from harness.api.dependencies import (
    Identity,
    ensure_permission,
    has_permission,
    require_identity,
)
from harness.scheduling.catalog import ScheduleCatalog, ScheduleSource, ScheduleTask

router = APIRouter(prefix="/v1/studio/schedules", tags=["schedules"])


def get_schedule_catalog(request: Request) -> ScheduleCatalog:
    container = getattr(request.app.state, "container", None)
    assistant = getattr(container, "assistant", None)
    automations = getattr(container, "automations", None)
    if assistant is None or automations is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "schedule_catalog_not_configured",
                "message": "Schedule catalog is not configured",
            },
        )
    return ScheduleCatalog(assistant.repository, automations)


Catalog = Annotated[ScheduleCatalog, Depends(get_schedule_catalog)]


@router.get("", response_model=list[ScheduleTask])
async def list_schedules(
    identity: Annotated[Identity, Depends(require_identity)],
    catalog: Catalog,
    specialist_id: Annotated[str | None, Query(alias="specialistId", max_length=128)] = None,
    source: Annotated[ScheduleSource | None, Query()] = None,
) -> list[ScheduleTask]:
    ensure_permission(identity, "tasks:read")
    if source is ScheduleSource.AUTOMATION and not has_permission(identity, "studio:read"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "permission_denied",
                "message": "Permission required: studio:read",
            },
        )
    if not has_permission(identity, "studio:read"):
        # A tasks-only credential still gets its objectives, never automation data.
        return await catalog.list(
            identity.tenant_id, identity.user_id, source=ScheduleSource.OBJECTIVE
        )
    return await catalog.list(
        identity.tenant_id,
        identity.user_id,
        specialist_id=specialist_id,
        source=source,
    )
