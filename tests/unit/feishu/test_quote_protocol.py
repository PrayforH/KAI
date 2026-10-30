"""引用回复的协议层：不依赖飞书 SDK，只用 normalize 的纯函数契约。"""

import json

from harness.feishu.protocol import normalize


def _payload(message: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "2.0",
        "header": {
            "event_type": "im.message.receive_v1",
            "app_id": "cli_test",
            "tenant_key": "tenant-key-1",
        },
        "event": {
            "sender": {
                "sender_type": "user",
                "tenant_key": "tenant-key-1",
                "sender_id": {"open_id": "ou_user"},
            },
            "message": message,
        },
    }


def test_quoted_message_carries_the_native_reply_id() -> None:
    incoming = normalize(
        _payload(
            {
                "message_id": "om_reply",
                "message_type": "text",
                "chat_type": "p2p",
                "parent_id": "om_original",
                "content": json.dumps({"text": "把这个改成 PDF"}),
            }
        ),
        "cli_test",
    )
    assert incoming is not None
    assert incoming.message_id == "om_reply"
    assert incoming.reply_to_message_id == "om_original"


def test_plain_message_has_no_reply_reference() -> None:
    incoming = normalize(
        _payload(
            {
                "message_id": "om_plain",
                "message_type": "text",
                "chat_type": "p2p",
                "content": json.dumps({"text": "你好"}),
            }
        ),
        "cli_test",
    )
    assert incoming is not None
    assert incoming.reply_to_message_id == ""


def test_root_id_is_used_when_parent_id_is_absent() -> None:
    incoming = normalize(
        _payload(
            {
                "message_id": "om_thread_reply",
                "message_type": "text",
                "chat_type": "p2p",
                "root_id": "om_root",
                "content": json.dumps({"text": "继续"}),
            }
        ),
        "cli_test",
    )
    assert incoming is not None
    assert incoming.reply_to_message_id == "om_root"
