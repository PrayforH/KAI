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
    has_id = isinstance(message.get("id"), (str, int)) and not isinstance(message.get("id"), bool)
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


def _diagnostic_fragments(value: object, *, limit: int = 40) -> tuple[str, ...]:
    """Flatten error metadata for classification without returning its content."""

    values: list[str] = []

    def visit(item: object) -> None:
        if len(values) >= limit:
            return
        if isinstance(item, str):
            values.append(item[:500].lower())
        elif isinstance(item, dict):
            for key, nested in cast(dict[object, object], item).items():
                if isinstance(key, str):
                    values.append(key[:200].lower())
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in cast(list[object] | tuple[object, ...], item)[:20]:
                visit(nested)

    visit(value)
    return tuple(values)


def _nested_http_status(value: object) -> int | None:
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        status = mapping.get("httpStatusCode")
        if isinstance(status, int) and not isinstance(status, bool):
            return status
        for nested in mapping.values():
            if (found := _nested_http_status(nested)) is not None:
                return found
    if isinstance(value, (list, tuple)):
        for nested in cast(list[object] | tuple[object, ...], value):
            if (found := _nested_http_status(nested)) is not None:
                return found
    return None


def _classified_error_code(error: Mapping[str, Any]) -> tuple[str, int | None]:
    info = error.get("codexErrorInfo")
    if isinstance(info, dict):
        typed_info = cast(dict[str, Any], info)
        wire_code = _text(next(iter(typed_info), "Other"), max_chars=200) or "Other"
    else:
        wire_code = _text(info, max_chars=200) or "Other"
    diagnostic = " ".join(_diagnostic_fragments((error.get("message"), info)))
    classifications = (
        (("context window", "context_length", "too many tokens"), "ContextWindowExceeded"),
        (("rate limit", "too many requests", "429"), "RateLimited"),
        (("unauthorized", "authentication", "invalid api key", "401"), "AuthenticationFailed"),
        (("tool call limit", "maximum tool", "max turns"), "ToolCallLimitExceeded"),
        (("connection", "connect", "unavailable", "timeout"), "HttpConnectionFailed"),
    )
    if wire_code.lower() == "other":
        for hints, classified in classifications:
            if any(hint in diagnostic for hint in hints):
                return classified, _nested_http_status(info)
    return wire_code, _nested_http_status(info)


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
            if isinstance((value := item.get("exitCode")), int) and not isinstance(value, bool)
            else None
        )
        payload["aggregated_output"] = _text(item.get("aggregatedOutput"), max_chars=20_000)
    return RuntimeEvent(type="tool.result", payload=payload)


def _turn_status(params: Mapping[str, Any]) -> str:
    turn = _mapping(params.get("turn"))
    return _text(turn.get("status"), max_chars=100) or _text(params.get("status"), max_chars=100)


_COLLAB_ITEM_TYPES = frozenset({"collabAgentToolCall", "collabToolCall"})
_COLLAB_TERMINAL_SUCCESS = frozenset({"completed", "shutdown"})
_COLLAB_TERMINAL_FAILURE = frozenset({"errored", "interrupted", "notFound"})


def _notification_thread_id(params: Mapping[str, Any]) -> str:
    return _identifier(params.get("threadId"))


def _collab_receiver_ids(item: Mapping[str, Any]) -> tuple[str, ...]:
    raw_ids = item.get("receiverThreadIds")
    values: list[object] = cast(list[object], raw_ids) if isinstance(raw_ids, list) else []
    values.extend((item.get("receiverThreadId"), item.get("newThreadId")))
    unique: list[str] = []
    for value in values:
        thread_id = _identifier(value)
        if thread_id and thread_id not in unique:
            unique.append(thread_id)
    return tuple(unique)


