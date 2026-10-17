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
import math
import re
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

UI_SCHEMA_VERSION = "harness.ui/v1"
PRESENTATION_POLICY_VERSION = "presentation-policy/8"

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
MAX_FORM_FIELDS = 12
MAX_ACTIONS = 4
MAX_ACTION_PROMPT = 400
MAX_ACTION_LABEL = 60
MAX_HTML_BYTES = 256 * 1024


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _scalar(value: Any) -> str | int | float | bool | None:
    """Normalise one cell.

    Only JSON scalars are renderable. Anything else is a shape error the caller
    must handle rather than a value to coerce silently.
    """

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("cell must be a finite number")
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        if len(cast(list[Any], value)) > MAX_STRING:
            raise ValueError("cell exceeds the string limit")
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
    if not isinstance(value, list) or not minimum <= len(cast(list[Any], value)) <= maximum:
        raise ValueError(f"rows must hold between {minimum} and {maximum} entries")
    rows: list[dict[str, Any]] = []
    for row in cast(list[Any], value):
        if not isinstance(row, dict) or not row:
            raise ValueError("every row must be a non-empty object")
        if len(cast(dict[str, Any], row)) > MAX_COLUMNS:
            raise ValueError(f"a row may carry at most {MAX_COLUMNS} columns")
        row = cast(dict[str, Any], row)
        rows.append({str(key): _scalar(item) for key, item in row.items()})
    return rows


class _Column(StrictModel):
    key: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=MAX_STRING)
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
    if isinstance(raw, dict):
        obj = cast(dict[str, Any], raw)
        if "key" not in obj and "id" in obj:
            return {**{k: v for k, v in obj.items() if k != "id"}, "key": obj["id"]}
        return obj
    return raw


