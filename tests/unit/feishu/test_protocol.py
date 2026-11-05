import json

import pytest

lark = pytest.importorskip("lark_oapi")

from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: E402
from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTrigger  # noqa: E402

from harness.feishu.protocol import normalize  # noqa: E402
from harness.feishu.repository import digest  # noqa: E402


def message(content="事项"):
    return {
        "schema": "2.0",
        "header": {
            "app_id": "app",
            "tenant_key": "ft",
            "event_type": "im.message.receive_v1",
            "event_id": "event1",
        },
        "event": {
            "sender": {"sender_type": "user", "tenant_key": "ft", "sender_id": {"open_id": "ou1"}},
            "message": {
                "message_id": "om1",
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": content}),
            },
        },
    }


def test_sdk_message_round_trip_and_platform_message_id_dedup():
    payload = message()
    one = normalize(json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(payload))), "app")
    payload["header"]["event_id"] = "different-delivery"
    two = normalize(payload, "app")
    assert one is not None and two is not None and one.key == two.key == "message:om1"


def test_raw_binding_secret_never_enters_durable_payload():
    incoming = normalize(message("绑定 ABCDEF123456"), "app")
    assert incoming and incoming.code_digest == digest("ABCDEF123456")
    assert "ABCDEF123456" not in incoming.model_dump_json()


@pytest.mark.parametrize("kind,body,key", [
    ("image", {"image_key": "img_safe"}, "img_safe"),
    ("file", {"file_key": "file_safe", "file_name": "报告.pdf"}, "file_safe"),
    ("audio", {"file_key": "file_voice", "duration": 2500}, "file_voice"),
])
def test_media_sdk_events_preserve_message_identity_and_bounded_resource(kind, body, key):
    payload = message()
    payload["event"]["message"].update(message_type=kind, content=json.dumps(body))
    incoming = normalize(json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(payload))), "app")
    assert incoming and incoming.kind == "message" and incoming.text == ""
    assert incoming.key == "message:om1" and incoming.message_id == "om1"
    assert incoming.resources[0].key == key and incoming.resources[0].kind == kind
    payload["event"]["message"]["chat_type"] = "group"
    assert normalize(payload, "app") is None


@pytest.mark.parametrize("body", [
    {"file_key": "../../private"}, {"file_key": "https://evil.test/key"},
    {"file_key": "file_ok", "duration": -1}, {"file_key": ""},
])
def test_reject_malformed_resource_references(body):
    payload = message()
    payload["event"]["message"].update(message_type="audio", content=json.dumps(body))
    assert normalize(payload, "app") is None


@pytest.mark.parametrize("locale", [None, "zh_cn", "en_us"])
def test_combined_post_keeps_caption_multiple_images_and_text_structure(locale):
    body = {"title": "看这两张", "content": [
        [{"tag": "img", "image_key": "img_one"}, {"tag": "img", "image_key": "img_two"}],
        [{"tag": "text", "text": "查下这个。"},
         {"tag": "a", "text": "资料", "href": "https://example.com/page"}],
        [{"tag": "code_block", "text": "print('hello')"}],
        [{"tag": "img", "image_key": "img_one"}],
    ]}
    payload = message()
    payload["event"]["message"].update(
        message_type="post", content=json.dumps({locale: body} if locale else body)
    )
    incoming = normalize(json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(payload))), "app")
    assert incoming and incoming.key == "message:om1"
    assert "看这两张" in incoming.text
    assert "查下这个。资料 (https://example.com/page)" in incoming.text
    assert "print('hello')" in incoming.text
    assert [r.key for r in incoming.resources] == ["img_one", "img_two"]
    assert "[图片 1][图片 2]" in incoming.text
    payload["event"]["message"]["chat_type"] = "group"
    assert normalize(payload, "app") is None


def test_post_only_image_uses_media_default_prompt_and_markdown_text_is_accepted():
    payload = message()
    payload["event"]["message"].update(message_type="post", content=json.dumps({
        "title": "", "content": [[{"tag": "img", "image_key": "img_one"}]],
    }))
    incoming = normalize(payload, "app")
    assert incoming and incoming.resources and not incoming.text
    payload["event"]["message"]["content"] = json.dumps({
        "content_v2": [[{"tag": "md", "text": "**请概括**"}]],
    })
    incoming = normalize(payload, "app")
    assert incoming and incoming.text == "**请概括**"


def test_post_binding_never_persists_binding_secret():
    payload = message()
    payload["event"]["message"].update(message_type="post", content=json.dumps({
        "content": [[{"tag": "text", "text": "绑定 ABCDEF123456"}]],
    }))
    incoming = normalize(payload, "app")
    assert incoming and incoming.kind == "bind" and incoming.code_digest == digest("ABCDEF123456")
    assert "ABCDEF123456" not in incoming.model_dump_json()


def test_post_unknown_embedded_content_gets_explicit_limitation_not_silent_loss():
    payload = message()
    payload["event"]["message"].update(message_type="post", content=json.dumps({
        "content": [[{"tag": "text", "text": "看看这个"}, {"tag": "media", "file_key": "vid"}]],
    }))
    incoming = normalize(payload, "app")
    assert incoming and "看看这个" in incoming.text and "暂不支持解析" in incoming.text
    assert not incoming.resources


@pytest.mark.parametrize("body", [
    {}, {"content": "bad"}, {"content": [[None]]},
    {"content": [[{"tag": "img", "image_key": "../../private"}]]},
    {"content": [[{"tag": "img", "image_key": f"img_{i}"} for i in range(11)]]},
])
def test_reject_invalid_and_oversized_posts(body):
    payload = message()
    payload["event"]["message"].update(message_type="post", content=json.dumps(body))
    assert normalize(payload, "app") is None


@pytest.mark.parametrize("field,value", [("chat_type", "group"), ("message_type", "image")])
def test_ignore_non_private_text(field, value):
    payload = message()
    payload["event"]["message"][field] = value
    assert normalize(payload, "app") is None


def test_reject_wrong_app_tenant_bot_and_malformed_input():
    assert normalize(message(), "other") is None
    payload = message()
    payload["event"]["sender"]["tenant_key"] = "another"
    assert normalize(payload, "app") is None
    payload = message()
    payload["event"]["sender"]["sender_type"] = "app"
    assert normalize(payload, "app") is None
    for invalid in (
        {},
        {"header": None},
        {"schema": "2.0", "header": {"app_id": "app"}, "event": []},
    ):
        assert normalize(invalid, "app") is None


def test_card_actor_scopes_dedup_and_discards_platform_update_token():
    payload = {
        "schema": "2.0",
        "header": {"event_type": "card.action.trigger", "app_id": "app", "tenant_key": "ft"},
        "event": {
            "operator": {"tenant_key": "ft", "open_id": "ou1"},
            "token": "platform-secret",
            "action": {"value": {"kai_action": "a" * 48}},
        },
    }
    one = normalize(json.loads(lark.JSON.marshal(P2CardActionTrigger(payload))), "app")
    payload["event"]["operator"]["open_id"] = "ou-attacker"
    two = normalize(payload, "app")
    assert one and two and one.key != two.key
    assert one.action_digest == two.action_digest
    assert "platform-secret" not in one.model_dump_json()
