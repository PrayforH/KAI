"""Acceptance must commit the execution intent with the Run it accepted."""

import asyncio
import time
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
from harness.core.events import RunEvent
from harness.core.models import Run, RunDispatchMode, RunStatus, Session
from harness.core.ports import (
    ExecutionCommandStatus,
    RunTask,
    execution_command_id,
)
from harness.reliability.metrics import ReliabilityMetrics
from harness.worker.dispatcher import ExecutionCommandDispatcher

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
    finished = creation.run.model_copy(
        update={
            "status": RunStatus.SUCCEEDED,
            "fencing_token": creation.run.fencing_token + 1,
        }
    )
    assert await harness.runs.compare_and_set(RunStatus.QUEUED, finished) is True

    await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    command = await harness.commands.get("tenant-a", creation.run.run_id)
    assert command is not None
    # The command exists from the first acceptance but is still pending, and no
    # second intent was added for the finished Run.
    assert (await harness.commands.backlog(now=NOW)).pending == 1
    assert (await harness.runs.get("tenant-a", creation.run.run_id)).status is (
        RunStatus.SUCCEEDED
    )


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


@pytest.mark.asyncio
async def test_an_unresponsive_event_bus_cannot_hold_a_committed_acceptance() -> None:
    """Fix 1: the notification is an optimization, never a precondition.

    A Redis that accepts the connection and then stops answering must not keep
    the caller open after its Run is already committed, and the timeout must
    not turn the accepted Run into an error.
    """

    harness = Harness()
    await harness.seed_session()

    class HangingBus:
        def __init__(self) -> None:
            self.attempted = False

        async def publish(self, event: RunEvent) -> None:
            self.attempted = True
            await asyncio.Event().wait()

        async def read(
            self, tenant_id: str, run_id: str, after_sequence: int = 0
        ) -> list[RunEvent]:
            del tenant_id, run_id, after_sequence
            return []

    hanging = HangingBus()
    service = RunService(
        harness.sessions,
        harness.runs,
        harness.queue,
        EventService(
            harness.events,
            hanging,
            clock=lambda: NOW,
            id_generator=harness.ids,
            notify_timeout_seconds=0.05,
        ),
        clock=lambda: NOW,
        id_generator=harness.ids,
        acceptance=InMemoryRunAcceptance(harness.runs, harness.events, harness.commands),
    )

    started = time.monotonic()
    creation = await asyncio.wait_for(
        service.create_with_result(
            "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
        ),
        timeout=3,
    )
    elapsed = time.monotonic() - started

    assert elapsed < 1, "a committed acceptance must not wait out a stalled bus"
    assert hanging.attempted is True
    assert creation.created is True
    # The durable facts are unaffected by the failed notification.
    stored = await harness.runs.get("tenant-a", creation.run.run_id)
    assert stored.status is RunStatus.QUEUED
    assert [
        item.type
        for item in await harness.events.list_after("tenant-a", creation.run.run_id, 0)
    ] == ["run.queued"]
    command = await harness.commands.get("tenant-a", creation.run.run_id)
    assert command is not None and command.status is ExecutionCommandStatus.PENDING


@pytest.mark.asyncio
async def test_an_unresponsive_event_bus_does_not_lose_the_dispatch_obligation() -> None:
    """Fix 1: with the notification stalled, the Dispatcher still delivers."""

    harness = Harness()
    await harness.seed_session()
    metrics = ReliabilityMetrics()

    class HangingBus:
        async def publish(self, event: RunEvent) -> None:
            await asyncio.Event().wait()

        async def read(
            self, tenant_id: str, run_id: str, after_sequence: int = 0
        ) -> list[RunEvent]:
            del tenant_id, run_id, after_sequence
            return []

    service = RunService(
        harness.sessions,
        harness.runs,
        harness.queue,
        EventService(
            harness.events,
            HangingBus(),
            clock=lambda: NOW,
            id_generator=harness.ids,
            notify_timeout_seconds=0.05,
        ),
        clock=lambda: NOW,
        id_generator=harness.ids,
        metrics=metrics,
        acceptance=InMemoryRunAcceptance(harness.runs, harness.events, harness.commands),
    )
    creation = await asyncio.wait_for(
        service.create_with_result(
            "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
        ),
        timeout=3,
    )

    delivered = await ExecutionCommandDispatcher(
        harness.commands,
        harness.queue,
        clock=lambda: NOW,
        owner="dispatcher-a",
    ).run_once()

    assert (delivered.claimed, delivered.dispatched) == (1, 1)
    task = await harness.queue.dequeue()
    assert task is not None and task.run_id == creation.run.run_id


