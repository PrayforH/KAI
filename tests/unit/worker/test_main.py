import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import OperationalError

from harness.adapters.memory import (
    InMemoryRunExecutionCommandRepository,
    InMemoryTaskQueue,
)
from harness.core.errors import NotFoundError
from harness.core.models import Run
from harness.core.ports import (
    ExecutionCommandStatus,
    RunExecutionCommand,
    RunTask,
    execution_command_id,
)
from harness.reliability.metrics import ReliabilityMetrics
from harness.worker.dispatcher import ExecutionCommandDispatcher
from harness.worker.main import maintenance_loop, worker_lifecycle, worker_loop

ORDER_NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


class Queue:
    def __init__(
        self,
        tasks: list[RunTask],
        *,
        fail_lease_renewal: bool = False,
        fail_dequeue_once: bool = False,
        fail_acknowledge_once: bool = False,
        fail_retry_once: bool = False,
    ) -> None:
        self.tasks = tasks
        self.fail_lease_renewal = fail_lease_renewal
        self.fail_dequeue_once = fail_dequeue_once
        self.fail_acknowledge_once = fail_acknowledge_once
        self.fail_retry_once = fail_retry_once
        self.acknowledged: list[RunTask] = []
        self.retried: list[RunTask] = []
        self.extended: list[RunTask] = []

    async def enqueue(self, task: RunTask) -> None:
        self.tasks.append(task)

    async def dequeue(self) -> RunTask | None:
        if self.fail_dequeue_once:
            self.fail_dequeue_once = False
            raise RuntimeError("redis unavailable")
        return self.tasks.pop(0) if self.tasks else None

    async def acknowledge(self, task: RunTask) -> None:
        if self.fail_acknowledge_once:
            self.fail_acknowledge_once = False
            raise RuntimeError("redis unavailable")
        self.acknowledged.append(task)

    async def retry(self, task: RunTask) -> None:
        if self.fail_retry_once:
            self.fail_retry_once = False
            raise RuntimeError("redis unavailable")
        self.retried.append(task)
        self.tasks.append(task)

    async def extend_lease(self, task: RunTask) -> None:
        if self.fail_lease_renewal:
            raise RuntimeError("redis unavailable")
        self.extended.append(task)


class Executor:
    def __init__(self, stop: asyncio.Event, *, fail: bool = False) -> None:
        self.stop = stop
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        self.calls.append((tenant_id, run_id))
        self.stop.set()
        if self.fail:
            raise RuntimeError("database unavailable")
        return Run.model_construct()


class SlowExecutor:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        del tenant_id, run_id
        self.started.set()
        await self.release.wait()
        self.stop.set()
        return Run.model_construct()


class ParallelExecutor:
    def __init__(self, stop: asyncio.Event, total: int) -> None:
        self.stop = stop
        self.total = total
        self.active = 0
        self.maximum_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.completed = 0

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        del tenant_id, run_id
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        if self.active == self.total:
            self.started.set()
        await self.release.wait()
        self.active -= 1
        self.completed += 1
        if self.completed == self.total:
            self.stop.set()
        return Run.model_construct()


class SessionSerialExecutor:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.first_started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.order: list[str] = []

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        del tenant_id
        self.order.append(f"start:{run_id}")
        if run_id == "run-1":
            self.first_started.set()
            await self.release_first.wait()
        else:
            self.second_started.set()
        self.order.append(f"end:{run_id}")
        if run_id == "run-2":
            self.stop.set()
        return Run.model_construct()


class BadThenGoodExecutor:
    def __init__(self, stop: asyncio.Event) -> None:
        self.stop = stop
        self.calls: list[str] = []

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        del tenant_id
        self.calls.append(run_id)
        if run_id == "run-bad":
            raise RuntimeError("poison task")
        self.stop.set()
        return Run.model_construct()


