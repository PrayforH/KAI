"""Normalize only supported private events; discard platform credentials immediately."""

import json
import re
from typing import Any, Literal, cast

from pydantic import ValidationError

from harness.feishu.models import Actor, Incoming, MessageResource
from harness.feishu.repository import actor_key, digest


def post_content(body: dict[str, Any]) -> tuple[str, list[MessageResource]]:
    """Read the received post shape and locale-wrapped send representations."""
    if "content" not in body and "content_v2" not in body:
        locales = [body.get(key) for key in ("zh_cn", "en_us", "ja_jp")]
        locales.extend(body.values())
        body = next(cast(dict[str, Any], value) for value in locales if isinstance(value, dict))
    rows = body.get("content", body.get("content_v2"))
    if not isinstance(rows, list):
        raise ValueError("invalid post rows")
    rows = cast(list[object], rows)
    if len(rows) > 200:
        raise ValueError("invalid post rows")
    texts = [body.get("title", "")]
    resources: list[MessageResource] = []
    seen: dict[tuple[str, str], int] = {}
    count = 0
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("invalid post row")
        parts: list[str] = []
        for node in cast(list[object], row):
            count += 1
            if not isinstance(node, dict) or count > 2000:
                raise ValueError("invalid post node")
            node = cast(dict[str, Any], node)
            tag = node.get("tag")
            if tag in {"text", "md", "code_block"}:
                parts.append(node.get("text", ""))
            elif tag == "a":
                label, href = node.get("text", ""), node.get("href", "")
                parts.append(f"{label} ({href})" if label and href else label or href)
            elif tag == "at":
                parts.append("@" + (node.get("user_name") or node.get("user_id") or ""))
            elif tag in {"img", "file", "audio"}:
                kind = cast(Literal["image", "file", "audio"], "image" if tag == "img" else tag)
                resource = MessageResource(
                    kind=kind,
                    key=node["image_key" if kind == "image" else "file_key"],
                    name=node.get("file_name", ""),
                    duration_ms=node.get("duration", 0),
                )
                identity = (kind, resource.key)
                if identity not in seen:
                    resources.append(resource)
                    seen[identity] = len(resources)
                label = {"image": "图片", "file": "文件", "audio": "语音"}[kind]
                parts.append(f"[{label} {seen[identity]}]")
            elif tag == "emotion":
                parts.append(f"[表情：{node.get('emoji_type', '')}]")
            elif tag == "hr":
                parts.append("\n")
            else:
                # Keep the usable caption/images, but never pretend that a
                # video, poll or other unknown embedded object was analyzed.
                parts.append("[此消息含暂不支持解析的内容；请单独发送文字、图片、文件或语音。]")
        texts.append("".join(parts))
    text = "\n".join(texts).strip()
    if (not text and not resources) or len(text) > 8000 or len(resources) > 10:
        raise ValueError("post content exceeds supported bounds")
    if resources and not re.sub(r"\[(?:图片|文件|语音) \d+\]", "", text).strip():
        text = ""
    return text, resources


def normalize(payload: dict[str, Any], app_id: str) -> Incoming | None:
    try:
        header, event = payload["header"], payload["event"]
        if payload.get("schema") != "2.0" or header.get("app_id") != app_id:
            return None
        if header["event_type"] == "im.message.receive_v1":
            sender, message = event["sender"], event["message"]
            message_type = message["message_type"]
            if sender["sender_type"] != "user" or message["chat_type"] != "p2p":
                return None
            if message_type not in {"text", "image", "file", "audio", "post"}:
                return None
            actor = Actor(
                app_id=app_id,
                tenant_key=sender["tenant_key"],
                open_id=sender["sender_id"]["open_id"],
            )
            if header["tenant_key"] != actor.tenant_key:
                return None
            body = json.loads(message["content"])
            resources: list[MessageResource] = []
            if message_type == "text":
                content = body["text"].strip()
                if not isinstance(content, str) or not content or len(content) > 8000:
                    return None
            elif message_type == "post":
                content, resources = post_content(body)
            else:
                resources = [
                    MessageResource(
                        kind=message_type,
                        key=body["image_key" if message_type == "image" else "file_key"],
                        name=body.get("file_name", ""),
                        duration_ms=body.get("duration", 0),
                    )
                ]
                content = ""
            message_id = message["message_id"]
            if not isinstance(message_id, str) or not message_id:
                return None
            # Even malformed bind attempts must not store their credential as chat text.
            binding = content.startswith("绑定")
            # 飞书把"回复某条消息"表达为 parent_id/root_id；这是可靠的原生引用 id，
            # 交给服务层映射到内部消息，不以自然语言摘要猜测。
            quoted = message.get("parent_id") or message.get("root_id") or ""
            quoted_id = quoted if isinstance(quoted, str) else ""
            return Incoming(
                actor=actor,
                key=f"message:{message_id}",
                message_id=message_id,
                kind="bind" if binding else "message",
                text="" if binding else content,
                code_digest=digest(content[2:].strip().upper()) if binding else "",
                resources=[] if binding else resources,
                reply_to_message_id=quoted_id[:128],
            )
        if header["event_type"] == "card.action.trigger":
            operator = event["operator"]
            actor = Actor(
                app_id=app_id, tenant_key=operator["tenant_key"], open_id=operator["open_id"]
            )
            if header["tenant_key"] != actor.tenant_key:
                return None
            token = event["action"]["value"].get("kai_action", "")
            if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{48}", token):
                return None
            action_digest = digest(token)
            return Incoming(
                actor=actor,
                key=f"action:{actor_key(actor)}:{action_digest}",
                kind="action",
                action_digest=action_digest,
            )
    except (KeyError, TypeError, AttributeError, ValueError, ValidationError, StopIteration):
        return None
    return None
