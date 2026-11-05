import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from harness.assistant.controls import ControlIntent
from harness.feishu.models import Incoming, MessageResource, PreparedMessage
from harness.feishu.storage import IncomingRow
from harness.weixin.storage import WeixinMessageRow as Message
from tests.integration.storage.test_feishu_channel import (
    ACTOR,
    NOW,
    chat_runtime,
    receive,
)
from tests.integration.storage.test_feishu_channel import (
    bind as fs_bind,
)
from tests.integration.storage.test_feishu_channel import (
    setup as fs_setup,
)
from tests.integration.storage.test_weixin_channel import bind, event, setup


@pytest.mark.asyncio
async def test_weixin_stop_bypasses_running_chat_and_persists_target_before_side_effect(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    c.conversation.submit.return_value.status = SimpleNamespace(is_terminal=False, value="running")
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [event()]})
    assert await c.process_once("tenant", "alice")
    target = ControlIntent(action="stop", target_run_id="run-shared")
    c.conversation.plan_control = AsyncMock(return_value=target)
    c.conversation.execute_control = AsyncMock(return_value="已请求停止，正在等待执行器退出。")
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {"msgs": [event(2, item_list=[{"type": 1, "text_item": {"text": "停止当前任务"}}])]},
    )
    assert await c.process_control_once("tenant", "alice")
    c.conversation.execute_control.assert_not_awaited()
    async with c.sessions() as s:
        control = await s.scalar(select(Message).where(Message.text == "停止当前任务"))
        assert control.control == target.model_dump() and control.status == "pending"
    assert await c.process_control_once("tenant", "alice")
    c.conversation.submit.assert_awaited_once()
    c.conversation.execute_control.assert_awaited_once()
    assert client.send.call_args.args[-1].startswith("已请求停止")
    async with c.sessions() as s:
        rows = list((await s.scalars(select(Message).order_by(Message.created_at))).all())
        assert [r.status for r in rows] == ["running", "done"]


@pytest.mark.asyncio
async def test_weixin_chat_lane_does_not_mistake_inflight_control_delivery_for_a_crash(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    c.conversation.plan_control = AsyncMock(return_value=ControlIntent(action="help"))
    c.conversation.execute_control = AsyncMock(return_value="助理帮助")
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {"msgs": [event(item_list=[{"type": 1, "text_item": {"text": "/help"}}])]},
    )
    await c.process_control_once("tenant", "alice")
    started, release = asyncio.Event(), asyncio.Event()

    async def send(*args):
        started.set()
        await release.wait()

    client.send.side_effect = send
    task = asyncio.create_task(c.process_control_once("tenant", "alice"))
    try:
        await asyncio.wait_for(started.wait(), 2)
        assert not await c.process_chat_once("tenant", "alice")
        async with c.sessions() as s:
            assert (await s.scalar(select(Message))).status == "sending"
    finally:
        release.set()
        await task
    assert client.send.await_count == 1


@pytest.mark.asyncio
async def test_weixin_attachment_caption_is_chat_not_a_destructive_control(database):
    c, client = setup(database)
    generation, credentials = await bind(c)
    c.media = AsyncMock()
    c.media.prepare.return_value = PreparedMessage(text="/stop")
    c.conversation.plan_control = AsyncMock()
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {
            "msgs": [
                event(
                    item_list=[
                        {"type": 1, "text_item": {"text": "/stop"}},
                        {"type": 2, "image_item": {"media": {"url": "private"}}},
                    ]
                )
            ]
        },
    )
    assert not await c.process_control_once("tenant", "alice")
    assert await c.process_chat_once("tenant", "alice")
    c.conversation.plan_control.assert_not_awaited()
    c.conversation.submit.assert_awaited_once()
    assert c.conversation.submit.call_args.args[-1] == "/stop"