@pytest.mark.asyncio
async def test_worker_loop_executes_scoped_task_and_stops() -> None:
    stop = asyncio.Event()
    queue = Queue([RunTask(tenant_id="tenant-a", run_id="run-1")])
    executor = Executor(stop)

    await worker_loop(queue, executor, stop=stop, poll_interval=0.001)

    assert executor.calls == [("tenant-a", "run-1")]
    assert queue.acknowledged == [RunTask(tenant_id="tenant-a", run_id="run-1")]
    assert queue.retried == []


@pytest.mark.asyncio
async def test_worker_loop_requeues_task_after_unexpected_failure() -> None:
    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task])
    executor = Executor(stop, fail=True)

    await worker_loop(queue, executor, stop=stop, poll_interval=0.001)

    assert queue.retried == [task]
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_worker_recovers_after_dequeue_failure_and_consumes_next_task() -> None:
    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task], fail_dequeue_once=True)
    executor = Executor(stop)
    metrics = ReliabilityMetrics()

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=0.001,
        metrics=metrics,
    )

    assert executor.calls == [("tenant-a", "run-1")]
    assert queue.acknowledged == [task]
    assert metrics.count(
        "harness_worker_queue_failures_total",
        labels={"operation": "dequeue"},
    ) == 1


@pytest.mark.asyncio
async def test_worker_contains_retry_failure_to_bad_task() -> None:
    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-bad")
    queue = Queue([task], fail_retry_once=True)
    executor = Executor(stop, fail=True)
    metrics = ReliabilityMetrics()

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=0.001,
        metrics=metrics,
    )

    assert queue.retried == []
    assert metrics.count(
        "harness_worker_queue_failures_total",
        labels={"operation": "retry"},
    ) == 1


@pytest.mark.asyncio
async def test_bad_task_does_not_block_the_following_run() -> None:
    stop = asyncio.Event()
    bad = RunTask(tenant_id="tenant-a", run_id="run-bad")
    good = RunTask(tenant_id="tenant-a", run_id="run-good")
    queue = Queue([bad, good])
    executor = BadThenGoodExecutor(stop)

    await worker_loop(queue, executor, stop=stop, poll_interval=0.001)

    assert executor.calls == ["run-bad", "run-good"]
    assert queue.retried == [bad]
    assert queue.acknowledged == [good]


@pytest.mark.asyncio
async def test_worker_contains_acknowledge_failure_after_terminal_run() -> None:
    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task], fail_acknowledge_once=True)
    executor = Executor(stop)
    metrics = ReliabilityMetrics()

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=0.001,
        metrics=metrics,
    )

    assert executor.calls == [("tenant-a", "run-1")]
    assert queue.acknowledged == []
    assert metrics.count(
        "harness_worker_queue_failures_total",
        labels={"operation": "acknowledge"},
    ) == 1


@pytest.mark.asyncio
async def test_worker_loop_can_stop_while_queue_is_empty() -> None:
    stop = asyncio.Event()
    queue = Queue([])
    executor = Executor(stop)
    task = asyncio.create_task(worker_loop(queue, executor, stop=stop, poll_interval=60))

    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=0.1)

    assert executor.calls == []


@pytest.mark.asyncio
async def test_worker_loop_runs_different_sessions_concurrently() -> None:
    stop = asyncio.Event()
    tasks = [
        RunTask(tenant_id="tenant-a", run_id="run-1", session_id="session-1"),
        RunTask(tenant_id="tenant-a", run_id="run-2", session_id="session-2"),
    ]
    queue = Queue(tasks.copy())
    executor = ParallelExecutor(stop, total=2)
    worker = asyncio.create_task(
        worker_loop(
            queue,
            executor,
            stop=stop,
            poll_interval=0.001,
            concurrency=2,
        )
    )

    await asyncio.wait_for(executor.started.wait(), timeout=0.1)
    assert executor.maximum_active == 2
    executor.release.set()
    await asyncio.wait_for(worker, timeout=0.2)

    assert queue.acknowledged == tasks


