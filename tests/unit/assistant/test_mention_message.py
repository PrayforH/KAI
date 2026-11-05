"""用户 @某个 Bot 发消息：回执幂等、目标必须可收消息。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.models import (
    HandoverStatus,
    Specialist,
    SpecialistHandover,
    SpecialistStatus,
)
from harness.core.errors import ConflictError, NotFoundError


def _specialist(status: SpecialistStatus = SpecialistStatus.READY) -> Specialist:
    now = datetime(2026, 11, 3, tzinfo=UTC)
    return Specialist(
        specialistId="specialist_a",
        tenantId="t",
        userId="u",
        name="资料 Bot",
        slug="ziliau-bot",
        description="",
        agentName="agent",
        agentVersion="1.0",
        status=status,
        config={},
        createdAt=now,
        updatedAt=now,
    )


def _service(
    specialist: Specialist | None = None,
    handovers: list[SpecialistHandover] | None = None,
    *, events: AsyncMock | None = None,
):
    from harness.assistant.service import AssistantService

    repo, agents, auth, projects, agui, runs, default_events = [AsyncMock() for _ in range(7)]
    events = events if events is not None else default_events
    if specialist is None:
        repo.get_specialist.side_effect = NotFoundError("missing")
    else:
        repo.get_specialist.return_value = specialist
    repo.handovers.return_value = handovers or []
    repo.record_handover.side_effect = lambda value: value
    auth.profile.return_value = (SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
    agui.get_binding.return_value = SimpleNamespace(
        session_id="session",
        agent_name="agent",
        agent_version="1.0",
        agent_owner_user_id="u",
        space_id=None,
    )
    service = AssistantService(
        repo, agents=agents, auth=auth, projects=projects, agui=agui, runs=runs, events=events
    )
    return service, repo


@pytest.mark.asyncio
async def test_mention_records_a_durable_handover() -> None:
    service, repo = _service(_specialist())
    handover = await service.record_mention_handover(
        "t", "u", "specialist_a", text="帮我看看这份资料", idempotency_key="key-12345678"
    )
    assert handover.specialist_id == "specialist_a"
    assert handover.message_id == "mention-specialist:key-12345678:specialist_a"
    assert handover.status is HandoverStatus.SENT
    assert handover.summary == "帮我看看这份资料"
    assert handover.source_text == "帮我看看这份资料"
    repo.record_handover.assert_awaited_once()


@pytest.mark.asyncio
async def test_mention_with_the_same_key_never_duplicates_the_receipt() -> None:
    existing = SpecialistHandover(
        handoverId="handover_old",
        tenantId="t",
        userId="u",
        specialistId="specialist_a",
        messageId="mention-specialist:key-12345678:specialist_a",
        summary="已经发过了",
        status=HandoverStatus.SENT,
        createdAt=datetime(2026, 11, 3, tzinfo=UTC),
        updatedAt=datetime(2026, 11, 3, tzinfo=UTC),
    )
    service, repo = _service(_specialist(), [existing])
    handover = await service.record_mention_handover(
        "t", "u", "specialist_a", text="帮我看看这份资料", idempotency_key="key-12345678"
    )
    assert handover.handover_id == "handover_old"
    repo.record_handover.assert_not_called()


@pytest.mark.asyncio
async def test_mention_to_an_archived_bot_is_refused() -> None:
    """删除/停用的 Bot 不再接收新消息：回执也不该留下。"""
    service, repo = _service()
    repo.get_specialist.side_effect = None
    repo.get_specialist.return_value = _specialist(SpecialistStatus.ARCHIVED)
    with pytest.raises(ConflictError):
        await service.record_mention_handover(
            "t", "u", "specialist_a", text="在吗", idempotency_key="key-12345678"
        )
    repo.record_handover.assert_not_called()


@pytest.mark.asyncio
async def test_mention_to_a_paused_bot_is_refused() -> None:
    """停用（暂停接单）的 Bot 也不接收新消息。"""
    service, repo = _service()
    repo.get_specialist.side_effect = None
    repo.get_specialist.return_value = _specialist(SpecialistStatus.PAUSED)
    with pytest.raises(ConflictError):
        await service.record_mention_handover(
            "t", "u", "specialist_a", text="在吗", idempotency_key="key-12345678"
        )
    repo.record_handover.assert_not_called()


@pytest.mark.asyncio
async def test_mention_to_another_users_bot_is_not_found() -> None:
    """跨用户 ID：别人的 Bot 既不能投递，也不能留下回执。"""
    service, repo = _service()
    repo.get_specialist.side_effect = NotFoundError("missing")
    with pytest.raises(NotFoundError):
        await service.record_mention_handover(
            "t", "u", "specialist_other_user", text="在吗", idempotency_key="key-12345678"
        )
    repo.record_handover.assert_not_called()


def test_routine_executions_carry_the_assistant_scope() -> None:
    """例行执行（每天那条提醒）必须带助理范围，否则退化成没有联网检索能力。

    早先每条天气提醒都答「本运行没有联网检索能力」，就是这条标记漏在聊天入口里。
    """
    from harness.assistant.service import routine_forwarded_props

    assert routine_forwarded_props() == {"assistantScope": True}


@pytest.mark.asyncio
async def test_send_links_real_child_run_and_retry_does_not_execute_again():
    from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN

    service, repo = _service(_specialist())
    service.specialist_thread = AsyncMock(return_value="assistant_specialist_a")
    service._agui.create_run_with_result.return_value = SimpleNamespace(
        run=SimpleNamespace(run_id="run-server-actual"),
        reused=False,
    )

    async def link(_tenant, _user, _id, run_id):
        sent = repo.record_handover.call_args.args[0]
        linked = sent.model_copy(update={"run_id": run_id})
        repo.handovers.return_value = [linked]
        return linked

    repo.link_mention_run.side_effect = link
    args = dict(text="请检查资料", idempotency_key="key-12345678", source_thread_id=MAIN)
    first = await service.send_mention_message("t", "u", "specialist_a", **args)
    second = await service.send_mention_message("t", "u", "specialist_a", **args)
    assert first == second
    assert first.source_thread_id == MAIN and first.target_thread_id == "assistant_specialist_a"
    assert first.run_id == "run-server-actual"
    service._agui.create_run_with_result.assert_awaited_once()
    request = service._agui.create_run_with_result.call_args.kwargs["request"]
    assert request.run_id == f"mention-{first.handover_id}"
    assert request.messages[0].content == "请检查资料"
    assert request.forwarded_props["assistantScope"] is True
    repo.create.assert_not_awaited()  # A message does not manufacture a duty or timer.
    with pytest.raises(ConflictError):
        await service.send_mention_message("t", "u", "specialist_a", **{**args, "text": "变更正文"})


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["task-ordinary", "assistant_other_user"])
async def test_send_rejects_non_assistant_or_foreign_origin_before_any_receipt(origin):
    service, repo = _service(_specialist())
    service._agui.get_binding.side_effect = NotFoundError("not owned")
    with pytest.raises((ConflictError, NotFoundError)):
        await service.send_mention_message(
            "t",
            "u",
            "specialist_a",
            text="在吗",
            idempotency_key="key-12345678",
            source_thread_id=origin,
        )
    repo.record_handover.assert_not_awaited()
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_reply_is_brief_and_links_back_to_named_bot():
    from harness.core.models import RunStatus

    service, repo = _service(_specialist())
    now = datetime(2026, 11, 3, tzinfo=UTC)
    repo.mention_message_runs.return_value = (
        [
            (
                SimpleNamespace(
                    handover_id="handover-one", target_thread_id="assistant_specialist_a",
                    reply_to_run_id=None
                ),
                SimpleNamespace(run_id="real-run", updated_at=now, status=RunStatus.SUCCEEDED),
                _specialist(),
            ),
        ],
        1,
    )
    service._events.list_after.return_value = []
    replies, total = await service.mention_messages("t", "u", "main")
    assert total == 1 and len(replies) == 1
    assert replies[0].message_id == "mention-result-handover-one"
    assert replies[0].source["name"] == "资料 Bot"
    assert replies[0].source["href"] == "/assistant?bot=specialist_a"
    assert replies[0].run_id == "real-run"
    repo.mention_message_runs.assert_awaited_once_with(
        "t", "u", "main", before=None, limit=100, message_id=None, direction="received"
    )


@pytest.mark.asyncio
async def test_receipt_metadata_does_not_replay_child_traces_until_selected() -> None:
    from harness.agui.response import HISTORY_SNAPSHOT_VERSION
    from harness.core.models import RunStatus

    events = AsyncMock()
    service, repo = _service(_specialist(), events=events)
    repo.mention_message_runs.return_value = ([
        (SimpleNamespace(handover_id="h", target_thread_id="child", reply_to_run_id=None),
         SimpleNamespace(run_id="r", updated_at=datetime(2026, 11, 3, tzinfo=UTC),
                         status=RunStatus.SUCCEEDED), _specialist())], 1)
    events.list_after.return_value = [SimpleNamespace(payload={
        "version": HISTORY_SNAPSHOT_VERSION, "response_text": "已核对资料。",
    })]
    messages, _ = await service.mention_messages("t", "u", "main", hydrate_results=False)
    events.list_after.assert_not_awaited()
    result = await service.hydrate_mention_result("t", messages[0])
    assert result.text == "已核对资料。"
    events.list_after.assert_awaited_once_with("t", "r", 0, types=("history.snapshot",))


@pytest.mark.asyncio
@pytest.mark.parametrize("dispatched", [False, True])
async def test_origin_send_restores_user_text_before_even_a_fast_child_reply(dispatched):
    from harness.core.models import RunStatus

    service, repo = _service(_specialist())
    created = datetime(2026, 11, 4, tzinfo=UTC)
    # Link acknowledgement happens after execution; it is not the send time.
    receipt = SimpleNamespace(
        handover_id="h-fast", created_at=created, sent_at=created + timedelta(seconds=2),
        summary="truncated", reply_to_run_id=None, target_thread_id="assistant_specialist_a",
    )
    prompt = "检查资料" * 150
    run = SimpleNamespace(
        run_id="child", updated_at=created + timedelta(seconds=1),
        input={"prompt": prompt, "assistant_dispatch": dispatched}, status=RunStatus.SUCCEEDED,
    )
    repo.mention_message_runs.return_value = ([(receipt, run, _specialist())], 1)
    sent, _ = await service.mention_messages("t", "u", "main", direction="sent")
    received, _ = await service.mention_messages("t", "u", "main", hydrate_results=False)
    assert sent[0].created_at == created < received[0].created_at
    assert sent[0].text == ("" if dispatched else prompt)
    service._events.list_after.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_origin_keeps_mention_but_child_receives_only_task_body(legacy):
    from harness.core.models import RunStatus

    service, repo = _service(_specialist())
    source = "@资料 Bot 检查这些资料" + ("详细内容" * 150 if not legacy else "")
    prompt = source.replace("@资料 Bot", "").strip()
    now = datetime(2026, 11, 4, tzinfo=UTC)
    receipt = SimpleNamespace(handover_id="origin-mention", summary=source[:400],
        source_text=None if legacy else source, created_at=now, sent_at=now,
        reply_to_run_id=None, target_thread_id="assistant_specialist_a")
    run = SimpleNamespace(run_id="r", input={"prompt":prompt}, status=RunStatus.SUCCEEDED,
                          updated_at=now)
    repo.mention_message_runs.return_value = ([(receipt,run,_specialist())],1)
    messages,_ = await service.mention_messages("t","u","main",direction="sent")
    assert messages[0].text == source
    assert run.input["prompt"] == prompt and not prompt.startswith("@")


@pytest.mark.asyncio
async def test_legacy_conversation_uses_pinned_identity_not_bot_defaults():
    service, _ = _service(_specialist())
    service.specialist_thread = AsyncMock(return_value="assistant_legacy")
    service._agui.get_binding.return_value = SimpleNamespace(
        agent_name="lead-agent",
        agent_version="pinned-version",
        agent_owner_user_id="shared-owner",
        space_id="team-space",
    )
    target = await service.specialist_conversation("t", "u", _specialist())
    assert target.thread_id == "assistant_legacy"
    assert (target.agent_name, target.agent_version) == ("lead-agent", "pinned-version")
    assert (target.agent_owner_user_id, target.space_id) == ("shared-owner", "team-space")
    service._agui.get_binding.assert_awaited_once_with(
        tenant_id="t",
        user_id="u",
        thread_id="assistant_legacy",
    )


@pytest.mark.asyncio
async def test_new_conversation_uses_defaults_without_creating_a_session_on_read():
    service, repo = _service(_specialist())
    service.specialist_thread = AsyncMock(return_value="assistant_new")
    service._agui.get_binding.side_effect = NotFoundError("not yet bound")
    target = await service.specialist_conversation("t", "u", _specialist())
    assert target.agent_name == "agent" and target.agent_version == "1.0"
    assert target.agent_owner_user_id == "u" and target.space_id is None
    service._agui.create_run_with_result.assert_not_awaited()
    repo.record_handover.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversation_lookup_does_not_hide_permission_errors():
    from harness.core.errors import PermissionDeniedError

    service, _ = _service(_specialist())
    service.specialist_thread = AsyncMock(return_value="assistant_denied")
    service._agui.get_binding.side_effect = PermissionDeniedError("denied")
    with pytest.raises(PermissionDeniedError):
        await service.specialist_conversation("t", "u", _specialist())


@pytest.mark.asyncio
async def test_shared_legacy_conversation_rechecks_access_before_sending():
    from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
    from harness.core.errors import PermissionDeniedError

    service, repo = _service(_specialist())
    service.specialist_thread = AsyncMock(return_value="assistant_shared")
    service._agui.get_binding.return_value = SimpleNamespace(
        agent_name="lead-agent",
        agent_version="pinned",
        agent_owner_user_id="owner",
        space_id="team",
    )
    service._team_spaces = AsyncMock()
    service._team_spaces.require_agent_access.side_effect = PermissionDeniedError("grant revoked")
    with pytest.raises(PermissionDeniedError):
        await service.send_mention_message(
            "t",
            "u",
            "specialist_a",
            text="继续",
            idempotency_key="shared-retry-key",
            source_thread_id=MAIN,
        )
    service._team_spaces.require_agent_access.assert_awaited_once_with(
        "t",
        "u",
        "team",
        "owner",
        "lead-agent",
        "pinned",
    )
    service._agui.create_run_with_result.assert_not_awaited()
    repo.record_handover.assert_not_awaited()


@pytest.mark.asyncio
async def test_recipient_token_is_not_a_second_delegation_instruction():
    from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN

    service, repo = _service(_specialist())
    service.specialist_thread = AsyncMock(return_value="assistant_specialist_a")
    service._agui.create_run_with_result.return_value = SimpleNamespace(
        run=SimpleNamespace(run_id="real")
    )
    text = "今天有什么快报 @资料 Bot；只总结"
    await service.send_mention_message(
        "t",
        "u",
        "specialist_a",
        text=text,
        idempotency_key="recipient-token-key",
        source_thread_id=MAIN,
    )
    request = service._agui.create_run_with_result.call_args.kwargs["request"]
    assert request.messages[0].content == "今天有什么快报 ；只总结"
    assert repo.record_handover.call_args.args[0].summary == text
    assert repo.record_handover.call_args.args[0].source_text == text


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [SpecialistStatus.NEEDS_INPUT, SpecialistStatus.BLOCKED])
async def test_new_message_is_not_rejected_by_an_old_execution_state(state):
    from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN

    service, repo = _service(_specialist(state))
    service.specialist_thread = AsyncMock(return_value="assistant_specialist_a")
    service._agui.create_run_with_result.return_value = SimpleNamespace(
        run=SimpleNamespace(run_id="new-request")
    )
    await service.send_mention_message(
        "t",
        "u",
        "specialist_a",
        text="这是新信息",
        idempotency_key="new-info-12345678",
        source_thread_id=MAIN,
        dispatched=True,
    )
    service._agui.create_run_with_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_declared_capability_permissions_still_gate_model_delegation():
    from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN

    bot = _specialist().model_copy(update={"config": {"required_local_tools": ["run_commands"]}})
    service, repo = _service(bot)
    with pytest.raises(ConflictError, match="待授权"):
        await service.send_mention_message(
            "t",
            "u",
            "specialist_a",
            text="检查资料",
            idempotency_key="needs-grant-12345678",
            source_thread_id=MAIN,
            dispatched=True,
        )
    repo.record_handover.assert_not_awaited()
    service._agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [
    ("succeeded", ""), ("cancelled", "已停止"), ("failed", "这次处理未完成"),
    ("timed_out", "处理超时"), ("waiting_approval", "有一项操作需要你确认"),
])
async def test_empty_child_receipt_reports_only_its_actual_lifecycle_state(state, expected):
    from harness.core.models import RunStatus
    service, repo = _service(_specialist())
    repo.mention_message_runs.return_value = ([
        (SimpleNamespace(handover_id="h", target_thread_id="assistant_specialist_a",
                         reply_to_run_id=None),
         SimpleNamespace(run_id="r", updated_at=datetime(2026, 11, 3, tzinfo=UTC),
                         status=RunStatus(state)), _specialist())], 1)
    service._events.list_after.return_value = []
    messages, _ = await service.mention_messages("t", "u", "main")
    assert messages[0].text == expected
    assert "尚无结果正文" not in messages[0].text
