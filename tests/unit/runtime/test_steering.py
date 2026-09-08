import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from claude_agent_sdk import ClaudeSDKClient, ResultMessage, UserMessage

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


async def setup_inbox():
    now = datetime.now(UTC)
    run = Run(
        run_id="r",
        tenant_id="t",
        session_id="s",
        status=RunStatus.RUNNING,
        idempotency_key="initial",
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
        prompts = []

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
async def test_sdk_terminal_boundary_preserves_undelivered_guidance():
    _, _, service, inbox = await setup_inbox()

    class Client:
        async def query(self, text):
            pytest.fail("Must not start another SDK turn after the terminal result")

        async def receive_messages(self):
            await service.steer("t", "r", "late", "save for the next turn")
            yield result()

    observed = [item async for item in _steerable_response(cast(ClaudeSDKClient, Client()), inbox)]
    assert len(observed) == 1
    assert inbox.closed
    state = await service.steering_state("t", "r")
    assert state["requests"][0]["status"] == "not_delivered"
    assert state["requests"][0]["text"] == "save for the next turn"


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
    assert await restarted.read() == []
    await service.steer("t", "r", "two", "new")
    assert await runs.compare_and_set(
        RunStatus.RUNNING, run.model_copy(update={"fencing_token": 1})
    )
    assert await inbox.read() == []
