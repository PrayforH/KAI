"""Stable, content-minimizing mapping for Codex app-server JSON-RPC messages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from harness.runtime.base import RuntimeEvent


class CodexProtocolError(RuntimeError):
    """Raised when app-server emits an invalid or unsupported wire envelope."""


class CodexMessageKind(StrEnum):
    RESPONSE = "response"
    ERROR = "error"
    NOTIFICATION = "notification"
    SERVER_REQUEST = "server_request"


@dataclass(frozen=True)
class CodexMessage:
    kind: CodexMessageKind
    payload: dict[str, Any]


def classify_codex_message(value: object) -> CodexMessage:
    """Classify one app-server JSONL object without trusting nested content."""

    if not isinstance(value, dict):
        raise CodexProtocolError("app-server message must be a JSON object")
    message = cast(dict[str, Any], value)
    has_id = isinstance(message.get("id"), (str, int)) and not isinstance(
        message.get("id"), bool
    )
    has_method = isinstance(message.get("method"), str) and bool(message["method"])
    if has_id and has_method:
        return CodexMessage(CodexMessageKind.SERVER_REQUEST, message)
    if has_method:
        return CodexMessage(CodexMessageKind.NOTIFICATION, message)
    if has_id and "result" in message and "error" not in message:
        return CodexMessage(CodexMessageKind.RESPONSE, message)
    if has_id and "error" in message and "result" not in message:
        return CodexMessage(CodexMessageKind.ERROR, message)
    raise CodexProtocolError("unrecognized app-server message envelope")


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return cast(dict[str, Any], value)


def _text(value: object, *, max_chars: int = 20_000) -> str:
    if not isinstance(value, str):
        return ""
    return value[:max_chars]


def _identifier(value: object) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    return str(value)[:200]


def _safe_usage(value: object) -> dict[str, int]:
    usage = _mapping(value)
    allowed = (
        "inputTokens",
        "cachedInputTokens",
        "cacheWriteInputTokens",
        "outputTokens",
        "reasoningOutputTokens",
        "totalTokens",
        "modelContextWindow",
    )
    return {
        key: number
        for key in allowed
        if isinstance((number := usage.get(key)), int)
        and not isinstance(number, bool)
        and number >= 0
    }


def _safe_command_item(item: Mapping[str, Any]) -> dict[str, Any]:
    command = item.get("command")
    if isinstance(command, list):
        command_value: str | list[str] = [
            _text(part, max_chars=2_000)
            for part in cast(list[object], command)
            if isinstance(part, str)
        ][:100]
    else:
        command_value = _text(command, max_chars=8_000)
    return {
        "tool_call_id": _identifier(item.get("id")),
        "name": "Bash",
        "arguments": {
            "command": command_value,
            "cwd": _text(item.get("cwd"), max_chars=2_000),
        },
    }


def _safe_file_change_item(item: Mapping[str, Any]) -> dict[str, Any]:
    changes: list[dict[str, str]] = []
    raw_changes = item.get("changes")
    if isinstance(raw_changes, list):
        for raw in cast(list[object], raw_changes)[:500]:
            change = _mapping(raw)
            path = _text(change.get("path"), max_chars=2_000)
            kind = _text(change.get("kind"), max_chars=100)
            if path or kind:
                changes.append({"path": path, "kind": kind})
    return {
        "tool_call_id": _identifier(item.get("id")),
        "name": "Edit",
        "arguments": {"changes": changes},
    }


def _safe_tool_item(item: Mapping[str, Any]) -> dict[str, Any]:
    server = _text(item.get("server"), max_chars=200)
    tool = _text(item.get("tool"), max_chars=200)
    name = "__".join(part for part in ("mcp", server, tool) if part) or "tool"
    arguments = item.get("arguments")
    return {
        "tool_call_id": _identifier(item.get("id")),
        "name": name,
        "arguments": arguments if isinstance(arguments, dict) else {},
    }


def _item_request(item: Mapping[str, Any]) -> RuntimeEvent | None:
    item_type = item.get("type")
    if item_type == "commandExecution":
        payload = _safe_command_item(item)
    elif item_type == "fileChange":
        payload = _safe_file_change_item(item)
    elif item_type in {"mcpToolCall", "dynamicToolCall"}:
        payload = _safe_tool_item(item)
    else:
        return None
    return RuntimeEvent(type="tool.request", payload=payload)


def _item_result(item: Mapping[str, Any]) -> RuntimeEvent | None:
    requested = _item_request(item)
    if requested is None:
        return None
    payload: dict[str, Any] = {
        "tool_call_id": requested.payload["tool_call_id"],
        "name": requested.payload["name"],
        "status": _text(item.get("status"), max_chars=100) or "completed",
    }
    if item.get("type") == "commandExecution":
        payload["exit_code"] = (
            value
            if isinstance((value := item.get("exitCode")), int)
            and not isinstance(value, bool)
            else None
        )
        payload["aggregated_output"] = _text(
            item.get("aggregatedOutput"), max_chars=20_000
        )
    return RuntimeEvent(type="tool.result", payload=payload)


def _turn_status(params: Mapping[str, Any]) -> str:
    turn = _mapping(params.get("turn"))
    return _text(turn.get("status"), max_chars=100) or _text(
        params.get("status"), max_chars=100
    )


def map_codex_notification(message: Mapping[str, Any]) -> list[RuntimeEvent]:
    """Map stable app-server notifications into vendor-neutral RuntimeEvents."""

    method = message.get("method")
    if not isinstance(method, str):
        return []
    params = _mapping(message.get("params"))

    if method == "thread/started":
        thread = _mapping(params.get("thread"))
        thread_id = _identifier(thread.get("id") or params.get("threadId"))
        return [
            RuntimeEvent(
                type="runtime.thread.started",
                payload={"thread_id": thread_id, "runtime": "codex-app-server"},
            )
        ]
    if method == "turn/started":
        return [RuntimeEvent(type="message.start")]
    if method == "item/agentMessage/delta":
        delta = _text(params.get("delta"))
        return [RuntimeEvent(type="message.delta", payload={"text": delta})] if delta else []
    if method == "item/started":
        event = _item_request(_mapping(params.get("item")))
        return [event] if event is not None else []
    if method == "item/completed":
        event = _item_result(_mapping(params.get("item")))
        return [event] if event is not None else []
    if method == "thread/tokenUsage/updated":
        usage = _safe_usage(params.get("tokenUsage") or params.get("usage"))
        return [RuntimeEvent(type="usage.updated", payload=usage)] if usage else []
    if method == "turn/completed":
        status = _turn_status(params) or "completed"
        return [
            RuntimeEvent(type="message.completed"),
            RuntimeEvent(type="runtime.turn.completed", payload={"status": status}),
        ]
    if method == "error":
        error = _mapping(params.get("error"))
        info = error.get("codexErrorInfo")
        if isinstance(info, dict):
            typed_info = cast(dict[str, Any], info)
            code = next(iter(typed_info), "Other")
            status = typed_info.get("httpStatusCode")
        else:
            code = _text(info, max_chars=200) or "Other"
            status = None
        payload: dict[str, Any] = {"code": code, "runtime": "codex-app-server"}
        if isinstance(status, int) and not isinstance(status, bool):
            payload["http_status"] = status
        return [RuntimeEvent(type="runtime.error", payload=payload)]
    return []
