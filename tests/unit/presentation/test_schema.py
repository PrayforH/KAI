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
        block,
        run_id="run-1",
        message_id="assistant-run-1",
        index=index,
        origin="model",
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


def test_accepts_the_categorical_chart_shape_observed_on_173() -> None:
    p = _validate(
        _block(
            kind="chart",
            data={
                "type": "bar",
                "categories": ["一季度", "二季度"],
                "series": [{"name": "利润（万元）", "data": [120, -30]}],
            },
            config={},
        )
    )
    assert p is not None
    assert p.data["rows"] == [
        {"category": "一季度", "series_0": 120},
        {"category": "二季度", "series_0": -30},
    ]
    assert p.config["y_fields"] == ["series_0"]
    assert p.config["y_labels"] == ["利润（万元）"]
    assert p.config["x_label"] == "类别"


def test_accepts_the_flat_form_observed_on_173() -> None:
    p = _validate(
        {
            "version": 1,
            "kind": "form",
            "fallback_text": "请填写项目",
            "fields": [
                {"name": "project_name", "label": "项目名称", "type": "text", "required": True}
            ],
            "submit_label": "生成项目摘要",
            "actions": [{"kind": "read", "label": "生成项目摘要", "prompt": "请根据字段生成摘要"}],
        }
    )
    assert p is not None
    assert p.data["fields"][0]["key"] == "project_name"
    assert p.config["submit_label"] == "生成项目摘要"


def test_accepts_the_labels_values_chart_observed_in_a_new_173_task() -> None:
    p = _validate(
        _block(
            kind="chart",
            data={
                "labels": ["一季度", "二季度"],
                "series": [{"name": "利润（万元）", "values": [120, -30]}],
            },
            config={"type": "bar"},
        )
    )
    assert p is not None
    assert p.config["chart_type"] == "bar"
    assert p.config["x_label"] == "类别"
    assert p.data["rows"][1]["series_0"] == -30


def test_rejects_conflicting_chart_aliases() -> None:
    assert (
        _validate(
            _block(
                kind="chart",
                data={
                    "labels": ["一季度"],
                    "categories": ["二季度"],
                    "series": [{"name": "利润", "values": [120]}],
                },
                config={"type": "bar"},
            )
        )
        is None
    )


