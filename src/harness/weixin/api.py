"""Signed-in owner only: QR login never returns channel tokens to the browser."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from harness.assistant.api import Reader, Writer
from harness.feishu.api import PrivateConnectionRoute
from harness.weixin.service import WeixinService

router = APIRouter(
    prefix="/v1/studio/assistant/weixin", tags=["assistant"], route_class=PrivateConnectionRoute
)


def service(request: Request) -> WeixinService:
    value = request.app.state.container.weixin
    if value is None:
        raise HTTPException(503, "微信接收器尚未配置")
    return value


Service = Annotated[WeixinService, Depends(service)]


class Verification(BaseModel):
    login_id: str = Field(alias="loginId", min_length=1, max_length=64)
    code: str = Field(pattern=r"^[0-9A-Za-z]{4,12}$")


@router.get("/status")
async def status(identity: Reader, weixin: Service):
    return await weixin.status(identity.tenant_id, identity.user_id)


@router.post("/login")
async def login(identity: Writer, weixin: Service):
    if identity.permissions is not None:
        raise HTTPException(403, "请使用本人网页登录连接微信")
    return await weixin.start(identity.tenant_id, identity.user_id)


@router.post("/verify")
async def verify(body: Verification, identity: Writer, weixin: Service):
    if identity.permissions is not None:
        raise HTTPException(403, "请使用本人网页登录连接微信")
    return await weixin.verify(identity.tenant_id, identity.user_id, body.login_id, body.code)


@router.post("/disconnect", status_code=204)
async def disconnect(identity: Writer, weixin: Service):
    await weixin.disconnect(identity.tenant_id, identity.user_id)


@router.post("/diagnose")
async def diagnose(identity: Reader, weixin: Service):
    return await weixin.diagnose(identity.tenant_id, identity.user_id)
