import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from harness.assistant.conversation import PersonalConversation
from harness.assistant.models import AssistantInboxItem
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.service import AssistantService
from harness.assistant.storage import AssistantInboxRow, ObjectiveRow
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import RunStatus
from harness.feishu.cards import text_message
from harness.feishu.delivery import FeishuDelivery
from harness.feishu.media import MediaRejectedError
from harness.feishu.models import Actor, FeishuSettings, Incoming, MessageResource, PreparedMessage
from harness.feishu.repository import FeishuRepository, digest
from harness.feishu.service import FeishuService
from harness.feishu.storage import (
    BindingCodeRow,
    BindingRow,
    CardActionRow,
    DeliveryRow,
    IncomingRow,
)
from tests.integration.storage.test_assistant_objectives import objective
from tests.unit.test_input_artifacts import build_service

NOW = datetime.now(UTC)
ACTOR = Actor(app_id="app", tenant_key="ft", open_id="ou")
SETTINGS = FeishuSettings(
    enabled=True,
    app_id="app",
    app_secret=SecretStr("test-only"),
    public_base_url="https://kai.test",
)


def setup(database):
    repo = FeishuRepository(database[1], "app")
    agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(6)]
    auth.profile.return_value = (SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
    agents.ensure_user_default.return_value = SimpleNamespace(name="assistant", version="1")
    assistant = AssistantService(
        PostgresAssistantRepository(database[1]),
        agents=agents,
        auth=auth,
        projects=projects,
        agui=agui,
        runs=runs,
        events=events,
    )
    service = FeishuService(
        repo,
        SETTINGS,
        assistant=assistant,
        auth=auth,
        agents=agents,
        conversation=PersonalConversation(agui=agui, runs=runs, agents=agents, events=events),
    )
    sender = AsyncMock()
    sender.send.return_value = "om-sent"
    return service, FeishuDelivery(repo, SETTINGS, sender, service.authorize), sender


async def bind(repo, actor=ACTOR, tenant="tenant-a", user="user-a", now=NOW):
    code = await repo.issue_code(tenant, user, now)
    async with repo.sessions.begin() as session:
        row = await repo.consume_code(session, actor, digest(code.code), now)
        return row.binding_id


async def receive(service, content, key="m1", actor=ACTOR):
    await service.repository.ingest(
        Incoming(actor=actor, key=key, kind="message", text=content), NOW
    )
    assert await service.process_once(NOW)


@pytest.mark.asyncio
async def test_media_preparation_commits_before_run_and_is_reused_after_receiver_recovery(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    run = chat_runtime(service)
    artifact = await build_service().upload(tenant_id='tenant-a', user_id='user-a',
        name='test.txt', media_type='text/plain', content=b'hello')
    service.media = AsyncMock()
    service.media.prepare.return_value = PreparedMessage(
        text='请阅读这份文件', artifacts=[artifact]
    )
    incoming = Incoming(actor=ACTOR, key='file-event', kind='message', message_id='om_file',
                        resources=[MessageResource(kind='file', key='file_test')])
    await service.repository.ingest(incoming, NOW)
    assert await service.process_once(NOW)
    assert await service.process_conversation_once(NOW)
    service.conversation.agui.create_run_with_result.assert_not_awaited()
    async with service.repository.sessions() as session:
        row = await session.scalar(select(IncomingRow))
        prepared = Incoming.model_validate(row.payload).prepared
        assert prepared and prepared.artifacts[0] == artifact
        assert row.run_id is None and row.status == 'chat_pending'
    # The model service can fail/restart independently after preparation commits.
    create = service.conversation.agui.create_run_with_result
    create.side_effect = RuntimeError('transient run creation failure')
    assert await service.process_conversation_once(NOW)
    create.side_effect = None
    run.status = RunStatus.SUCCEEDED
    assert await service.process_conversation_once(NOW + timedelta(seconds=3))
    service.media.prepare.assert_awaited_once()
    request = create.call_args.kwargs['request']
    assert request.thread_id == 'kai-personal-assistant-v1'
    assert request.messages[-1].content[1].source.value == artifact.input_artifact_id
    assert await delivery.deliver_once(NOW + timedelta(seconds=4))
    assert sender.send.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('bound', [False, True])
async def test_unbound_or_revoked_media_never_downloads_or_starts_model(database, bound):
    service, _, _ = setup(database)
    service.media = AsyncMock()
    chat_runtime(service)
    if bound:
        await bind(service.repository)
    await service.repository.ingest(Incoming(actor=ACTOR, key='private-image', kind='message',
        message_id='om_image', resources=[MessageResource(kind='image', key='img_test')]), NOW)
    assert await service.process_once(NOW)
    if bound:
        await service.repository.unbind('tenant-a', 'user-a', NOW)
    await service.process_conversation_once(NOW)
    service.media.prepare.assert_not_awaited()
    service.conversation.agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_permanent_media_failure_sends_one_explanation_without_model_run(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    chat_runtime(service)
    service.media = AsyncMock()
    service.media.prepare.side_effect = MediaRejectedError('附件太大，请压缩后重试。')
    await service.repository.ingest(Incoming(actor=ACTOR, key='oversize', kind='message',
        message_id='om_large', resources=[MessageResource(kind='file', key='file_large')]), NOW)
    assert await service.process_once(NOW)
    assert await service.process_conversation_once(NOW)
    assert not await service.process_conversation_once(NOW + timedelta(seconds=10))
    service.conversation.agui.create_run_with_result.assert_not_awaited()
    assert await delivery.deliver_once(NOW)
    assert sender.send.call_args.args[1].content['text'] == '附件太大，请压缩后重试。'


async def last_token(repo):
    async with repo.sessions() as session:
        return (
            await session.scalars(select(CardActionRow).order_by(CardActionRow.expires_at.desc()))
        ).first()


async def click(service, action_digest, actor=ACTOR, key="click1", now=NOW):
    await service.repository.ingest(
        Incoming(actor=actor, key=key, kind="action", action_digest=action_digest), now
    )
    assert await service.process_once(now)


def chat_runtime(service):
    conversation = service.conversation
    conversation.agui.get_thread_record.side_effect = NotFoundError("new thread")
    run = SimpleNamespace(run_id="chat-run", tenant_id="tenant-a", status=RunStatus.QUEUED)
    conversation.agui.create_run_with_result.return_value = SimpleNamespace(run=run)
    conversation.runs.get.return_value = run
    conversation.events.list_after.return_value = [
        SimpleNamespace(type="message.delta", payload={"text": "你好，今天需要我帮你做什么？"})
    ]
    return run


@pytest.mark.asyncio
async def test_ordinary_chat_uses_shared_thread_and_returns_model_answer(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    run = chat_runtime(service)
    await receive(service, "你好")
    async with service.repository.sessions() as session:
        row = await session.scalar(select(IncomingRow))
        assert row.status == "chat_pending" and row.binding_id and row.run_id is None
        assert await session.scalar(select(func.count()).select_from(ObjectiveRow)) == 0
        assert await session.scalar(select(func.count()).select_from(DeliveryRow)) == 0
    assert await service.process_conversation_once(NOW)
    kwargs = service.conversation.agui.create_run_with_result.call_args.kwargs
    assert kwargs["tenant_id"] == "tenant-a" and kwargs["user_id"] == "user-a"
    assert kwargs["request"].thread_id == "kai-personal-assistant-v1"
    assert kwargs["request"].messages[0].content == "你好"
    assert kwargs["deduplicate_active_input"] is False
    run.status = RunStatus.SUCCEEDED
    assert await service.process_conversation_once(NOW + timedelta(seconds=2))
    assert await delivery.deliver_once(NOW + timedelta(seconds=3))
    assert sender.send.call_args.args[1].content["text"] == "你好，今天需要我帮你做什么？"
    assert await service.repository.ingest(
        Incoming(actor=ACTOR, key="m1", kind="message", text="你好"), NOW
    )
    assert not await service.process_conversation_once(NOW + timedelta(seconds=4))
    service.conversation.agui.create_run_with_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_unbind_before_chat_submission_cannot_start_a_run(database):
    service, _, _ = setup(database)
    await bind(service.repository)
    chat_runtime(service)
    await receive(service, "帮我分析一下")
    await service.repository.unbind("tenant-a", "user-a", NOW + timedelta(seconds=1))
    assert await service.process_conversation_once(NOW + timedelta(seconds=2))
    service.conversation.agui.create_run_with_result.assert_not_awaited()
    async with service.repository.sessions() as session:
        assert (await session.scalar(select(IncomingRow))).status == "skipped"


@pytest.mark.asyncio
async def test_chat_recovers_committed_run_after_result_transaction_failure(database, monkeypatch):
    service, _, _ = setup(database)
    await bind(service.repository)
    run = chat_runtime(service)
    run.status = RunStatus.SUCCEEDED
    await receive(service, "你好")
    original = service.repository.reply
    failing = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    monkeypatch.setattr(service.repository, "reply", failing)
    assert await service.process_conversation_once(NOW)
    async with service.repository.sessions() as session:
        row = await session.scalar(select(IncomingRow))
        assert row.run_id is None and row.attempts == 1
        assert row.status == "chat_pending"
    service.conversation.agui.get_thread_record.side_effect = None
    service.conversation.agui.get_thread_record.return_value = SimpleNamespace(
        session_ids=("original-session",)
    )
    service.conversation.runs.find_by_idempotency_key.return_value = run
    monkeypatch.setattr(service.repository, "reply", original)
    assert await service.process_conversation_once(NOW + timedelta(seconds=3))
    service.conversation.agui.create_run_with_result.assert_awaited_once()
    assert service.conversation.runs.find_by_idempotency_key.call_args.args[:2] == (
        "tenant-a",
        "original-session",
    )
    async with service.repository.sessions() as session:
        assert (await session.scalar(select(IncomingRow))).status == "done"
        assert await session.scalar(select(func.count()).select_from(DeliveryRow)) == 1


@pytest.mark.asyncio
async def test_waiting_approval_notifies_once_without_granting_permission(database):
    service, _, _ = setup(database)
    await bind(service.repository)
    run = chat_runtime(service)
    run.status = RunStatus.WAITING_APPROVAL
    await receive(service, "请处理这份文件")
    await service.process_conversation_once(NOW)
    await service.process_conversation_once(NOW + timedelta(seconds=2))
    async with service.repository.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DeliveryRow)) == 1
        assert (await session.scalar(select(IncomingRow))).status == "chat_running"


@pytest.mark.asyncio
async def test_binding_is_single_use_atomic_private_and_expiring(database):
    service, _, _ = setup(database)
    repo = service.repository
    code = await repo.issue_code("tenant-a", "user-a", NOW)

    async def consume(actor):
        async with repo.sessions.begin() as session:
            return await repo.consume_code(session, actor, digest(code.code), NOW)

    results = await asyncio.gather(
        consume(ACTOR),
        consume(ACTOR.model_copy(update={"open_id": "other"})),
        return_exceptions=True,
    )
    assert sum(isinstance(value, BindingRow) for value in results) == 1
    assert sum(isinstance(value, ConflictError) for value in results) == 1
    async with repo.sessions() as session:
        stored = await session.get(BindingCodeRow, digest(code.code))
        assert stored and stored.consumed_at and stored.digest != code.code
    assert not (await service.status("tenant-a", "other-user")).bound
    other = await repo.issue_code("tenant-a", "other-user", NOW)
    with pytest.raises(ConflictError):
        async with repo.sessions.begin() as session:
            await repo.consume_code(session, ACTOR, digest(other.code), NOW + timedelta(minutes=11))


@pytest.mark.asyncio
async def test_code_rotation_and_unbind_revoke_unconsumed_codes(database):
    service, _, _ = setup(database)
    repo = service.repository
    first = await repo.issue_code("tenant-a", "user-a", NOW)
    with pytest.raises(ConflictError):
        await repo.issue_code("tenant-a", "user-a", NOW + timedelta(seconds=2))
    second = await repo.issue_code("tenant-a", "user-a", NOW + timedelta(seconds=31))
    await repo.unbind("tenant-a", "user-a", NOW + timedelta(seconds=32))
    for code in (first, second):
        with pytest.raises(ConflictError):
            async with repo.sessions.begin() as session:
                await repo.consume_code(
                    session, ACTOR, digest(code.code), NOW + timedelta(seconds=33)
                )


@pytest.mark.asyncio
async def test_disabled_account_cannot_consume_binding_code(database):
    service, _, _ = setup(database)
    code = await service.repository.issue_code("tenant-a", "user-a", NOW)
    service.auth.profile.return_value = (
        SimpleNamespace(disabled=True),
        SimpleNamespace(role="member"),
    )
    await service.repository.ingest(
        Incoming(actor=ACTOR, key="bind1", kind="bind", code_digest=digest(code.code)), NOW
    )
    await service.process_once(NOW)
    async with service.repository.sessions() as session:
        assert await service.repository.actor_binding(session, ACTOR) is None
        assert (await session.get(BindingCodeRow, digest(code.code))).consumed_at is None


@pytest.mark.asyncio
async def test_duplicate_message_creates_one_objective_and_atomic_reply(database):
    service, _, _ = setup(database)
    await bind(service.repository)
    await receive(service, "新建 采购整改\n检查材料")
    assert await service.repository.ingest(
        Incoming(actor=ACTOR, key="m1", kind="message", text="新建 采购整改\n检查材料"), NOW
    )
    assert not await service.process_once(NOW)
    async with service.repository.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ObjectiveRow)) == 1
        assert await session.scalar(select(func.count()).select_from(DeliveryRow)) == 1
        assert (await session.scalar(select(IncomingRow))).status == "done"


