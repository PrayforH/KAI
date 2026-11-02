from harness.weixin.service import _quoted_message_id


def test_quote_can_preserve_an_explicit_sent_client_id():
    assert _quoted_message_id({"refer": {"client_id": "kai-wx-original"}}) == "kai-wx-original"
    assert _quoted_message_id({"quote": {"message_id": "native-id"}}) == "native-id"


def test_unquoted_text_and_top_level_client_id_are_not_routing_evidence():
    assert _quoted_message_id({"client_id": "kai-wx-original", "text": "kai-wx-original"}) == ""
    assert _quoted_message_id({"quote": {"client_id": 42}}) == ""
