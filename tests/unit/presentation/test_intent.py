import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from harness.adapters.memory import InMemorySessionRepository
from harness.config import Settings
from harness.core.models import Run, RunStatus, Session
from harness.presentation.intent import UIIntentSelector, presentation_variants
from harness.presentation.projection import presentations_for_events
from harness.presentation.repositories import InMemoryPresentationRepository
from harness.presentation.service import PresentationService
from tests.unit.presentation.test_projection import TABLE, _answer, _fence, _tool_result


def table(rows=None):
    rows = (
        rows
        if rows is not None
        else [{"month": f"2026-{i:02d}", "count": i * 10} for i in range(1, 7)]
    )
    return presentations_for_events(_tool_result({"rows": rows}))[0]


def response(payload, finish="stop"):
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": finish, "message": {"content": json.dumps(payload)}}]},
    )


def selector(transport):
    return UIIntentSelector("http://gateway/v1", SecretStr("private"), "d1-3B", transport=transport)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["text", "table", "chart", "comparison"])
async def test_selects_validated_original_data_and_never_creates_actions(kind):
    original = table()
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        user = json.loads(payload["messages"][1]["content"])
        assert set(user["available_kinds"]) == {"text", "table", "chart", "comparison"}
        assert len(user["result_summary"]["rows"]) == 4
        assert "tools" not in payload
        return response({"kind": kind})

    chosen = await selector(httpx.MockTransport(handler)).select(
        original, request="展示本月统计", allowed_kinds=("table", "chart", "comparison")
    )
    assert len(calls) == 1
    if kind == "text":
        assert chosen is None
    else:
        assert chosen.kind == kind
        assert chosen.presentation_id == original.presentation_id
        assert chosen.source_refs == original.source_refs
        assert chosen.actions == ()
        if kind in {"chart", "table"}:
            assert chosen.data["rows"] == original.data["rows"]
        if kind == "chart":
            assert chosen.config["chart_type"] == "line"
        if kind == "comparison":
            assert [item["values"]["count"] for item in chosen.data["items"]] == [
                10,
                20,
                30,
                40,
                50,
                60,
            ]


def test_chart_requires_complete_numeric_data_and_identifiers_are_not_metrics():
    ids = table([{"name": str(i), "id": i} for i in range(6)])
    assert "chart" not in presentation_variants(ids)
    strings = table([{"name": str(i), "count": "unknown"} for i in range(6)])
    assert "chart" not in presentation_variants(strings)
    missing = table([{"name": str(i), "count": None if i == 5 else i} for i in range(6)])
    assert "chart" not in presentation_variants(missing)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "upstream",
    [
        response({"kind": "form"}),
        response({"kind": "chart", "data": {"invented": 1}}),
        response({"kind": "chart"}, "length"),
        response({"kind": "unknown"}),
        httpx.Response(401, json={"error": "private provider"}),
        httpx.Response(200, text="bad json"),
    ],
)
async def test_protocol_and_upstream_failures_preserve_legacy_table(upstream):
    original = table()
    chosen = await selector(httpx.MockTransport(lambda _: upstream)).select(
        original, request="分析", allowed_kinds=("table", "chart", "comparison")
    )
    assert chosen == original


@pytest.mark.asyncio
async def test_disallowed_renderer_cannot_bypass_client_capabilities():
    original = table()
    chosen = await selector(httpx.MockTransport(lambda _: response({"kind": "comparison"}))).select(
        original, request="对比", allowed_kinds=("table",)
    )
    assert chosen == original


@pytest.mark.asyncio
async def test_explicit_model_blocks_bypass_selector():
    original = presentations_for_events(_answer(_fence(TABLE)))[0]
    calls = []

    def handler(request):
        calls.append(request)
        return response({"kind": "text"})

    assert (
        await selector(httpx.MockTransport(handler)).select(
            original, request="查看", allowed_kinds=("table",)
        )
        == original
    )
    assert calls == []