def test_rejects_a_form_with_conflicting_flat_and_nested_content() -> None:
    assert (
        _validate(
            {
                "version": 1,
                "kind": "form",
                "fallback_text": "请填写项目",
                "fields": [],
                "data": {"fields": []},
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "change",
    [
        {"series": [{"name": "利润", "data": [120]}]},
        {"series": [{"name": "利润", "data": [120, -30], "onclick": "alert(1)"}]},
        {"type": "pie"},
        {"type": "unsupported"},
    ],
)
def test_rejects_unsafe_or_ambiguous_categorical_charts(change: dict[str, object]) -> None:
    assert (
        _validate(
            _block(
                kind="chart",
                data={
                    "type": "bar",
                    "categories": ["一季度", "二季度"],
                    "series": [{"name": "利润", "data": [120, -30]}],
                    **change,
                },
                config={},
            )
        )
        is None
    )


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
    invalid = _validate({"version": 1, "kind": "diagram", "fallback_text": "流程。", "data": {}})

    assert valid is not None
    assert invalid is None


def test_normalises_timeline_date_and_description_observed_on_173() -> None:
    result = _validate(
        {
            "version": 1,
            "kind": "timeline",
            "fallback_text": "需求确认后冻结协议。",
            "data": {
                "events": [
                    {"date": "2026-10-01", "title": "需求确认", "description": "确定范围"},
                    {"date": "2026-10-05", "title": "协议冻结", "description": "固定协议"},
                ]
            },
        }
    )
    assert result is not None
    assert result.data["events"] == [
        {"at": "2026-10-01", "title": "需求确认", "detail": "确定范围"},
        {"at": "2026-10-05", "title": "协议冻结", "detail": "固定协议"},
    ]


@pytest.mark.parametrize(
    "event",
    [
        {"at": "2026-10-01", "date": "2026-10-02", "title": "确认"},
        {"at": "2026-10-01", "title": "确认", "detail": "甲", "description": "乙"},
        {"date": "2026-10-01", "title": "确认", "onclick": "alert(1)"},
    ],
)
def test_rejects_ambiguous_or_unknown_timeline_fields(event: dict[str, object]) -> None:
    assert (
        _validate(
            {
                "version": 1,
                "kind": "timeline",
                "fallback_text": "节点。",
                "data": {"events": [event]},
            }
        )
        is None
    )


def test_normalises_data_only_diagram_without_interpreting_ids_or_labels() -> None:
    result = _validate(
        {
            "version": 1,
            "kind": "diagram",
            "fallback_text": "提交后执行，失败重试。",
            "data": {
                "nodes": [
                    {"id": "submit", "label": "提交任务", "shape": "start"},
                    {"id": "exec", "label": '执行"]\nclick n0 "https://example.com"'},
                    {"id": "retry", "label": "重试"},
                ],
                "edges": [
                    {"from": "submit", "to": "exec"},
                    {"from": "exec", "to": "retry", "label": "失败"},
                    {"from": "retry", "to": "exec", "label": "再次执行"},
                ],
            },
        }
    )
    assert result is not None
    source = result.data["mermaid"]
    assert source.startswith('flowchart LR\n  n0(["提交任务"])')
    assert "  n0 --> n1" in source
    assert '  n1 -->|"失败"| n2' in source
    assert '  n2 -->|"再次执行"| n1' in source
    assert "\nclick" not in source and "#34;#93;#10;click" in source
    assert len(source.splitlines()) == 7


@pytest.mark.parametrize(
    "data",
    [
        {"nodes": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}], "edges": []},
        {"nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "missing"}]},
        {"nodes": [{"id": "a", "label": "A", "onclick": "alert(1)"}], "edges": []},
        {"nodes": [{"id": "a", "label": "A", "shape": "script"}], "edges": []},
        {
            "nodes": [{"id": "a", "label": "A"}],
            "edges": [{"from": "a", "to": "a", "href": "https://example.com"}],
        },
        {"mermaid": "flowchart LR\nA-->B", "nodes": [], "edges": []},
        {"nodes": [{"id": str(i), "label": "A"} for i in range(25)], "edges": []},
        {"nodes": [{"id": "a", "label": "A"}], "edges": [{"from": "a", "to": "a"}] * 49},
    ],
)
def test_rejects_invalid_or_executable_diagram_data(data: dict[str, object]) -> None:
    assert (
        _validate({"version": 1, "kind": "diagram", "fallback_text": "流程。", "data": data})
        is None
    )


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
                "dimensions": [
                    {"id": "language", "label": "开发语言"},
                    {"id": "scale", "label": "规模"},
                ],
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


def test_accepts_bare_string_dimensions() -> None:
    """A live 173 answer wrote `"dimensions": ["成本","周期","风险"]`."""
    presentation = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "三个方案的对比。",
            "data": {
                "dimensions": ["成本", "周期"],
                "items": [
                    {"key": "方案A", "values": {"成本": "120 万", "周期": "6 个月"}},
                    {"key": "方案B", "values": {"成本": "90 万", "周期": "8 个月"}},
                ],
            },
        }
    )

    assert presentation is not None
    assert presentation.data["dimensions"] == [
        {"key": "成本", "label": "成本", "align": None},
        {"key": "周期", "label": "周期", "align": None},
    ]
    assert presentation.data["items"][0]["label"] == "方案A"


def test_accepts_a_bare_string_column_on_a_table() -> None:
    presentation = _validate(
        _block(data={"columns": ["名称"], "rows": [{"名称": "甲"}, {"名称": "乙"}]})
    )

    assert presentation is not None
    assert presentation.data["columns"] == [{"key": "名称", "label": "名称", "align": None}]


def test_still_rejects_an_item_with_no_name() -> None:
    presentation = _validate(
        {
            "version": 1,
            "kind": "comparison",
            "fallback_text": "对比。",
            "data": {
                "dimensions": ["a"],
                "items": [
                    {"values": {"a": "1"}},
                    {"label": "乙", "values": {"a": "2"}},
                ],
            },
        }
    )

    assert presentation is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_nonfinite_values(value: float) -> None:
    assert _validate(_block(data={"rows": [{"value": value}]})) is None


def test_positional_table_rows_observed_on_79_are_normalized_in_explicit_order():
    p = _validate(_block(data={"columns": ["名称", "数量"], "rows": [["甲", 3], ["乙", 0]]}))
    assert p is not None
    assert p.data["rows"] == [{"名称": "甲", "数量": 3}, {"名称": "乙", "数量": 0}]


@pytest.mark.parametrize("columns,rows", [
    (None, [["甲", 3]]),
    (["名称", "数量"], [["甲"]]),
    (["名称"], [["甲", 3]]),
    (["名称"], [["甲"], {"名称": "乙"}]),
    (["名称"], [[{"nested": 3}]]),
    (["名称", "名称"], [["甲", 3]]),
])
def test_ambiguous_or_unsafe_positional_rows_remain_rejected(columns: object, rows: object):
    assert _validate(_block(data={"columns": columns, "rows": rows})) is None
