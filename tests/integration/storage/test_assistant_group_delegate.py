"""Real PG roster, reply deduplication and parent delivery audit; no model network."""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from harness.adapters.memory import InMemoryEventBus, InMemorySessionRepository, InMemoryTaskQueue
from harness.application.events import EventService
from harness.application.runs import RunService
from harness.assistant.models import AssistantGroupMessageAuthor
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.errors import NotFoundError, PermissionDeniedError
from harness.core.models import RunStatus
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository
from tests.unit.assistant.test_group_chat import _service, _specialist
from tests.unit.assistant.test_group_delegate import NOW, setup_delegate


async def setup_pg(database):
    service, _, agui = _service()
    service.repository = PostgresAssistantRepository(database[1])
    await service.repository.create_specialist(_specialist("specialist_pm", "项目助理"))
    setup = await setup_delegate(service)
    runs = PostgresRunRepository(database[1])
    parent = setup[3]
    await runs.add(parent)
    service._runs = runs
    events = EventService(PostgresEventRepository(database[1]), InMemoryEventBus(),
                          clock=lambda: NOW, id_generator=lambda _: uuid4().hex)
    service._events = events

    async def create_run(**kwargs):
        # Execution boundary is stubbed; child ownership and identity really persist.
        key = kwargs["request"].run_id
        child = parent.model_copy(update={
            "run_id": key, "session_id": kwargs["request"].thread_id,
            "idempotency_key": key, "input": {"group_context": kwargs["group_context"]},
        })
        existing = await runs.find_by_idempotency_key("t", child.session_id, key)
        if existing is None:
            await runs.add(child)
        return SimpleNamespace(run=existing or child)

    agui.create_run_with_result.side_effect = create_run
    return service, setup[1], setup[2], parent, setup[4], events


@pytest.mark.asyncio
async def test_group_delegate_pg_persists_expert_reply_parent_link_and_replay(database):
    service, group, posted, run, session, events = await setup_pg(database)
    first, second = await asyncio.gather(*(
        service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
        for _ in range(2)
    ))
    assert first == second
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    expert_rows = [r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]
    assert len(expert_rows) == 1
    expert = expert_rows[0]
    assert expert.specialist_id == "specialist_pm"
    assert expert.reply_to_message_id == posted.message.message_id
    child = await service._runs.get("t", expert.run_id)
    assert child.input["group_context"]["memberId"] == "specialist_pm"
    deliveries = await events.list_after("t", run.run_id, 0)
    assert len(deliveries) == 1
    assert deliveries[0].payload["runId"] == child.run_id
    assert deliveries[0].payload["sourceThreadId"].endswith("_primary")
    assert deliveries[0].payload["groupId"] == group.group_id
    # A freshly constructed service reads confirmation from the persisted event.
    read_service, _, _ = _service()
    read_service._runs, read_service._events = service._runs, events
    confirmation = await read_service._hydrate_group_message("t", posted.replies[0])
    assert confirmation.text == "已转交 项目助理"
    await events.append(tenant_id="t", run_id=child.run_id, session_id=child.session_id,
                        event_type="message.delta", payload={"text": "专家成品"})
    finished = child.model_copy(update={"status": RunStatus.SUCCEEDED,
                                       "fencing_token": child.fencing_token + 1})
    assert await service._runs.compare_and_set(RunStatus.RUNNING, finished)
    visible = await service._hydrate_group_message("t", expert)
    assert visible.text == "专家成品"
    assert visible.author is AssistantGroupMessageAuthor.SPECIALIST
    assert visible.specialist_id == "specialist_pm"
    assert len(await service.repository.list_specialists("t", "u")) == 1
    service.create_specialist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["outside_group", "removed", "other_user", "other_tenant"])