@pytest.mark.asyncio
async def test_timeout_fallback_and_cancellation():
    async def wait(_):
        await asyncio.sleep(1)
        return response({"kind": "chart"})

    subject = UIIntentSelector(
        "http://gateway/v1",
        SecretStr("private"),
        "d1-3B",
        timeout_seconds=0.01,
        transport=httpx.MockTransport(wait),
    )
    original = table()
    assert (
        await subject.select(original, request="趋势", allowed_kinds=("table", "chart")) == original
    )
    task = asyncio.create_task(subject.select(original, request="趋势", allowed_kinds=("table",)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_snapshot_freezes_choice_and_history_does_not_call_model_again():
    calls = []

    def handler(request):
        calls.append(request)
        return response({"kind": "chart"})

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
        intent_selector=selector(httpx.MockTransport(handler)),
    )
    run = Run(
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.SUCCEEDED,
        idempotency_key="test",
        created_at=now,
        updated_at=now,
        input={"prompt": "展示每月趋势"},
    )
    events = _tool_result({"rows": table().data["rows"]})
    first = await service.materialize(run, events)
    second = await service.materialize(run, events)
    assert first == second and first[0].kind == "chart"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_text_only_mode_does_not_call_selector_or_emit_widgets():
    calls = []

    def handler(request):
        calls.append(request)
        return response({"kind": "chart"})

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
        intent_selector=selector(httpx.MockTransport(handler)),
    )
    run = Run(
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.SUCCEEDED,
        idempotency_key="test",
        created_at=now,
        updated_at=now,
        input={"prompt": "只要文字", "ui_presentation_mode": "text"},
    )
    assert await service.materialize(run, _answer(_fence(TABLE))) == []
    assert calls == []


def test_auto_shape_projection_can_wait_until_final_selection():
    assert (
        presentations_for_events(_tool_result({"rows": table().data["rows"]}), include_shapes=False)
        == []
    )
    assert presentations_for_events(_answer(_fence(TABLE)), include_shapes=False)[0].kind == "table"


def test_disabled_by_default():
    assert UIIntentSelector.from_settings(Settings(ui_intent_model="")) is None


@pytest.mark.asyncio
async def test_only_scoped_active_presentation_preferences_are_sent():
    from types import SimpleNamespace

    from harness.memory_bank.models import MemoryStatus, MemoryType

    now = datetime.now(UTC)
    sessions = InMemorySessionRepository()
    await sessions.add(
        Session(
            tenant_id="tenant-a",
            session_id="session-1",
            user_id="u",
            agent_name="a",
            agent_version="1",
            created_at=now,
            agent_owner_user_id="agent-owner",
        )
    )
    preference = SimpleNamespace(
        memory_type=MemoryType.PREFERENCE,
        topic="ui.presentation",
        content="月度统计使用图表",
        conditions="月度数值",
        updated_at=now,
    )
    unrelated = SimpleNamespace(
        memory_type=MemoryType.PREFERENCE,
        topic="language",
        content="回答用中文",
        conditions="",
        updated_at=now,
    )
    fact = SimpleNamespace(
        memory_type=MemoryType.FACT,
        topic="ui.presentation",
        content="工具曾生成表格",
        conditions="",
        updated_at=now,
    )
    memory = SimpleNamespace(
        repository=SimpleNamespace(
            list_entries=AsyncMock(return_value=[preference, unrelated, fact])
        )
    )
    calls = []

    def handler(request):
        user = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(user)
        return response({"kind": "chart"})

    service = PresentationService(
        InMemoryPresentationRepository(),
        sessions=sessions,
        runs=AsyncMock(),
        events=AsyncMock(),
        queue=AsyncMock(),
        memory=memory,
        intent_selector=selector(httpx.MockTransport(handler)),
    )
    run = Run(
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.SUCCEEDED,
        idempotency_key="test",
        created_at=now,
        updated_at=now,
        input={"prompt": "本月统计"},
    )
    assert (await service.materialize(run, _tool_result({"rows": table().data["rows"]})))[
        0
    ].kind == "chart"
    memory.repository.list_entries.assert_awaited_once_with(
        "tenant-a",
        "u",
        agent_name="a",
        owner_id="agent-owner",
        statuses=frozenset({MemoryStatus.ACTIVE}),
        limit=200,
        active_at=memory.repository.list_entries.call_args.kwargs["active_at"],
    )
    assert calls[0]["preferences"] == [
        {"content": preference.content, "conditions": preference.conditions}
    ]


@pytest.mark.asyncio
async def test_memory_unavailable_does_not_break_presentation_selection():
    from types import SimpleNamespace

    now = datetime.now(UTC)
    sessions = InMemorySessionRepository()
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
    memory = SimpleNamespace(
        repository=SimpleNamespace(list_entries=AsyncMock(side_effect=RuntimeError))
    )
    service = PresentationService(
        InMemoryPresentationRepository(),
        sessions=sessions,
        runs=AsyncMock(),
        events=AsyncMock(),
        queue=AsyncMock(),
        memory=memory,
        intent_selector=selector(httpx.MockTransport(lambda _: response({"kind": "table"}))),
    )
    run = Run(
        tenant_id="tenant-a",
        run_id="run-1",
        session_id="session-1",
        status=RunStatus.SUCCEEDED,
        idempotency_key="test",
        created_at=now,
        updated_at=now,
        input={"prompt": "本月统计"},
    )
    assert (await service.materialize(run, _tool_result({"rows": table().data["rows"]})))[
        0
    ].kind == "table"
