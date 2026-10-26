"""Authenticated Web binding controls; app credentials stay in private deployment config."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from harness.api.dependencies import Identity, ensure_permission
from harness.assistant.api import Reader, Writer
from harness.feishu.configuration import (
    ConfigureConnection,
    ConnectionConfiguration,
    ConnectionDiagnostics,
    ConnectionRevision,
    FeishuConnectionControl,
)
from harness.feishu.models import BindingCode, ConnectionStatus, NotificationPreference
from harness.feishu.service import FeishuService


class PrivateConnectionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise HTTPException(422, "飞书配置输入无效，请检查填写内容。") from None

        return handle


router = APIRouter(
    prefix="/v1/studio/assistant/feishu",
    tags=["assistant"],
    route_class=PrivateConnectionRoute,
)


async def service(request: Request, identity: Reader) -> FeishuService:
    container = request.app.state.container
    if container.feishu_connections is not None and container.feishu_factory is not None:
        return container.feishu_factory(
            await container.feishu_connections.resolve(identity.tenant_id)
        )
    value = getattr(request.app.state.container, "feishu", None)
    if not isinstance(value, FeishuService):
        raise HTTPException(503, "Feishu is not configured")
    return value


Service = Annotated[FeishuService, Depends(service)]


def control(request: Request) -> FeishuConnectionControl:
    value = request.app.state.container.feishu_connections
    if value is None:
        raise HTTPException(503, "飞书连接管理未启用")
    return value


Control = Annotated[FeishuConnectionControl, Depends(control)]


def can_manage(identity: Identity) -> bool:
    return bool(identity.roles & {"owner", "admin"}) and identity.permissions is None


def manage(identity: Identity) -> None:
    ensure_permission(identity, "operations:admin")
    if not can_manage(identity):
        raise HTTPException(403, "只有工作区管理员可以配置飞书应用")


@router.get("/connection")
async def configuration(identity: Reader, connections: Control) -> ConnectionConfiguration:
    result = await connections.get(identity.tenant_id)
    return result.model_copy(update={"can_manage": can_manage(identity)})


@router.put("/connection")
async def configure_connection(
    body: ConfigureConnection,
    identity: Writer,
    connections: Control,
) -> ConnectionConfiguration:
    manage(identity)
    result = await connections.configure(identity.tenant_id, identity.user_id, body)
    return result.model_copy(update={"can_manage": True})


@router.post("/connection/disconnect")
async def disconnect_connection(
    body: ConnectionRevision,
    identity: Writer,
    connections: Control,
) -> ConnectionConfiguration:
    manage(identity)
    result = await connections.disconnect(
        identity.tenant_id, identity.user_id, body.expected_revision
    )
    return result.model_copy(update={"can_manage": True})


@router.post("/connection/diagnose")
async def diagnose_connection(
    identity: Reader,
    connections: Control,
    feishu: Service,
) -> ConnectionDiagnostics:
    return await connections.diagnose(identity.tenant_id, identity.user_id, feishu)


@router.post("/connection/test", status_code=202)
async def test_connection(
    identity: Writer, connections: Control, feishu: Service
) -> dict[str, str]:
    key = await connections.send_test(identity.tenant_id, identity.user_id, feishu)
    return {"requestId": key, "status": "pending"}


@router.get("/connection/tests/{request_id}")
async def connection_test_status(
    request_id: str,
    identity: Reader,
    connections: Control,
    feishu: Service,
) -> dict[str, str | None]:
    return await connections.test_status(identity.tenant_id, identity.user_id, feishu, request_id)


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
