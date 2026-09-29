"""The accept -> persist -> dispatch -> execute loop on real PostgreSQL and Redis.

Every durable assertion here is made against real rows in PostgreSQL and real
entries in Redis; nothing is verified through a mock. The one piece of scenery
is the Session lookup, because a Session is not what this loop is about.
"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from harness.adapters.memory import (
    InMemoryArtifactStore,
    InMemorySessionRepository,
    InMemoryWorkspaceSnapshotRepository,
)
from harness.application.events import EventService
from harness.application.runs import RunCreation, RunService
from harness.application.workspaces import WorkspaceService
from harness.core.errors import ConflictError
from harness.core.models import RunStatus, Session
from harness.core.ports import ExecutionCommandStatus, RunTask
from harness.reliability.metrics import ReliabilityMetrics
from harness.runtime.fake import FakeRuntime
from harness.sandbox.local import LocalSandboxProvider
from harness.storage.database import SessionFactory
from harness.storage.execution_commands import (
    PostgresRunAcceptance,
    PostgresRunExecutionCommandRepository,
)
from harness.storage.models import EventRow, RunExecutionCommandRow, RunRow
from harness.storage.redis import AsyncRedisClient, RedisEventBus, RedisTaskQueue
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository
from harness.worker.dispatcher import ExecutionCommandDispatcher
from harness.worker.main import worker_loop
from harness.worker.orchestrator import RunOrchestrator

DatabaseFixture = tuple[AsyncEngine, SessionFactory]

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
REDIS_DATABASE = 12


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class ToggleableQueue:
    """A real Redis queue that can be taken offline like the real thing."""

    def __init__(self, queue: RedisTaskQueue) -> None:
        self._queue = queue
        self.available = True
        self.failures = 0

    async def enqueue(self, task: RunTask) -> None:
        if not self.available:
            self.failures += 1
            raise ConnectionError(
                "Cannot connect to redis://harness:redis-secret@127.0.0.1:6379/0"
            )
        await self._queue.enqueue(task)

    async def dequeue(self) -> RunTask | None:
        return await self._queue.dequeue()

    async def acknowledge(self, task: RunTask) -> None:
        await self._queue.acknowledge(task)

    async def retry(self, task: RunTask) -> None:
        await self._queue.retry(task)

    async def extend_lease(self, task: RunTask) -> None:
        await self._queue.extend_lease(task)

    async def stats(self) -> dict[str, int]:
        return await self._queue.stats()


def id_generator() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return generate


class Arrangement:
    """Real PostgreSQL repositories plus a real Redis queue."""

    def __init__(
        self,
        sessions_factory: SessionFactory,
        client: AsyncRedisClient,
        clock: MutableClock | None = None,
    ) -> None:
        self.clock = clock or MutableClock()
        self.ids = id_generator()
        self.runs = PostgresRunRepository(sessions_factory)
        self.events = PostgresEventRepository(sessions_factory)
        self.acceptance = PostgresRunAcceptance(sessions_factory)
        self.commands = PostgresRunExecutionCommandRepository(sessions_factory)
        self.redis = RedisTaskQueue(
            client,
            namespace="dispatch-test",
            visibility_timeout_seconds=60,
            retry_delay_seconds=1,
        )
        self.queue = ToggleableQueue(self.redis)
        self.sessions = InMemorySessionRepository()
        self.metrics = ReliabilityMetrics()
        self.event_service = EventService(
            self.events,
            RedisEventBus(client, namespace="dispatch-test"),
            clock=self.clock,
            id_generator=self.ids,
        )

    def service(self, *, acceptance: bool = True) -> RunService:
        return RunService(
            self.sessions,
            self.runs,
            self.queue,
            self.event_service,
            clock=self.clock,
            id_generator=self.ids,
            metrics=self.metrics,
            acceptance=self.acceptance if acceptance else None,
        )

    def dispatcher(
        self,
        *,
        owner: str = "dispatcher-a",
        lease_seconds: float = 30,
    ) -> ExecutionCommandDispatcher:
        return ExecutionCommandDispatcher(
            self.commands,
            self.queue,
            clock=self.clock,
            owner=owner,
            lease_seconds=lease_seconds,
            batch_size=10,
            interval_seconds=0.5,
            retry_base_seconds=2,
            retry_max_seconds=8,
            metrics=self.metrics,
        )

    async def seed_session(self, session_id: str = "session-1") -> Session:
        session = Session(
            session_id=session_id,
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
        await self.sessions.add(session)
        return session


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[AsyncRedisClient]:
    client: Redis = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        os.getenv("HARNESS_TEST_REDIS_URL", f"redis://127.0.0.1:6379/{REDIS_DATABASE}"),
        decode_responses=True,
    )
    await client.flushdb()  # pyright: ignore[reportUnknownMemberType]
    try:
        yield cast(AsyncRedisClient, client)
    finally:
        await client.aclose()


@pytest_asyncio.fixture
async def arranged(
    database: DatabaseFixture, redis_client: AsyncRedisClient
) -> Arrangement:
    _, sessions_factory = database
    return Arrangement(sessions_factory, redis_client)


async def count_rows(
    engine: AsyncEngine, table: type[object], **filters: object
) -> int:
    statement = select(func.count()).select_from(cast(FromClause, table))
    for column, value in filters.items():
        statement = statement.where(cast(ColumnElement[bool], getattr(table, column) == value))
    async with engine.connect() as connection:
        return int(await connection.scalar(statement) or 0)


@pytest.mark.asyncio
async def test_acceptance_commits_run_event_and_obligation_in_one_transaction(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    engine, _ = database
    await arranged.seed_session()

    creation = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    assert creation.created is True
    assert await count_rows(engine, RunRow, run_id=creation.run.run_id) == 1
    assert await count_rows(engine, EventRow, run_id=creation.run.run_id) == 1
    assert (
        await count_rows(engine, RunExecutionCommandRow, run_id=creation.run.run_id) == 1
    )
    # Acceptance commits the intent; it does not publish anything itself.
    assert (await arranged.redis.stats())["ready"] == 0


@pytest.mark.asyncio
async def test_a_conflicting_event_leaves_no_partial_run_obligation_or_event(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 2: a failing transaction must leave nothing behind."""

    engine, sessions_factory = database
    await arranged.seed_session()
    # Occupy the sequence the acceptance transaction will try to write, so its
    # event insert fails after the Run insert inside the same transaction.
    async with sessions_factory() as session:
        session.add(
            EventRow(
                event_id="squatter",
                tenant_id="tenant-a",
                run_id="run-1",
                sequence=1,
                timestamp=NOW,
                payload={
                    "event_id": "squatter",
                    "run_id": "run-1",
                    "session_id": "session-1",
                    "tenant_id": "tenant-a",
                    "sequence": 1,
                    "type": "run.queued",
                    "timestamp": NOW.isoformat(),
                    "payload": {},
                },
            )
        )
        await session.commit()

    with pytest.raises(ConflictError):
        await arranged.service().create_with_result(
            "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
        )

    assert await count_rows(engine, RunRow, run_id="run-1") == 0
    assert await count_rows(engine, RunExecutionCommandRow, run_id="run-1") == 0
    # Only the squatter event remains; the rejected Run wrote nothing.
    assert await count_rows(engine, EventRow, run_id="run-1") == 1
    # The rejection is recoverable, not poisoned: the same key still works once
    # the conflict is gone.
    async with sessions_factory() as session:
        await session.execute(delete(EventRow).where(EventRow.run_id == "run-1"))
        await session.commit()
    recovered = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    assert recovered.created is True
    assert await count_rows(engine, RunRow, run_id=recovered.run.run_id) == 1