def _item_label(item: dict[str, Any]) -> Any:
    """A comparison item's name, however the model spelled it."""

    for field in ("label", "key", "name"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _columns(value: Any, field_name: str = "columns") -> list[_Column]:
    if not isinstance(value, list) or not 1 <= len(cast(list[Any], value)) <= MAX_COLUMNS:
        raise ValueError(f"{field_name} must hold between 1 and {MAX_COLUMNS} entries")
    columns: list[_Column] = []
    seen: set[str] = set()
    for item in cast(list[Any], value):
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
    raw_rows = data.get("rows")
    columns = _columns(data["columns"]) if data.get("columns") else None
    entries = cast(list[Any], raw_rows) if isinstance(raw_rows, list) else []
    if any(isinstance(row, list) for row in entries):
        if columns is None or not 1 <= len(entries) <= MAX_ROWS:
            raise ValueError("positional rows require explicit columns and bounded rows")
        if not all(
            isinstance(row, list) and len(cast(list[Any], row)) == len(columns) for row in entries
        ):
            raise ValueError("positional rows must all match the declared columns")
        raw_rows = [
            dict(zip((column.key for column in columns), cast(list[Any], row), strict=True))
            for row in entries
        ]
    rows = _rows(raw_rows, maximum=MAX_ROWS)
    columns = columns if columns is not None else _sorted_columns(rows)
    if len(rows) * len(columns) > MAX_CELLS:
        raise ValueError(f"table exceeds {MAX_CELLS} cells")
    for row in rows:
        for column in columns:
            cell = row.get(column.key)
            if isinstance(cell, str) and len(cell) > MAX_STRING:
                raise ValueError(f"cell in column {column.key} exceeds {MAX_STRING} characters")
    return (
        {"columns": [column.model_dump() for column in columns], "rows": rows},
        {
            "caption": _text(
                config.get("caption"), maximum=MAX_STRING, field_name="caption", required=False
            )
        },
    )


def _comparison(
    data: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    dimensions = _columns(data.get("dimensions"), "dimensions")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 2 <= len(cast(list[Any], raw_items)) <= MAX_ITEMS:
        raise ValueError(f"items must hold between 2 and {MAX_ITEMS} entries")
    declared = [dimension.key for dimension in dimensions]
    items: list[dict[str, Any]] = []
    for item in cast(list[Any], raw_items):
        if not isinstance(item, dict):
            raise ValueError("every item must be an object")
        item = cast(dict[str, Any], item)
        label = _text(_item_label(item), maximum=MAX_STRING, field_name="item label")
        values = item.get("values")
        if isinstance(values, list):
            # A model that reads `values` as "one value per dimension, in order"
            # is describing the same table; align it instead of rejecting.
            if len(cast(list[Any], values)) != len(declared):
                raise ValueError("positional values must match dimensions in length")
            values = dict(zip(declared, cast(list[Any], values), strict=True))
        if not isinstance(values, dict):
            raise ValueError("item values must be an object or a positional list")
        values = cast(dict[str, Any], values)
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
        {
            "highlight": _text(
                config.get("highlight"), maximum=MAX_STRING, field_name="highlight", required=False
            )
        },
    )


_CHART_TYPES = ("bar", "line", "area", "pie")


def _field_alias(value: dict[str, Any], key: str, alias: str) -> Any:
    if key in value and alias in value and value[key] != value[alias]:
        raise ValueError(f"conflicting fields: {key}, {alias}")
    return value.get(key, value.get(alias))


def _categorical_chart(
    data: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Some models emit the common categories/series shape. Convert only that
    # bounded data-only shape; scripts and arbitrary chart options stay rejected.
    if set(data) - {"type", "categories", "labels", "series"}:
        raise ValueError("unknown categorical chart fields")
    categories, series = _field_alias(data, "categories", "labels"), data.get("series")
    if not isinstance(categories, list) or not 1 <= len(cast(list[Any], categories)) <= MAX_ROWS:
        raise ValueError("invalid chart categories")
    if not isinstance(series, list) or not 1 <= len(cast(list[Any], series)) <= MAX_CHART_SERIES:
        raise ValueError("invalid chart series")
    rows = [{"category": _scalar(label)} for label in cast(list[Any], categories)]
    labels: list[str] = []
    fields: list[str] = []
    for index, item in enumerate(cast(list[Any], series)):
        if not isinstance(item, dict) or set(cast(dict[str, Any], item)) - {
            "name",
            "data",
            "values",
        }:
            raise ValueError("invalid categorical series")
        item = cast(dict[str, Any], item)
        values = _field_alias(item, "data", "values")
        if not isinstance(values, list) or len(cast(list[Any], values)) != len(rows):
            raise ValueError("series length must match categories")
        field = f"series_{index}"
        fields.append(field)
        labels.append(_text(item.get("name"), maximum=64, field_name="series name"))
        for row, value in zip(rows, cast(list[Any], values), strict=True):
            row[field] = _scalar(value)
    chart_type = config.get("chart_type", data.get("type"))
    if "type" in data and "chart_type" in config and data["type"] != chart_type:
        raise ValueError("chart types disagree")
    if set(config) & {"x_field", "y_fields", "y_field", "y_labels"}:
        raise ValueError("categorical chart cannot mix row-based field mapping")
    data = {"rows": rows}
    config = {
        **config,
        "chart_type": chart_type,
        "x_field": "category",
        "x_label": config.get("x_label") or "类别",
        "y_fields": fields,
        "y_labels": labels,
    }
    return data, config


def _chart(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if "type" in config:
        config = {
            **{k: v for k, v in config.items() if k != "type"},
            "chart_type": _field_alias(config, "chart_type", "type"),
        }
    if "categories" in data or "labels" in data:
        data, config = _categorical_chart(data, config)
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
    if (
        not isinstance(y_fields, list)
        or not 1 <= len(cast(list[Any], y_fields)) <= MAX_CHART_SERIES
    ):
        raise ValueError(f"y_fields must hold between 1 and {MAX_CHART_SERIES} entries")
    labels = config.get("y_labels")
    if labels is not None and (
        not isinstance(labels, list)
        or len(cast(list[Any], labels)) != len(cast(list[Any], y_fields))
    ):
        raise ValueError("y_labels must match y_fields in length")
    y_fields = cast(list[Any], y_fields)
    labels = cast(list[Any] | None, labels)
    resolved: list[str] = []
    for field in y_fields:
        if not isinstance(field, str) or field not in keys:
            raise ValueError(f"y field {field!r} is not a column of rows")
        if field == x_field:
            raise ValueError("a series cannot reuse the x field")
        resolved.append(field)
    if chart_type == "pie" and len(resolved) != 1:
        raise ValueError("a pie chart carries exactly one series")
    if chart_type == "pie" and any(
        isinstance(row.get(resolved[0]), int | float) and row[resolved[0]] < 0 for row in rows
    ):
        raise ValueError("a pie chart cannot represent negative shares")
    return (
        {"rows": rows},
        {
            "chart_type": chart_type,
            "x_field": x_field,
            "y_fields": resolved,
            "y_labels": [
                _text(label, maximum=64, field_name="y_label") for label in (labels or resolved)
            ],
            "x_label": _text(
                config.get("x_label"), maximum=64, field_name="x_label", required=False
            ),
            "y_label": _text(
                config.get("y_label"), maximum=64, field_name="y_label", required=False
            ),
            "unit": _text(config.get("unit"), maximum=32, field_name="unit", required=False),
        },
    )


class _DiagramNode(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=MAX_STRING)
    shape: Literal["rect", "start", "end", "decision"] = "rect"


class _DiagramEdge(StrictModel):
    source: str = Field(alias="from", min_length=1, max_length=64)
    target: str = Field(alias="to", min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=MAX_STRING)


def _diagram_label(label: str) -> str:
    # Mermaid numeric entities keep label characters out of the graph grammar.
    # IDs are generated separately; neither labels nor author-supplied IDs can
    # introduce directives, links, styles or extra edges.
    return (
        '"'
        + "".join(
            f"#{ord(char)};" if char in '#"&<>[]{}()|;`\\' or ord(char) < 32 else char
            for char in label
        )
        + '"'
    )


def _diagram(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if config:
        raise ValueError("unknown diagram config")
    if "mermaid" in data:
        if set(data) != {"mermaid"}:
            raise ValueError("conflicting diagram formats")
        return {
            "mermaid": _text(data["mermaid"], maximum=MAX_MERMAID_CHARS, field_name="mermaid")
        }, {}
    if set(data) != {"nodes", "edges"}:
        raise ValueError("diagram needs Mermaid source or nodes and edges")
    raw_nodes, raw_edges = data["nodes"], data["edges"]
    if not isinstance(raw_nodes, list) or not 1 <= len(cast(list[Any], raw_nodes)) <= MAX_ITEMS:
        raise ValueError("invalid diagram nodes")
    if not isinstance(raw_edges, list) or len(cast(list[Any], raw_edges)) > 2 * MAX_ITEMS:
        raise ValueError("invalid diagram edges")
    nodes = [_DiagramNode.model_validate(node) for node in cast(list[Any], raw_nodes)]
    edges = [_DiagramEdge.model_validate(edge) for edge in cast(list[Any], raw_edges)]
    identifiers = {node.id: f"n{index}" for index, node in enumerate(nodes)}
    if len(identifiers) != len(nodes):
        raise ValueError("duplicate diagram node IDs")
    lines = ["flowchart LR"]
    for node in nodes:
        label = _diagram_label(_text(node.label, maximum=MAX_STRING, field_name="node label"))
        opening, closing = {
            "rect": ("[", "]"),
            "start": ("([", "])"),
            "end": ("([", "])"),
            "decision": ("{", "}"),
        }[node.shape]
        lines.append(f"  {identifiers[node.id]}{opening}{label}{closing}")
    for edge in edges:
        if edge.source not in identifiers or edge.target not in identifiers:
            raise ValueError("diagram edge references an unknown node")
        label = _text(edge.label, maximum=MAX_STRING, field_name="edge label", required=False)
        middle = f"|{_diagram_label(label)}|" if label else ""
        lines.append(f"  {identifiers[edge.source]} -->{middle} {identifiers[edge.target]}")
    mermaid = _text("\n".join(lines), maximum=MAX_MERMAID_CHARS, field_name="mermaid")
    return {"mermaid": mermaid}, {}


def _timeline(
    data: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    if set(data) != {"events"} or config:
        raise ValueError("unknown timeline fields")
    raw_events = data.get("events")
    if not isinstance(raw_events, list) or not 1 <= len(cast(list[Any], raw_events)) <= MAX_EVENTS:
        raise ValueError(f"events must hold between 1 and {MAX_EVENTS} entries")
    events: list[dict[str, Any]] = []
    for item in cast(list[Any], raw_events):
        if not isinstance(item, dict):
            raise ValueError("every timeline entry must be an object")
        item = cast(dict[str, Any], item)
        if set(item) - {"at", "date", "title", "detail", "description"}:
            raise ValueError("unknown timeline event fields")
        events.append(
            {
                "at": _text(_field_alias(item, "at", "date"), maximum=64, field_name="at"),
                "title": _text(item.get("title"), maximum=MAX_STRING, field_name="title"),
                "detail": _text(
                    _field_alias(item, "detail", "description"),
                    maximum=MAX_STRING,
                    field_name="detail",
                    required=False,
                ),
            }
        )
    return {"events": events}, {}


def _metric(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(cast(list[Any], raw_items)) <= 8:
        raise ValueError("items must hold between 1 and 8 entries")
    items: list[dict[str, Any]] = []
    for item in cast(list[Any], raw_items):
        if not isinstance(item, dict):
            raise ValueError("every metric must be an object")
        item = cast(dict[str, Any], item)
        items.append(
            {
                "label": _text(item.get("label"), maximum=MAX_STRING, field_name="label"),
                "value": _scalar(item.get("value")),
                "unit": _text(item.get("unit"), maximum=32, field_name="unit", required=False),
                "hint": _text(
                    item.get("hint"), maximum=MAX_STRING, field_name="hint", required=False
                ),
            }
        )
    return {"items": items}, {}


def _summary(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A compact factual snapshot; metrics retain their existing scalar contract."""
    if config or set(data) - {
        "items",
        "metrics",
        "headline",
        "subtitle",
        "subheadline",
        "sources",
        "updated_at",
    }:
        raise ValueError("unknown summary fields")
    metrics, _ = _metric({"items": _field_alias(data, "items", "metrics")}, {})
    raw_sources = data.get("sources", [])
    if not isinstance(raw_sources, list) or len(cast(list[Any], raw_sources)) > 4:
        raise ValueError("summary supports at most four sources")
    sources: list[dict[str, str]] = []
    for raw in cast(list[Any], raw_sources):
        if not isinstance(raw, dict) or set(cast(dict[str, Any], raw)) != {"label", "url"}:
            raise ValueError("invalid summary source")
        raw = cast(dict[str, Any], raw)
        url = _text(raw["url"], maximum=2048, field_name="source URL")
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("summary source must be an HTTP(S) URL without credentials")
        sources.append(
            {"label": _text(raw["label"], maximum=120, field_name="source label"), "url": url}
        )
    return {
        **metrics,
        "headline": _text(
            data.get("headline"), maximum=MAX_STRING, field_name="headline", required=False
        ),
        "subtitle": _text(
            _field_alias(data, "subtitle", "subheadline"),
            maximum=MAX_STRING,
            field_name="subtitle",
            required=False,
        ),
        "updated_at": _text(
            data.get("updated_at"), maximum=120, field_name="updated_at", required=False
        ),
        "sources": sources,
    }, {}


_FIELD_TYPES = ("text", "number", "date", "single", "multi")
_CHOICE_TYPES = ("single", "multi")
# ``local`` is deliberately absent: a client-side interaction is the component's
# own affordance (sorting, toggling a series), so declaring it would ask the
# platform to round-trip something that never leaves the browser.
_ACTION_KINDS = ("conversation", "read", "write")


def _first_text(item: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _form(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    raw_fields = data.get("fields")
    if (
        not isinstance(raw_fields, list)
        or not 1 <= len(cast(list[Any], raw_fields)) <= MAX_FORM_FIELDS
    ):
        raise ValueError(f"fields must hold between 1 and {MAX_FORM_FIELDS} entries")
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in cast(list[Any], raw_fields):
        if not isinstance(item, dict):
            raise ValueError("every field must be an object")
        item = cast(dict[str, Any], item)
        key = _text(
            _first_text(item, "key", "id", "name", "label"), maximum=64, field_name="field key"
        )
        if key in seen:
            raise ValueError(f"duplicate field key: {key}")
        seen.add(key)
        field_type = item.get("type", "text")
        if field_type not in _FIELD_TYPES:
            raise ValueError(f"field type must be one of {list(_FIELD_TYPES)}")
        options: Any = item.get("options") or []
        if field_type in _CHOICE_TYPES:
            if not isinstance(options, list) or not 2 <= len(cast(list[Any], options)) <= 8:
                raise ValueError("a choice field carries between 2 and 8 options")
            options = [
                _text(option, maximum=120, field_name="field option")
                for option in cast(list[Any], options)
            ]
            if len(set(options)) != len(cast(list[Any], options)):
                raise ValueError("choice options must be unique")
        elif options:
            raise ValueError(f"a {field_type} field cannot carry options")
        required = item.get("required", False)
        if not isinstance(required, bool):
            raise ValueError("required must be a boolean")
        fields.append(
            {
                "key": key,
                "label": _text(
                    item.get("label"), maximum=120, field_name="field label", required=False
                )
                or key,
                "type": field_type,
                "required": required,
                "options": options,
                "placeholder": _text(
                    item.get("placeholder"),
                    maximum=120,
                    field_name="field placeholder",
                    required=False,
                ),
            }
        )
    return (
        {"fields": fields},
        {
            "submit_label": _text(
                config.get("submit_label"),
                maximum=MAX_ACTION_LABEL,
                field_name="submit_label",
                required=False,
            )
            or "提交"
        },
    )


def _actions(raw: Any) -> list[dict[str, Any]]:
    """The declared actions. Ids are assigned by the platform, never by the model.

    An action only ever asks for a turn: the text it contributes becomes an
    ordinary user message, so an action can reach nothing the composer cannot.
    """

    if raw is None:
        return []
    if not isinstance(raw, list) or len(cast(list[Any], raw)) > MAX_ACTIONS:
        raise ValueError(f"actions must hold at most {MAX_ACTIONS} entries")
    actions: list[dict[str, Any]] = []
    for item in cast(list[Any], raw):
        if not isinstance(item, dict):
            raise ValueError("every action must be an object")
        item = cast(dict[str, Any], item)
        if set(item) - {"kind", "label", "prompt", "style"}:
            raise ValueError("unknown action fields")
        kind = item.get("kind")
        if kind not in _ACTION_KINDS:
            raise ValueError(f"action kind must be one of {list(_ACTION_KINDS)}")
        prompt = _text(
            item.get("prompt"),
            maximum=MAX_ACTION_PROMPT,
            field_name="action prompt",
            required=False,
        )
        if not prompt:
            raise ValueError("an action that reaches the platform must carry a prompt")
        style = item.get("style", "default")
        if style not in {"primary", "default"}:
            raise ValueError("unknown action style")
        actions.append(
            {
                "kind": kind,
                "label": _text(
                    item.get("label"), maximum=MAX_ACTION_LABEL, field_name="action label"
                ),
                "prompt": prompt,
                "style": style,
            }
        )
    if sum(a["style"] == "primary" for a in actions) > 1:
        raise ValueError("only one primary action is allowed")
    return actions


def _html(data: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    # The model names a published artifact, never markup, a path or a URL.
    if set(data) != {"artifact_id"} or set(config) - {"height"}:
        raise ValueError("HTML needs only an artifact reference")
    artifact_id = _text(data["artifact_id"], maximum=128, field_name="artifact_id")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", artifact_id) is None:
        raise ValueError("invalid artifact identifier")
    height = config.get("height", 560)
    if type(height) is not int or not 320 <= height <= 900:
        raise ValueError("HTML height must be between 320 and 900")
    return {"artifact_id": artifact_id}, {"height": height}


_BUILDERS = {
    "table": _table,
    "comparison": _comparison,
    "chart": _chart,
    "diagram": _diagram,
    "timeline": _timeline,
    "metric": _metric,
    "summary": _summary,
    "form": _form,
    "html": _html,
}


class PresentationBlock(StrictModel):
    """What a model may author inside a ``harness-ui`` fence."""

    version: Literal[1] = 1
    kind: str
    title: str | None = None
    fallback_text: str
    data: dict[str, Any]
    config: dict[str, Any] = Field(default_factory=dict)
    actions: list[dict[str, Any]] | None = None


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
    actions: tuple[dict[str, Any], ...] = ()
    decision: dict[str, Any]
    source_text: str | None = None
    source_refs: tuple[dict[str, Any], ...] = ()
    snapshot_hash: str = ""


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
    raw = cast(dict[str, Any], raw)
    if len(json.dumps(raw, default=str)) > MAX_BLOCK_CHARS:
        return None
    block_input = raw
    # A flat form is an observed authoring shorthand, not a separate protocol.
    # Normalize it before strict validation; conflicting or unknown fields fail.
    if raw.get("kind") == "form" and "fields" in raw and "data" not in raw:
        config_input = raw.get("config", {})
        if not isinstance(config_input, dict):
            return None
        config_input = cast(dict[str, Any], config_input)
        if (
            "submit_label" in raw
            and "submit_label" in config_input
            and raw["submit_label"] != config_input["submit_label"]
        ):
            return None
        block_input = {
            **{k: v for k, v in raw.items() if k not in {"fields", "submit_label"}},
            "data": {"fields": raw["fields"]},
            "config": {
                **config_input,
                **({"submit_label": raw["submit_label"]} if "submit_label" in raw else {}),
            },
        }
    # Real model outputs sometimes place the reference beside kind/title.
    # Accept that one observed shorthand; strict validation still rejects
    # duplicate references, markup, claimed metadata and arbitrary fields.
    if raw.get("kind") == "html" and "artifact_id" in raw and "data" not in raw:
        block_input = {
            **{k: v for k, v in raw.items() if k != "artifact_id"},
            "data": {"artifact_id": raw["artifact_id"]},
        }
    try:
        block = PresentationBlock.model_validate(block_input)
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
        actions = _actions(block.actions)
        if block.kind == "form" and not {"read", "write"} & {a["kind"] for a in actions}:
            raise ValueError("a form needs a read or write action to submit into")
        if block.kind == "form" and not block.config.get("submit_label"):
            config["submit_label"] = next(
                a["label"] for a in actions if a["kind"] in {"read", "write"}
            )
    except (ValueError, ValidationError):
        return None
    presentation_id = presentation_id_for(run_id, message_id, index, raw)
    return Presentation(
        presentation_id=presentation_id,
        run_id=run_id,
        message_id=message_id,
        kind=block.kind,
        renderer_version=f"{block.kind}/1",
        title=title or None,
        fallback_text=fallback,
        data=data,
        config=config,
        # Ids are the platform's, one per declared action, in declaration order:
        # a client can address an action without the model ever naming it.
        actions=tuple(
            {"action_id": f"{presentation_id}-a{position}", **action}
            for position, action in enumerate(actions)
        ),
        decision={
            "reason_code": reason_code,
            "rule_version": PRESENTATION_POLICY_VERSION,
            "origin": origin,
        },
    )
