"""Production event wiring: audit stalls cannot change tool outcomes."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from harness.assistant.group_tools import assistant_group_management_overlay
from harness.assistant.models import AssistantGroupMessageAuthor
from tests.unit.assistant.test_group_delegate import payload, setup_delegate


async def call_tool(service, group, run, session, events):
    overlay = await assistant_group_management_overlay(
        service, events, run, session, group_id=group.group_id, member_id="primary")
    tool = next(t for t in overlay.tools if t.name == "assistant_group_delegate")
    return await tool.handler({"specialist_id": "specialist_pm"})


async def maintenance(service):
    # The memory roster fixture has no scheduler; only unrelated work is stubbed.
    if not hasattr(service.repository, "schedule_due"):
        service.repository.pending_managed_events = AsyncMock(return_value=[])
        service.repository.schedule_due = AsyncMock()
        service.repository.claim = AsyncMock(return_value=None)
    await service.process_once()


async def check_hanging_success(setup, failure):
    service, group, _, run, session, events = setup[:6]
    sink = service._events if failure == "persist" else events._bus
    method = "append" if failure == "persist" else "publish"
    original = getattr(sink, method)
    entered = asyncio.Event()

    async def hang(event):
        if event.type == "assistant.group.tool" or failure == "all_publish":
            entered.set()
            await asyncio.Event().wait()
        return await original(event)

    setattr(sink, method, hang)
    started = asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(5 if failure == "all_publish" else 3):
            result = await call_tool(service, group, run, session, events)
    finally:
        setattr(sink, method, original)
    assert entered.is_set()
    assert payload(result) == {"status": "delivered", "text": "已转交 项目助理"}
    assert "isError" not in result
    elapsed = asyncio.get_running_loop().time() - started
    print(f"{failure} delivered in {elapsed:.3f}s")
    pending = await service.repository.pending_group_delivery_notices()
    tool_notice = next(n for n in pending if n["event_type"] == "assistant.group.tool")
    assert tool_notice["payload"]["status"] == "succeeded"
    ids = {n["event_id"] for n in pending}
    # The actual maintenance entry point must recover without a model/tool retry.
    await maintenance(service)
    assert not await service.repository.pending_group_delivery_notices()
    audits = await service._events.list_after("t", run.run_id, 0)
    assert len([e for e in audits if e.type == "assistant.group.tool"]) == 1
    assert len([e for e in audits if e.type == "ui.assistant_delivery"]) == 1
    assert ids <= {e.event_id for e in audits}
    await maintenance(service)
    assert [e.event_id for e in await service._events.list_after("t", run.run_id, 0)] == [
        e.event_id for e in audits]
    service._agui.create_run_with_result.assert_awaited_once()
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    assert len([r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]) == 1


async def check_hanging_failure(setup):
    service, group, _, run, session, events = setup[:6]
    await service.remove_assistant_group_member("t", "u", group.group_id,
                                                 member_id="specialist_pm")
    publish = events._bus.publish
    entered = asyncio.Event()

    async def hang(event):
        entered.set()
        await asyncio.Event().wait()

    events._bus.publish = hang
    try:
        async with asyncio.timeout(3):
            result = await call_tool(service, group, run, session, events)
    finally:
        events._bus.publish = publish
    assert entered.is_set()
    assert result["isError"] is True
    assert payload(result)["status"] == "failed"
    assert payload(result)["statusCode"] == 403
    assert "活跃专家" in payload(result)["error"]
    pending = await service.repository.pending_group_delivery_notices()
    assert len(pending) == 1
    assert pending[0]["payload"]["status"] == "failed"
    await maintenance(service)
    await maintenance(service)
    assert not await service.repository.pending_group_delivery_notices()
    audits = await service._events.list_after("t", run.run_id, 0)
    assert len(audits) == 1
    assert audits[0].event_id == pending[0]["event_id"]
    service._agui.create_run_with_result.assert_not_awaited()
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    assert not [r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]


async def check_slow_head(setup):
    service, _, _, run, _, events = setup[:6]
    notices = [await service.repository.record_group_delivery_notice({
        "event_id": f"group-tool-{uuid4().hex}", "tenant_id": "t", "run_id": run.run_id,
        "session_id": run.session_id, "event_type": "assistant.group.tool",
        "timestamp": datetime.now(UTC).isoformat(), "payload": {"index": index},
    }) for index in range(26)]
    head = notices[0]["event_id"]
    publish = events._bus.publish
    later_published = asyncio.Event()

    async def hang_head(event):
        if event.event_id == head:
            await asyncio.Event().wait()
        later_published.set()
        await publish(event)

    events._bus.publish = hang_head
    try:
        async with asyncio.timeout(3):
            await service._drain_group_delivery_notices()
        assert later_published.is_set(), "slow head starved the rest of this batch"
        pending = await service.repository.pending_group_delivery_notices(limit=30)
        assert {n["event_id"] for n in pending} == {head, notices[25]["event_id"]}
        # A still-slow head also cannot block the next page on the next pass.
        async with asyncio.timeout(3):
            await service._drain_group_delivery_notices()
        pending = await service.repository.pending_group_delivery_notices()
        assert [n["event_id"] for n in pending] == [head]
    finally:
        events._bus.publish = publish
    await service._drain_group_delivery_notices()
    assert not await service.repository.pending_group_delivery_notices()
    audits = await service._events.list_after("t", run.run_id, 0)
    assert {e.event_id for e in audits} == {n["event_id"] for n in notices}
    assert len(audits) == 26
    service._agui.create_run_with_result.assert_not_awaited()


async def check_cancelled_drain(setup):
    service, _, _, run, _, events = setup[:6]
    for _ in range(3):
        await service.repository.record_group_delivery_notice({
            "event_id": f"group-tool-{uuid4().hex}", "tenant_id": "t",
            "run_id": run.run_id, "session_id": run.session_id,
            "event_type": "assistant.group.tool", "timestamp": datetime.now(UTC).isoformat(),
            "payload": {},
        })
    publish = events._bus.publish
    entered = asyncio.Event()
    active = 0

    async def hang(event):
        nonlocal active
        active += 1
        try:
            if active == 3:
                entered.set()
            await asyncio.Event().wait()
        finally:
            active -= 1

    events._bus.publish = hang
    task = asyncio.create_task(service._drain_group_delivery_notices())
    try:
        async with asyncio.timeout(1):
            await entered.wait()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        events._bus.publish = publish
    assert active == 0, "cancelled batch left a publisher running"
    assert len(await service.repository.pending_group_delivery_notices()) == 3
    await maintenance(service)
    await maintenance(service)
    assert not await service.repository.pending_group_delivery_notices()
    assert len(await service._events.list_after("t", run.run_id, 0)) == 3
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["persist", "publish", "all_publish"])
async def test_delegate_hanging_audit_returns_and_recovers(failure):
    await check_hanging_success(await setup_delegate(), failure)


@pytest.mark.asyncio
async def test_delegate_hanging_failure_audit_returns_failed_and_recovers():
    await check_hanging_failure(await setup_delegate())


@pytest.mark.asyncio
async def test_group_notice_slow_head_does_not_starve_batch():
    await check_slow_head(await setup_delegate())


@pytest.mark.asyncio
async def test_group_notice_cancelled_batch_joins_publishers_and_recovers():
    await check_cancelled_drain(await setup_delegate())