@pytest.mark.asyncio
async def test_feishu_control_is_not_blocked_by_an_unfinished_chat(database):
    c, delivery, sender = fs_setup(database)
    await fs_bind(c.repository)
    run = chat_runtime(c)
    await receive(c, "开始长任务", key="long-chat")
    await c.process_conversation_once(NOW)
    target = ControlIntent(action="stop", target_run_id=run.run_id)
    c.conversation.plan_control = AsyncMock(return_value=target)
    c.conversation.execute_control = AsyncMock(return_value="已请求停止，正在等待执行器退出。")
    await c.repository.ingest(
        Incoming(actor=ACTOR, key="stop", kind="message", text="停止当前任务"),
        NOW + timedelta(seconds=1),
    )
    assert await c.process_once(NOW + timedelta(seconds=1))
    c.conversation.execute_control.assert_not_awaited()
    async with c.repository.sessions() as s:
        row = await s.scalar(select(IncomingRow).where(IncomingRow.event_key == "stop"))
        assert Incoming.model_validate(row.payload).control == target and row.status == "pending"
    assert await c.process_once(NOW + timedelta(seconds=2))
    assert await delivery.deliver_once(NOW + timedelta(seconds=3))
    assert "等待执行器退出" in sender.send.call_args.args[1].content["text"]
    c.conversation.agui.create_run_with_result.assert_awaited_once()
    async with c.repository.sessions() as s:
        row = await s.scalar(select(IncomingRow).where(IncomingRow.event_key == "long-chat"))
        assert row.status == "chat_running"


@pytest.mark.asyncio
async def test_feishu_control_recovery_reuses_persisted_target(database, monkeypatch):
    c, _, _ = fs_setup(database)
    await fs_bind(c.repository)
    target = ControlIntent(action="stop", target_run_id="original-run")
    c.conversation.plan_control = AsyncMock(return_value=target)
    c.conversation.execute_control = AsyncMock(return_value="已停止当前执行。")
    await c.repository.ingest(Incoming(actor=ACTOR, key="stop", kind="message", text="/stop"), NOW)
    assert await c.process_once(NOW)
    original = c.repository.reply
    monkeypatch.setattr(
        c.repository, "reply", AsyncMock(side_effect=RuntimeError("crash after cancel"))
    )
    assert await c.process_once(NOW)
    monkeypatch.setattr(c.repository, "reply", original)
    c.conversation.plan_control.return_value = ControlIntent(
        action="stop", target_run_id="unrelated-new-run"
    )
    assert await c.process_once(NOW + timedelta(seconds=3))
    c.conversation.plan_control.assert_awaited_once()
    assert all(
        call.args[2].target_run_id == "original-run"
        for call in c.conversation.execute_control.call_args_list
    )


@pytest.mark.asyncio
async def test_feishu_attachment_caption_remains_chat(database):
    c, _, _ = fs_setup(database)
    await fs_bind(c.repository)
    chat_runtime(c)
    c.conversation.plan_control = AsyncMock()
    await c.repository.ingest(
        Incoming(
            actor=ACTOR,
            key="file",
            kind="message",
            text="/stop",
            resources=[MessageResource(kind="file", key="file")],
        ),
        NOW,
    )
    assert await c.process_once(NOW)
    c.conversation.plan_control.assert_not_awaited()
    async with c.repository.sessions() as s:
        assert (await s.scalar(select(IncomingRow))).status == "chat_pending"


@pytest.mark.asyncio
async def test_weixin_help_receipt_does_not_erase_chat_diagnostics(database):
    c, _ = setup(database)
    generation, credentials = await bind(c)
    await c.ingest("tenant", "alice", generation, credentials, {"msgs": [event()]})
    await c.process_once("tenant", "alice")
    c.conversation.plan_control = AsyncMock(return_value=ControlIntent(action="help"))
    c.conversation.execute_control = AsyncMock(return_value="助理帮助")
    await c.ingest(
        "tenant",
        "alice",
        generation,
        credentials,
        {"msgs": [event(2, item_list=[{"type": 1, "text_item": {"text": "/help"}}])]},
    )
    await c.process_control_once("tenant", "alice")
    await c.process_control_once("tenant", "alice")
    assert all(item["status"] == "pass" for item in (await c.diagnose("tenant", "alice"))["checks"])
