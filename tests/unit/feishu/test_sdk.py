import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from harness.feishu.cards import card
from harness.feishu.delivery import DeliveryRejectedError
from harness.feishu.models import Actor, FeishuSettings

pytest.importorskip("lark_oapi")

from harness.feishu.sdk import SdkSender  # noqa: E402


@pytest.mark.asyncio
async def test_official_sdk_request_uses_private_open_id_and_stable_uuid():
    settings = FeishuSettings(
        app_id="app", app_secret=SecretStr("test-only"), public_base_url="https://kai.test"
    )
    sender = SdkSender(settings)
    sender.client.im.v1.message.acreate = AsyncMock(
        return_value=SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om1"))
    )
    result = await sender.send(
        Actor(app_id="app", tenant_key="tenant", open_id="ou1"),
        card("title", "body", settings.public_base_url),
        "stable-id",
    )
    request = sender.client.im.v1.message.acreate.call_args.args[0]
    assert request.request_body.uuid == "stable-id"
    assert request.request_body.receive_id == "ou1"
    assert request.request_body.msg_type == "interactive"
    assert json.loads(request.request_body.content)["schema"] == "2.0"
    assert result == "om1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error", [(403, DeliveryRejectedError), (429, ConnectionError), (503, ConnectionError)]
)
async def test_sdk_rejections_never_expose_external_response(status, error):
    sender = SdkSender(FeishuSettings(app_id="app", app_secret=SecretStr("test-only")))
    sender.client.im.v1.message.acreate = AsyncMock(
        return_value=SimpleNamespace(
            success=lambda: False,
            raw=SimpleNamespace(status_code=status),
            msg="secret external payload",
        )
    )
    with pytest.raises(error) as raised:
        await sender.send(
            Actor(app_id="app", tenant_key="t", open_id="o"),
            card("t", "b", "https://kai.test"),
            "id",
        )
    assert "secret" not in str(raised.value)
