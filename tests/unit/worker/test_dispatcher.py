"""Durable dispatch behaviour: leasing, backoff, reclaim and observation."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import (
    InMemoryRunExecutionCommandRepository,
    InMemoryTaskQueue,
)
from harness.core.ports import (
    ExecutionCommandStatus,
    RunExecutionCommand,
    RunTask,
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


class HangingQueue(InMemoryTaskQueue):
    """A queue that accepts the connection and then never answers.

    This is the shape a stalled Redis actually takes: the socket is open, the
    write goes out, and nothing ever comes back. It reproduces what an
    immediately-raised ConnectionError cannot.
    """

    def __init__(self, *, absorb: bool = False) -> None:
        super().__init__()
        self.hang = asyncio.Event()
        self.received = 0
        self.published: list[RunTask] = []
        self._absorb = absorb

    async def enqueue(self, task: RunTask) -> None:
        if self.hang.is_set():
            self.received += 1
            if self._absorb:
                # The server side took the command before the client gave up.
                self.published.append(task)
            await asyncio.Event().wait()
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
    enqueue_timeout_seconds: float = 5,
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
        enqueue_timeout_seconds=enqueue_timeout_seconds,
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


@pytest.mark.asyncio
async def test_a_hung_publish_times_out_and_keeps_the_obligation() -> None:
    """Fix 1: Redis accepts the connection, then stops answering.

    The pass has to end on its own, the obligation has to survive as pending,
    and it has to be retried later - an unresponsive server must never look
    like a completed hand-off, and the caller must never wait forever.
    """

    commands = InMemoryRunExecutionCommandRepository()
    queue = HangingQueue()
    metrics = ReliabilityMetrics()
    clock = MutableClock()
    queue.hang.set()
    await commands.insert(command("run-1", START))
    pending = dispatcher(
        commands, queue, clock, metrics=metrics, enqueue_timeout_seconds=0.05
    )

    started = time.monotonic()
    cycle = await asyncio.wait_for(pending.run_once(), timeout=2)
    elapsed = time.monotonic() - started

    assert elapsed < 1, "a stalled publish must be bounded, not waited out"
    assert (cycle.claimed, cycle.dispatched, cycle.rescheduled) == (1, 0, 1)
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.PENDING
    assert stored.lease_owner is None
    assert stored.failures == 1
    assert stored.last_error is not None
    assert stored.last_error.startswith("unconfirmed:")
    assert "TimeoutError" in stored.last_error
    assert (
        metrics.count(
            "harness_queue_dispatch_failures_total",
            labels={"operation": "enqueue_timeout"},
        )
        == 1
    )

    # Recovery is a real publish, exactly as the at-least-once contract says.
    queue.hang.clear()
    clock.advance(2)
    recovered = await pending.run_once()
    assert (recovered.claimed, recovered.dispatched) == (1, 1)
    assert (await queue.dequeue()).run_id == "run-1"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_a_publish_that_may_have_landed_is_redelivered_not_dropped() -> None:
    """Fix 1: the outcome of a timed-out publish is unknown, so treat it that way.

    The server may already hold the command when the client gives up. The
    obligation must stay pending so it is delivered again; at-least-once plus
    the Worker's idempotency is what keeps that duplicate harmless.
    """

    commands = InMemoryRunExecutionCommandRepository()
    queue = HangingQueue(absorb=True)
    clock = MutableClock()
    queue.hang.set()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock, enqueue_timeout_seconds=0.05)

    await asyncio.wait_for(pending.run_once(), timeout=2)

    # The "server" kept it...
    assert [task.run_id for task in queue.published] == ["run-1"]
    # ...but the platform does not claim the hand-off happened.
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.PENDING
    assert stored.dispatched_at is None

    # The retry produces a second delivery attempt for the same single Run.
    queue.hang.clear()
    clock.advance(2)
    retried = await pending.run_once()
    assert retried.dispatched == 1
    assert queue.received == 1
    assert (await commands.get("tenant-a", "run-1")).status is (  # type: ignore[union-attr]
        ExecutionCommandStatus.DISPATCHED
    )


@pytest.mark.asyncio
async def test_cancelling_a_stuck_dispatch_pass_propagates_and_keeps_the_lease() -> None:
    """Fix 1: an outer cancel must not be swallowed by the publish bound."""

    commands = InMemoryRunExecutionCommandRepository()
    queue = HangingQueue()
    clock = MutableClock()
    queue.hang.set()
    await commands.insert(command("run-1", START))
    pending = dispatcher(
        commands, queue, clock, enqueue_timeout_seconds=30, lease_seconds=30
    )

    task = asyncio.create_task(pending.run_once())
    for _ in range(100):
        if queue.received:
            break
        await asyncio.sleep(0.01)
    assert queue.received == 1, "the publish should have been attempted by now"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The pass died mid-publish, so the claim is left to its lease instead of
    # being silently completed.
    stored = await commands.get("tenant-a", "run-1")
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.PENDING


@pytest.mark.asyncio
async def test_the_pending_age_gauge_returns_to_zero_after_the_backlog_clears() -> None:
    """Fix 5: backlog -> recovered delivery -> empty backlog reports zero."""

    commands = InMemoryRunExecutionCommandRepository()
    queue = FailingQueue()
    metrics = ReliabilityMetrics()
    clock = MutableClock()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock, metrics=metrics)

    # Backlog: the publish fails, so the obligation stays and ages visibly.
    queue.available = False
    clock.advance(120)
    await pending.run_once()

    assert metrics.count("harness_dispatch_commands", labels={"state": "pending"}) == 1
    assert metrics.count("harness_dispatch_pending_age_seconds") == 120

    # Recovery: the retry succeeds and the backlog is drained.
    queue.available = True
    clock.advance(2)
    await pending.run_once()

    assert metrics.count("harness_dispatch_commands", labels={"state": "pending"}) == 0
    assert metrics.count("harness_dispatch_commands", labels={"state": "dispatched"}) == 1
    # The gauge reports the recovered state instead of the age of a backlog
    # that no longer exists.
    assert metrics.count("harness_dispatch_pending_age_seconds") == 0

    # And it stays at zero on a later idle pass.
    clock.advance(600)
    await pending.run_once()
    assert metrics.count("harness_dispatch_pending_age_seconds") == 0


@pytest.mark.asyncio
async def test_a_stuck_publish_cannot_hold_shutdown_open() -> None:
    """Fix 4: exit stays bounded even when a publish is stuck."""

    commands = InMemoryRunExecutionCommandRepository()
    queue = HangingQueue()
    clock = MutableClock()
    queue.hang.set()
    await commands.insert(command("run-1", START))
    pending = dispatcher(commands, queue, clock, enqueue_timeout_seconds=30)

    started = time.monotonic()
    async with running_dispatcher(pending, enabled=True, grace_seconds=0.05):
        for _ in range(100):
            if (await pending.health()).last_claimed:
                break
            await asyncio.sleep(0.01)
    elapsed = time.monotonic() - started

    assert elapsed < 2, "shutdown must not wait out a stuck publish"
    assert (await pending.health()).running is False


@pytest.mark.asyncio
async def test_a_command_removed_while_publishing_is_not_reported_as_a_lost_lease() -> None:
    """Fix 2: a deleted obligation is a completed one, not a lost lease."""

    commands = InMemoryRunExecutionCommandRepository()
    clock = MutableClock()

    class RemovingQueue(InMemoryTaskQueue):
        async def enqueue(self, task: RunTask) -> None:
            await super().enqueue(task)
            # The Run and its obligation are deleted while the pass is open.
            await commands.remove(execution_command_id(task.run_id))

    removing = RemovingQueue()
    await commands.insert(command("run-1", START))

    cycle = await dispatcher(commands, removing, clock).run_once()

    assert (cycle.claimed, cycle.dispatched, cycle.lost_lease) == (1, 0, 0)
    assert await commands.get("tenant-a", "run-1") is None
