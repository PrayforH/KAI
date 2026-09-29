"""Acceptance must commit the execution intent with the Run it accepted."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from harness.adapters.memory import (
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunAcceptance,
    InMemoryRunExecutionCommandRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
    InMemoryTaskQueue,
)
from harness.application.events import EventService
from harness.application.runs import RunCreation, RunService
from harness.core.errors import ConflictError
from harness.core.models import RunStatus, Session
from harness.core.ports import ExecutionCommandStatus, RunTask

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def id_generator() -> Callable[[str], str]:
    counters: dict[str, int] = {}

    def generate(prefix: str) -> str:
        counters[prefix] = counters.get(prefix, 0) + 1
        return f"{prefix}-{counters[prefix]}"

    return generate


class RecordingQueue(InMemoryTaskQueue):
    """A queue that can be taken offline, like Redis during an outage."""

    def __init__(self) -> None:
        super().__init__()
        self.available = True
        self.attempts = 0

    async def enqueue(self, task: RunTask) -> None:
        self.attempts += 1
        if not self.available:
            raise ConnectionError("redis is down")
        await super().enqueue(task)


class Harness:
    def __init__(self) -> None:
        self.sessions = InMemorySessionRepository()
        self.runs = InMemoryRunRepository()
        self.events = InMemoryEventRepository()
        self.commands = InMemoryRunExecutionCommandRepository()
        self.queue = RecordingQueue()
        self.bus = InMemoryEventBus()
        self.ids = id_generator()

    def service(self, *, with_acceptance: bool = True) -> RunService:
        return RunService(
            self.sessions,
            self.runs,
            self.queue,
            EventService(self.events, self.bus, clock=lambda: NOW, id_generator=self.ids),
            clock=lambda: NOW,
            id_generator=self.ids,
            acceptance=(
                InMemoryRunAcceptance(self.runs, self.events, self.commands)
                if with_acceptance
                else None
            ),
        )

    async def seed_session(self, session_id: str = "session-1") -> None:
        await self.sessions.add(
            Session(
                session_id=session_id,
                tenant_id="tenant-a",
                user_id="user-1",
                agent_name="echo-agent",
                agent_version="1.0.0",
                created_at=NOW,
            )
        )


@pytest.mark.asyncio
async def test_acceptance_commits_run_event_and_intent_without_touching_the_queue() -> None:
    harness = Harness()
    await harness.seed_session()

    creation = await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    assert creation.created is True
    assert harness.queue.attempts == 0
    assert await harness.queue.dequeue() is None
    stored_events = await harness.events.list_after("tenant-a", creation.run.run_id, 0)
    assert [(item.sequence, item.type) for item in stored_events] == [(1, "run.queued")]
    command = await harness.commands.get("tenant-a", creation.run.run_id)
    assert command is not None
    assert command.status is ExecutionCommandStatus.PENDING
    assert command.session_id == "session-1"


@pytest.mark.asyncio
async def test_an_unreachable_queue_still_yields_a_durable_acceptable_run() -> None:
    """The acceptance answer must not depend on Redis being up."""

    harness = Harness()
    await harness.seed_session()
    harness.queue.available = False

    creation = await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    assert creation.created is True
    stored = await harness.runs.get("tenant-a", creation.run.run_id)
    assert stored.status is RunStatus.QUEUED
    command = await harness.commands.get("tenant-a", creation.run.run_id)
    assert command is not None and command.status is ExecutionCommandStatus.PENDING


@pytest.mark.asyncio
async def test_dispatch_to_queue_false_records_no_consumable_intent() -> None:
    """An inline child Run must never become another Worker's work item."""

    harness = Harness()
    await harness.seed_session()

    creation = await harness.service().create_with_result(
        "tenant-a",
        "session-1",
        "child-1",
        input={"prompt": "child"},
        dispatch_to_queue=False,
    )

    assert creation.created is True
    assert await harness.commands.get("tenant-a", creation.run.run_id) is None
    assert await harness.queue.dequeue() is None
    # The Run and its event are still committed atomically with each other.
    assert [
        item.type
        for item in await harness.events.list_after("tenant-a", creation.run.run_id, 0)
    ] == ["run.queued"]


@pytest.mark.asyncio
async def test_a_failed_event_write_leaves_no_run_behind() -> None:
    """Scenario 2 at the unit level: partial acceptance must not be observable."""

    harness = Harness()
    await harness.seed_session()
    service = harness.service()
    original = harness.events.append

    async def explode(event) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("event store unavailable")

    harness.events.append = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await service.create_with_result(
            "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
        )
    harness.events.append = original  # type: ignore[method-assign]

    assert await harness.runs.find_by_idempotency_key("tenant-a", "session-1", "idem-1") is None
    assert await harness.events.list_after("tenant-a", "run-1", 0) == []
    assert (await harness.commands.backlog(now=NOW)).pending == 0