@pytest.mark.asyncio
async def test_retrying_an_inline_run_never_turns_it_into_a_queue_task() -> None:
    """Fix 3: the recorded mode wins over the retry's arguments.

    Reproduces the reported defect: an inline child was created first, then
    retried with default arguments, which used to manufacture a dispatch
    intent and hand the child to another Worker.
    """

    harness = Harness()
    await harness.seed_session()
    service = harness.service()

    child = await service.create_with_result(
        "tenant-a",
        "session-1",
        "child-1",
        input={"prompt": "child"},
        dispatch_to_queue=False,
    )
    assert child.run.dispatch_mode is RunDispatchMode.INLINE

    # Same key, default arguments: a plain retry.
    retried = await service.create_with_result(
        "tenant-a", "session-1", "child-1", input={"prompt": "child"}
    )

    assert retried.created is False
    assert retried.run.run_id == child.run.run_id
    assert retried.run.dispatch_mode is RunDispatchMode.INLINE
    assert await harness.commands.get("tenant-a", child.run.run_id) is None
    assert (await harness.commands.backlog(now=NOW)).pending == 0
    assert await harness.queue.dequeue() is None


@pytest.mark.asyncio
async def test_same_input_deduplication_does_not_queue_an_inline_run() -> None:
    """Fix 3: the dedup path reads the Run's own mode, not the caller's flag."""

    harness = Harness()
    await harness.seed_session()
    service = harness.service()
    child = await service.create_with_result(
        "tenant-a",
        "session-1",
        "child-1",
        input={"prompt": "child"},
        dispatch_to_queue=False,
    )

    duplicate = await service.create_with_result(
        "tenant-a",
        "session-1",
        "other-key",
        input={"prompt": "child"},
        deduplicate_active_input=True,
    )

    assert duplicate.deduplicated is True
    assert duplicate.run.run_id == child.run.run_id
    assert await harness.commands.get("tenant-a", child.run.run_id) is None
    assert await harness.queue.dequeue() is None


@pytest.mark.asyncio
async def test_a_queued_run_whose_intent_was_lost_is_still_repaired() -> None:
    """Fix 3 keeps the repair ability for Runs that were genuinely queued."""

    harness = Harness()
    await harness.seed_session()
    service = harness.service()
    created = await service.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    assert created.run.dispatch_mode is RunDispatchMode.QUEUED
    # The intent is lost - for example the row was removed with its Run gone.
    await harness.commands.remove(execution_command_id(created.run.run_id))
    assert (await harness.commands.backlog(now=NOW)).pending == 0

    retried = await service.create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )

    assert retried.run.run_id == created.run.run_id
    command = await harness.commands.get("tenant-a", created.run.run_id)
    assert command is not None and command.status is ExecutionCommandStatus.PENDING


@pytest.mark.asyncio
async def test_a_legacy_run_without_a_recorded_mode_is_left_for_an_operator() -> None:
    """Fix 3: nothing proves a legacy Run was meant to be queued, so it is not.

    Auto-dispatching here is what used to convert an inline child into a
    background task, so the rule is explicit: unknown means untouched, and it
    is counted so the leftovers can be found.
    """

    harness = Harness()
    await harness.seed_session()
    metrics = ReliabilityMetrics()
    legacy = Run(
        run_id="run-legacy",
        session_id="session-1",
        tenant_id="tenant-a",
        status=RunStatus.QUEUED,
        idempotency_key="legacy-key",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": "hello"},
        dispatch_mode=None,
    )
    await harness.runs.add(legacy)
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

    retried = await service.create_with_result(
        "tenant-a", "session-1", "legacy-key", input={"prompt": "hello"}
    )

    assert retried.run.run_id == legacy.run_id
    assert retried.run.dispatch_mode is None
    assert await harness.commands.get("tenant-a", legacy.run_id) is None
    assert (
        metrics.count(
            "harness_dispatch_unbackfilled_total", labels={"reason": "unknown_mode"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_the_recorded_mode_survives_a_status_change() -> None:
    """Fix 3: the mode is durable, so the Dispatcher can never be told otherwise."""

    harness = Harness()
    await harness.seed_session()
    created = await harness.service().create_with_result(
        "tenant-a", "session-1", "idem-1", input={"prompt": "hello"}
    )
    finished = created.run.model_copy(
        update={
            "status": RunStatus.SUCCEEDED,
            "fencing_token": created.run.fencing_token + 1,
        }
    )
    assert await harness.runs.compare_and_set(RunStatus.QUEUED, finished) is True

    stored = await harness.runs.get("tenant-a", created.run.run_id)

    assert stored.status is RunStatus.SUCCEEDED
    assert stored.dispatch_mode is RunDispatchMode.QUEUED
