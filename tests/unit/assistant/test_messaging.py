"""Bot operations are independent, durable and scoped to the actual request."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.adapters.memory import InMemoryEventBus, InMemoryEventRepository
from harness.application.events import EventService
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD as MAIN
from harness.assistant.messaging import assistant_messaging_overlay
from harness.assistant.models import SpecialistHandover
from harness.core.errors import ConflictError, PermissionDeniedError
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
        validate_assistant_control_run=AsyncMock(),
        repository=SimpleNamespace(
            list_specialists=AsyncMock(return_value=[bot]),
            get_specialist=AsyncMock(return_value=bot),
        ),
        create_specialist=AsyncMock(return_value=(bot, True)),
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


def payload(result):
    return json.loads(text(result))


async def controls(assistant, events, run, session):
    overlay = await assistant_messaging_overlay(assistant, events, run, session)
    return {t.name: t.handler for t in overlay.tools}


@pytest.mark.asyncio
async def test_creation_is_only_a_contact_and_retry_survives_reconstruction():
    assistant, events, run, session, bot = setup()
    args = {"request_key": "new-bot", "name": "任意领域", "description": "按当前请求工作"}
    tools = await controls(assistant, events, run, session)
    first = await tools["assistant_create_specialist"](args)
    restored = await controls(assistant, events, run, session)
    assert await restored["assistant_create_specialist"](args) == first
    assistant.create_specialist.assert_awaited_once()
    assistant.send_mention_message.assert_not_awaited()
    assert payload(first)["specialistId"] == bot.specialist_id
    assert payload(first)["status"] == "created"
    assert [e.type for e in await events.list_after("t", "parent", 0)] == [
        "assistant.bot.creation.requested",
        "assistant.bot.created",
    ]
    changed = await restored["assistant_create_specialist"](args | {"name": "different"})
    assert changed["isError"]
    assistant.create_specialist.assert_awaited_once()


@pytest.mark.asyncio
async def test_delivery_uses_original_message_and_origin_without_a_routing_plan():
    assistant, events, run, session, bot = setup()
    overlay = await assistant_messaging_overlay(assistant, events, run, session)
    assert bot.description in overlay.instructions
    assert not hasattr(overlay, "workflow")
    assert not hasattr(overlay, "system_prefix")
    tools = {t.name: t.handler for t in overlay.tools}
    result = await tools["assistant_send_message"](
        {"request_key": "m1", "specialist_id": bot.specialist_id}
    )
    assert payload(result)["status"] == "delivered"
    args = assistant.send_mention_message.call_args.kwargs
    assert args["text"] == run.input["prompt"]
    assert args["quoted_message_context"] == run.input["quoted_message_context"]
    assert args["source_thread_id"] == MAIN and args["dispatched"] is True
    assert [e.type for e in await events.list_after("t", "parent", 0)] == [
        "assistant.message.requested",
        "ui.assistant_delivery",
    ]
    assistant.create_specialist.assert_not_awaited()


@pytest.mark.asyncio
async def test_distinct_messages_are_allowed_and_retries_never_duplicate_delivery():
    assistant, events, run, session, bot = setup()
    tools = await controls(assistant, events, run, session)
    args = {"request_key": "m1", "specialist_id": bot.specialist_id, "text": "先做资料检查"}
    results = await asyncio.gather(*(tools["assistant_send_message"](args) for _ in range(3)))
    restored = await controls(assistant, events, run, session)
    assert await restored["assistant_send_message"](args) == results[0]
    assistant.send_mention_message.assert_awaited_once()
    for changed in ({"text": "different"}, {"specialist_id": "another"}):
        assert (await restored["assistant_send_message"](args | changed))["isError"]
    await restored["assistant_send_message"](args | {"request_key": "m2", "text": "再检查另一份"})
    assert assistant.send_mention_message.await_count == 2
    assert "来源请求：" in assistant.send_mention_message.call_args.kwargs["quoted_message_context"]


@pytest.mark.asyncio
async def test_interrupted_delivery_retries_the_same_transport_identity():
    assistant, events, run, session, bot = setup()
    tools = await controls(assistant, events, run, session)
    args = {"request_key": "m1", "specialist_id": bot.specialist_id}
    assistant.send_mention_message.side_effect = RuntimeError("connection interrupted")
    with pytest.raises(RuntimeError):
        await tools["assistant_send_message"](args)
    restored = await controls(assistant, events, run, session)
    assistant.send_mention_message.side_effect = None
    assert payload(await restored["assistant_send_message"](args))["status"] == "delivered"
    assert {c.kwargs["idempotency_key"] for c in assistant.send_mention_message.call_args_list} == {
        "message:parent:m1"
    }
    assert len(await events.list_after("t", "parent", 0)) == 2


@pytest.mark.asyncio
async def test_attachments_are_forwarded_without_business_filters_or_scope_expansion():
    assistant, events, run, session, bot = setup()
    run = run.model_copy(
        update={
            "input": run.input
            | {
                "input_artifact_ids": ["current", "inherited"],
                "inherited_input_artifact_ids": ["inherited"],
            }
        }
    )
    tools = await controls(assistant, events, run, session)
    args = {"request_key": "file1", "specialist_id": bot.specialist_id}
    await tools["assistant_send_message"](args)
    assert assistant.send_mention_message.call_args.kwargs["input_artifact_ids"] == ["current"]
    await tools["assistant_send_message"](
        args | {"request_key": "file2", "input_artifact_ids": ["inherited"]}
    )
    assert assistant.send_mention_message.call_args.kwargs["input_artifact_ids"] == ["inherited"]
    assert (
        await tools["assistant_send_message"](
            args | {"request_key": "bad", "input_artifact_ids": ["not-owned"]}
        )
    )["isError"]
    assert assistant.send_mention_message.await_count == 2
    assert (await tools["assistant_send_message"](args | {"input_artifact_ids": []}))["isError"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ConflictError("已停止"), PermissionDeniedError("not owned")])
async def test_lifecycle_and_ownership_admission_apply_to_both_operations(error):
    assistant, events, run, session, bot = setup()
    assistant.validate_assistant_control_run.side_effect = error
    tools = await controls(assistant, events, run, session)
    assert (await tools["assistant_create_specialist"]({"request_key": "c1", "name": "new"}))[
        "isError"
    ]
    assert (
        await tools["assistant_send_message"](
            {"request_key": "m1", "specialist_id": bot.specialist_id}
        )
    )["isError"]
    assistant.create_specialist.assert_not_awaited()
    assistant.send_mention_message.assert_not_awaited()
    assert not await events.list_after("t", "parent", 0)