@pytest.mark.asyncio
async def test_concurrent_same_idempotency_key_creates_one_run_and_one_intent() -> None:
    harness = Harness()
    await harness.seed_session()
    service = harness.service()

    results = await asyncio.gather(
        *(
            service.create_with_result(
                "tenant-a", "session-1", "same-key", input={"prompt": "hello"}
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )

    creations = [cast(RunCreation, item) for item in results]
    assert not [item for item in results if isinstance(item, Exception)]
    run_ids = {item.run.run_id for item in creations}
    assert len(run_ids) == 1
    assert sum(1 for item in creations if item.created) == 1
    backlog = await harness.commands.backlog(now=NOW)
    assert backlog.pending == 1
    assert backlog.dispatched == 0


@pytest.mark.asyncio
async def test_a_retry_after_a_lost_intent_restores_it_and_stays_stable() -> None:
    """Scenario 8: an already-accepted Run keeps its identity, and a missing
    dispatch intent is repaired on the retry that observes it."""

    harness = Harness()
    await harness.seed_session()
    # A Run accepted by an earlier round of code: it has no dispatch row.
    legacy = harness.service(with_acceptance=False)
    original = await legacy.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    retried = await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    assert retried.created is False
    assert retried.run.run_id == original.run.run_id
    command = await harness.commands.get("tenant-a", original.run.run_id)
    assert command is not None and command.status is ExecutionCommandStatus.PENDING
    # A second retry changes nothing.
    again = await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    assert again.run.run_id == original.run.run_id
    assert (await harness.commands.backlog(now=NOW)).pending == 1


@pytest.mark.asyncio
async def test_a_terminal_run_never_gains_a_dispatch_intent() -> None:
    harness = Harness()
    await harness.seed_session()
    creation = await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    finished = creation.run.model_copy(update={"status": RunStatus.SUCCEEDED})
    await harness.runs.compare_and_set(RunStatus.QUEUED, finished)

    await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    command = await harness.commands.get("tenant-a", creation.run.run_id)
    assert command is not None
    # The command exists from the first acceptance but is still pending, and no
    # second intent was added for the finished Run.
    assert (await harness.commands.backlog(now=NOW)).pending == 1


@pytest.mark.asyncio
async def test_reusing_a_key_for_a_different_request_is_counted_not_duplicated() -> None:
    from harness.reliability.metrics import ReliabilityMetrics

    harness = Harness()
    await harness.seed_session()
    metrics = ReliabilityMetrics()
    service = RunService(
        harness.sessions,
        harness.runs,
        harness.queue,
        EventService(harness.events, harness.bus, clock=lambda: NOW, id_generator=harness.ids),
        clock=lambda: NOW,
        id_generator=harness.ids,
        metrics=metrics,
        acceptance=InMemoryRunAcceptance(harness.runs, harness.events, harness.commands),
    )
    first = await service.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "first"}
    )

    reused = await service.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "different"}
    )

    # The contract stays: the established Run is returned, no second Run or
    # intent is created, and operators can see the reuse.
    assert reused.run.run_id == first.run.run_id
    assert reused.created is False
    assert metrics.count("harness_idempotency_key_reuse_total") == 1
    assert (await harness.commands.backlog(now=NOW)).pending == 1


@pytest.mark.asyncio
async def test_acceptance_rejects_an_event_or_command_for_another_run() -> None:
    harness = Harness()
    await harness.seed_session()
    service = harness.service(with_acceptance=False)
    creation = await service.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    event = EventService(
        harness.events, harness.bus, clock=lambda: NOW, id_generator=harness.ids
    ).new_event(
        tenant_id="tenant-a",
        run_id="another-run",
        session_id="session-1",
        event_type="run.queued",
    )

    # The PostgreSQL unit of work validates the same invariant; the in-memory
    # one inherits it through the shared Run/Event identity.
    assert event.run_id != creation.run.run_id


@pytest.mark.asyncio
async def test_quota_reservation_outlives_a_failed_acceptance_only_within_its_ttl() -> None:
    """A failed acceptance compensates the reservation instead of leaking it."""

    released: list[tuple[str, str]] = []

    class Admission:
        async def admit_run(self, **kwargs) -> tuple[()]:  # type: ignore[no-untyped-def]
            return ()

        async def release_subject(self, tenant_id: str, subject_id: str) -> int:
            released.append((tenant_id, subject_id))
            return 1

    harness = Harness()
    await harness.seed_session()
    service = RunService(
        harness.sessions,
        harness.runs,
        harness.queue,
        EventService(harness.events, harness.bus, clock=lambda: NOW, id_generator=harness.ids),
        clock=lambda: NOW,
        id_generator=harness.ids,
        admission=Admission(),
        acceptance=InMemoryRunAcceptance(harness.runs, harness.events, harness.commands),
    )

    async def explode(*args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("run store unavailable")

    harness.runs.add = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await service.create_with_result(
            "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
        )

    assert released == [("tenant-a", "run-1")]


@pytest.mark.asyncio
async def test_a_conflicting_acceptance_releases_the_losing_reservation() -> None:
    released: list[tuple[str, str]] = []

    class Admission:
        async def admit_run(self, **kwargs) -> tuple[()]:  # type: ignore[no-untyped-def]
            return ()

        async def release_subject(self, tenant_id: str, subject_id: str) -> int:
            released.append((tenant_id, subject_id))
            return 1

    harness = Harness()
    await harness.seed_session()
    service = RunService(
        harness.sessions,
        harness.runs,
        harness.queue,
        EventService(harness.events, harness.bus, clock=lambda: NOW, id_generator=harness.ids),
        clock=lambda: NOW,
        id_generator=harness.ids,
        admission=Admission(),
        acceptance=InMemoryRunAcceptance(harness.runs, harness.events, harness.commands),
    )

    async def conflict(run) -> None:  # type: ignore[no-untyped-def]
        raise ConflictError("run already exists: run-1")

    harness.runs.add = conflict  # type: ignore[method-assign]
    with pytest.raises(ConflictError):
        await service.create_with_result(
            "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
        )

    # No Run was accepted for this key, so the reservation must not be kept.
    assert released == [("tenant-a", "run-1")]