@pytest.mark.asyncio
async def test_a_run_accepted_before_the_crash_is_delivered_by_a_restarted_dispatcher(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 3: the process dies after the commit and before any publish."""

    engine, _ = database
    await arranged.seed_session()
    creation = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    # Nothing was published, and the process is gone. A fresh Dispatcher with a
    # fresh identity is the restart.
    assert (await arranged.redis.stats())["ready"] == 0
    restarted = arranged.dispatcher(owner="dispatcher-restarted")
    cycle = await restarted.run_once()

    assert (cycle.claimed, cycle.dispatched) == (1, 1)
    task = await arranged.redis.dequeue()
    assert task is not None
    assert (task.tenant_id, task.run_id) == ("tenant-a", creation.run.run_id)
    assert (
        await count_rows(engine, RunExecutionCommandRow, run_id=creation.run.run_id) == 1
    )
    stored = await arranged.commands.get("tenant-a", creation.run.run_id)
    assert stored is not None and stored.status is ExecutionCommandStatus.DISPATCHED


@pytest.mark.asyncio
async def test_an_unreachable_redis_still_accepts_and_recovers(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 4: Redis down means slower delivery, never a lost Run."""

    _, _ = database
    await arranged.seed_session()
    arranged.queue.available = False

    creation = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    assert creation.created is True

    blocked = await arranged.dispatcher().run_once()
    assert (blocked.claimed, blocked.dispatched, blocked.rescheduled) == (1, 0, 1)
    stored = await arranged.commands.get("tenant-a", creation.run.run_id)
    assert stored is not None
    assert stored.status is ExecutionCommandStatus.PENDING
    assert stored.failures == 1
    assert stored.available_at == NOW + timedelta(seconds=2)
    assert stored.last_error is not None
    # The diagnostic is durable and free of the credential in the Redis URL.
    assert "redis-secret" not in stored.last_error
    assert "ConnectionError" in stored.last_error
    assert (await arranged.redis.stats())["ready"] == 0

    # Recovery: the same obligation is delivered once Redis returns.
    arranged.queue.available = True
    arranged.clock.advance(2)
    recovered = await arranged.dispatcher().run_once()

    assert (recovered.claimed, recovered.dispatched) == (1, 1)
    task = await arranged.redis.dequeue()
    assert task is not None and task.run_id == creation.run.run_id


@pytest.mark.asyncio
async def test_a_publish_that_cannot_be_recorded_is_redelivered_without_double_work(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 5: publish succeeded, the acknowledgement did not."""

    engine, _ = database
    await arranged.seed_session()
    creation = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    published = await arranged.dispatcher(owner="dispatcher-a", lease_seconds=30).run_once()
    assert (published.claimed, published.dispatched) == (1, 1)
    # The Dispatcher died between the publish and closing its claim: simulate
    # that by reopening the obligation it had closed, lease still recorded.
    async with engine.begin() as connection:
        await connection.execute(
            update(RunExecutionCommandRow)
            .where(RunExecutionCommandRow.run_id == creation.run.run_id)
            .values(status="pending", lease_owner="dispatcher-a")
        )

    arranged.clock.advance(31)
    redelivered = await arranged.dispatcher(owner="dispatcher-b").run_once()

    assert (redelivered.claimed, redelivered.dispatched) == (1, 1)
    # Redis collapses the repeat while the task is still pending, so the queue
    # holds exactly one deliverable task for this Run.
    assert (await arranged.redis.stats())["ready"] == 1
    task = await arranged.redis.dequeue()
    assert task is not None
    assert await arranged.redis.dequeue() is None
    stored = await arranged.commands.get("tenant-a", creation.run.run_id)
    assert stored is not None and stored.status is ExecutionCommandStatus.DISPATCHED


@pytest.mark.asyncio
async def test_two_dispatchers_claim_disjoint_work_and_take_over_expired_leases(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 6."""

    _, _ = database
    await arranged.seed_session()
    service = arranged.service()
    claimed_runs: list[str] = []
    for index in range(6):
        created = await service.create_with_result(
            "tenant-a", "session-1", f"idem-{index}", input={"prompt": f"hello {index}"}
        )
        claimed_runs.append(created.run.run_id)
    first = arranged.dispatcher(owner="dispatcher-a")
    second = arranged.dispatcher(owner="dispatcher-b")

    left, right = await asyncio.gather(first.run_once(), second.run_once())

    assert left.claimed + right.claimed == 6
    assert left.dispatched + right.dispatched == 6
    for command in claimed_runs:
        stored = await arranged.commands.get("tenant-a", command)
        assert stored is not None
        assert stored.status is ExecutionCommandStatus.DISPATCHED
        assert stored.lease_owner is None
    assert (await arranged.redis.stats())["ready"] == 6

    # A claimer that stalls past its lease is taken over by its peer, and the
    # abandoned obligation is still delivered exactly once more.
    pending = await service.create_with_result(
        "tenant-a", "session-1", "idem-stalled", input={"prompt": "stall me"}
    )
    abandoned = await arranged.commands.claim_pending(
        owner="dispatcher-stalled", lease_seconds=15, limit=1, now=NOW
    )
    assert len(abandoned) == 1
    assert abandoned[0].run_id == pending.run.run_id
    arranged.clock.advance(16)
    takeover = await arranged.dispatcher(owner="dispatcher-c").run_once()

    assert (takeover.claimed, takeover.dispatched) == (1, 1)
    stored = await arranged.commands.get(abandoned[0].tenant_id, abandoned[0].run_id)
    assert stored is not None
    assert stored.attempts == 2
    assert stored.lease_owner is None


@pytest.mark.asyncio
async def test_concurrent_same_idempotency_key_yields_one_run_and_one_obligation(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 7: PostgreSQL arbitrates, so replicas cannot both win."""

    engine, _ = database
    await arranged.seed_session()
    service = arranged.service()

    results = await asyncio.gather(
        *(
            service.create_with_result(
                "tenant-a", "session-1", "same-key", input={"prompt": "hello"}
            )
            for _ in range(6)
        ),
        return_exceptions=True,
    )

    creations = [cast(RunCreation, item) for item in results]
    assert not [item for item in results if isinstance(item, BaseException)]
    run_ids = {item.run.run_id for item in creations}
    assert len(run_ids) == 1
    assert await count_rows(engine, RunRow, session_id="session-1") == 1
    assert await count_rows(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 1
    assert sum(1 for item in creations if item.created) == 1


@pytest.mark.asyncio
async def test_a_retry_returns_a_stable_run_and_recovers_a_missing_obligation(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 8: a Run accepted without an obligation is repaired on retry."""

    engine, _ = database
    await arranged.seed_session()
    legacy = arranged.service(acceptance=False)
    original = await legacy.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    # The legacy path published a task and recorded no durable intent, which is
    # the hole this round closes. Losing that publish strands the Run.
    assert (await arranged.redis.stats())["ready"] == 1
    lost = await arranged.redis.dequeue()
    assert lost is not None
    await arranged.redis.acknowledge(lost)
    assert (await arranged.redis.stats())["ready"] == 0
    assert (
        await count_rows(engine, RunExecutionCommandRow, run_id=original.run.run_id) == 0
    )

    retried = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    assert retried.created is False
    assert retried.run.run_id == original.run.run_id
    assert (
        await count_rows(engine, RunExecutionCommandRow, run_id=original.run.run_id) == 1
    )
    again = await arranged.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    assert again.run.run_id == original.run.run_id
    assert (
        await count_rows(engine, RunExecutionCommandRow, run_id=original.run.run_id) == 1
    )
    arrived = await arranged.dispatcher().run_once()
    assert (arrived.claimed, arrived.dispatched) == (1, 1)
    task = await arranged.redis.dequeue()
    assert task is not None and task.run_id == original.run.run_id


@pytest.mark.asyncio
async def test_an_inline_child_run_never_becomes_another_workers_task(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 9: dispatch_to_queue=False stays invisible to the Dispatcher."""

    engine, _ = database
    await arranged.seed_session()

    creation = await arranged.service().create_with_result(
        "tenant-a",
        "session-1",
        "child-1",
        input={"prompt": "child"},
        dispatch_to_queue=False,
    )

    assert await count_rows(engine, RunRow, run_id=creation.run.run_id) == 1
    assert await count_rows(engine, EventRow, run_id=creation.run.run_id) == 1
    assert (
        await count_rows(engine, RunExecutionCommandRow, run_id=creation.run.run_id) == 0
    )
    backlog = await arranged.commands.backlog(now=arranged.clock())
    assert backlog.pending == 0
    assert (await arranged.dispatcher().run_once()).claimed == 0
    assert (await arranged.redis.stats())["ready"] == 0


@pytest.mark.asyncio
async def test_a_submitted_run_reaches_a_worker_and_finishes(
    arranged: Arrangement, database: DatabaseFixture, tmp_path: Path
) -> None:
    """Scenario 1: submit -> durable accept -> dispatch -> Worker executes."""

    _, _ = database
    session = await arranged.seed_session()
    runtime = FakeRuntime()
    orchestrator = RunOrchestrator(
        sessions=arranged.sessions,
        runs=arranged.runs,
        events=arranged.event_service,
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: datetime.now(UTC),
        workspaces=WorkspaceService(
            InMemoryArtifactStore(),
            snapshots=InMemoryWorkspaceSnapshotRepository(),
        ),
    )
    creation = await arranged.service().create_with_result(
        "tenant-a", session.session_id, "idem-1", input={"prompt": "hello harness"}
    )
    delivered = await arranged.dispatcher().run_once()
    assert delivered.dispatched == 1

    stop = asyncio.Event()
    worker = asyncio.create_task(
        worker_loop(
            arranged.redis,
            orchestrator,
            stop=stop,
            poll_interval=0.01,
            metrics=arranged.metrics,
        )
    )
    try:
        for _ in range(200):
            current = await arranged.runs.get("tenant-a", creation.run.run_id)
            if current.status.is_terminal:
                break
            await asyncio.sleep(0.02)
        stop.set()
        await asyncio.wait_for(worker, timeout=10)
    finally:
        if not worker.done():
            worker.cancel()

    finished = await arranged.runs.get("tenant-a", creation.run.run_id)
    assert finished.status is RunStatus.SUCCEEDED
    assert runtime.execution_count == 1
    assert (await arranged.redis.stats())["ready"] == 0
    assert (await arranged.redis.stats())["processing"] == 0
    events = await arranged.events.list_after("tenant-a", creation.run.run_id, 0)
    assert events[0].type == "run.queued"
    assert events[-1].type == "run.succeeded"


@pytest.mark.asyncio
async def test_a_late_duplicate_task_does_not_reexecute_a_terminal_run(
    arranged: Arrangement, database: DatabaseFixture, tmp_path: Path
) -> None:
    """Scenario 10: a redelivered message must not repeat effective work."""

    _, _ = database
    session = await arranged.seed_session()
    runtime = FakeRuntime()
    orchestrator = RunOrchestrator(
        sessions=arranged.sessions,
        runs=arranged.runs,
        events=arranged.event_service,
        runtime=runtime,
        sandbox=LocalSandboxProvider(root=tmp_path),
        clock=lambda: datetime.now(UTC),
        workspaces=WorkspaceService(
            InMemoryArtifactStore(),
            snapshots=InMemoryWorkspaceSnapshotRepository(),
        ),
    )
    creation = await arranged.service().create_with_result(
        "tenant-a", session.session_id, "idem-1", input={"prompt": "hello harness"}
    )
    await arranged.dispatcher().run_once()
    first = await arranged.redis.dequeue()
    assert first is not None
    await orchestrator.execute(first.tenant_id, first.run_id)
    await arranged.redis.acknowledge(first)
    assert (await arranged.runs.get("tenant-a", first.run_id)).status is RunStatus.SUCCEEDED
    assert runtime.execution_count == 1

    # A late duplicate of the same Run arrives after the terminal state.
    await arranged.redis.enqueue(RunTask(tenant_id="tenant-a", run_id=first.run_id))
    duplicate = await arranged.redis.dequeue()
    assert duplicate is not None
    late = await orchestrator.execute(duplicate.tenant_id, duplicate.run_id)
    await arranged.redis.acknowledge(duplicate)

    assert late.status is RunStatus.SUCCEEDED
    assert runtime.execution_count == 1
    terminal_events = [
        event.type
        for event in await arranged.events.list_after("tenant-a", creation.run.run_id, 0)
    ].count("run.succeeded")
    assert terminal_events == 1


@pytest.mark.asyncio
async def test_dispatching_records_delivery_without_executing_or_owning_the_run(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Scenario 10: delivery is not execution.

    A Run already owned by a Worker must be handed to the queue, not started or
    moved by the Dispatcher. Ownership stays with the Worker: the orchestrator's
    reclaim path bumps the fencing token and the stale owner yields, which
    `test_recovered_provisioning_run_is_reclaimed_and_completed` already pins.
    """

    _, _ = database
    session = await arranged.seed_session()
    creation = await arranged.service().create_with_result(
        "tenant-a", session.session_id, "idem-1", input={"prompt": "hello harness"}
    )
    owned = creation.run.model_copy(
        update={"status": RunStatus.PROVISIONING, "fencing_token": 1}
    )
    assert await arranged.runs.compare_and_set(RunStatus.QUEUED, owned) is True

    delivered = await arranged.dispatcher().run_once()

    assert (delivered.claimed, delivered.dispatched) == (1, 1)
    unchanged = await arranged.runs.get("tenant-a", creation.run.run_id)
    assert unchanged == owned
