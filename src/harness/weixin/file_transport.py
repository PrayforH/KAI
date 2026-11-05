"""Weixin file transport; credentials and conversation contexts stay owner-bound."""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from harness.assistant.file_delivery import ChannelFileRow
from harness.core.models import Artifact
from harness.weixin.storage import WeixinConnectionRow, WeixinMessageRow

if TYPE_CHECKING:
    from harness.weixin.service import WeixinService


class WeixinFileTransport:
    channel, retry_send, route_prefix = "weixin", False, ""

    def __init__(self, service: "WeixinService") -> None:
        self.service = service

    async def target(self, session: AsyncSession, row: ChannelFileRow) -> dict[str, Any] | None:
        connection = await session.get(
            WeixinConnectionRow, (row.tenant_id, row.user_id), with_for_update=True
        )
        message = await session.get(WeixinMessageRow, row.source_id)
        if (
            not connection
            or not connection.enabled
            or connection.generation != row.route
            or not message
            or message.generation != row.route
            or (message.tenant_id, message.user_id) != (row.tenant_id, row.user_id)
        ):
            return None
        await self.service.authorize(row.tenant_id, row.user_id)
        return {
            "credentials": self.service.open(connection, "credentials", connection.ciphertext),
            "context": self.service.open(connection, "context", message.context_ciphertext)[
                "context"
            ],
        }

    async def upload(
        self, target: dict[str, Any], artifact: Artifact, content: bytes
    ) -> dict[str, Any]:
        return await self.service.client.upload_file(target["credentials"], artifact.name, content)

    async def send(self, target: dict[str, Any], payload: dict[str, Any], request_id: str) -> None:
        await self.service.client.send_item(
            target["credentials"], target["context"], request_id, payload
        )
