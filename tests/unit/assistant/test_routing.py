"""Host-side decisions, replay and workflow admission, independent of model wording."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.adapters.memory import InMemoryEventBus, InMemoryEventRepository
from harness.application.events import EventService
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
from harness.assistant.models import SpecialistHandover, SpecialistStatus
from harness.assistant.routing import assistant_routing_overlay
from harness.core.models import Run, RunStatus, Session
from tests.unit.assistant.test_mention_message import _specialist

NOW = datetime(2026, 11, 3, tzinfo=UTC)


def setup():
    counter = iter(range(1000))
    events = EventService(
        InMemoryEventRepository(),
        InMemoryEventBus(),
        clock=lambda: NOW,
        id_generator=lambda prefix: f"{prefix}-{next(counter)}",
    )
    bot = _specialist().model_copy(update={"description": "查询各地实时天气和预报"})
    assistant = SimpleNamespace(
        validate_routing_parent=AsyncMock(),
        repository=SimpleNamespace(
            list_specialists=AsyncMock(return_value=[bot]),
            get_specialist=AsyncMock(return_value=bot),
        ),
        send_mention_message=AsyncMock(
            return_value=SpecialistHandover(
                handoverId="receipt-route",
                specialistId=bot.specialist_id,
                tenantId="t",
                userId="u",
                messageId="mention-specialist:route:parent",
                sourceThreadId=MAIN,
                targetThreadId="assistant_original_weather",
                runId="child-run",
                createdAt=NOW,
                updatedAt=NOW,
            )
        ),
    )
    run = Run(
        run_id="parent",
        session_id="main-session",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="parent",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "武汉明天会下雨吗", "quoted_message_context": "本人先前选择：武汉"},
    )
    session = Session(
        session_id="main-session",
        tenant_id="t",
        user_id="u",
        agent_name="main",
        agent_version="1",
        created_at=NOW,
    )
    return assistant, events, run, session, bot


def text(result):
    return result["content"][0]["text"]


@pytest.mark.asyncio
async def test_semantic_directory_and_original_message_delivery_without_new_objective():
    assistant, events, run, session, bot = setup()
    overlay = await assistant_routing_overlay(assistant, events, run, session)
    assert bot.description in overlay.instructions
    assert overlay.workflow.before_tool("WebFetch")
    tool = overlay.tools[0].handler
    result = await tool(
        {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "职责包含天气查询"}
    )
    assert "已交给" in text(result) and "child-run" not in text(result)
    args = assistant.send_mention_message.call_args.kwargs
    assert args["text"] == run.input["prompt"]
    assert args["quoted_message_context"] == run.input["quoted_message_context"]
    assert args["source_thread_id"] == MAIN and args["dispatched"] is True
    assert overlay.workflow.before_tool("Bash")
    assert overlay.workflow.before_tool("assistant_specialist_status")  # No polling after dispatch.
    assert overlay.workflow.before_finish() is None
    recorded = await events.list_after("t", "parent", 0)
    assert [e.type for e in recorded] == ["assistant.route.intent", "ui.assistant_delivery"]
    assert recorded[-1].payload["runId"] == "child-run"


@pytest.mark.asyncio
async def test_parallel_retries_and_recovery_deliver_once_and_do_not_change_decision():
    assistant, events, run, session, bot = setup()
    overlay = await assistant_routing_overlay(assistant, events, run, session)
    args = {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "天气职责匹配"}
    await asyncio.gather(*(overlay.tools[0].handler(args) for _ in range(3)))
    recovered = await assistant_routing_overlay(assistant, events, run, session)
    await recovered.tools[0].handler(args | {"reason": "重试说明"})
    assert assistant.send_mention_message.await_count == 1
    assert "不能重复派发" in text(
        await recovered.tools[0].handler({"action": "direct", "reason": "尝试改派"})
    )
    assert len(await events.list_after("t", "parent", 0)) == 2


@pytest.mark.asyncio
async def test_persisted_intent_retries_same_target_after_interrupted_delivery():
    assistant, events, run, session, bot = setup()
    assistant.send_mention_message.side_effect = RuntimeError("connection interrupted")
    overlay = await assistant_routing_overlay(assistant, events, run, session)
    args = {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "职责匹配"}
    with pytest.raises(RuntimeError):
        await overlay.tools[0].handler(args)
    recovered = await assistant_routing_overlay(assistant, events, run, session)
    assert recovered.workflow.before_finish()
    assert recovered.workflow.before_tool("WebFetch")
    assistant.send_mention_message.side_effect = None
    await recovered.tools[0].handler(args)
    assert assistant.send_mention_message.await_count == 2
    assert {c.kwargs["idempotency_key"] for c in assistant.send_mention_message.call_args_list} == {
        "route:parent"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["direct", "clarify"])
async def test_direct_and_clarify_do_not_deliver_or_grant_permissions(action):
    assistant, events, run, session, bot = setup()
    overlay = await assistant_routing_overlay(assistant, events, run, session)
    await overlay.tools[0].handler({"action": action, "reason": "无匹配职责或城市不明确"})
    assistant.send_mention_message.assert_not_awaited()
    assert (overlay.workflow.before_tool("WebSearch") is None) is (action == "direct")
    assert overlay.workflow.before_finish() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["paused", "archived", "blocked", "draft", "needs_input"])
async def test_unavailable_target_is_not_accepted_and_can_be_clarified(state):
    assistant, events, run, session, bot = setup()
    assistant.repository.get_specialist.return_value = bot.model_copy(
        update={"status": SpecialistStatus(state)}
    )
    overlay = await assistant_routing_overlay(assistant, events, run, session)
    result = await overlay.tools[0].handler(
        {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "查询天气"}
    )
    assert "未完成" in text(result)
    assistant.send_mention_message.assert_not_awaited()
    assert not await events.list_after("t", "parent", 0)
    await overlay.tools[0].handler({"action": "clarify", "reason": "匹配助理不可用"})


@pytest.mark.asyncio
async def test_unknown_target_missing_grant_and_attachment_never_drop_input_or_start_work():
    assistant, events, run, session, bot = setup()
    overlay = await assistant_routing_overlay(assistant, events, run, session)
    await overlay.tools[0].handler(
        {"action": "dispatch", "specialist_id": "another-user", "reason": "不能跨用户"}
    )
    bot = bot.model_copy(update={"config": {"required_local_tools": ["run_commands"]}})
    assistant.repository.get_specialist.return_value = bot
    await overlay.tools[0].handler(
        {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "缺授权"}
    )
    attached = run.model_copy(update={"input": run.input | {"input_artifact_ids": ["owned-file"]}})
    attached_router = await assistant_routing_overlay(assistant, events, attached, session)
    await attached_router.tools[0].handler(
        {"action": "dispatch", "specialist_id": bot.specialist_id, "reason": "有附件"}
    )
    assistant.send_mention_message.assert_not_awaited()
    assert not await events.list_after("t", "parent", 0)
