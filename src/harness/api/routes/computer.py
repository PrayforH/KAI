"""Owner desktop control: observe by default, explicit takeover and return.

The desktop lease is the single authority for keyboard/pointer ownership. The
web panel and the viewer poll the same state; the node's control gate enforces
it again before every agent action.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.computer.desktop import (
    DesktopControl,
    DesktopControlService,
)
from harness.core.errors import NotFoundError

router = APIRouter(prefix="/v1/studio/assistant/computer", tags=["assistant-computer"])


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
) -> DesktopControl:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.request_human(identity.tenant_id, identity.user_id)
    except NotFoundError as error:
        raise _not_found(error) from None


@router.post("/desktop/return")
async def return_control(
    identity: Annotated[Identity, Depends(require_identity)],
    service: Service,
) -> DesktopControl:
    ensure_permission(identity, "tasks:write")
    try:
        return await service.release_human(identity.tenant_id, identity.user_id)
    except NotFoundError as error:
        raise _not_found(error) from None