@pytest.mark.asyncio
async def test_reply_storage_failure_rolls_back_objective_then_retries_once(database):
    service, _, _ = setup(database)
    await bind(service.repository)
    original = service.repository.reply
    service.repository.reply = AsyncMock(side_effect=RuntimeError("storage outage"))
    await receive(service, "新建 回滚验证")
    assert await service.assistant.repository.list("tenant-a", "user-a") == []
    service.repository.reply = original
    await service.process_once(NOW + timedelta(seconds=10))
    assert len(await service.assistant.repository.list("tenant-a", "user-a")) == 1


@pytest.mark.asyncio
async def test_card_wrong_actor_cannot_poison_legitimate_click_and_replay_is_harmless(database):
    service, _, _ = setup(database)
    await bind(service.repository)
    other = ACTOR.model_copy(update={"open_id": "other"})
    await bind(service.repository, other, user="other-user")
    await service.assistant.repository.create(objective())
    await receive(service, "暂停 整改跟进")
    token = await last_token(service.repository)
    await click(service, token.digest, actor=other, key="wrong-actor")
    assert (
        await service.assistant.repository.get("tenant-a", "user-a", "objective-a")
    ).revision == 1
    await click(service, token.digest, key="real-actor")
    obj = await service.assistant.repository.get("tenant-a", "user-a", "objective-a")
    assert obj.status.value == "paused" and obj.revision == 2
    await click(service, token.digest, key="replay-different-event")
    assert (
        await service.assistant.repository.get("tenant-a", "user-a", "objective-a")
    ).revision == 2


