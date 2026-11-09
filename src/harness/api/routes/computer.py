"""Owner desktop control: observe by default, explicit takeover and return.

The desktop lease is the single authority for keyboard/pointer ownership. The
web panel and the viewer poll the same state; the node's control gate enforces
it again before every agent action.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.computer.desktop import (
    DesktopControl,
    DesktopControlService,
)
from harness.computer.targets import ComputerTargetService
from harness.core.errors import ConflictError, NotFoundError

router = APIRouter(prefix="/v1/studio/assistant/computer", tags=["assistant-computer"])

if TYPE_CHECKING:
    from harness.computer.desktop_registry import DesktopRegistry


def desktop_service(request: Request) -> DesktopControlService:
    value = getattr(request.app.state.container, "computer_desktop", None)
    if not isinstance(value, DesktopControlService):
        raise HTTPException(503, "Computer desktop control is not configured")
    return value


Service = Annotated[DesktopControlService, Depends(desktop_service)]


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(404, "这台账号还没有可用的电脑。")


@router.get("/desktop")
async def state(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Service,
) -> DesktopControl:
    ensure_permission(identity, "tasks:read")
    try:
        return await service.view(identity.tenant_id, identity.user_id)
    except NotFoundError as error:
        raise _not_found(error) from None


@router.post("/desktop/takeover")
async def takeover(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Service,
    binding_key: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> DesktopControl:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.request_human(
            identity.tenant_id, identity.user_id, expected_binding=binding_key
        )
    except NotFoundError as error:
        raise _not_found(error) from None
    except ConflictError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/desktop/heartbeat")
async def heartbeat(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Service,
    binding_key: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> DesktopControl:
    """Keep an active human takeover alive while the owner is using it.

    The takeover TTL is a safety net for an abandoned session, not a hard
    limit during work: an open viewer refreshes it on every poll.
    """

    ensure_permission(identity, "tasks:write")
    try:
        return await service.refresh_human(
            identity.tenant_id, identity.user_id, expected_binding=binding_key
        )
    except NotFoundError as error:
        raise _not_found(error) from None


@router.post("/desktop/return")
async def return_control(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Service,
    binding_key: Annotated[str | None, Query(min_length=64, max_length=64)] = None,
) -> DesktopControl:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.release_human(
            identity.tenant_id, identity.user_id, expected_binding=binding_key
        )
    except NotFoundError as error:
        raise _not_found(error) from None


def target_service(request: Request) -> ComputerTargetService:
    value = getattr(request.app.state.container, "computer_targets", None)
    if not isinstance(value, ComputerTargetService):
        raise HTTPException(503, "Computer selection is not configured")
    return value


class SelectTarget(BaseModel):
    target_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")


@router.get("/targets")
async def targets(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ComputerTargetService, Depends(target_service)],
):
    ensure_permission(identity, "tasks:read")
    try:
        return await service.view(identity.tenant_id, identity.user_id)
    except NotFoundError:
        raise HTTPException(404, "没有可用的电脑") from None


@router.post("/targets/select")
async def select_target(
    body: SelectTarget,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[ComputerTargetService, Depends(target_service)],
):
    ensure_permission(identity, "tasks:write")
    try:
        return await service.select(identity.tenant_id, identity.user_id, body.target_id)
    except NotFoundError:
        raise HTTPException(404, "没有这台电脑的使用权限") from None
    except ConflictError:
        raise HTTPException(409, "电脑正在使用中，请等待当前操作结束并交还控制后再切换") from None


class BotDesktop(BaseModel):
    """One Bot's desktop slot inside the owner's Computer."""

    desktopId: str
    specialistId: str
    state: str
    display: str | None = None
    unixUser: str
    vncSocket: str | None = None
    lastActiveAt: datetime | None = None


def desktop_registry(request: Request) -> "DesktopRegistry":
    value = getattr(request.app.state.container, "computer_desktops", None)
    if value is None:
        raise HTTPException(503, "Per-Bot desktops are not configured")
    return value


Registry = Annotated["DesktopRegistry", Depends(desktop_registry)]


@router.get("/desktops")
async def desktops(
    identity: Annotated[Identity, Depends(require_identity)],
    registry: Registry,
) -> list[BotDesktop]:
    """Every Bot slot this owner has, including stopped ones."""

    ensure_permission(identity, "tasks:read")
    rows = await registry.list(identity.tenant_id, identity.user_id)
    return [
        BotDesktop(
            desktopId=row.desktop_id,
            specialistId=row.specialist_id,
            state=row.state.value if hasattr(row.state, "value") else str(row.state),
            display=row.display,
            unixUser=row.unix_user,
            vncSocket=row.vnc_socket,
            lastActiveAt=row.last_active_at,
        )
        for row in rows
    ]


@router.post("/desktops/{desktop_id}/stop")
async def stop_desktop(
    desktop_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    registry: Registry,
) -> BotDesktop:
    """Fence one Bot's slot; the guest unit stops on the next ensure."""

    ensure_permission(identity, "tasks:write")
    try:
        row = await registry.stop(identity.tenant_id, identity.user_id, desktop_id)
    except NotFoundError as error:
        raise HTTPException(404, "Bot desktop not found") from None
    return BotDesktop(
        desktopId=row.desktop_id,
        specialistId=row.specialist_id,
        state=row.state.value if hasattr(row.state, "value") else str(row.state),
        display=row.display,
        unixUser=row.unix_user,
        vncSocket=row.vnc_socket,
        lastActiveAt=row.last_active_at,
    )
