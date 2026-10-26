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
