"""Turn durable run events into presentations, deterministically.

Two producers feed the same protocol object:

* the model, by ending its answer with a fenced ``harness-ui`` block, and
* the platform, by projecting an existing structured tool result whose shape is
  already a table.

Both live in one pure function over the run's events so the live stream and the
rebuilt history cannot disagree: the same events always yield the same
presentations, in the same order. Nothing here mutates business state, and a
wrong or unrenderable shape simply yields nothing — the answer text is untouched.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from typing import Any

from harness.core.events import RunEvent
from harness.presentation.catalog import MAX_PRESENTATIONS_PER_TURN
from harness.presentation.schema import (
    MAX_COLUMNS,
    MAX_ROWS,
    Presentation,
    validate_block,
)

_FENCE = re.compile(r"```([^\n]*)\n([\s\S]*?)\n```")
_BARE_OBJECT_START = re.compile(r'(?:^|\n)[ \t]*(?=\{\s*"version"\s*:\s*1\s*,)')

# Tools whose results already have a purpose-built presentation, or which are
# themselves projections. A generic table on top of them would duplicate the UI.
_SHAPE_EXCLUDED_TOOLS = frozenset(
    {
        "harness_run_activity",
        "harness_present_artifact",
        "harness_present_ui",
        "harness_request_approval",
    }
)
_SHAPE_EXCLUDED_PREFIXES = ("mcp__harness-knowledge__", "mcp__harness-memory__")
# Only these keys are treated as "the list of rows" inside a result object. A
# looser rule starts inventing tables out of arbitrary payloads.
_SHAPE_ROW_KEYS = ("rows", "items", "records", "results", "entries", "data", "points")
_SHAPE_MIN_ROWS = 5
_SHAPE_MAX_COLUMNS = 6
_SHAPE_TEXT_CELL_LIMIT = 160


def _is_error(payload: dict[str, Any]) -> bool:
    return bool(payload.get("is_error")) or bool(payload.get("redacted"))


def _text_parts(payload: dict[str, Any]) -> list[str]:
    content = payload.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return parts
    if isinstance(content, dict):
        text = content.get("text")
        return [text] if isinstance(text, str) else []
    return []


def _loads_structured(payload: dict[str, Any]) -> Any:
    for text in _text_parts(payload):
        candidate = text.strip()
        if not candidate or candidate[0] not in "[{":
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def _scalar_row(row: dict[str, Any], keys: Sequence[str]) -> dict[str, Any] | None:
    kept: dict[str, Any] = {}
    for key in keys:
        value = row.get(key)
        if value is None or isinstance(value, bool | int | float):
            kept[key] = value
        elif isinstance(value, str) and len(value) <= _SHAPE_TEXT_CELL_LIMIT:
            kept[key] = value
    return kept or None


def _shape_rows(parsed: Any) -> list[dict[str, Any]] | None:
    if isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict):
        rows = next(
            (parsed[key] for key in _SHAPE_ROW_KEYS if isinstance(parsed.get(key), list)),
            None,
        )
    else:
        return None
    if not isinstance(rows, list) or not _SHAPE_MIN_ROWS <= len(rows) <= MAX_ROWS:
        return None
    if not all(isinstance(row, dict) and row for row in rows):
        return None
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    if len(keys) < 2:
        return None
    shaped: list[dict[str, Any]] = []
    for row in rows:
        cell = _scalar_row(row, keys[:_SHAPE_MAX_COLUMNS])
        if cell is None:
            return None
        shaped.append(cell)
    if any(len(row) < 2 for row in shaped):
        return None
    return shaped


def _shape_block(parsed: Any) -> dict[str, Any] | None:
    rows = _shape_rows(parsed)
    if rows is None:
        return None
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    columns = [{"key": key, "label": key} for key in keys[:MAX_COLUMNS]]
    return {
        "version": 1,
        "kind": "table",
        "fallback_text": f"共 {len(rows)} 条记录，字段：{'、'.join(keys[:MAX_COLUMNS])}。",
        "data": {"columns": columns, "rows": rows},
    }


def _fenced_blocks(text: str) -> Iterable[Any]:
    matched_spans: list[tuple[int, int]] = []
    for match in _FENCE.finditer(text):
        matched_spans.append((match.start(), match.end()))
        if match.group(1).strip().lower() != "harness-ui":
            continue
        try:
            yield json.loads(match.group(2))
        except (ValueError, TypeError):
            continue
    # Models occasionally drop the fence but keep the object. Accept only a
    # complete brace-balanced object that starts a line and declares a kind.
    for start in _BARE_OBJECT_START.finditer(text):
        offset = start.start() + len(start.group(0))
        if any(begin <= offset < end for begin, end in matched_spans):
            continue
        depth = 0
        quoted = False
        escaped = False
        for index in range(offset, min(len(text), offset + 64_000)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(text[offset : index + 1])
                    except (ValueError, TypeError):
                        break
                    if isinstance(candidate, dict) and isinstance(candidate.get("kind"), str):
                        yield candidate
                    break


def _message_buffers(events: Sequence[RunEvent], run_id: str) -> dict[str, list[str]]:
    buffers: dict[str, list[str]] = {}
    for event in events:
        if event.type == "message.delta":
            message_id = _message_id(event, run_id)
            text = event.payload.get("text")
            if isinstance(text, str):
                buffers.setdefault(message_id, []).append(text)
    return buffers


def _message_id(event: RunEvent, run_id: str) -> str:
    raw = event.payload.get("message_id")
    if isinstance(raw, str) and raw:
        return raw
    return f"assistant-{run_id}"


def presentations_for_events(
    events: Sequence[RunEvent],
    *,
    limit: int = MAX_PRESENTATIONS_PER_TURN,
) -> list[Presentation]:
    """Every presentation the run's events justify, in durable sequence order."""

    run_id = next((event.run_id for event in events if event.run_id), "")
    if not run_id:
        return []
    tool_names = {
        str(event.payload.get("tool_call_id")): str(event.payload.get("name", ""))
        for event in events
        if event.type in {"tool.request", "tool.allowed"}
        and event.payload.get("tool_call_id")
    }
    buffers = _message_buffers(events, run_id)
    presentations: list[Presentation] = []
    index = 0
    for event in events:
        if event.type == "message.completed":
            message_id = _message_id(event, run_id)
            text = "".join(buffers.get(message_id, ()))
            if not text:
                continue
            for block in _fenced_blocks(text):
                presentation = validate_block(
                    block,
                    run_id=run_id,
                    message_id=message_id,
                    index=index,
                    origin="model",
                    reason_code="model_selected",
                )
                if presentation is not None:
                    presentations.append(presentation)
                    index += 1
        elif event.type == "tool.result":
            if _is_error(event.payload):
                continue
            tool_name = tool_names.get(str(event.payload.get("tool_call_id")), "")
            if tool_name in _SHAPE_EXCLUDED_TOOLS or tool_name.startswith(
                _SHAPE_EXCLUDED_PREFIXES
            ):
                continue
            block = _shape_block(_loads_structured(event.payload))
            if block is None:
                continue
            presentation = validate_block(
                block,
                run_id=run_id,
                message_id=_message_id(event, run_id),
                index=index,
                origin="shape",
                reason_code="structured_tool_result",
            )
            if presentation is not None:
                presentations.append(presentation)
                index += 1
        if len(presentations) >= limit:
            break
    return presentations[:limit]
