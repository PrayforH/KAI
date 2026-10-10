"""The harness.ui/v1 validator: what it accepts, and everything it must refuse.

A rejected block is not an error the user sees — it means the answer stays as the
model wrote it. These cases pin the boundary so a loosened rule cannot silently
turn into a broken widget.
"""

from __future__ import annotations

import pytest

from harness.presentation.schema import MAX_STRING, validate_block


def _block(**overrides: object) -> dict[str, object]:
    block: dict[str, object] = {
        "version": 1,
        "kind": "table",
        "fallback_text": "两个方案的对比。",
        "data": {"columns": [{"key": "name", "label": "方案"}], "rows": [{"name": "甲"}]},
    }
    block.update(overrides)
    return block


def _validate(block: object, *, index: int = 0):
    return validate_block(
        block, run_id="run-1", message_id="assistant-run-1", index=index, origin="model",
        reason_code="model_selected",
    )


def test_accepts_a_well_formed_table() -> None:
    presentation = _validate(_block())

    assert presentation is not None
    assert presentation.kind == "table"
    assert presentation.renderer_version == "table/1"
    assert presentation.schema_version == "harness.ui/v1"
    assert presentation.run_id == "run-1"
    assert presentation.decision["origin"] == "model"


def test_identity_is_deterministic_for_the_same_block() -> None:
    first = _validate(_block())
    second = _validate(_block())

    assert first is not None and second is not None
    assert first.presentation_id == second.presentation_id


def test_index_participates_in_identity() -> None:
    first = _validate(_block(), index=0)
    second = _validate(_block(), index=1)

    assert first is not None and second is not None
    assert first.presentation_id != second.presentation_id


@pytest.mark.parametrize(
    "block",
    [
        {},
        {"version": 2, "kind": "table", "fallback_text": "x", "data": {}},
        _block(kind="sparkline"),
        _block(extra="not allowed"),
        _block(fallback_text=""),
        _block(fallback_text=5),
        _block(data={}),
    ],
)
def test_rejects_unknown_or_malformed_blocks(block: object) -> None:
    assert _validate(block) is None


def test_rejects_non_scalar_cells() -> None:
    block = _block(
        data={
            "columns": [{"key": "name", "label": "方案"}],
            "rows": [{"name": {"nested": "object"}}],
        }
    )

    assert _validate(block) is None


def test_rejects_an_over_long_cell() -> None:
    block = _block(
        data={
            "columns": [{"key": "name", "label": "方案"}],
            "rows": [{"name": "长" * (MAX_STRING + 1)}],
        }
    )

    assert _validate(block) is None


def test_rejects_too_many_rows() -> None:
    block = _block(
        data={
            "columns": [{"key": "name", "label": "方案"}],
            "rows": [{"name": f"行{index}"} for index in range(201)],
        }
    )

    assert _validate(block) is None


def test_rejects_duplicate_column_keys() -> None:
    block = _block(
        data={
            "columns": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}],
            "rows": [{"a": 1}],
        }
    )

    assert _validate(block) is None


def test_comparison_requires_declared_dimensions() -> None:
    valid = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "两个方案对比。",
            "data": {
                "dimensions": [{"key": "price", "label": "价格"}],
                "items": [
                    {"label": "甲", "values": {"price": "120"}},
                    {"label": "乙", "values": {"price": "98"}},
                ],
            },
        }
    )
    undeclared = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "两个方案对比。",
            "data": {
                "dimensions": [{"key": "price", "label": "价格"}],
                "items": [
                    {"label": "甲", "values": {"weight": "120"}},
                    {"label": "乙", "values": {"weight": "98"}},
                ],
            },
        }
    )

    assert valid is not None
    assert undeclared is None


def test_chart_rejects_a_field_that_is_not_a_column() -> None:
    block = {
        "version": 1,
        "kind": "chart",
        "fallback_text": "季度处理量。",
        "data": {"rows": [{"quarter": "Q1", "count": 120}]},
        "config": {"chart_type": "bar", "x_field": "quarter", "y_fields": ["missing"]},
    }

    assert _validate(block) is None


def test_chart_rejects_a_pie_with_two_series() -> None:
    block = {
        "version": 1,
        "kind": "chart",
        "fallback_text": "季度处理量。",
        "data": {"rows": [{"quarter": "Q1", "a": 1, "b": 2}]},
        "config": {"chart_type": "pie", "x_field": "quarter", "y_fields": ["a", "b"]},
    }

    assert _validate(block) is None


def test_metric_accepts_scalar_values_and_rejects_an_object() -> None:
    valid = _validate(
        {
            "version": 1,
            "kind": "metric",
            "fallback_text": "关键指标。",
            "data": {"items": [{"label": "转化率", "value": "18.4", "unit": "%"}]},
        }
    )
    invalid = _validate(
        {
            "version": 1,
            "kind": "metric",
            "fallback_text": "关键指标。",
            "data": {"items": [{"label": "转化率", "value": {"rate": 0.184}}]},
        }
    )

    assert valid is not None
    assert invalid is None


def test_diagram_requires_mermaid_source() -> None:
    valid = _validate(
        {
            "version": 1,
            "kind": "diagram",
            "fallback_text": "受理到审批的流程。",
            "data": {"mermaid": "flowchart LR\n  A[受理] --> B[审批]"},
        }
    )
    invalid = _validate(
        {"version": 1, "kind": "diagram", "fallback_text": "流程。", "data": {}}
    )

    assert valid is not None
    assert invalid is None


def test_accepts_id_as_a_column_alias_and_normalises_to_key() -> None:
    """Live models write `id` about as often as `key`; the answer is the same."""
    presentation = _validate(
        _block(
            data={
                "columns": [{"id": "name", "label": "方案"}],
                "rows": [{"name": "甲"}, {"name": "乙"}],
            }
        )
    )

    assert presentation is not None
    assert presentation.data["columns"] == [{"key": "name", "label": "方案", "align": None}]


def test_accepts_positional_comparison_values_in_dimension_order() -> None:
    presentation = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "两个平台对比。",
            "data": {
                "dimensions": [{"id": "language", "label": "开发语言"}, {"id": "scale", "label": "规模"}],
                "items": [
                    {"label": "iOS", "values": ["Swift", "百万级"]},
                    {"label": "Android", "values": ["Kotlin", "百万级"]},
                ],
            },
        }
    )

    assert presentation is not None
    assert presentation.data["items"][0]["values"] == {"language": "Swift", "scale": "百万级"}


def test_rejects_positional_values_that_do_not_match_dimensions() -> None:
    presentation = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "对比。",
            "data": {
                "dimensions": [{"key": "a"}, {"key": "b"}],
                "items": [
                    {"label": "甲", "values": ["only-one"]},
                    {"label": "乙", "values": ["x", "y"]},
                ],
            },
        }
    )

    assert presentation is None


def test_rejects_values_keyed_by_an_undeclared_dimension() -> None:
    presentation = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "对比。",
            "data": {
                "dimensions": [{"key": "a"}],
                "items": [
                    {"label": "甲", "values": {"b": "x"}},
                    {"label": "乙", "values": {"a": "y"}},
                ],
            },
        }
    )

    assert presentation is None
