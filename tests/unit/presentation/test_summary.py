"""Snapshots choose presentation, not facts, from successful run-owned evidence."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from harness.adapters.memory import InMemorySessionRepository
from harness.core.models import Run, RunStatus, Session
from harness.presentation.projection import presentations_for_events, tool_result_evidence
from harness.presentation.repositories import InMemoryPresentationRepository
from harness.presentation.schema import validate_block
from harness.presentation.service import PresentationService
from tests.unit.presentation.test_intent import response, selector
from tests.unit.presentation.test_projection import TABLE, _answer, _event, _fence, _tool_result

SNAPSHOT = {
    "version": 1,
    "kind": "summary",
    "title": "上海天气",
    "fallback_text": "上海多云，当前气温23°C。",
    "data": {
        "headline": "多云",
        "items": [{"label": "气温", "value": 23, "unit": "°C"}],
        "sources": [{"label": "来源", "url": "https://example.org/forecast"}],
        "updated_at": "2026-10-14 16:00",
    },
}


def validate(block):
    return validate_block(
        block,
        run_id="run-1",
        message_id="assistant-run-1",
        index=0,
        origin="model",
        reason_code="model_selected",
    )


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,x", "https://user:pass@example.org/", "/forecast"],
)
def test_summary_rejects_executable_or_credentialed_sources(url):
    assert (
        validate(
            {**SNAPSHOT, "data": {**SNAPSHOT["data"], "sources": [{"label": "x", "url": url}]}}
        )
        is None
    )


def test_summary_preserves_facts_units_time_and_provenance_without_executable_config():
    p = validate(SNAPSHOT)
    assert p is not None and p.renderer_version == "summary/1"
    assert p.data["items"][0]["value"] == 23
    assert p.data["items"][0]["unit"] == "°C"
    assert p.data["sources"] == SNAPSHOT["data"]["sources"]
    assert p.data["updated_at"] == "2026-10-14 16:00"
    assert validate({**SNAPSHOT, "config": {"html": "<script>bad</script>"}}) is None
    assert (
        validate(
            {
                **SNAPSHOT,
                "data": {**SNAPSHOT["data"], "items": [{"label": "x", "value": float("nan")}]},
            }
        )
        is None
    )


def test_scalar_snapshots_are_opt_in_and_do_not_dress_nested_tool_payloads():
    events = _tool_result({"状态": "已发货", "预计到达": "明天"})
    assert presentations_for_events(events) == []
    proposed = presentations_for_events(events, include_summaries=True)
    assert len(proposed) == 1 and proposed[0].kind == "summary"
    assert proposed[0].decision["origin"] == "shape"
    assert (
        presentations_for_events(
            _tool_result({"token": {"secret": "x"}, "success": True}), include_summaries=True
        )
        == []
    )
    assert (
        presentations_for_events(
            _tool_result({"status": "unknown", "count": 2}, is_error=True), include_summaries=True
        )
        == []
    )
    assert presentations_for_events(events, include_summaries=True, include_shapes=False) == []


def test_evidence_is_bounded_and_omits_errors_redacted_results_and_memory():
    events = _tool_result({"状态": "已发货", "时间": "明天"})
    for key, batch in [
        (
            "memory",
            _tool_result({"private": "memory"}, name="mcp__harness-memory__query", sequence=5),
        ),
        ("error", _tool_result({"error": "bad"}, is_error=True, sequence=7)),
    ]:
        events.extend(
            e.model_copy(update={"payload": {**e.payload, "tool_call_id": key}}) for e in batch
        )
    events += [_event("tool.result", 10, content="hidden", redacted=True)]
    evidence = tool_result_evidence(events)
    assert len(evidence) == 1 and "已发货" in evidence[0]["content"]
    assert evidence[0]["sequence"] == 3
    assert (
        len(tool_result_evidence([_event("tool.result", 1, content="x" * 3000)])[0]["content"])
        == 1600
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["summary", "text", "unknown"])
async def test_terminal_selection_uses_request_and_actual_evidence_and_restores_exactly(kind):
    calls = []

    def upstream(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(data)
        assert data["request"] == "上海今天的天气怎么样？"
        assert data["result_summary"] == {"气温": "23 °C"}
        assert "23" in data["result_evidence"][0]["content"]
        assert data["available_kinds"] == ["text", "summary"]
        return response({"kind": kind})

    sessions = InMemorySessionRepository()
    now = datetime.now(UTC)
    await sessions.add(
        Session(
            tenant_id="tenant-a",
            session_id="session-1",
            user_id="u",
            agent_name="a",
            agent_version="1",
            created_at=now,
        )
    )
    service = PresentationService(
        InMemoryPresentationRepository(),
        sessions=sessions,
        runs=AsyncMock(),
        events=AsyncMock(),
        queue=AsyncMock(),
        intent_selector=selector(httpx.MockTransport(upstream)),
    )
    run = Run(
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.RUNNING,
        idempotency_key="test",
        created_at=now,
        updated_at=now,
        input={"prompt": "上海今天的天气怎么样？"},
    )
    events = _tool_result({"location": "Shanghai", "temperature": 23, "condition": "Cloudy"})
    events += _answer(_fence(SNAPSHOT))
    assert await service.materialize(run, events) == [] and calls == []
    completed = run.model_copy(update={"status": RunStatus.SUCCEEDED})
    first = await service.materialize(completed, events)
    assert await service.materialize(completed, events) == first
    assert len(calls) == 1
    model = next(p for p in first if p.decision.get("origin") == "model")
    assert model.source_text == _fence(SNAPSHOT)
    if kind == "summary":
        assert model.decision["selector_model"] == "d1-3B"
        assert model.decision.get("display") != "text"
    else:
        assert model.decision["display"] == "text"


@pytest.mark.asyncio
async def test_converting_a_queried_model_table_keeps_its_source_for_fence_replacement():
    source = _fence(
        {
            **TABLE,
            "data": {
                "rows": [
                    {"date": "2026-10-14", "temperature": 23},
                    {"date": "2026-10-15", "temperature": 21},
                ]
            },
        }
    )
    original = presentations_for_events(_answer(source))[0]
    selected = await selector(httpx.MockTransport(lambda _: response({"kind": "chart"}))).select(
        original,
        request="看看温度变化",
        allowed_kinds=("chart", "table"),
        result_evidence=[{"content": "真实预报 23、21"}],
    )
    assert selected.kind == "chart" and selected.source_text == source
    assert selected.decision["origin"] == "model"
    assert selected.data["rows"] == original.data["rows"]


@pytest.mark.asyncio
async def test_selection_preserves_application_actions_instead_of_dropping_the_block():
    original = validate(
        {**SNAPSHOT, "actions": [{"kind": "read", "label": "查看明天", "prompt": "查询明天的天气"}]}
    )

    def unexpected(_):
        raise AssertionError("action-bearing blocks remain authoritative")

    chosen = await selector(httpx.MockTransport(unexpected)).select(
        original,
        request="查看天气",
        allowed_kinds=("summary",),
        result_evidence=[{"content": "查询结果"}],
    )
    assert chosen == original and chosen.actions
