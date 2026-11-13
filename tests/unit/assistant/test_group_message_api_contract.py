"""群聊发送请求契约：引用目标走 replyToMessageId，非法值在进服务前就被拒。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from harness.assistant.api import GroupMessageRequest, GroupUpdateRequest
from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupAttachment,
    AssistantGroupMessage,
    AssistantGroupMessageAuthor,
)


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


def test_attachments_are_accepted_by_alias_and_default_to_empty() -> None:
    body = GroupMessageRequest.model_validate(
        {"text": "带附件", "attachmentIds": ["gdoc_1", "gdoc_2"]}
    )
    assert body.attachment_ids == ("gdoc_1", "gdoc_2")
    assert GroupMessageRequest.model_validate({"text": "没有附件"}).attachment_ids == ()


def test_more_than_ten_attachments_are_rejected() -> None:
    with pytest.raises(ValidationError):
        GroupMessageRequest.model_validate(
            {"text": "太多", "attachmentIds": [f"gdoc_{i}" for i in range(11)]}
        )


def test_group_update_needs_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        GroupUpdateRequest.model_validate({})


def test_group_update_keeps_null_semantics() -> None:
    body = GroupUpdateRequest.model_validate({"description": None})
    assert "description" in body.model_fields_set
    with pytest.raises(ValidationError):
        GroupUpdateRequest.model_validate({"name": None})


def _group(**overrides) -> AssistantGroup:
    now = datetime(2026, 11, 13, tzinfo=UTC)
    return AssistantGroup(
        groupId="group_a",
        name="项目群",
        creatorUserId="u",
        primaryPresent=True,
        createdAt=now,
        updatedAt=now,
        **overrides,
    )


def test_group_without_description_omits_the_key() -> None:
    assert "description" not in _group().model_dump(mode="json", by_alias=True)
    assert (
        _group(description="对外项目").model_dump(mode="json", by_alias=True)[
            "description"
        ]
        == "对外项目"
    )


def test_message_without_attachments_omits_the_key() -> None:
    message = AssistantGroupMessage(
        messageId="gmsg_1",
        groupId="group_a",
        author=AssistantGroupMessageAuthor.USER,
        authorName="我",
        text="hi",
        createdAt=datetime(2026, 11, 13, tzinfo=UTC),
    )
    assert "attachments" not in message.model_dump(mode="json", by_alias=True)


def test_message_attachments_serialise_with_aliases() -> None:
    message = AssistantGroupMessage(
        messageId="gmsg_1",
        groupId="group_a",
        author=AssistantGroupMessageAuthor.USER,
        authorName="我",
        text="hi",
        attachments=(
            AssistantGroupAttachment(
                documentId="gdoc_1",
                name="方案.txt",
                contentType="text/plain",
                sizeBytes=3,
                downloadUrl="/v1/studio/assistant/groups/group_a/library/documents/gdoc_1/content",
            ),
        ),
        createdAt=datetime(2026, 11, 13, tzinfo=UTC),
    )
    payload = message.model_dump(mode="json", by_alias=True)["attachments"]
    assert payload == [
        {
            "documentId": "gdoc_1",
            "name": "方案.txt",
            "contentType": "text/plain",
            "sizeBytes": 3,
            "downloadUrl": "/v1/studio/assistant/groups/group_a/library/documents/gdoc_1/content",
        }
    ]
