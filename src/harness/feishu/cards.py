"""Small JSON 2.0 cards. User and agent text never becomes executable markup."""

from typing import Any
from urllib.parse import urlencode

from harness.feishu.models import MessageContent


def text_message(content: str) -> MessageContent:
    return MessageContent(msg_type="text", content={"text": content[:12000]})


def callback_button(label: str, token: str) -> dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": label[:80]},
        "type": "default",
        "behaviors": [{"type": "callback", "value": {"kai_action": token}}],
    }


def card(
    title: str,
    content: str,
    base_url: str,
    *,
    objective_id: str = "",
    buttons: list[dict[str, Any]] | None = None,
) -> MessageContent:
    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "plain_text", "content": content[:6000]}},
        *(buttons or []),
    ]
    if base_url:
        url = base_url.rstrip("/") + "/studio/assistant"
        if objective_id:
            url += "?" + urlencode({"objective": objective_id})
        elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "在 KAI 查看"},
                "type": "default",
                "behaviors": [{"type": "open_url", "default_url": url}],
            }
        )
    return MessageContent(
        msg_type="interactive",
        content={
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title[:120]}, "template": "blue"},
            "body": {"elements": elements},
        },
    )