@pytest.mark.asyncio
async def test_worker_loop_serializes_runs_from_the_same_session() -> None:
    stop = asyncio.Event()
    tasks = [
        RunTask(tenant_id="tenant-a", run_id="run-1", session_id="session-1"),
        RunTask(tenant_id="tenant-a", run_id="run-2", session_id="session-1"),
    ]
    queue = Queue(tasks.copy())
    executor = SessionSerialExecutor(stop)
    worker = asyncio.create_task(
        worker_loop(
            queue,
            executor,
            stop=stop,
            poll_interval=0.001,
            concurrency=2,
        )
    )

    await asyncio.wait_for(executor.first_started.wait(), timeout=0.1)
    await asyncio.sleep(0)
    assert not executor.second_started.is_set()
    executor.release_first.set()
    await asyncio.wait_for(executor.second_started.wait(), timeout=0.1)
    await asyncio.wait_for(worker, timeout=0.2)

    assert executor.order == ["start:run-1", "end:run-1", "start:run-2", "end:run-2"]
    assert queue.acknowledged == tasks


@pytest.mark.asyncio
async def test_worker_runs_expiry_maintenance_while_queue_is_idle() -> None:
    stop = asyncio.Event()
    queue = Queue([])
    executor = Executor(stop)
    calls = 0

    async def maintenance() -> object:
        nonlocal calls
        calls += 1
        stop.set()
        return 0

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=60,
        maintenance=maintenance,
    )

    assert calls == 1
    assert executor.calls == []


@pytest.mark.asyncio
async def test_worker_loop_renews_lease_during_long_execution() -> None:
    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task])
    executor = SlowExecutor(stop)
    worker = asyncio.create_task(
        worker_loop(
            queue,
            executor,
            stop=stop,
            poll_interval=0.001,
            lease_heartbeat_interval=0.01,
        )
    )

    await executor.started.wait()
    await asyncio.sleep(0.025)
    executor.release.set()
    await asyncio.wait_for(worker, timeout=0.2)

    assert queue.extended
    assert queue.acknowledged == [task]


@pytest.mark.asyncio
async def test_worker_continues_when_lease_renewal_temporarily_fails() -> None:
    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task], fail_lease_renewal=True)
    executor = SlowExecutor(stop)
    metrics = ReliabilityMetrics()
    worker = asyncio.create_task(
        worker_loop(
            queue,
            executor,
            stop=stop,
            poll_interval=0.001,
            lease_heartbeat_interval=0.005,
            metrics=metrics,
        )
    )

    await executor.started.wait()
    await asyncio.sleep(0.012)
    executor.release.set()
    await asyncio.wait_for(worker, timeout=0.2)

    assert queue.acknowledged == [task]
    assert metrics.count(
        "harness_worker_queue_failures_total",
        labels={"operation": "extend_lease"},
    ) >= 1


@pytest.mark.asyncio
async def test_control_plane_maintenance_runs_while_a_child_run_is_active() -> None:
    stop = asyncio.Event()
    queue = Queue([RunTask(tenant_id="tenant-a", run_id="run-1")])
    executor = SlowExecutor(stop)
    reconciled = asyncio.Event()

    async def reconcile() -> object:
        reconciled.set()
        return 0

    worker = asyncio.create_task(worker_loop(queue, executor, stop=stop, poll_interval=0.001))
    controller = asyncio.create_task(
        maintenance_loop(
            reconcile,
            stop=stop,
            poll_interval=0.001,
            label="eval",
        )
    )

    await executor.started.wait()
    await asyncio.wait_for(reconciled.wait(), timeout=0.1)
    assert not worker.done()
    executor.release.set()
    await asyncio.gather(worker, controller)


class FailingExecutor:
    def __init__(self, stop: asyncio.Event, error: BaseException) -> None:
        self.stop = stop
        self.error = error

    async def execute(self, tenant_id: str, run_id: str) -> Run:
        self.stop.set()
        raise self.error


