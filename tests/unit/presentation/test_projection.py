"""Event -> presentation projection.

The live stream and a rebuilt history both read this function, so the cases here
are about determinism and restraint: the same events must always produce the same
blocks, and an ordinary tool result must not be dressed up as a table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from harness.core.events import RunEvent
from harness.presentation.projection import presentations_for_events

RUN_ID = "run-1"
MESSAGE_ID = "assistant-run-1"


def _event(event_type: str, sequence: int, **payload: object) -> RunEvent:
    return RunEvent(
        event_id=f"event-{sequence}",
        tenant_id="tenant-a",
        run_id=RUN_ID,
        session_id="session-1",
        sequence=sequence,
        timestamp=datetime(2026, 10, 10, tzinfo=UTC),
        type=event_type,
        payload=payload,
    )


def _answer(text: str) -> list[RunEvent]:
    return [
        _event("message.start", 1, message_id=MESSAGE_ID),
        _event("message.delta", 2, message_id=MESSAGE_ID, text=text),
        _event("message.completed", 3, message_id=MESSAGE_ID),
    ]


def _fence(payload: object) -> str:
    return f"```harness-ui\n{json.dumps(payload, ensure_ascii=False)}\n```"


TABLE = {
    "version": 1,
    "kind": "table",
    "fallback_text": "两个方案的对比。",
    "data": {
        "columns": [{"key": "name", "label": "方案"}],
        "rows": [{"name": "甲"}, {"name": "乙"}],
    },
}


def test_projects_a_fenced_block_from_the_answer() -> None:
    presentations = presentations_for_events(_answer(f"结论如下。\n\n{_fence(TABLE)}"))

    assert [presentation.kind for presentation in presentations] == ["table"]
    assert presentations[0].decision["origin"] == "model"
    assert presentations[0].message_id == MESSAGE_ID


def test_ignores_a_block_that_fails_validation() -> None:
    broken = {**TABLE, "kind": "sparkline"}

    assert presentations_for_events(_answer(f"结论。\n\n{_fence(broken)}")) == []


def test_ignores_other_fences() -> None:
    text = '答案。\n\n```json\n{"kind": "table"}\n```'

    assert presentations_for_events(_answer(text)) == []


def test_accepts_a_bare_protocol_object_when_the_fence_is_missing() -> None:
    text = f"结论。\n\n{json.dumps(TABLE, ensure_ascii=False)}"

    presentations = presentations_for_events(_answer(text))

    assert [presentation.kind for presentation in presentations] == ["table"]


def test_caps_the_turn_at_two_blocks() -> None:
    text = "\n\n".join(_fence(TABLE) for _ in range(3))

    assert len(presentations_for_events(_answer(text))) == 2


def _tool_result(
    content: object,
    *,
    name: str = "mcp__harness-data__query",
    sequence: int = 2,
    is_error: bool = False,
) -> list[RunEvent]:
    return [
        _event("tool.request", sequence, tool_call_id="tc-1", name=name, arguments={}),
        _event(
            "tool.result",
            sequence + 1,
            tool_call_id="tc-1",
            content=json.dumps(content),
            is_error=is_error,
        ),
    ]


def test_projects_a_structured_tool_result_as_a_table() -> None:
    rows = [{"name": f"行{index}", "count": index} for index in range(6)]

    presentations = presentations_for_events(_tool_result({"rows": rows}))

    assert [presentation.kind for presentation in presentations] == ["table"]
    assert presentations[0].decision["origin"] == "shape"
    assert len(presentations[0].data["rows"]) == 6


def test_leaves_a_small_result_as_text() -> None:
    rows = [{"name": f"行{index}", "count": index} for index in range(4)]

    assert presentations_for_events(_tool_result({"rows": rows})) == []


def test_leaves_a_single_column_result_as_text() -> None:
    rows = [{"name": f"行{index}"} for index in range(6)]

    assert presentations_for_events(_tool_result({"rows": rows})) == []


def test_ignores_a_failed_tool_result() -> None:
    rows = [{"name": f"行{index}", "count": index} for index in range(6)]

    assert presentations_for_events(_tool_result({"rows": rows}, is_error=True)) == []


def test_ignores_tools_that_already_have_their_own_presentation() -> None:
    rows = [{"name": f"行{index}", "count": index} for index in range(6)]

    for name in ("mcp__harness-knowledge__search_knowledge", "harness_run_activity"):
        assert presentations_for_events(_tool_result({"rows": rows}, name=name)) == []


def test_ignores_a_non_json_tool_result() -> None:
    events = [
        _event("tool.request", 2, tool_call_id="tc-1", name="Bash", arguments={}),
        _event("tool.result", 3, tool_call_id="tc-1", content="plain text output", is_error=False),
    ]

    assert presentations_for_events(events) == []


def test_is_deterministic_across_repeated_projection() -> None:
    events = _answer(f"结论。\n\n{_fence(TABLE)}") + _tool_result(
        {"rows": [{"name": f"行{index}", "count": index} for index in range(6)]}
    )

    first = presentations_for_events(events)
    second = presentations_for_events(events)

    assert [item.presentation_id for item in first] == [item.presentation_id for item in second]
    assert [item.kind for item in first] == ["table", "table"]


def test_records_exact_accepted_source_and_event_provenance() -> None:
    rejected = _fence({**TABLE, "kind": "unsupported"})
    accepted = _fence(TABLE)
    presentations = presentations_for_events(_answer(rejected + "\n" + accepted))
    assert len(presentations) == 1
    assert presentations[0].source_text == accepted
    assert presentations[0].source_refs[0]["sequence"] == 3
    assert len(presentations[0].snapshot_hash) == 64


def test_explicit_answer_block_is_not_starved_by_search_tables() -> None:
    rows = [{"name": f"row-{i}", "count": i} for i in range(5)]
    events = [
        _event("tool.request", 1, tool_call_id="search1", name="WebSearch"),
        _event("tool.result", 2, tool_call_id="search1", content=json.dumps(rows)),
        _event("tool.request", 3, tool_call_id="search2", name="WebSearch"),
        _event("tool.result", 4, tool_call_id="search2", content=json.dumps(rows)),
        _event("message.start", 5, message_id=MESSAGE_ID),
        _event("message.delta", 6, message_id=MESSAGE_ID, text=_fence(TABLE)),
        _event("message.completed", 7, message_id=MESSAGE_ID),
    ]
    blocks = presentations_for_events(events)
    assert len(blocks) == 2
    assert blocks[0].decision["origin"] == "model"
    assert blocks[0].source_text == _fence(TABLE)
