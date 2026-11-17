"""Group coordination forwards the actual user request to an existing seat only."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from harness.adapters.memory import InMemoryEventBus, InMemoryEventRepository
from harness.agui.activity import build_run_activity
from harness.application.events import EventService
from harness.assistant.group_tools import assistant_group_management_overlay
from harness.assistant.models import AssistantGroupMessageAuthor
from harness.assistant.service import group_thread_id
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Run, RunStatus, Session
from tests.unit.assistant.test_group_chat import _service, _specialist

NOW = datetime.now(UTC)
TOOL = "assistant_group_delegate"


async def setup_delegate(service=None):
    if service is None:
        service, _, _ = _service([_specialist("specialist_pm", "项目助理")])
    group = await service.create_assistant_group("t", "u", name="协调群")
    await service.add_assistant_group_member("t", "u", group.group_id,
                                             specialist_id="specialist_pm")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@主助理 请分析技术方案",
    )
    run = Run(run_id=posted.replies[0].run_id, session_id="parent-session",
              tenant_id="t", status=RunStatus.RUNNING, idempotency_key="parent",
              created_at=NOW, updated_at=NOW, input={
                  "conversation_surface": "assistant",
                  "group_context": {"groupId": group.group_id, "memberId": "primary",
                                    "triggerMessageId": posted.message.message_id,
                                    "triggerAuthor": "user"},
              })
    session = Session(session_id=run.session_id, tenant_id="t", user_id="u",
                      agent_name="agent", agent_version="1.0", created_at=NOW,
                      environment="production", runtime_type="claude-agent-sdk")
    service._runs.get.return_value = run
    service._agui.get_thread_record_for_session.return_value = SimpleNamespace(
        thread_id=group_thread_id(group.group_id, "primary"))
    events = EventService(InMemoryEventRepository(), InMemoryEventBus(),
                          clock=lambda: NOW, id_generator=lambda _: uuid4().hex)
    service._events = events
    service.create_specialist = AsyncMock(side_effect=AssertionError("must not create"))
    service._agui.create_run_with_result.reset_mock()
    overlay = await assistant_group_management_overlay(
        service, events, run, session, group_id=group.group_id, member_id="primary")
    return service, group, posted, run, session, events, overlay


def payload(result):
    return json.loads(result["content"][0]["text"])


async def invoke(setup, **arguments):
    overlay = setup[-1]
    tools = {tool.name: tool.handler for tool in overlay.tools}
    return await tools[TOOL]({"specialist_id": "specialist_pm", **arguments})


@pytest.mark.asyncio
async def test_delegate_dispatches_specialist_in_group_and_records_actual_origin():
    setup = await setup_delegate()
    service, group, posted, run, _, events, overlay = setup
    tools = {tool.name for tool in overlay.tools}
    assert TOOL in tools
    assert not tools & {"assistant_create_task", "assistant_delegate_to_specialist",
                        "assistant_create_specialist", "assistant_send_message"}
    result = payload(await invoke(setup))
    assert result == {"status": "delivered", "text": "已转交 项目助理"}
    kwargs = service._agui.create_run_with_result.await_args.kwargs
    assert kwargs["request"].thread_id == group_thread_id(group.group_id, "specialist_pm")
    assert kwargs["group_context"]["memberId"] == "specialist_pm"
    assert "请分析技术方案" in kwargs["request"].messages[0].content
    assert kwargs["assistant_dispatch"] is False
    rows = await service.repository.list_group_messages("t", "u", group.group_id)
    replies = [r for r in rows if r.author is AssistantGroupMessageAuthor.SPECIALIST]
    assert len(replies) == 1
    assert replies[0].specialist_id == "specialist_pm"
    assert replies[0].reply_to_message_id == posted.message.message_id
    delivery = next(e for e in await events.list_after("t", run.run_id, 0)
                    if e.type == "ui.assistant_delivery")
    assert delivery.payload["sourceThreadId"] == group_thread_id(group.group_id, "primary")
    assert delivery.payload["groupId"] == group.group_id
    assert delivery.payload["actorMemberId"] == "primary"
    assert delivery.payload["triggerMessageId"] == posted.message.message_id
    assert delivery.payload["runId"] == replies[0].run_id
    # Repeated tool calls do not create duplicate expert messages or child runs.
    assert payload(await invoke(setup)) == result
    assert service._agui.create_run_with_result.await_count == 1
    service.create_specialist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["outside", "missing", "creator", "primary",
                                      "removed", "mismatched_specialist", "archived"])
async def test_delegate_hard_rejects_invalid_or_stale_target(mutation):
    setup = await setup_delegate()
    service, group, *_ = setup
    roster = service.repository.members[group.group_id]
    target = "specialist_pm"
    if mutation in {"outside", "missing"}:
        target = "specialist_outside"
        if mutation == "outside":
            service.repository._specialists[target] = _specialist(target, "群外专家")
    elif mutation in {"creator", "primary"}:
        target = "user:u" if mutation == "creator" else "primary"
    elif mutation == "removed":
        roster[-1] = roster[-1].model_copy(update={"status": "removed"})
    elif mutation == "mismatched_specialist":
        roster[-1] = roster[-1].model_copy(update={"member_id": "wrong-seat"})
    else:
        from harness.assistant.models import SpecialistStatus
        bot = service.repository._specialists[target]
        service.repository._specialists[target] = bot.model_copy(
            update={"status": SpecialistStatus.ARCHIVED})
    result = await invoke(setup, specialist_id=target)
    assert result["isError"] is True
    service._agui.create_run_with_result.assert_not_awaited()
    service.create_specialist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["seat", "inactive_primary", "thread", "tenant",
                                      "user", "stopped", "trigger", "parent_context", "fencing"])
async def test_service_revalidates_authority_even_bypassing_tool_discovery(mutation):
    setup = await setup_delegate()
    service, group, _, run, session, *_ = setup
    if mutation == "seat":
        run.input["group_context"]["memberId"] = "specialist_pm"
    elif mutation == "inactive_primary":
        roster = service.repository.members[group.group_id]
        roster[0] = roster[0].model_copy(update={"status": "removed"})
    elif mutation == "thread":
        service._agui.get_thread_record_for_session.return_value.thread_id = "assistant"
    elif mutation in {"tenant", "user"}:
        session = session.model_copy(update={f"{mutation}_id": "other"})
        if mutation == "user":
            service.repository.get_group = AsyncMock(side_effect=NotFoundError("not owned"))
    elif mutation == "stopped":
        service._runs.get.return_value = run.model_copy(update={"status": RunStatus.CANCELLED})
    elif mutation == "trigger":
        run.input["group_context"]["triggerMessageId"] = "missing"
    elif mutation == "fencing":
        service._runs.get.return_value = run.model_copy(update={"fencing_token": 2})
    else:
        service._runs.get.return_value = run.model_copy(update={"input": {}})
    with pytest.raises((PermissionDeniedError, ConflictError, NotFoundError)):
        await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_specialist_and_direct_primary_do_not_receive_delegate():
    setup = await setup_delegate()
    service, group, _, run, session, events, _ = setup
    for group_id, member_id in [(group.group_id, "specialist_pm"), (None, "primary")]:
        overlay = await assistant_group_management_overlay(
            service, events, run, session, group_id=group_id, member_id=member_id)
        assert TOOL not in {t.name for t in overlay.tools}


@pytest.mark.asyncio
async def test_delegate_rejects_model_selected_context_and_names():
    setup = await setup_delegate()
    for extra in [{"groupId": "other"}, {"text": "伪造正文"}, {"specialist_id": "项目助理"}]:
        assert (await invoke(setup, **extra))["isError"] is True
    setup[0]._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [True, False])
async def test_delegate_checks_shared_group_binding_access(denied):
    setup = await setup_delegate()
    service = setup[0]
    service._agui.get_binding.return_value = SimpleNamespace(
        agent_name="shared", agent_version="1", agent_owner_user_id="owner", space_id="space")
    service._team_spaces = SimpleNamespace(require_agent_access=AsyncMock(
        side_effect=PermissionDeniedError("revoked") if denied else None,
        return_value=SimpleNamespace(connection_mode=SimpleNamespace(value="service_owned"))))
    result = await invoke(setup)
    service._team_spaces.require_agent_access.assert_awaited_once_with(
        "t", "u", "space", "owner", "shared", "1")
    if denied:
        assert result["isError"] is True
        service._agui.create_run_with_result.assert_not_awaited()
    else:
        assert payload(result)["status"] == "delivered"
        assert service._agui.create_run_with_result.await_args.kwargs["space_id"] == "space"


@pytest.mark.asyncio
async def test_delivery_activity_is_safe_and_primary_reply_is_only_confirmation():
    setup = await setup_delegate()
    service, _, posted, run, _, events, _ = setup
    await invoke(setup)
    activity = build_run_activity(await events.list_after("t", run.run_id, 0))
    item = next(i for i in activity["items"] if i["event_type"] == "ui.assistant_delivery")
    # Keep the existing frontend activity enum; classify by deliveryKind.
    assert item["kind"] == "result"
    assert item["title"] == "已转交 项目助理"
    assert item["metadata"] == {"deliveryKind": "group_delegate", "specialistName": "项目助理"}
    assert item["summary"] is None
    service._runs.get.return_value = run.model_copy(update={"status": RunStatus.SUCCEEDED})
    await events.append(tenant_id="t", run_id=run.run_id, session_id=run.session_id,
                        event_type="message.delta", payload={"text": "复杂工作正文不应冒充专家"})
    hydrated = await service._hydrate_group_message("t", posted.replies[0])
    assert hydrated.text == "已转交 项目助理"
    assert hydrated.status == "completed"


@pytest.mark.asyncio
async def test_retry_returns_original_delivery_even_if_member_display_name_changed():
    setup = await setup_delegate()
    service, group, *_ = setup
    first = payload(await invoke(setup))
    roster = service.repository.members[group.group_id]
    roster[-1] = roster[-1].model_copy(update={"name": "新名字"})
    assert payload(await invoke(setup)) == first
    assert service._agui.create_run_with_result.await_count == 1


async def empty_delegated_reply(*, shared=False):
    setup = await setup_delegate()
    service, group, _, parent, session, _, _ = setup
    if shared:
        service._agui.get_binding.return_value = SimpleNamespace(
            agent_name="shared", agent_version="1", agent_owner_user_id="owner", space_id="space")
        service._team_spaces = SimpleNamespace(require_agent_access=AsyncMock(
            return_value=SimpleNamespace(connection_mode=SimpleNamespace(value="caller_owned"))))
    await service.delegate_assistant_group(parent, session, specialist_id="specialist_pm")
    stored = next(m for m in service.repository.messages[group.group_id]
                  if m.author is AssistantGroupMessageAuthor.SPECIALIST)
    first = service._agui.create_run_with_result.await_args.kwargs
    child = parent.model_copy(update={
        "run_id": stored.run_id, "idempotency_key": first["request"].run_id,
        "input": {"group_context": first["group_context"]}, "status": RunStatus.SUCCEEDED,
    })
    service._runs.get.return_value = child
    service._agui.create_run_with_result.reset_mock()
    if shared:
        service._team_spaces.require_agent_access.reset_mock()
    group = await service.repository.get_group("t", "u", group.group_id)
    return service, group, stored, session


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_empty_delegated_reply_preserves_shared_binding_and_rechecks_access(legacy):
    import asyncio

    from harness.agui.service import AguiRunService
    from harness.core.models import AguiThreadBinding

    service, group, stored, session = await empty_delegated_reply(shared=True)
    if legacy:
        child = service._runs.get.return_value
        context = dict(child.input["group_context"])
        context.pop("deliveryKind", None)
        service._runs.get.return_value = child.model_copy(
            update={"input": {"group_context": context}})
    actual = object.__new__(AguiRunService)
    actual._lock = asyncio.Lock()
    actual._bindings = SimpleNamespace(get_by_thread=AsyncMock(return_value=AguiThreadBinding(
        tenant_id="t", user_id="u", thread_id=group_thread_id(group.group_id, "specialist_pm"),
        session_id="shared-session", created_at=NOW, updated_at=NOW)))
    actual._sessions = SimpleNamespace(get=AsyncMock(return_value=session.model_copy(update={
        "session_id": "shared-session", "agent_name": "shared", "agent_version": "1",
        "agent_owner_user_id": "owner", "team_ids": ("space",), "connection_mode": "caller_owned",
    })))

    async def check_binding(**kwargs):
        await actual._resolve_binding(
            tenant_id="t", user_id="u", thread_id=kwargs["request"].thread_id,
            agent_name=kwargs["agent_name"], agent_version=kwargs["agent_version"],
            agent_owner_user_id=kwargs["agent_owner_user_id"], space_id=kwargs.get("space_id"),
            connection_mode=kwargs["connection_mode"])
        return SimpleNamespace(run=SimpleNamespace(run_id="retried"))

    service._agui.create_run_with_result.side_effect = check_binding
    retried = await service._retry_empty_group_reply("t", "u", group, stored)
    assert retried.run_id == "retried"
    assert retried.message_id == stored.message_id
    service._team_spaces.require_agent_access.assert_awaited_once_with(
        "t", "u", "space", "owner", "shared", "1")
    kwargs = service._agui.create_run_with_result.await_args.kwargs
    assert kwargs["space_id"] == "space"
    assert kwargs["connection_mode"] == "caller_owned"
    assert kwargs["group_context"]["deliveryKind"] == "group_delegate"


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked", ["local", "shared", "member", "primary"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_empty_delegated_reply_rejects_revoked_authority(revoked, legacy):
    service, group, stored, _ = await empty_delegated_reply(shared=revoked == "shared")
    if legacy:
        child = service._runs.get.return_value
        context = dict(child.input["group_context"])
        context.pop("deliveryKind", None)
        service._runs.get.return_value = child.model_copy(
            update={"input": {"group_context": context}})
    if revoked == "local":
        specialist = service.repository._specialists["specialist_pm"]
        service.repository._specialists["specialist_pm"] = specialist.model_copy(update={
            "config": {"required_local_tools": ["run_commands"], "local_tool_permissions": []}})
    elif revoked == "shared":
        service._team_spaces.require_agent_access.side_effect = PermissionDeniedError("revoked")
    else:
        await service.repository.remove_group_member(
            "t", "u", group.group_id, "specialist_pm" if revoked == "member" else "primary")
    if revoked in {"member", "primary"}:
        assert await service._retry_empty_group_reply("t", "u", group, stored) is None
    else:
        error = ConflictError if revoked == "local" else PermissionDeniedError
        reason = "待授权本地能力" if revoked == "local" else "revoked"
        with pytest.raises(error, match=reason):
            await service._retry_empty_group_reply("t", "u", group, stored)
    service._agui.create_run_with_result.assert_not_awaited()
