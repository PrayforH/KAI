import json

import pytest
from pydantic import ValidationError

from harness.feishu.cards import callback_button, card
from harness.feishu.models import FeishuSettings


@pytest.mark.parametrize("credentials", [{}, {"app_id": "app"}, {"app_secret": "test-secret"}])
def test_enabled_receiver_still_requires_both_credentials(credentials):
    with pytest.raises(ValidationError):
        FeishuSettings(enabled=True, **credentials)


def test_long_connection_without_web_origin_keeps_callback_actions():
    settings = FeishuSettings(enabled=True, app_id="app", app_secret="test-secret")
    assert settings.configured
    payload = card(
        "确认执行",
        "测试事项",
        settings.public_base_url,
        buttons=[callback_button("执行", "test-action")],
    ).content
    serialized = json.dumps(payload)
    assert '"callback"' in serialized
    assert "open_url" not in serialized
    assert "在 KAI 查看" not in serialized


@pytest.mark.parametrize(
    "origin", ["http://kai.test", "https://user:pass@kai.test", "https://kai.test/path"]
)
def test_configured_web_origin_still_requires_safe_https(origin):
    with pytest.raises(ValidationError):
        FeishuSettings(public_base_url=origin)


def test_web_link_keeps_objective_and_callback_when_https_is_configured():
    payload = card(
        "事项",
        "详情",
        "https://kai.test",
        objective_id="objective 1",
        buttons=[callback_button("暂停", "test-action")],
    ).content
    buttons = payload["body"]["elements"][1:]
    assert buttons[0]["behaviors"][0]["type"] == "callback"
    assert (
        buttons[1]["behaviors"][0]["default_url"]
        == "https://kai.test/studio/assistant?objective=objective+1"
    )