class CodexNotificationMapper:
    """Project app-server notifications without mixing child threads into the lead."""

    def __init__(self, root_thread_id: str) -> None:
        self._root_thread_id = root_thread_id
        self._started_children: set[str] = set()
        self._terminal_children: set[str] = set()
        self._child_summaries: dict[str, str] = {}
        self._child_metadata: dict[str, dict[str, Any]] = {}

    def _is_child(self, params: Mapping[str, Any]) -> bool:
        thread_id = _notification_thread_id(params)
        return bool(thread_id and thread_id != self._root_thread_id)

    def _start_child(
        self,
        thread_id: str,
        *,
        item: Mapping[str, Any] | None = None,
    ) -> list[RuntimeEvent]:
        if not thread_id or thread_id in self._started_children:
            return []
        self._started_children.add(thread_id)
        collab = item or {}
        prompt = _text(collab.get("prompt"), max_chars=2_000)
        alias = (
            _text(collab.get("agentType"), max_chars=200)
            or _text(collab.get("agent"), max_chars=200)
            or "codex-native"
        )
        metadata = {
            "event_schema": "harness.subagent.v1",
            "task_id": thread_id,
            "parent_tool_use_id": _identifier(collab.get("id")),
            "alias": alias,
            "agent_name": "codex-native",
            "depth": 1,
            "status": "running",
        }
        if prompt:
            metadata["description"] = prompt
        model = _text(collab.get("model"), max_chars=200)
        if model:
            metadata["model"] = model
        self._child_metadata[thread_id] = metadata
        return [RuntimeEvent(type="subagent.started", payload=metadata)]

    def _finish_child(self, thread_id: str, status: str) -> list[RuntimeEvent]:
        if not thread_id or thread_id in self._terminal_children:
            return []
        events = self._start_child(thread_id)
        self._terminal_children.add(thread_id)
        failed = status in _COLLAB_TERMINAL_FAILURE or status == "failed"
        payload = {
            **self._child_metadata.get(
                thread_id,
                {
                    "event_schema": "harness.subagent.v1",
                    "task_id": thread_id,
                    "alias": "codex-native",
                    "agent_name": "codex-native",
                    "depth": 1,
                },
            ),
            "status": status or ("failed" if failed else "completed"),
        }
        summary = self._child_summaries.get(thread_id, "").strip()
        if summary:
            payload["summary"] = summary[-2_000:]
        if failed:
            payload["error_code"] = f"codex_subagent_{status or 'failed'}"
        events.append(
            RuntimeEvent(
                type="subagent.failed" if failed else "subagent.completed",
                payload=payload,
            )
        )
        return events

    def _collab_events(self, item: Mapping[str, Any]) -> list[RuntimeEvent]:
        if item.get("type") not in _COLLAB_ITEM_TYPES:
            return []
        tool = _text(item.get("tool"), max_chars=100)
        states = _mapping(item.get("agentsStates"))
        receiver_ids = list(_collab_receiver_ids(item))
        receiver_ids.extend(thread_id for thread_id in states if thread_id not in receiver_ids)
        events: list[RuntimeEvent] = []
        for thread_id in receiver_ids:
            if tool == "spawnAgent":
                events.extend(self._start_child(thread_id, item=item))
            state = _mapping(states.get(thread_id))
            status = _text(state.get("status"), max_chars=100)
            message = _text(state.get("message"), max_chars=2_000)
            if message:
                self._child_summaries[thread_id] = message
            if status in _COLLAB_TERMINAL_SUCCESS | _COLLAB_TERMINAL_FAILURE:
                events.extend(self._finish_child(thread_id, status))
        return events

    def map(self, message: Mapping[str, Any]) -> list[RuntimeEvent]:
        method = message.get("method")
        if not isinstance(method, str):
            return []
        params = _mapping(message.get("params"))
        item = _mapping(params.get("item"))

        if method == "thread/started":
            thread = _mapping(params.get("thread"))
            started_thread_id = _identifier(thread.get("id") or params.get("threadId"))
            if started_thread_id and started_thread_id != self._root_thread_id:
                return self._start_child(started_thread_id)

        if item.get("type") in _COLLAB_ITEM_TYPES:
            return self._collab_events(item)

        if self._is_child(params):
            thread_id = _notification_thread_id(params)
            if method == "turn/started":
                return self._start_child(thread_id)
            if method == "item/agentMessage/delta":
                delta = _text(params.get("delta"))
                if not delta:
                    return []
                self._child_summaries[thread_id] = (
                    self._child_summaries.get(thread_id, "") + delta
                )[-2_000:]
                return [
                    *self._start_child(thread_id),
                    RuntimeEvent(
                        type="subagent.delta",
                        payload={"task_id": thread_id, "text": delta},
                    ),
                ]
            if method in {"item/started", "item/completed"}:
                requested = _item_request(item)
                if requested is None:
                    return []
                return [
                    *self._start_child(thread_id),
                    RuntimeEvent(
                        type="subagent.updated",
                        payload={
                            "task_id": thread_id,
                            "status": ("running" if method == "item/started" else "completed"),
                            "last_tool_name": requested.payload["name"],
                        },
                    ),
                ]
            if method == "turn/completed":
                return self._finish_child(thread_id, _turn_status(params) or "completed")
            return []

        return map_codex_notification(message)


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
    if method == "item/reasoning/summaryTextDelta":
        delta = _text(params.get("delta"))
        if not delta:
            return []
        item_id = _identifier(params.get("itemId"))
        payload: dict[str, Any] = {"text": delta}
        if item_id:
            payload["item_id"] = item_id
        return [RuntimeEvent(type="reasoning.summary.delta", payload=payload)]
    if method == "item/reasoning/textDelta":
        # Raw hidden reasoning is intentionally not persisted or projected.
        # Only the provider-authored summary channel above is user-visible.
        return []
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
        code, status = _classified_error_code(error)
        payload: dict[str, Any] = {"code": code, "runtime": "codex-app-server"}
        if isinstance(status, int) and not isinstance(status, bool):
            payload["http_status"] = status
        return [RuntimeEvent(type="runtime.error", payload=payload)]
    return []