@pytest.mark.asyncio
async def test_a_deleted_target_is_retired_instead_of_retried_forever() -> None:
    """Fix 2: a task whose Run is gone converges instead of looping.

    Reproduces the reported behaviour: the Run (and its dispatch obligation)
    were deleted, so the Worker must not keep re-queueing a task it can never
    execute.
    """

    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-deleted")
    queue = Queue([task])
    executor = FailingExecutor(stop, NotFoundError("run not found: run-deleted"))
    metrics = ReliabilityMetrics()

    async def absent(tenant_id: str, run_id: str) -> Run:
        raise NotFoundError(f"run not found: {run_id}")

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=0.001,
        metrics=metrics,
        run_target=absent,
    )

    assert queue.acknowledged == [task]
    assert queue.retried == []
    retired = metrics.count(
        "harness_worker_queue_failures_total", labels={"operation": "target_gone"}
    )
    assert retired == 1


@pytest.mark.asyncio
async def test_an_unverifiable_target_is_still_retried() -> None:
    """Fix 2: a database outage is not evidence that the Run was deleted."""

    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task])
    executor = FailingExecutor(stop, NotFoundError("run not found: run-1"))

    async def unavailable(tenant_id: str, run_id: str) -> Run:
        raise OperationalError("select 1", {}, RuntimeError("database is down"))

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=0.001,
        run_target=unavailable,
    )

    assert queue.retried == [task]
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_a_failure_with_an_existing_target_is_retried() -> None:
    """Fix 2: a present target keeps the existing retry behaviour."""

    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task])
    executor = FailingExecutor(stop, NotFoundError("transient probe failure"))

    async def present(tenant_id: str, run_id: str) -> Run:
        return Run.model_construct()

    await worker_loop(
        queue,
        executor,
        stop=stop,
        poll_interval=0.001,
        run_target=present,
    )

    assert queue.retried == [task]
    assert queue.acknowledged == []


@pytest.mark.asyncio
async def test_without_a_probe_nothing_is_treated_as_deleted() -> None:
    """Fix 2: absent probe configuration means the old behaviour, not a guess."""

    stop = asyncio.Event()
    task = RunTask(tenant_id="tenant-a", run_id="run-1")
    queue = Queue([task])
    executor = FailingExecutor(stop, NotFoundError("run not found: run-1"))

    await worker_loop(queue, executor, stop=stop, poll_interval=0.001)

    assert queue.retried == [task]
    assert queue.acknowledged == []


class OrderedQueue(InMemoryTaskQueue):
    """An in-memory queue that records each successful hand-off."""

    def __init__(self, orders: list[str]) -> None:
        super().__init__()
        self.orders = orders
        self.published: list[str] = []

    async def enqueue(self, task: RunTask) -> None:
        await super().enqueue(task)
        self.published.append(task.run_id)
        self.orders.append(f"publish:{task.run_id}")


def order_command(run_id: str) -> RunExecutionCommand:
    return RunExecutionCommand(
        command_id=execution_command_id(run_id),
        tenant_id="tenant-a",
        run_id=run_id,
        session_id="session-1",
        status=ExecutionCommandStatus.PENDING,
        created_at=ORDER_NOW,
        available_at=ORDER_NOW,
    )


def order_clock() -> datetime:
    return ORDER_NOW


class OrderRecordingContainer:
    """A container that records when its shared resources were released."""

    def __init__(self, dispatcher: ExecutionCommandDispatcher) -> None:
        self.dispatcher = dispatcher
        self.close = self._close
        self.closed = False

    async def _close(self) -> None:
        health = await self.dispatcher.health()
        assert health.running is False, (
            "shared resources were released while the Dispatcher still ran"
        )
        self.closed = True


async def _dispatcher_over(
    queue: InMemoryTaskQueue,
) -> ExecutionCommandDispatcher:
    return ExecutionCommandDispatcher(
        InMemoryRunExecutionCommandRepository(),
        queue,
        clock=order_clock,
        owner="dispatcher-a",
        interval_seconds=0.01,
    )