async def test_group_delegate_pg_rejects_roster_and_ownership_boundaries(database, boundary):
    service, group, _, run, session, _ = await setup_pg(database)
    target = "specialist_pm"
    if boundary == "outside_group":
        other = _specialist("specialist_outside", "群外专家")
        await service.repository.create_specialist(other)
        target = other.specialist_id
    elif boundary == "removed":
        await service.remove_assistant_group_member("t", "u", group.group_id, member_id=target)
    elif boundary == "other_user":
        session = session.model_copy(update={"user_id": "other"})
    else:
        run = run.model_copy(update={"tenant_id": "other"})
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await service.delegate_assistant_group(run, session, specialist_id=target)
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_delegate_pg_original_quote_and_no_extra_at_dispatch(database):
    service, group, _, run, session, _ = await setup_pg(database)
    quoted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="改个群名，新技术预研 @项目助理",
    )
    current = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@主助理 重试",
        reply_to_message_id=quoted.message.message_id,
    )
    run = run.model_copy(update={
        "fencing_token": run.fencing_token + 1,
        "input": {**run.input, "group_context": {
            **run.input["group_context"], "triggerMessageId": current.message.message_id}},
    })
    assert await service._runs.compare_and_set(RunStatus.RUNNING, run)
    service._agui.create_run_with_result.reset_mock()
    await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    assert service._agui.create_run_with_result.await_count == 1
    body = service._agui.create_run_with_result.await_args.kwargs["request"].messages[0].content
    assert "改个群名，新技术预研 @项目助理" in body
    assert body.endswith("现在要回复的消息：\n重试")


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", ["primary", "specialist_pm"])
async def test_group_delegate_pg_removal_committed_before_admission_is_rejected(database, removed):
    service, group, _, run, session, events = await setup_pg(database)
    original = service.repository.get_group
    changed = False

    async def stale_roster(*args):
        nonlocal changed
        roster = await original(*args)
        if not changed:
            changed = True
            # A different committed transaction after the initial roster read.
            await service.repository.remove_group_member("t", "u", group.group_id, removed)
        return roster

    service.repository.get_group = stale_roster
    with pytest.raises(PermissionDeniedError):
        await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    service._agui.create_run_with_result.assert_not_awaited()
    assert not await events.list_after("t", run.run_id, 0)
    assert not [r for r in await service.repository.list_group_messages("t", "u", group.group_id)
                if r.author is AssistantGroupMessageAuthor.SPECIALIST]


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", ["primary", "specialist_pm"])
async def test_group_delegate_pg_admission_holds_same_lock_as_member_removal(database, removed):
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    service, group, _, run, session, _ = await setup_pg(database)
    original = service._agui.create_run_with_result.side_effect

    async def attempt_removal_at_create(**kwargs):
        # Independent transaction must be unable to commit a removal between
        # admission and run creation. A bounded PG lock timeout proves blocking.
        with pytest.raises(DBAPIError) as error:
            async with database[1].begin() as transaction:
                await transaction.execute(text("SET LOCAL lock_timeout = '150ms'"))
                await service.repository.with_session(transaction).remove_group_member(
                    "t", "u", group.group_id, removed)
        assert error.value.orig.sqlstate == "55P03"
        return await original(**kwargs)

    service._agui.create_run_with_result.side_effect = attempt_removal_at_create
    await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    # Once the admitted dispatch has committed, removal proceeds normally.
    await service.repository.remove_group_member("t", "u", group.group_id, removed)
    service._agui.create_run_with_result.reset_mock()
    with pytest.raises(PermissionDeniedError):
        await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_delegate_pg_two_services_use_real_run_creation_without_conflict(database):
    service, group, _, parent, session, events = await setup_pg(database)
    sessions = InMemorySessionRepository()
    child_session = session.model_copy(update={"session_id": "expert-session"})
    await sessions.add(child_session)
    workers = [RunService(sessions, service._runs, InMemoryTaskQueue(), events,
                          clock=lambda: NOW, id_generator=lambda _: uuid4().hex)
               for _ in range(2)]
    calls = 0

    async def real_create(**kwargs):
        nonlocal calls
        worker = workers[calls % 2]
        calls += 1
        return await worker.create_with_result(
            "t", child_session.session_id, kwargs["request"].run_id,
            input={"group_context": kwargs["group_context"],
                   "prompt": kwargs["request"].messages[0].content},
            deduplicate_active_input=False)

    service._agui.create_run_with_result.side_effect = real_create
    second = service.with_repository(PostgresAssistantRepository(database[1]))
    results = await asyncio.gather(*(
        worker.delegate_assistant_group(parent, session, specialist_id="specialist_pm")
        for worker in (service, second)
    ), return_exceptions=True)
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert results[0] == results[1]
    assert calls == 1
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    assert len([r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]) == 1
    child_runs = await service._runs.list_for_sessions("t", [child_session.session_id], limit=200)
    assert len(child_runs) == 1
    assert len([e for e in await events.list_after("t", parent.run_id, 0)
                if e.type == "ui.assistant_delivery"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", [False, True])
async def test_group_delegate_pg_empty_shared_retry_keeps_policy_and_same_message(
    database, revoked,
):
    from unittest.mock import AsyncMock

    service, group, _, parent, session, _ = await setup_pg(database)
    service._agui.get_binding.return_value = SimpleNamespace(
        agent_name="shared", agent_version="1", agent_owner_user_id="owner", space_id="space")
    release = SimpleNamespace(connection_mode=SimpleNamespace(value="caller_owned"))
    service._team_spaces = SimpleNamespace(require_agent_access=AsyncMock(return_value=release))
    payload = await service.delegate_assistant_group(parent, session, specialist_id="specialist_pm")
    stored = await service.repository.get_group_message(
        "t", "u", group.group_id, payload["messageId"])
    child = await service._runs.get("t", stored.run_id)
    assert child.input["group_context"]["deliveryKind"] == "group_delegate"
    assert await service._runs.compare_and_set(RunStatus.RUNNING, child.model_copy(update={
        "status": RunStatus.SUCCEEDED, "fencing_token": child.fencing_token + 1}))
    service._team_spaces.require_agent_access.reset_mock()
    service._agui.create_run_with_result.reset_mock()
    if revoked:
        service._team_spaces.require_agent_access.side_effect = PermissionDeniedError("revoked")
        with pytest.raises(PermissionDeniedError):
            await service._retry_empty_group_reply("t", "u", group, stored)
        service._agui.create_run_with_result.assert_not_awaited()
        assert (await service.repository.get_group_message(
            "t", "u", group.group_id, stored.message_id)).run_id == child.run_id
    else:
        other = service.with_repository(PostgresAssistantRepository(database[1]))
        results = await asyncio.gather(*(
            worker._retry_empty_group_reply("t", "u", group, stored)
            for worker in (service, other)
        ))
        assert sum(r is not None for r in results) == 1
        service._agui.create_run_with_result.assert_awaited_once()
        kwargs = service._agui.create_run_with_result.await_args.kwargs
        assert kwargs["space_id"] == "space"
        assert kwargs["connection_mode"] == "caller_owned"
        current = await service.repository.get_group_message(
            "t", "u", group.group_id, stored.message_id)
        assert current.run_id != child.run_id
        retried_run = await service._runs.get("t", current.run_id)
        assert retried_run.input["group_context"]["deliveryKind"] == "group_delegate"
    service._team_spaces.require_agent_access.assert_awaited_once_with(
        "t", "u", "space", "owner", "shared", "1")
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    assert len([r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]) == 1


@pytest.mark.asyncio
async def test_group_delegate_pg_publish_failure_keeps_committed_reply_for_replay(database):
    from unittest.mock import AsyncMock

    service, group, _, parent, session, events = await setup_pg(database)
    publish = events._bus.publish
    events._bus.publish = AsyncMock(side_effect=RuntimeError("fan-out unavailable"))
    with pytest.raises(RuntimeError, match="fan-out unavailable"):
        await service.delegate_assistant_group(parent, session, specialist_id="specialist_pm")
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    expert = next(r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST)
    events._bus.publish = publish
    result = await service.delegate_assistant_group(parent, session, specialist_id="specialist_pm")
    assert result["messageId"] == expert.message_id
    service._agui.create_run_with_result.assert_awaited_once()
