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

import hashlib
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, cast

from pydantic import ValidationError

from harness.core.events import RunEvent
from harness.core.models import Artifact, ArtifactStatus
from harness.presentation.catalog import MAX_PRESENTATIONS_PER_TURN
from harness.presentation.schema import (
    MAX_BLOCK_CHARS,
    MAX_COLUMNS,
    MAX_HTML_BYTES,
    MAX_ROWS,
    MAX_STRING,
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
        for block in cast(list[Any], content):
            if isinstance(block, dict):
                text = cast(dict[str, Any], block).get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return parts
    if isinstance(content, dict):
        text = cast(dict[str, Any], content).get("text")
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
        rows = cast(list[Any], parsed)
    elif isinstance(parsed, dict):
        parsed = cast(dict[str, Any], parsed)
        rows = next(
            (parsed[key] for key in _SHAPE_ROW_KEYS if isinstance(parsed.get(key), list)),
            None,
        )
    else:
        return None
    if not isinstance(rows, list) or not _SHAPE_MIN_ROWS <= len(cast(list[Any], rows)) <= MAX_ROWS:
        return None
    rows = cast(list[Any], rows)
    if not all(isinstance(row, dict) and bool(cast(dict[str, Any], row)) for row in rows):
        return None
    rows = cast(list[dict[str, Any]], rows)
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


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _object_end(text: str, offset: int) -> int | None:
    """Index just past the brace-balanced object that starts at ``offset``."""

    depth = 0
    in_string = False
    escaped = False
    for index in range(offset, min(len(text), offset + MAX_BLOCK_CHARS)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _unfenced_blocks(text: str, spans: list[tuple[int, int]]) -> Iterator[tuple[Any, str]]:
    """A model occasionally keeps the object and drops the fence. Accept only a
    complete, brace-balanced object that starts a line and declares a kind."""

    for start in _BARE_OBJECT_START.finditer(text):
        offset = start.start() + len(start.group(0))
        if any(begin <= offset < end for begin, end in spans):
            continue
        end = _object_end(text, offset)
        if end is None:
            continue
        candidate = _maybe_json(text[offset:end])
        if isinstance(candidate, dict) and isinstance(
            cast(dict[str, Any], candidate).get("kind"), str
        ):
            yield candidate, text[offset:end]


def _fenced_blocks(text: str) -> Iterator[tuple[Any, str]]:
    spans: list[tuple[int, int]] = []
    for match in _FENCE.finditer(text):
        spans.append((match.start(), match.end()))
        if match.group(1).strip().lower() != "harness-ui":
            continue
        parsed = _maybe_json(match.group(2))
        if parsed is not None:
            yield parsed, match.group(0)
    yield from _unfenced_blocks(text, spans)


@dataclass(frozen=True)
class _Candidate:
    """An unvalidated block plus the provenance the protocol records for it."""

    block: Any
    message_id: str
    origin: str
    reason_code: str
    sequence: int
    source_text: str | None = None


def _message_id(event: RunEvent, run_id: str) -> str:
    raw = event.payload.get("message_id")
    if isinstance(raw, str) and raw:
        return raw
    return f"assistant-{run_id}"


def _tool_names(events: Sequence[RunEvent]) -> dict[str, str]:
    return {
        str(event.payload.get("tool_call_id")): str(event.payload.get("name", ""))
        for event in events
        if event.type in {"tool.request", "tool.allowed"} and event.payload.get("tool_call_id")
    }


def _candidates(events: Sequence[RunEvent], run_id: str) -> Iterator[_Candidate]:
    """Every block the run's events justify, in durable sequence order."""

    tool_names = _tool_names(events)
    buffers: dict[str, list[str]] = {}
    for event in events:
        if event.type == "message.delta":
            text = event.payload.get("text")
            if isinstance(text, str):
                buffers.setdefault(_message_id(event, run_id), []).append(text)
            continue
        if event.type == "message.completed":
            message_id = _message_id(event, run_id)
            for block, source_text in _fenced_blocks("".join(buffers.pop(message_id, ()))):
                yield _Candidate(
                    block, message_id, "model", "model_selected", event.sequence, source_text
                )
            continue
        if event.type != "tool.result" or _is_error(event.payload):
            continue
        tool_name = tool_names.get(str(event.payload.get("tool_call_id")), "")
        if tool_name in _SHAPE_EXCLUDED_TOOLS or tool_name.startswith(_SHAPE_EXCLUDED_PREFIXES):
            continue
        block = _shape_block(_loads_structured(event.payload))
        if block is not None:
            yield _Candidate(
                block, _message_id(event, run_id), "shape", "structured_tool_result", event.sequence
            )


def _html_artifacts(events: Sequence[RunEvent], run_id: str) -> dict[str, tuple[Artifact, int]]:
    """Only this run's authoritative, unambiguous ready files can back a frame."""
    scope = next((e for e in events if e.run_id == run_id), None)
    found: dict[str, tuple[Artifact, int]] = {}
    conflicting: set[str] = set()
    for event in events:
        if (
            scope is None
            or event.type != "artifact.ready"
            or (event.tenant_id, event.run_id, event.session_id)
            != (scope.tenant_id, run_id, scope.session_id)
        ):
            continue
        try:
            artifact = Artifact.model_validate(event.payload)
        except ValidationError:
            continue
        if (
            artifact.run_id != run_id
            or artifact.tenant_id != scope.tenant_id
            or artifact.status is not ArtifactStatus.READY
            or artifact.media_type.split(";", 1)[0].strip().lower() != "text/html"
            or not artifact.sha256
            or re.fullmatch(r"[0-9a-f]{64}", artifact.sha256) is None
            or artifact.size_bytes is None
            or not 0 < artifact.size_bytes <= MAX_HTML_BYTES
        ):
            continue
        previous = found.get(artifact.artifact_id)
        if previous is not None and previous[0] != artifact:
            conflicting.add(artifact.artifact_id)
        else:
            found.setdefault(artifact.artifact_id, (artifact, event.sequence))
    return {key: value for key, value in found.items() if key not in conflicting}


def presentations_for_events(
    events: Sequence[RunEvent],
    *,
    limit: int = MAX_PRESENTATIONS_PER_TURN,
) -> list[Presentation]:
    """Every presentation the run's events justify, in durable sequence order.

    The same events always yield the same blocks with the same ids, which is what
    lets the live stream and a rebuilt history agree without storing a copy.
    """

    run_id = next((event.run_id for event in events if event.run_id), "")
    if not run_id:
        return []
    presentations: list[Presentation] = []
    html_artifacts = _html_artifacts(events, run_id)
    # Explicit answer blocks take precedence over incidental tool-result tables.
    # Otherwise two search results can exhaust the turn's budget before its form.
    candidates = sorted(
        _candidates(events, run_id), key=lambda c: (c.origin != "model", c.sequence)
    )
    for candidate in candidates:
        if len(presentations) >= limit:
            break
        presentation = validate_block(
            candidate.block,
            run_id=run_id,
            message_id=candidate.message_id,
            index=len(presentations),
            origin=candidate.origin,
            reason_code=candidate.reason_code,
        )
        if presentation is not None:
            refs: tuple[dict[str, Any], ...] = (
                {"run_id": run_id, "sequence": candidate.sequence, "origin": candidate.origin},
            )
            if presentation.kind == "html":
                ready = html_artifacts.get(presentation.data["artifact_id"])
                if ready is None:
                    continue
                artifact, sequence = ready
                presentation = presentation.model_copy(
                    update={
                        "data": {
                            "artifact_id": artifact.artifact_id,
                            "name": artifact.name[:MAX_STRING],
                            "sha256": artifact.sha256,
                            "size_bytes": artifact.size_bytes,
                        }
                    }
                )
                refs += ({"run_id": run_id, "sequence": sequence, "origin": "artifact"},)
            presentations.append(
                presentation.model_copy(
                    update={
                        "source_text": candidate.source_text,
                        "source_refs": refs,
                        "snapshot_hash": hashlib.sha256(
                            json.dumps(
                                {
                                    "data": presentation.data,
                                    "config": presentation.config,
                                    "actions": presentation.actions,
                                },
                                sort_keys=True,
                                ensure_ascii=False,
                                allow_nan=False,
                            ).encode()
                        ).hexdigest(),
                    }
                )
            )
    return presentations
