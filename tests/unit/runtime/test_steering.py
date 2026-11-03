import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from claude_agent_sdk import (
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    UserMessage,
)

from harness.adapters.memory import (
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryTaskQueue,
)
from harness.application.events import EventService
from harness.application.runs import RunService
from harness.core.errors import ConflictError
from harness.core.models import Run, RunStatus, Session
from harness.runtime.claude_sdk import _steerable_response
from harness.runtime.steering import SteeringInbox


async def setup_inbox(*, assistant: bool = False):
    now = datetime.now(UTC)
    run = Run(
        run_id="r",
        tenant_id="t",
        session_id="s",
        status=RunStatus.RUNNING,
        idempotency_key="initial",
        input={"conversation_surface": "assistant"} if assistant else {},
        created_at=now,
        updated_at=now,
    )
    runs = InMemoryRunRepository()
    await runs.add(run)
    sessions = InMemorySessionRepository()
    await sessions.add(
        Session(
            session_id="s",
            tenant_id="t",
            user_id="u",
            agent_name="a",
            agent_version="1",
            created_at=now,
        )
    )
    events = EventService(
        InMemoryEventRepository(),
        InMemoryEventBus(),
        clock=lambda: now,
        id_generator=lambda prefix: f"{prefix}-{uuid4()}",
    )
    service = RunService(
        sessions,
        runs,
        InMemoryTaskQueue(),
        events,
        clock=lambda: now,
        id_generator=lambda prefix: f"{prefix}-{uuid4()}",
    )
    return run, runs, service, SteeringInbox(run, runs, events)


@pytest.mark.asyncio
async def test_durable_guidance_deduplicates_and_reports_closed_boundary():
    run, runs, service, inbox = await setup_inbox()
    with pytest.raises(ConflictError):
        await service.steer("t", "r", "one", "guide")
    await inbox.open()
    await service.steer("t", "r", "one", "guide")
    await service.steer("t", "r", "one", "guide")
    with pytest.raises(ConflictError):
        await service.steer("t", "r", "one", "different")
    items = await inbox.read()
    assert len(items) == 1
    await inbox.sending(items[0])
    await inbox.acknowledge(items[0])
    assert await inbox.read() == []
    state = await service.steering_state("t", "r")
    assert state["requests"][0]["status"] == "accepted"
    await service.steer("t", "r", "late", "too late")
    await inbox.close()
    state = await service.steering_state("t", "r")
    assert state["available"] is False
    assert state["requests"][1]["status"] == "not_delivered"
    with pytest.raises(ConflictError):
        await service.steer("t", "r", "closed", "after close")
    assert await runs.compare_and_set(
        RunStatus.RUNNING,
        run.model_copy(
            update={
                "status": RunStatus.CANCELLED,
                "fencing_token": 1,
            }
        ),
    )
    assert await inbox.read() == []


def result():
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="sdk",
        stop_reason="end_turn",
    )


@pytest.mark.asyncio
async def test_sdk_merged_guidance_completes_on_one_result_without_hanging():
    # Production regression: two query() inputs produced one terminal result,
    # then receive_messages stayed open for the next turn until the run reaper.
    _, _, service, inbox = await setup_inbox()
    delivered = asyncio.Event()

    class Client:
        prompts: list[str] = []

        async def query(self, text):
            self.prompts.append(text)
            delivered.set()

        async def receive_messages(self):
            await service.steer("t", "r", "one", "change direction")
            yield "original progress"
            await delivered.wait()
            yield UserMessage(content="change direction")
            yield "guided answer"
            yield result()
            await asyncio.Event().wait()

    client = Client()
    async with asyncio.timeout(1):
        observed = [
            item async for item in _steerable_response(cast(ClaudeSDKClient, client), inbox)
        ]
    assert client.prompts == ["change direction"]
    assert [item for item in observed if isinstance(item, str)] == [
        "original progress",
        "guided answer",
    ]
    assert len([item for item in observed if isinstance(item, ResultMessage)]) == 1
    assert inbox.closed
    assert (await service.steering_state("t", "r"))["requests"][0]["status"] == "accepted"