@pytest.mark.asyncio
async def test_stale_card_cannot_enqueue_after_web_edit(database):
    service, _, _ = setup(database)
    await bind(service.repository)
    await service.assistant.repository.create(objective())
    await receive(service, "执行 整改跟进")
    token = await last_token(service.repository)
    await service.assistant.repository.update(
        "tenant-a", "user-a", "objective-a", expected_revision=1, now=NOW, note="新版要求"
    )
    await click(service, token.digest)
    assert await service.assistant.repository.executions("tenant-a", "user-a", "objective-a") == []


async def queue_reply(service, category="reply"):
    async with service.repository.sessions.begin() as session:
        binding = await service.repository.actor_binding(session, ACTOR)
        await service.repository.reply(
            session,
            key="out1",
            actor=ACTOR,
            binding=binding,
            content=text_message("private result"),
            now=NOW,
            category=category,
        )


@pytest.mark.asyncio
async def test_unbind_and_rebind_never_retargets_pending_private_delivery(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    await queue_reply(service)
    await service.repository.unbind("tenant-a", "user-a", NOW)
    await bind(
        service.repository,
        ACTOR.model_copy(update={"open_id": "new-actor"}),
        now=NOW + timedelta(seconds=31),
    )
    assert await delivery.deliver_once(NOW)
    sender.send.assert_not_called()
    async with service.repository.sessions() as session:
        assert (await session.scalar(select(DeliveryRow))).status == "skipped"


@pytest.mark.asyncio
async def test_failed_send_reuses_uuid_and_crash_cannot_restart_dedup_window(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    await queue_reply(service)
    sender.send.side_effect = TimeoutError()
    assert await delivery.deliver_once(NOW)
    sender.send.side_effect = None
    sender.send.return_value = "sent"
    assert await delivery.deliver_once(NOW + timedelta(seconds=10))
    assert sender.send.call_args_list[0].args[2] == sender.send.call_args_list[1].args[2]
    # A fresh request claimed before a process crash retains its first-attempt clock.
    async with service.repository.sessions.begin() as session:
        row = await session.scalar(select(DeliveryRow))
        row.status, row.first_attempt_at = "pending", None
        row.next_attempt_at = NOW
    claimed = await delivery.claim(NOW)
    assert claimed
    assert not await delivery.deliver_once(NOW + timedelta(minutes=51))
    assert sender.send.await_count == 2
    async with service.repository.sessions() as session:
        row = await session.scalar(select(DeliveryRow))
        assert row.status == "uncertain" and row.first_attempt_at == NOW


@pytest.mark.asyncio
async def test_notification_collection_filters_exact_repeats_and_pre_binding_history(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    await service.assistant.repository.create(objective())

    async def notice(index, content, created):
        item = AssistantInboxItem(
            itemId=f"i{index}",
            objectiveId="objective-a",
            kind="succeeded",
            title="结果",
            content=content,
            createdAt=created,
        )
        async with service.repository.sessions.begin() as session:
            session.add(
                AssistantInboxRow(
                    item_id=item.item_id,
                    objective_id="objective-a",
                    tenant_id="tenant-a",
                    user_id="user-a",
                    dedup_key=item.item_id,
                    created_at=created,
                    payload=item.model_dump(mode="json"),
                )
            )

    await notice(0, "old", NOW - timedelta(seconds=1))
    await notice(1, "same result", NOW)
    await notice(2, "same   result", NOW + timedelta(seconds=1))
    assert await delivery.collect(NOW) == 2
    assert await delivery.collect(NOW) == 0
    async with service.repository.sessions() as session:
        rows = (await session.scalars(select(DeliveryRow))).all()
        assert sorted(row.status for row in rows) == ["pending", "skipped"]
    await service.repository.preferences("tenant-a", "user-a", False)
    await delivery.deliver_once(NOW)
    sender.send.assert_not_called()


@pytest.mark.asyncio
async def test_unbind_waits_for_inflight_send_then_blocks_future_delivery(database):
    service, delivery, sender = setup(database)
    await bind(service.repository)
    await queue_reply(service)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_send(*args):
        started.set()
        await release.wait()
        return "sent"

    sender.send.side_effect = slow_send
    sending = asyncio.create_task(delivery.deliver_once(NOW))
    await asyncio.wait_for(started.wait(), 2)
    unbinding = asyncio.create_task(service.repository.unbind("tenant-a", "user-a", NOW))
    await asyncio.sleep(0.05)
    assert not unbinding.done()
    release.set()
    await sending
    await unbinding
    assert not (await service.status("tenant-a", "user-a")).bound
