"""Dispatch obligations must not outlive the Run they point at.

Reproduces the reported defect on real PostgreSQL: a deleted Session/Run used
to leave its pending obligation behind, so the Dispatcher handed the queue a
task whose target no longer existed and the Worker retried it forever.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import FromClause

from harness.adapters.memory import InMemorySessionRepository
from harness.application.events import EventService
from harness.application.runs import RunService
from harness.core.models import RunDispatchMode, RunStatus, Session
from harness.core.ports import ExecutionCommandStatus, RunTask
from harness.lifecycle.models import (
    DataLifecycleJob,
    LifecycleAdapterResult,
    LifecycleAdapterStatus,
    LifecycleJobKind,
    LifecycleJobStatus,
    LifecycleScope,
    LifecycleScopeKind,
)
from harness.storage.database import SessionFactory
from harness.storage.execution_commands import (
    PostgresRunAcceptance,
    PostgresRunExecutionCommandRepository,
)
from harness.storage.lifecycle_adapters import PostgresLifecycleAdapter
from harness.storage.models import (
    EventRow,
    RunExecutionCommandRow,
    RunRow,
    SessionRow,
)
from harness.storage.redis import AsyncRedisClient, RedisEventBus, RedisTaskQueue
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository
from harness.worker.dispatcher import ExecutionCommandDispatcher

DatabaseFixture = tuple[AsyncEngine, SessionFactory]

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
# A dedicated database index and a private key namespace: this suite deletes
# only what it created and never flushes a database someone else may be using.
REDIS_DATABASE = 13
NAMESPACE = "run-command-lifecycle-test"


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def id_generator() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return generate


@pytest_asyncio.fixture
async def redis_client() -> AsyncGenerator[AsyncRedisClient, None]:
    """A scoped Redis client that clears only keys under its own namespace.

    ``flushdb`` would also erase whatever else shares the index, so cleanup is
    restricted to this suite's key prefix.
    """

    client: Redis = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        os.getenv("HARNESS_TEST_REDIS_URL", f"redis://127.0.0.1:6379/{REDIS_DATABASE}"),
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    await _clear_namespace(client)
    try:
        yield cast(AsyncRedisClient, client)
    finally:
        await _clear_namespace(client)
        await client.aclose()


async def _clear_namespace(client: Redis) -> None:
    keys = await client.keys(f"{NAMESPACE}:*")  # pyright: ignore[reportUnknownMemberType]
    if keys:
        await client.delete(*keys)  # pyright: ignore[reportUnknownMemberType]


class Arrangement:
    def __init__(self, sessions: SessionFactory, client: AsyncRedisClient) -> None:
        self.sessions = sessions
        self.clock = MutableClock()
        self.ids = id_generator()
        self.runs = PostgresRunRepository(sessions)
        self.events = PostgresEventRepository(sessions)
        self.acceptance = PostgresRunAcceptance(sessions)
        self.commands = PostgresRunExecutionCommandRepository(sessions)
        self.queue = RedisTaskQueue(
            client,
            namespace=NAMESPACE,
            visibility_timeout_seconds=60,
            retry_delay_seconds=1,
        )
        self.session_lookup = InMemorySessionRepository()
        self.service = RunService(
            self.session_lookup,
            self.runs,
            self.queue,
            EventService(
                self.events,
                RedisEventBus(client, namespace=NAMESPACE),
                clock=self.clock,
                id_generator=self.ids,
            ),
            clock=self.clock,
            id_generator=self.ids,
            acceptance=self.acceptance,
        )

    def dispatcher(self) -> ExecutionCommandDispatcher:
        return ExecutionCommandDispatcher(
            self.commands,
            self.queue,
            clock=self.clock,
            owner=f"dispatcher-{self.ids('o')}",
            lease_seconds=30,
            interval_seconds=0.5,
            retry_base_seconds=2,
            retry_max_seconds=8,
        )

    async def seed_session(self, tenant_id: str, session_id: str) -> Session:
        session = Session(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="1.0.0",
            created_at=NOW,
        )
        await self.session_lookup.add(session)
        async with self.sessions() as db:
            db.add(
                SessionRow(
                    tenant_id=tenant_id,
                    session_id=session_id,
                    user_id=session.user_id,
                    payload=session.model_dump(mode="json"),
                )
            )
            await db.commit()
        return session

    async def accept(
        self,
        tenant_id: str,
        session_id: str,
        key: str,
        *,
        dispatch_to_queue: bool = True,
    ):
        return await self.service.create_with_result(
            tenant_id,
            session_id,
            key,
            input={"prompt": "hello"},
            dispatch_to_queue=dispatch_to_queue,
        )


@pytest_asyncio.fixture
async def arranged(
    database: DatabaseFixture, redis_client: AsyncRedisClient
) -> Arrangement:
    _, sessions = database
    return Arrangement(sessions, redis_client)


def delete_job(tenant_id: str, *, kind: LifecycleJobKind = LifecycleJobKind.DELETE):
    return DataLifecycleJob(
        tenantId=tenant_id,
        jobId=f"job:{tenant_id}",
        kind=kind,
        scope=LifecycleScope(kind=LifecycleScopeKind.TENANT, subjectId=tenant_id),
        requestedBy="admin",
        idempotencyKey=f"key:{tenant_id}",
        status=LifecycleJobStatus.QUEUED,
        adapters=(
            LifecycleAdapterResult(
                adapter="postgresql",
                status=LifecycleAdapterStatus.PENDING,
                attempts=0,
                updatedAt=NOW,
            ),
        ),
        retentionCutoffs=(
            {
                "sessions": NOW + timedelta(days=30),
                "artifacts": NOW + timedelta(days=30),
                "traces": NOW + timedelta(days=30),
                "evals": NOW + timedelta(days=30),
            }
            if kind is LifecycleJobKind.RETENTION
            else {}
        ),
        createdAt=NOW,
        updatedAt=NOW,
    )


async def count(
    engine: AsyncEngine, table: type[object], **filters: object
) -> int:
    statement = select(func.count()).select_from(cast(FromClause, table))
    for column, value in filters.items():
        statement = statement.where(
            cast(ColumnElement[bool], getattr(table, column) == value)
        )
    async with engine.connect() as connection:
        return int(await connection.scalar(statement) or 0)


@pytest.mark.asyncio
async def test_deleting_a_tenant_removes_its_dispatch_obligations(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    engine, _ = database
    await arranged.seed_session("tenant-a", "session-1")
    await arranged.seed_session("tenant-b", "session-2")
    pending = await arranged.accept("tenant-a", "session-1", "key-a")
    other_tenant = await arranged.accept("tenant-b", "session-2", "key-b")
    assert (
        await count(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 1
    )

    deleted = await PostgresLifecycleAdapter(arranged.sessions).delete(
        delete_job("tenant-a")
    )

    assert deleted > 0
    assert await count(engine, RunRow, tenant_id="tenant-a") == 0
    assert await count(engine, EventRow, tenant_id="tenant-a") == 0
    assert await count(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 0
    # Tenant isolation: nothing of the neighbouring tenant is touched.
    assert await count(engine, RunRow, tenant_id="tenant-b") == 1
    assert await count(engine, RunExecutionCommandRow, tenant_id="tenant-b") == 1
    assert (
        await arranged.commands.get("tenant-b", other_tenant.run.run_id) is not None
    )
    # And a deleted Run leaves nothing for the Dispatcher to hand out: the
    # only claimable obligation left belongs to the neighbouring tenant.
    cycle = await arranged.dispatcher().run_once()
    assert cycle.claimed == 1
    task = await arranged.queue.dequeue()
    assert task is not None
    assert task.run_id == other_tenant.run.run_id
    assert task.run_id != pending.run.run_id


@pytest.mark.asyncio
async def test_retention_cleanup_removes_dispatch_obligations_too(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    engine, _ = database
    await arranged.seed_session("tenant-a", "session-1")
    await arranged.accept("tenant-a", "session-1", "key-a")

    await PostgresLifecycleAdapter(arranged.sessions).delete(
        delete_job("tenant-a", kind=LifecycleJobKind.RETENTION)
    )

    assert await count(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 0
    assert (await arranged.dispatcher().run_once()).claimed == 0


@pytest.mark.asyncio
async def test_pending_dispatched_and_leased_obligations_are_all_removed(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """The three states a command can be in when a deletion arrives."""

    engine, _ = database
    await arranged.seed_session("tenant-a", "session-1")
    states: dict[str, str] = {}
    for key in ("pending", "dispatched", "leased"):
        creation = await arranged.service.create_with_result(
            "tenant-a", "session-1", key, input={"prompt": f"hello {key}"}
        )
        states[key] = creation.run.run_id
    # One obligation is in flight under a live lease: it was claimed and the
    # pass that claimed it died before publishing.
    in_flight = await arranged.commands.claim_pending(
        owner="dispatcher-in-flight", lease_seconds=300, limit=1, now=NOW
    )
    assert len(in_flight) == 1
    states["leased"] = in_flight[0].run_id
    # The other two are delivered normally.
    assert (await arranged.dispatcher().run_once()).dispatched == 2
    assert await count(engine, RunExecutionCommandRow, status="dispatched") == 2
    leased_row = await arranged.commands.get("tenant-a", states["leased"])
    assert leased_row is not None
    assert leased_row.status is ExecutionCommandStatus.PENDING
    assert leased_row.lease_owner == "dispatcher-in-flight"

    await PostgresLifecycleAdapter(arranged.sessions).delete(delete_job("tenant-a"))

    assert await count(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 0
    for run_id in states.values():
        assert await arranged.commands.get("tenant-a", run_id) is None


@pytest.mark.asyncio
async def test_a_deletion_that_lands_after_the_claim_is_not_reported_as_a_lost_lease(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """The race the report calls out: the obligation is deleted mid-publish."""

    engine, _ = database
    await arranged.seed_session("tenant-a", "session-1")
    creation = await arranged.accept("tenant-a", "session-1", "key-a")

    class DeletingQueue:
        def __init__(self, inner: RedisTaskQueue) -> None:
            self._inner = inner

        async def enqueue(self, task: RunTask) -> None:
            await self._inner.enqueue(task)
            # The tenant is deleted while this pass holds the lease.
            await PostgresLifecycleAdapter(arranged.sessions).delete(
                delete_job("tenant-a")
            )

        async def dequeue(self) -> RunTask | None:
            return await self._inner.dequeue()

        async def acknowledge(self, task: RunTask) -> None:
            await self._inner.acknowledge(task)

        async def retry(self, task: RunTask) -> None:
            await self._inner.retry(task)

        async def extend_lease(self, task: RunTask) -> None:
            await self._inner.extend_lease(task)

        async def stats(self) -> dict[str, int]:
            return await self._inner.stats()

    dispatcher = ExecutionCommandDispatcher(
        arranged.commands,
        DeletingQueue(arranged.queue),
        clock=arranged.clock,
        owner="dispatcher-racing",
        lease_seconds=30,
    )

    cycle = await dispatcher.run_once()

    # The publish reported success, and closing the claim found the obligation
    # deliberately removed - a completed removal, not a stolen lease.
    assert (cycle.claimed, cycle.dispatched, cycle.lost_lease) == (1, 0, 0)
    assert await count(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 0
    assert await arranged.commands.get("tenant-a", creation.run.run_id) is None
    # The one task that was already published is absorbed by the consumer: the
    # target is definitively gone, so it is retired rather than retried.
    task = await arranged.queue.dequeue()
    assert task is not None
    assert task.run_id == creation.run.run_id


@pytest.mark.asyncio
async def test_an_inline_run_is_never_given_an_obligation_by_a_retry(
    arranged: Arrangement, database: DatabaseFixture
) -> None:
    """Fix 3 on the real store: the recorded mode is what a retry reads."""

    engine, _ = database
    await arranged.seed_session("tenant-a", "session-1")
    child = await arranged.accept(
        "tenant-a", "session-1", "child-1", dispatch_to_queue=False
    )
    assert child.run.dispatch_mode is RunDispatchMode.INLINE

    retried = await arranged.service.create_with_result(
        "tenant-a", "session-1", "child-1", input={"prompt": "hello"}
    )

    assert retried.run.run_id == child.run.run_id
    assert (
        await count(engine, RunExecutionCommandRow, run_id=child.run.run_id) == 0
    )
    # The mode survived the round trip through the database payload.
    stored = await arranged.runs.get("tenant-a", child.run.run_id)
    assert stored.dispatch_mode is RunDispatchMode.INLINE
    assert (await arranged.dispatcher().run_once()).claimed == 0


@pytest.mark.asyncio
async def test_retry_cannot_restore_a_command_after_concurrent_deletion(
    arranged: Arrangement, database: DatabaseFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = database
    await arranged.seed_session("tenant-a", "session-1")
    await arranged.accept("tenant-a", "session-1", "key-a")
    repairing = asyncio.Event()
    deleted = asyncio.Event()
    ensure = arranged.acceptance.ensure_command

    async def paused_ensure(command):
        repairing.set()
        await deleted.wait()
        return await ensure(command)

    monkeypatch.setattr(arranged.acceptance, "ensure_command", paused_ensure)
    retry = asyncio.create_task(arranged.accept("tenant-a", "session-1", "key-a"))
    try:
        await asyncio.wait_for(repairing.wait(), timeout=3)
        await PostgresLifecycleAdapter(arranged.sessions).delete(delete_job("tenant-a"))
    finally:
        deleted.set()
        await asyncio.wait_for(retry, timeout=3)

    assert await count(engine, RunRow, tenant_id="tenant-a") == 0
    assert await count(engine, RunExecutionCommandRow, tenant_id="tenant-a") == 0
    assert (await arranged.dispatcher().run_once()).claimed == 0


@pytest.mark.asyncio
async def test_repair_rechecks_the_current_run_status(arranged: Arrangement) -> None:
    await arranged.seed_session("tenant-a", "session-1")
    creation = await arranged.accept("tenant-a", "session-1", "key-a")
    command = await arranged.commands.get("tenant-a", creation.run.run_id)
    assert command is not None
    async with arranged.sessions() as db:
        await db.execute(delete(RunExecutionCommandRow))
        await db.commit()
    updated = creation.run.model_copy(
        update={"status": RunStatus.CANCELLED, "fencing_token": 1}
    )
    assert await arranged.runs.compare_and_set(RunStatus.QUEUED, updated)
    assert await arranged.acceptance.ensure_command(command) is False
    assert await arranged.commands.get("tenant-a", creation.run.run_id) is None


@pytest.mark.asyncio
async def test_repair_waits_for_an_uncommitted_run_deletion(arranged: Arrangement) -> None:
    await arranged.seed_session("tenant-a", "session-1")
    creation = await arranged.accept("tenant-a", "session-1", "key-a")
    command = await arranged.commands.get("tenant-a", creation.run.run_id)
    assert command is not None
    async with arranged.sessions() as db:
        await db.execute(delete(RunExecutionCommandRow))
        await db.commit()
    async with arranged.sessions() as deletion:
        await deletion.execute(
            delete(RunRow).where(RunRow.run_id == creation.run.run_id)
        )
        repair = asyncio.create_task(arranged.acceptance.ensure_command(command))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(repair), timeout=0.1)
            await deletion.commit()
            assert await asyncio.wait_for(repair, timeout=3) is False
        finally:
            await deletion.rollback()
            if not repair.done():
                repair.cancel()
            await asyncio.gather(repair, return_exceptions=True)
    assert await arranged.commands.get("tenant-a", creation.run.run_id) is None