@pytest.mark.asyncio
async def test_sdk_receipt_after_first_result_waits_for_guided_answer():
    _, _, service, inbox = await setup_inbox()
    delivered = asyncio.Event()

    class Client:
        async def query(self, text):
            delivered.set()

        async def receive_messages(self):
            await service.steer("t", "r", "one", "next direction")
            await delivered.wait()
            yield result()
            yield UserMessage(content="next direction")
            yield "guided answer"
            yield result()
            await asyncio.Event().wait()

    async with asyncio.timeout(1):
        observed = [
            item async for item in _steerable_response(cast(ClaudeSDKClient, Client()), inbox)
        ]
    assert "guided answer" in observed
    assert len([item for item in observed if isinstance(item, ResultMessage)]) == 1
    assert (await service.steering_state("t", "r"))["requests"][0]["status"] == "accepted"


@pytest.mark.asyncio
async def test_sdk_missing_receipt_keeps_guidance_without_hanging(monkeypatch):
    monkeypatch.setattr("harness.runtime.claude_sdk.SDK_STEERING_RECEIPT_TIMEOUT_SECONDS", 0.01)
    _, _, service, inbox = await setup_inbox()
    delivered = asyncio.Event()

    class Client:
        async def query(self, text):
            delivered.set()

        async def receive_messages(self):
            await service.steer("t", "r", "one", "unconfirmed direction")
            await delivered.wait()
            yield result()
            await asyncio.Event().wait()

    async with asyncio.timeout(1):
        observed = [
            item async for item in _steerable_response(cast(ClaudeSDKClient, Client()), inbox)
        ]
    assert len([item for item in observed if isinstance(item, ResultMessage)]) == 1
    assert inbox.closed
    request = (await service.steering_state("t", "r"))["requests"][0]
    assert request["status"] == "failed"
    assert request["text"] == "unconfirmed direction"


@pytest.mark.asyncio
async def test_sdk_terminal_boundary_drains_a_correction_before_closing_session():
    _, _, service, inbox = await setup_inbox()
    submitted = asyncio.Event()

    class Client:
        async def query(self, text):
            assert text == "save for the next turn"
            submitted.set()

        async def receive_messages(self):
            await service.steer("t", "r", "late", "save for the next turn")
            yield result()
            await submitted.wait()
            yield UserMessage(content="save for the next turn")
            yield "corrected answer"
            yield result()

    async with asyncio.timeout(1):
        observed = [
            item async for item in _steerable_response(cast(ClaudeSDKClient, Client()), inbox)
        ]
    assert "corrected answer" in observed
    assert len([item for item in observed if isinstance(item, ResultMessage)]) == 1
    assert inbox.closed
    state = await service.steering_state("t", "r")
    assert state["requests"][0]["status"] == "accepted"


@pytest.mark.asyncio
async def test_sdk_failure_preserves_prompt_and_cancellation_closes_inbox():
    _, _, service, inbox = await setup_inbox()

    attempted = asyncio.Event()

    class Client:
        async def query(self, text):
            attempted.set()
            raise RuntimeError("transport down")

        async def receive_messages(self):
            await service.steer("t", "r", "one", "keep this")
            await attempted.wait()
            yield result()

    await anext(_steerable_response(cast(ClaudeSDKClient, Client()), inbox))
    state = await service.steering_state("t", "r")
    assert state["requests"][0]["status"] == "failed"
    assert state["requests"][0]["text"] == "keep this"
    assert not state["available"]

    _, _, _, inbox2 = await setup_inbox()

    class Waiting:
        async def receive_messages(self):
            yield "started"
            await asyncio.Event().wait()

    generator = _steerable_response(cast(ClaudeSDKClient, Waiting()), inbox2)
    assert await anext(generator) == "started"
    await generator.aclose()
    assert inbox2.closed


@pytest.mark.asyncio
async def test_restarted_or_stale_worker_never_replays_uncertain_guidance():
    run, runs, service, inbox = await setup_inbox()
    await inbox.open()
    await service.steer("t", "r", "one", "only once")
    item = (await inbox.read())[0]
    await inbox.sending(item)
    restarted = SteeringInbox(run, runs, inbox.events)
    await restarted.open()
    assert await restarted.read() == []
    request = (await service.steering_state("t", "r"))["requests"][0]
    assert request["status"] == "unknown"
    assert request["text"] == "only once"
    # Reopening again does not manufacture another receipt or replay the input.
    await restarted.open()
    events = await inbox.events.list_after("t", "r", 0)
    assert len([e for e in events if e.type == "run.steer.unknown"]) == 1
    await service.steer("t", "r", "two", "new")
    assert await runs.compare_and_set(
        RunStatus.RUNNING, run.model_copy(update={"fencing_token": 1})
    )
    assert await inbox.read() == []
    await inbox.acknowledge(item)
    assert (await service.steering_state("t", "r"))["requests"][0]["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_steering", [False, True])
