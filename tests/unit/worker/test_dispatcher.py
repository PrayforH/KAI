"""Durable dispatch behaviour: leasing, backoff, reclaim and observation."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import (
    InMemoryRunExecutionCommandRepository,
    InMemoryTaskQueue,
)
from harness.core.ports import (
    ExecutionCommandStatus,
    RunExecutionCommand,
    execution_command_id,
)
from harness.reliability.metrics import ReliabilityMetrics
from harness.worker.dispatcher import (
    ExecutionCommandDispatcher,
    running_dispatcher,
    safe_error,
)

START = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class FailingQueue(InMemoryTaskQueue):
    """A queue that refuses publishes until the test lets it recover."""

    def __init__(self) -> None:
        super().__init__()
        self.failures = 0
        self.available = True

    async def enqueue(self, task) -> None:  # type: ignore[no-untyped-def]
        if not self.available:
            self.failures += 1
            raise ConnectionError(
                "Cannot connect to redis://harness:redis-secret@127.0.0.1:6379/0"
            )
        await super().enqueue(task)


def command(run_id: str, now: datetime, *, tenant_id: str = "tenant-a") -> RunExecutionCommand:
    return RunExecutionCommand(
        command_id=execution_command_id(run_id),
        tenant_id=tenant_id,
        run_id=run_id,
        session_id="session-1",
        status=ExecutionCommandStatus.PENDING,
        created_at=now,
        available_at=now,
    )


def dispatcher(
    commands: InMemoryRunExecutionCommandRepository,
    queue: InMemoryTaskQueue,
    clock: MutableClock,
    *,
    owner: str = "dispatcher-a",
    lease_seconds: float = 60,
    metrics: ReliabilityMetrics | None = None,
) -> ExecutionCommandDispatcher:
    return ExecutionCommandDispatcher(
        commands,
        queue,
        clock=clock,
        owner=owner,
        lease_seconds=lease_seconds,
        batch_size=10,
        interval_seconds=0.5,
        retry_base_seconds=2,
        retry_max_seconds=8,
        metrics=metrics,
    )


@pytest.mark.asyncio
async def test_claiming_publishes_and_closes_the_obligation() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = InMemoryTaskQueue()
    clock = MutableClock()
    await commands.insert(command("run-1", START))

    cycle = await dispatcher(commands, queue, clock).run_once()

    assert (cycle.claimed, cycle.dispatched, cycle.rescheduled) == (1, 1, 0)
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.DISPATCHED
    assert stored.attempts == 1
    assert stored.lease_owner is None
    task = await queue.dequeue()
    assert task is not None
    assert (task.tenant_id, task.run_id, task.session_id) == ("tenant-a", "run-1", "session-1")


@pytest.mark.asyncio
async def test_publish_failure_backs_off_and_then_retries() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = FailingQueue()
    clock = MutableClock()
    queue.available = False
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock)

    first = await pending.run_once()

    assert (first.claimed, first.dispatched, first.rescheduled) == (1, 0, 1)
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    # The obligation survives the outage: still pending, counted, and diagnosed.
    assert stored.status is ExecutionCommandStatus.PENDING
    assert stored.failures == 1
    assert stored.lease_owner is None
    assert stored.available_at == START + timedelta(seconds=2)
    assert stored.last_error is not None
    assert "ConnectionError" in stored.last_error
    assert "redis-secret" not in stored.last_error

    # Backoff is respected: the same instant claims nothing.
    assert (await pending.run_once()).claimed == 0

    queue.available = True
    clock.advance(2)
    second = await pending.run_once()

    assert (second.claimed, second.dispatched) == (1, 1)
    assert (await queue.dequeue()).run_id == "run-1"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_backoff_is_capped_and_grows_with_attempts() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = FailingQueue()
    clock = MutableClock()
    queue.available = False
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock)

    delays: list[float] = []
    for _ in range(5):
        await pending.run_once()
        stored = await commands.get("tenant-a", "run-1")
        assert stored is not None
        delays.append((stored.available_at - clock()).total_seconds())
        clock.advance(delays[-1])

    assert delays == [2, 4, 8, 8, 8]
    assert queue.failures == 5


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_by_another_dispatcher() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    clock = MutableClock()
    await commands.insert(command("run-1", START))
    survivor = dispatcher(
        commands, InMemoryTaskQueue(), clock, owner="dispatcher-b", lease_seconds=30
    )
    # Owner A claims and then dies before it can close the lease.
    abandoned = await commands.claim_pending(
        owner="dispatcher-a", lease_seconds=30, limit=10, now=START
    )
    assert len(abandoned) == 1

    # A live lease is not stolen...
    assert (await survivor.run_once()).claimed == 0

    # ...but an expired one is, and the obligation is still delivered.
    clock.advance(31)
    takeover = await survivor.run_once()

    assert (takeover.claimed, takeover.dispatched) == (1, 1)
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.DISPATCHED
    assert stored.lease_owner is None
    assert stored.attempts == 2


@pytest.mark.asyncio
async def test_a_reclaimed_command_cannot_be_closed_by_the_stale_owner() -> None:
    """Publish-then-crash redelivery must stay possible, never be lost."""

    commands = InMemoryRunExecutionCommandRepository()
    clock = MutableClock()
    await commands.insert(command("run-1", START))
    stalled = await commands.claim_pending(
        owner="dispatcher-a", lease_seconds=30, limit=10, now=START
    )
    reclaimed = await commands.claim_pending(
        owner="dispatcher-b", lease_seconds=30, limit=10, now=START + timedelta(seconds=31)
    )
    assert reclaimed[0].lease_owner == "dispatcher-b"

    # Owner A's late acknowledgement and late retry are both refused, so the
    # obligation stays pending for a further delivery instead of being lost.
    assert await commands.mark_dispatched(stalled[0], now=clock()) is False
    assert (
        await commands.reschedule(
            stalled[0], available_at=clock(), error="late owner"
        )
        is False
    )
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.PENDING
    assert stored.lease_owner == "dispatcher-b"
    assert stored.failures == 0


@pytest.mark.asyncio
async def test_health_reports_backlog_and_the_oldest_pending_age() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = InMemoryTaskQueue()
    clock = MutableClock()
    await commands.insert(command("run-old", START))
    await commands.insert(command("run-new", START + timedelta(seconds=30)))
    pending = dispatcher(commands, queue, clock)
    clock.advance(90)

    health = await pending.health()

    assert health.running is False
    assert health.backlog.pending == 2
    assert health.backlog.ready == 2
    assert health.backlog.dispatched == 0
    assert health.backlog.oldest_pending_age_seconds == 90


@pytest.mark.asyncio
async def test_observations_expose_backlog_age_and_failures() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = FailingQueue()
    clock = MutableClock()
    queue.available = False
    metrics = ReliabilityMetrics()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock, metrics=metrics)

    await pending.run_once()

    failures = metrics.count(
        "harness_queue_dispatch_failures_total", labels={"operation": "enqueue"}
    )
    assert failures == 1
    assert metrics.count("harness_dispatch_commands", labels={"state": "pending"}) == 1
    assert metrics.count("harness_dispatch_pending_age_seconds") == 0

    queue.available = True
    clock.advance(60)
    await pending.run_once()

    assert metrics.count("harness_queue_dispatch_total") == 1
    assert metrics.count("harness_dispatch_commands", labels={"state": "dispatched"}) == 1


@pytest.mark.asyncio
async def test_start_and_stop_are_idempotent_and_observed() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = InMemoryTaskQueue()
    clock = MutableClock()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock)

    await pending.start()
    await pending.start()
    for _ in range(50):
        if (await queue.stats())["ready"]:
            break
        await asyncio.sleep(0.01)
    assert (await pending.health()).running is True

    await pending.stop()
    await pending.stop()

    health = await pending.health()
    assert health.running is False
    assert (await queue.stats())["ready"] == 1


@pytest.mark.asyncio
async def test_the_worker_lifecycle_helper_delivers_then_stops_cleanly() -> None:
    """`running_dispatcher` is what the Worker entrypoint wraps consumption in."""

    commands = InMemoryRunExecutionCommandRepository()
    queue = InMemoryTaskQueue()
    clock = MutableClock()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock)

    async with running_dispatcher(pending, enabled=True) as running:
        assert running is pending
        for _ in range(50):
            if (await queue.stats())["ready"]:
                break
            await asyncio.sleep(0.01)
        assert (await pending.health()).running is True

    assert (await queue.stats())["ready"] == 1
    assert (await pending.health()).running is False


@pytest.mark.asyncio
async def test_a_disabled_dispatcher_delivers_nothing() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = InMemoryTaskQueue()
    clock = MutableClock()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock)

    async with running_dispatcher(pending, enabled=False) as running:
        assert running is None

    assert (await queue.stats())["ready"] == 0
    assert (await pending.health()).backlog.pending == 1


@pytest.mark.asyncio
async def test_a_missing_dispatcher_is_tolerated_by_the_lifecycle_helper() -> None:
    async with running_dispatcher(None, enabled=True) as running:
        assert running is None


def test_safe_error_strips_credentials_from_transport_errors() -> None:
    redis_error = ConnectionError(
        "Error connecting to redis://harness:redis-secret@127.0.0.1:6379/0"
    )

    described = safe_error(redis_error)

    assert "redis-secret" not in described
    assert "ConnectionError" in described
    assert safe_error(ValueError(""))


def test_safe_error_truncates_a_long_diagnostic() -> None:
    described = safe_error(RuntimeError("x" * 5_000))

    assert len(described) == 200
    assert described.endswith("…")
    assert described.startswith("RuntimeError: ")


def test_a_dispatcher_refuses_an_incoherent_configuration() -> None:
    commands = InMemoryRunExecutionCommandRepository()
    queue = InMemoryTaskQueue()
    clock = MutableClock()

    with pytest.raises(ValueError):
        ExecutionCommandDispatcher(
            commands,
            queue,
            clock=clock,
            owner="dispatcher-a",
            lease_seconds=0,
        )
    with pytest.raises(ValueError):
        ExecutionCommandDispatcher(
            commands,
            queue,
            clock=clock,
            owner="dispatcher-a",
            retry_base_seconds=10,
            retry_max_seconds=1,
        )
