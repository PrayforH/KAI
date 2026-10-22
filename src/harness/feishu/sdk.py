"""Official SDK boundary. No SDK objects leak into persisted/domain contracts."""
# Generated SDK builders are untyped; suppress unknowns only at this boundary.
# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

import json

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from harness.feishu.delivery import DeliveryRejectedError
from harness.feishu.models import Actor, FeishuSettings, MessageContent


class SdkSender:
    def __init__(self, settings: FeishuSettings) -> None:
        self.client = (
            lark.Client.builder()
            .app_id(settings.app_id)
            .app_secret(settings.app_secret.get_secret_value())
            .timeout(5)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

    async def send(self, actor: Actor, content: MessageContent, request_id: str) -> str:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(actor.open_id)
                .msg_type(content.msg_type)
                .content(json.dumps(content.content, ensure_ascii=False))
                .uuid(request_id)
                .build()
            )
            .build()
        )
        assert self.client.im is not None
        response = await self.client.im.v1.message.acreate(request)
        if not response.success():
            # Rate limits, 5xx and unrecognized results remain retryable within the
            # durable UUID window. Definitive 4xx rejections need admin attention.
            status = response.raw.status_code if response.raw else None
            if status and 400 <= status < 500 and status != 429:
                raise DeliveryRejectedError("Feishu rejected the request")
            raise ConnectionError("Feishu delivery not confirmed")
        if response.data is None or not response.data.message_id:
            raise ConnectionError("Feishu delivery not confirmed")
        return response.data.message_id