@pytest.mark.parametrize("finish_before_result", [False, True])
async def test_background_agents_finish_and_summarize_before_run_closes(
    with_steering: bool, finish_before_result: bool,
):
    _, _, _, inbox = await setup_inbox()
    final = result()
    final.num_turns = 3

    def state(value: str) -> SystemMessage:
        return SystemMessage(subtype="session_state_changed", data={"state": value})

    def started(name: str) -> TaskStartedMessage:
        return TaskStartedMessage(
            subtype="task_started", data={}, task_id=name, description=name,
            uuid=name, session_id="sdk", task_type="local_agent",
        )

    class Client:
        async def receive_messages(self):
            yield state("running")
            yield started("alpha")
            yield started("beta")
            if not finish_before_result:
                yield result()
            yield TaskUpdatedMessage(
                subtype="task_updated", data={}, task_id="alpha",
                patch={"status": "completed"},
            )
            yield TaskNotificationMessage(
                subtype="task_notification", data={}, task_id="beta", status="completed",
                output_file="", summary="done", uuid="terminal-beta", session_id="sdk",
            )
            # Even when both children finish first, a queued lead turn is owed.
            yield result()
            yield "both agent receipts summarized"
            yield final
            yield state("idle")
            await asyncio.Event().wait()

    async with asyncio.timeout(1):
        observed = [item async for item in _steerable_response(
            cast(ClaudeSDKClient, Client()), inbox if with_steering else None,
        )]
    assert [item for item in observed if isinstance(item, ResultMessage)] == [final]
    assert "both agent receipts summarized" in observed
    if with_steering:
        assert inbox.closed


@pytest.mark.asyncio
async def test_background_stream_closure_cannot_report_intermediate_success():
    class Client:
        async def receive_messages(self):
            yield SystemMessage(subtype="session_state_changed", data={"state": "running"})
            yield result()

    observed: list[Any] = []
    with pytest.raises(RuntimeError, match="before background work reached idle"):
        async for message in _steerable_response(cast(ClaudeSDKClient, Client()), None):
            observed.append(message)
    assert not any(isinstance(item, ResultMessage) for item in observed)


@pytest.mark.asyncio
async def test_assistant_redirect_interrupts_then_consumes_boundary_before_sending_burst():
    _, _, service, inbox = await setup_inbox(assistant=True)
    interrupted, submitted = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    class Client:
        async def interrupt(self):
            calls.append("interrupt")
            interrupted.set()

        async def query(self, text):
            calls.append(text)
            if text == "补充第二点":
                submitted.set()

        async def receive_messages(self):
            await service.steer("t", "r", "one", "调整方向")
            await service.steer("t", "r", "two", "补充第二点")
            yield "original work"
            # The original task never completes naturally. A correction must
            # interrupt it, rather than waiting for its final answer.
            await interrupted.wait()
            assert calls == ["interrupt"]
            boundary = result()
            boundary.is_error = True
            boundary.subtype = "interrupted"
            yield boundary
            await submitted.wait()
            yield UserMessage(content="调整方向")
            yield UserMessage(content="补充第二点")
            yield "new direction applied"
            yield result()

    async with asyncio.timeout(2):
        observed = [
            item async for item in _steerable_response(cast(ClaudeSDKClient, Client()), inbox)
        ]
    assert calls == ["interrupt", "调整方向", "补充第二点"]
    assert "new direction applied" in observed
    assert all(not item.is_error for item in observed if isinstance(item, ResultMessage))
    state = await service.steering_state("t", "r")
    assert [item["status"] for item in state["requests"]] == ["accepted", "accepted"]


@pytest.mark.asyncio
async def test_real_sdk_failure_without_redirect_is_not_hidden_by_late_input():
    _, _, service, inbox = await setup_inbox()

    class Client:
        async def query(self, text):
            pytest.fail("A provider failure must not silently start a replacement turn")

        async def receive_messages(self):
            await service.steer("t", "r", "late", "keep me")
            failure = result()
            failure.is_error = True
            yield failure

    observed = [item async for item in _steerable_response(cast(ClaudeSDKClient, Client()), inbox)]
    assert len(observed) == 1 and observed[0].is_error
    assert (await service.steering_state("t", "r"))["requests"][0]["status"] == "not_delivered"
