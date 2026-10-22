"""Authenticated Web binding controls; app credentials stay in private deployment config."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from harness.assistant.api import Reader, Writer
from harness.feishu.models import BindingCode, ConnectionStatus, NotificationPreference
from harness.feishu.service import FeishuService

router = APIRouter(prefix="/v1/studio/assistant/feishu", tags=["assistant"])


def service(request: Request) -> FeishuService:
    value = getattr(request.app.state.container, "feishu", None)
    if not isinstance(value, FeishuService):
        raise HTTPException(503, "Feishu is not configured")
    return value


Service = Annotated[FeishuService, Depends(service)]


@router.get("/status")
async def status(identity: Reader, feishu: Service) -> ConnectionStatus:
    return await feishu.status(identity.tenant_id, identity.user_id)


@router.post("/binding-code", status_code=201)
async def binding_code(identity: Writer, feishu: Service) -> BindingCode:
    return await feishu.issue_code(identity.tenant_id, identity.user_id)


@router.delete("/binding", status_code=204)
async def unbind(identity: Writer, feishu: Service) -> None:
    await feishu.repository.unbind(identity.tenant_id, identity.user_id, datetime.now(UTC))


@router.put("/notifications", status_code=204)
async def notifications(body: NotificationPreference, identity: Writer, feishu: Service) -> None:
    await feishu.repository.preferences(identity.tenant_id, identity.user_id, body.enabled)
