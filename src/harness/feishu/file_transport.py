"""Feishu transport for durable, owner-bound artifact receipts."""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from harness.assistant.file_delivery import ChannelFileRow
from harness.core.models import Artifact
from harness.feishu.media import FeishuResourceClient
from harness.feishu.models import Actor, file_message
from harness.feishu.repository import binding_value
from harness.feishu.storage import BindingRow

if TYPE_CHECKING:
    from harness.feishu.service import FeishuService


class FeishuFileTransport:
    channel, retry_send = "feishu", True

    def __init__(self, service: "FeishuService") -> None:
        self.service = service
        self.route_prefix = service.settings.app_id + ":"
        self.uploader = FeishuResourceClient(service.settings)

    async def target(self, session: AsyncSession, row: ChannelFileRow) -> Actor | None:
        binding_id = row.route.removeprefix(self.route_prefix)
        binding = await session.get(BindingRow, binding_id, with_for_update=True)
        if (
            not binding
            or binding.revoked_at
            or binding.app_id != self.service.settings.app_id
            or (binding.tenant_id, binding.user_id) != (row.tenant_id, row.user_id)
        ):
            return None
        await self.service.authorize(row.tenant_id, row.user_id)
        return binding_value(binding).actor

    async def upload(self, target: Actor, artifact: Artifact, content: bytes) -> dict[str, Any]:
        key = await self.uploader.upload_file(artifact.name, artifact.media_type, content)
        return {"file_key": key}

    async def send(self, target: Actor, payload: dict[str, Any], request_id: str) -> str:
        from harness.feishu.sdk import SdkSender

        return await SdkSender(self.service.settings).send(
            target, file_message(payload["file_key"]), request_id
        )
