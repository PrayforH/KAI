"""群聊发送请求契约：引用目标走 replyToMessageId，非法值在进服务前就被拒。"""

import pytest
from pydantic import ValidationError

from harness.assistant.api import GroupMessageRequest


def test_reply_target_is_accepted_by_its_alias() -> None:
    body = GroupMessageRequest.model_validate(
        {"text": "引用一下", "replyToMessageId": "gmsg_abc"}
    )
    assert body.reply_to_message_id == "gmsg_abc"


def test_absent_quote_stays_none() -> None:
    assert GroupMessageRequest.model_validate({"text": "没有引用"}).reply_to_message_id is None


@pytest.mark.parametrize("value", ["", "x" * 129])
def test_malformed_reply_target_is_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        GroupMessageRequest.model_validate(
            {"text": "引用一下", "replyToMessageId": value}
        )


def test_unknown_fields_are_still_rejected() -> None:
    with pytest.raises(ValidationError):
        GroupMessageRequest.model_validate(
            {"text": "引用一下", "quotedMessageId": "gmsg_abc"}
        )
