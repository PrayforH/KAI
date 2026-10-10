"""The ``harness.ui/v1`` payload: server-owned identity plus model-authored content.

A model (or the deterministic shape projection) submits only ``kind``, ``data``,
``config`` and a ``fallback_text``. Everything that decides what the user is shown
— ids, revision, owning run/message, renderer version, policy version — is filled
here, so a model can never address another run or invent a download URL.

Validation is deliberately strict and total: an unknown kind, an extra field, an
out-of-range row count or an over-long cell rejects the whole block. Callers must
render the untouched answer instead of a partial widget.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

UI_SCHEMA_VERSION = "harness.ui/v1"
PRESENTATION_POLICY_VERSION = "presentation-policy/1"

# Resource limits. These are the numbers the catalog advertises to the model and
# the numbers validation enforces; they live here once so the two cannot drift.
MAX_ROWS = 200
MAX_COLUMNS = 12
MAX_CELLS = 2_000
MAX_ITEMS = 24
MAX_EVENTS = 60
MAX_CHART_SERIES = 4
MAX_STRING = 600
MAX_MERMAID_CHARS = 8_000
MAX_BLOCK_CHARS = 64_000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _scalar(value: Any) -> str | int | float | bool | None:
    """Normalise one cell.

    Only JSON scalars are renderable. Anything else is a shape error the caller
    must handle rather than a value to coerce silently.
    """

    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value
    raise ValueError("cell is not a JSON scalar")


def _text(value: Any, *, maximum: int, field_name: str, required: bool = True) -> str:
    """One short, trimmed string field. A missing value is allowed only when optional."""

    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text and required:
        raise ValueError(f"{field_name} is required")
    if len(text) > maximum:
        raise ValueError(f"{field_name} exceeds {maximum} characters")
    return text


def _rows(value: Any, *, maximum: int, minimum: int = 1) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"rows must hold between {minimum} and {maximum} entries")
    rows: list[dict[str, Any]] = []
    for row in value:
        if not isinstance(row, dict) or not row:
            raise ValueError("every row must be a non-empty object")
        if len(row) > MAX_COLUMNS:
            raise ValueError(f"a row may carry at most {MAX_COLUMNS} columns")
        rows.append({str(key): _scalar(item) for key, item in row.items()})
    return rows


class _Column(StrictModel):
    key: str
    label: str | None = None
    align: Literal["left", "right", "center"] | None = None


def _normalise_column(raw: Any) -> Any:
    """Accept the ways models actually name a column.

    ``{"id": ...}`` and ``{"key": ...}`` mean the same thing, and a dimension that
    is only a name arrives as a bare string. Normalising keeps an otherwise
    correct answer from being discarded over a synonym; everything downstream —
    renderer included — sees ``{"key", "label"}``.
    """

    if isinstance(raw, str):
        text = raw.strip()
        return {"key": text, "label": text} if text else raw
    if isinstance(raw, dict) and "key" not in raw and "id" in raw:
        return {**{k: v for k, v in raw.items() if k != "id"}, "key": raw["id"]}
    return raw


def _item_label(item: dict[str, Any]) -> Any:
    """A comparison item's name, however the model spelled it."""

    for field in ("label", "key", "name"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _columns(value: Any, field_name: str = "columns") -> list[_Column]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_COLUMNS:
        raise ValueError(f"{field_name} must hold between 1 and {MAX_COLUMNS} entries")
    columns: list[_Column] = []
    seen: set[str] = set()
    for item in value:
        column = _Column.model_validate(_normalise_column(item))
        if not column.key.strip():
            raise ValueError("column key must not be empty")
        if column.key in seen:
            raise ValueError(f"duplicate column key: {column.key}")
        seen.add(column.key)
        columns.append(column)
    return columns


def _sorted_columns(rows: list[dict[str, Any]]) -> list[_Column]:
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    return [_Column(key=key, label=None) for key in keys[:MAX_COLUMNS]]


def _table(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _rows(data.get("rows"), maximum=MAX_ROWS)
    columns = _columns(data["columns"]) if data.get("columns") else _sorted_columns(rows)
    if len(rows) * len(columns) > MAX_CELLS:
        raise ValueError(f"table exceeds {MAX_CELLS} cells")
    for row in rows:
        for column in columns:
            cell = row.get(column.key)
            if isinstance(cell, str) and len(cell) > MAX_STRING:
                raise ValueError(f"cell in column {column.key} exceeds {MAX_STRING} characters")
    return (
        {"columns": [column.model_dump() for column in columns], "rows": rows},
        {"caption": _text(config.get("caption"), maximum=MAX_STRING, field_name="caption",
                          required=False)},
    )


def _comparison(
    data: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    dimensions = _columns(data.get("dimensions"), "dimensions")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 2 <= len(raw_items) <= MAX_ITEMS:
        raise ValueError(f"items must hold between 2 and {MAX_ITEMS} entries")
    declared = [dimension.key for dimension in dimensions]
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("every item must be an object")
        label = _text(_item_label(item), maximum=MAX_STRING, field_name="item label")
        values = item.get("values")
        if isinstance(values, list):
            # A model that reads `values` as "one value per dimension, in order"
            # is describing the same table; align it instead of rejecting.
            if len(values) != len(declared):
                raise ValueError("positional values must match dimensions in length")
            values = dict(zip(declared, values, strict=True))
        if not isinstance(values, dict):
            raise ValueError("item values must be an object or a positional list")
        unknown = set(values) - set(declared)
        if unknown:
            raise ValueError(f"item carries undeclared dimensions: {sorted(unknown)}")
        items.append(
            {"label": label, "values": {key: _scalar(value) for key, value in values.items()}}
        )
    return (
        {
            "dimensions": [dimension.model_dump() for dimension in dimensions],
            "items": items,
        },
        {"highlight": _text(config.get("highlight"), maximum=MAX_STRING,
                            field_name="highlight", required=False)},
    )


_CHART_TYPES = ("bar", "line", "area", "pie")


def _chart(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    chart_type = config.get("chart_type")
    if chart_type not in _CHART_TYPES:
        raise ValueError(f"chart_type must be one of {list(_CHART_TYPES)}")
    rows = _rows(data.get("rows"), maximum=MAX_ROWS)
    keys = list(rows[0])
    x_field = _text(config.get("x_field"), maximum=64, field_name="x_field")
    if x_field not in keys:
        raise ValueError(f"x_field {x_field!r} is not a column of rows")
    y_fields = config.get("y_fields")
    if y_fields is None:
        y_fields = [config.get("y_field")]
    if not isinstance(y_fields, list) or not 1 <= len(y_fields) <= MAX_CHART_SERIES:
        raise ValueError(f"y_fields must hold between 1 and {MAX_CHART_SERIES} entries")
    labels = config.get("y_labels")
    if labels is not None and (not isinstance(labels, list) or len(labels) != len(y_fields)):
        raise ValueError("y_labels must match y_fields in length")
    resolved: list[str] = []
    for field in y_fields:
        if not isinstance(field, str) or field not in keys:
            raise ValueError(f"y field {field!r} is not a column of rows")
        if field == x_field:
            raise ValueError("a series cannot reuse the x field")
        resolved.append(field)
    if chart_type == "pie" and len(resolved) != 1:
        raise ValueError("a pie chart carries exactly one series")
    return (
        {"rows": rows},
        {
            "chart_type": chart_type,
            "x_field": x_field,
            "y_fields": resolved,
            "y_labels": [
                _text(label, maximum=64, field_name="y_label") for label in (labels or resolved)
            ],
            "x_label": _text(config.get("x_label"), maximum=64, field_name="x_label",
                             required=False),
            "y_label": _text(config.get("y_label"), maximum=64, field_name="y_label",
                             required=False),
            "unit": _text(config.get("unit"), maximum=32, field_name="unit", required=False),
        },
    )


def _diagram(
    data: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    mermaid = _text(data.get("mermaid"), maximum=MAX_MERMAID_CHARS, field_name="mermaid")
    return {"mermaid": mermaid}, {}


def _timeline(
    data: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_events = data.get("events")
    if not isinstance(raw_events, list) or not 1 <= len(raw_events) <= MAX_EVENTS:
        raise ValueError(f"events must hold between 1 and {MAX_EVENTS} entries")
    events: list[dict[str, Any]] = []
    for item in raw_events:
        if not isinstance(item, dict):
            raise ValueError("every timeline entry must be an object")
        events.append(
            {
                "at": _text(item.get("at"), maximum=64, field_name="at"),
                "title": _text(item.get("title"), maximum=MAX_STRING, field_name="title"),
                "detail": _text(item.get("detail"), maximum=MAX_STRING, field_name="detail",
                                required=False),
            }
        )
    return {"events": events}, {}


def _metric(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 8:
        raise ValueError("items must hold between 1 and 8 entries")
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("every metric must be an object")
        items.append(
            {
                "label": _text(item.get("label"), maximum=MAX_STRING, field_name="label"),
                "value": _scalar(item.get("value")),
                "unit": _text(item.get("unit"), maximum=32, field_name="unit", required=False),
                "hint": _text(item.get("hint"), maximum=MAX_STRING, field_name="hint",
                              required=False),
            }
        )
    return {"items": items}, {}


_BUILDERS = {
    "table": _table,
    "comparison": _comparison,
    "chart": _chart,
    "diagram": _diagram,
    "timeline": _timeline,
    "metric": _metric,
}


class PresentationBlock(StrictModel):
    """What a model may author inside a ``harness-ui`` fence."""

    version: Literal[1] = 1
    kind: str
    title: str | None = None
    fallback_text: str
    data: dict[str, Any]
    config: dict[str, Any] = Field(default_factory=dict)


class Presentation(StrictModel):
    """The server-filled protocol object handed to a renderer."""

    schema_version: str = UI_SCHEMA_VERSION
    presentation_id: str
    revision: int = 1
    run_id: str
    message_id: str
    placement: Literal["inline", "workspace"] = "inline"
    kind: str
    renderer_version: str
    title: str | None = None
    fallback_text: str
    data: dict[str, Any]
    config: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any]


def presentation_id_for(run_id: str, message_id: str, index: int, payload: dict[str, Any]) -> str:
    """A stable id: the same events always rebuild the same presentation."""

    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    seed = f"{run_id}:{message_id}:{index}:{digest}".encode()
    return "ui_" + hashlib.sha256(seed).hexdigest()[:24]


def validate_block(
    raw: Any,
    *,
    run_id: str,
    message_id: str,
    index: int,
    origin: str,
    reason_code: str,
) -> Presentation | None:
    """Return the validated presentation, or ``None`` when nothing may be shown."""

    if not isinstance(raw, dict):
        return None
    if len(json.dumps(raw, default=str)) > MAX_BLOCK_CHARS:
        return None
    try:
        block = PresentationBlock.model_validate(raw)
    except ValidationError:
        return None
    builder = _BUILDERS.get(block.kind)
    if builder is None:
        return None
    try:
        data, config = builder(block.data, block.config)
        title = (
            _text(block.title, maximum=MAX_STRING, field_name="title", required=False)
            if block.title
            else ""
        )
        fallback = _text(block.fallback_text, maximum=MAX_STRING, field_name="fallback_text")
    except (ValueError, ValidationError):
        return None
    return Presentation(
        presentation_id=presentation_id_for(run_id, message_id, index, raw),
        run_id=run_id,
        message_id=message_id,
        kind=block.kind,
        renderer_version=f"{block.kind}/1",
        title=title or None,
        fallback_text=fallback,
        data=data,
        config=config,
        decision={
            "reason_code": reason_code,
            "rule_version": PRESENTATION_POLICY_VERSION,
            "origin": origin,
        },
    )
