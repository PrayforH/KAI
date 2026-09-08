import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import (
    InMemoryCancellationWakeup,
    InMemoryEventBus,
    InMemoryEventRepository,
)
from harness.core.errors import ConflictError
from harness.core.events import RunEvent


def event(event_id: str, sequence: int) -> RunEvent:
    return RunEvent(
        event_id=event_id,
        run_id="run-1",
        session_id="session-1",
        tenant_id="tenant-a",
        sequence=sequence,
        type="run.status",
        timestamp=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_event_repository_enforces_order_and_idempotency() -> None:
    repository = InMemoryEventRepository()
    first = event("event-1", 1)

    await repository.append(first)
    await repository.append(first)

    assert await repository.list_after("tenant-a", "run-1", 0) == [first]
    with pytest.raises(ConflictError, match="sequence"):
        await repository.append(event("event-3", 3))


@pytest.mark.asyncio
async def test_event_repository_reads_latest_type_for_exact_session() -> None:
    repository = InMemoryEventRepository()
    first = event("event-1", 1).model_copy(update={"type": "context.window.observed"})
    second = event("event-2", 1).model_copy(
        update={
            "run_id": "run-2",
            "type": "context.window.observed",
            "timestamp": first.timestamp + timedelta(microseconds=1),
        }
    )
    other = event("event-3", 1).model_copy(
        update={"run_id": "run-3", "session_id": "session-2", "type": second.type}
    )
    await repository.append(first)
    await repository.append(second)
    await repository.append(other)

    assert (
        await repository.latest_for_session_type("tenant-a", "session-1", "context.window.observed")
        == second
    )
    assert (
        await repository.latest_for_session_type("tenant-b", "session-1", "context.window.observed")
        is None
    )
    assert (
        await repository.latest_for_session_types(
            "tenant-a",
            "session-1",
            ("context.window.observed", "context.window.unavailable"),
        )
        == second
    )


@pytest.mark.asyncio
async def test_event_bus_replays_after_sequence() -> None:
    bus = InMemoryEventBus()
    first = event("event-1", 1)
    second = event("event-2", 2)
    await bus.publish(first)
    await bus.publish(second)

    assert await bus.read("tenant-a", "run-1", after_sequence=1) == [second]


@pytest.mark.asyncio
async def test_event_bus_wakes_waiters_without_polling() -> None:
    bus = InMemoryEventBus()
    waiter = asyncio.create_task(bus.wait("tenant-a", "run-1", 0, timeout_seconds=0.5))
    await asyncio.sleep(0)

    await bus.publish(event("event-1", 1))

    assert await waiter is True
    assert await bus.wait("tenant-a", "run-1", 1, timeout_seconds=0.01) is False


@pytest.mark.asyncio
async def test_cancellation_wakeup_is_race_safe_and_monotonic() -> None:
    wakeup = InMemoryCancellationWakeup()
    waiter = asyncio.create_task(wakeup.wait("tenant-a", "run-1", 2, timeout_seconds=0.5))
    await asyncio.sleep(0)

    await wakeup.publish("tenant-a", "run-1", 3)

    assert await waiter is True
    assert await wakeup.wait("tenant-a", "run-1", 2, timeout_seconds=0.01) is True
    assert await wakeup.wait("tenant-a", "run-1", 3, timeout_seconds=0.01) is False

    await wakeup.publish("tenant-a", "run-1", 1)
    assert await wakeup.wait("tenant-a", "run-1", 3, timeout_seconds=0.01) is False
