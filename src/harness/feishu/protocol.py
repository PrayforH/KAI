"""Normalize only supported private events; discard platform credentials immediately."""

import json
import re
from typing import Any

from pydantic import ValidationError

from harness.feishu.models import Actor, Incoming, MessageResource
from harness.feishu.repository import actor_key, digest


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
            if message_type not in {"text", "image", "file", "audio"}:
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
            binding = message_type == "text" and content.startswith("绑定")
            return Incoming(
                actor=actor,
                key=f"message:{message_id}",
                message_id=message_id,
                kind="bind" if binding else "message",
                text="" if binding else content,
                code_digest=digest(content[2:].strip().upper()) if binding else "",
                resources=resources,
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
    except (KeyError, TypeError, AttributeError, ValueError, ValidationError):
        return None
    return None