@pytest.mark.asyncio
async def test_the_dispatcher_stops_before_shared_resources_are_released() -> None:
    """Fix 4: the container is released only after the Dispatcher has stopped."""

    orders: list[str] = []
    queue = OrderedQueue(orders)
    commands = InMemoryRunExecutionCommandRepository()
    await commands.insert(order_command("run-1"))
    dispatcher = ExecutionCommandDispatcher(
        commands, queue, clock=order_clock, owner="dispatcher-a", interval_seconds=0.01
    )
    container = OrderRecordingContainer(dispatcher)

    async with worker_lifecycle(container, dispatch_enabled=True):
        for _ in range(100):
            if queue.published:
                break
            await asyncio.sleep(0.01)

    assert orders == ["publish:run-1"]
    assert container.closed is True
    assert (await dispatcher.health()).running is False


@pytest.mark.asyncio
async def test_shared_resources_are_released_when_the_body_raises() -> None:
    """Fix 4: an abnormal exit keeps the same stop-then-release order."""

    queue = OrderedQueue([])
    dispatcher = await _dispatcher_over(queue)
    container = OrderRecordingContainer(dispatcher)

    with pytest.raises(RuntimeError):
        async with worker_lifecycle(container, dispatch_enabled=True):
            raise RuntimeError("worker body failed")

    assert container.closed is True
    assert (await dispatcher.health()).running is False


@pytest.mark.asyncio
async def test_a_disabled_dispatcher_still_releases_shared_resources() -> None:
    """Fix 4: nothing is left open when dispatching is switched off."""

    queue = OrderedQueue([])
    dispatcher = await _dispatcher_over(queue)
    container = OrderRecordingContainer(dispatcher)

    async with worker_lifecycle(container, dispatch_enabled=False):
        pass

    assert container.closed is True
    assert (await dispatcher.health()).running is False
    assert queue.published == []


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_again", [False, True])
async def test_cancelled_shutdown_joins_dispatcher_before_closing_resources(
    cancel_again: bool,
) -> None:
    publishing = asyncio.Event()
    shutting_down = asyncio.Event()
    cleaning_up = asyncio.Event()
    allow_cleanup = asyncio.Event()
    finished = asyncio.Event()

    class Queue(InMemoryTaskQueue):
        async def enqueue(self, task: RunTask) -> None:
            publishing.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning_up.set()
                await allow_cleanup.wait()
                finished.set()

    commands = InMemoryRunExecutionCommandRepository()
    await commands.insert(order_command("run-1"))
    dispatcher = ExecutionCommandDispatcher(
        commands, Queue(), clock=order_clock, owner="dispatcher-a",
        enqueue_timeout_seconds=60,
    )
    closed = False

    class Container:
        def __init__(self, dispatcher: ExecutionCommandDispatcher) -> None:
            self.dispatcher = dispatcher

        async def close(self) -> None:
            nonlocal closed
            assert finished.is_set(), "Dispatcher still uses the shared resources"
            closed = True

    container = Container(dispatcher)

    async def owner() -> None:
        async with worker_lifecycle(container, dispatch_enabled=True, grace_seconds=60):
            await publishing.wait()
            shutting_down.set()

    task = asyncio.create_task(owner())
    await asyncio.wait_for(shutting_down.wait(), timeout=3)
    background = dispatcher._task
    assert background is not None
    try:
        task.cancel()
        await asyncio.wait_for(cleaning_up.wait(), timeout=1)
        assert not closed
        if cancel_again:
            task.cancel()
            await asyncio.sleep(0)
            assert not closed
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=3)
        assert closed and background.done()
        assert (await dispatcher.health()).running is False
    finally:
        allow_cleanup.set()
        background.cancel()
        await asyncio.gather(background, task, return_exceptions=True)
