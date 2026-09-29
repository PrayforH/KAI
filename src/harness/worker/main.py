"""Worker entry helpers."""

import asyncio
import logging
import signal
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from harness.config import Settings
from harness.core.errors import NotFoundError
from harness.core.models import Run
from harness.core.ports import RunTask, TaskQueue
from harness.reliability.metrics import ReliabilityMetrics
from harness.sandbox.lease import SandboxLeaseService, SandboxLeaseState
from harness.worker.dispatcher import ExecutionCommandDispatcher, running_dispatcher
from harness.worker.orchestrator import RunOrchestrator

logger = logging.getLogger(__name__)


class RunExecutor(Protocol):
    async def execute(self, tenant_id: str, run_id: str) -> Run: ...


class WorkerRuntime(Protocol):
    """The shared-resource surface a Worker owns for its whole run.

    Declared read-only so a frozen container satisfies it; the Worker only
    reads these, and never rebinds them.
    """

    @property
    def dispatcher(self) -> ExecutionCommandDispatcher | None: ...

    @property
    def close(self) -> Callable[[], Awaitable[None]] | None: ...


@asynccontextmanager
async def worker_lifecycle(
    container: WorkerRuntime,
    *,
    dispatch_enabled: bool,
    grace_seconds: float = 5,
) -> AsyncGenerator[None, None]:
    """Own the Dispatcher and shared-resource lifetime of a Worker process.

    Shutdown order is the contract, not an implementation detail: the
    Dispatcher is stopped and awaited **before** the container releases Redis
    and the database. Releasing first would let a still-running pass publish
    against a closed pool, or quietly reconnect after shutdown had begun.

    The release sits outside the dispatcher scope on purpose: cleanup attached
    to the body would run *before* that scope exits, which is the very ordering
    this function exists to prevent.
    """

    try:
        async with running_dispatcher(
            container.dispatcher, enabled=dispatch_enabled, grace_seconds=grace_seconds
        ):
            yield
    finally:
        if container.close is not None:
            await container.close()


class SessionGate(Protocol):
    """Orders Runs of one session across every worker that shares a queue."""

    def acquire(
        self, session_key: tuple[str, str]
    ) -> AbstractAsyncContextManager[None]: ...


class _LocalSessionGate:
    """In-process session ordering; the historical worker_loop behaviour."""

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._users: dict[tuple[str, str], int] = {}

    @asynccontextmanager
    async def acquire(self, session_key: tuple[str, str]):
        lock = self._locks.setdefault(session_key, asyncio.Lock())
        self._users[session_key] = self._users.get(session_key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._users[session_key] - 1
            if remaining == 0:
                self._users.pop(session_key, None)
                self._locks.pop(session_key, None)
            else:
                self._users[session_key] = remaining


async def run_once(orchestrator: RunOrchestrator, tenant_id: str, run_id: str) -> Run:
    """Execute one already-dequeued Run."""

    return await orchestrator.execute(tenant_id, run_id)


async def _wait_for_work(stop: asyncio.Event, poll_interval: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=poll_interval)
    except TimeoutError:
        pass


async def _renew_task_lease(
    queue: TaskQueue,
    task: RunTask,
    *,
    stop: asyncio.Event,
    interval: float,
    metrics: ReliabilityMetrics | None = None,
    sandbox_leases: SandboxLeaseService | None = None,
) -> None:
    while not stop.is_set():
        await _wait_for_work(stop, interval)
        if not stop.is_set():
            if sandbox_leases is not None:
                await _renew_sandbox_lease(sandbox_leases, task)
            try:
                await queue.extend_lease(task)
            except Exception:
                # A transient Redis failure must not terminate the worker while
                # the executor is still producing a terminal Run state. The
                # visibility lease may expire and cause a duplicate delivery,
                # which is safe because Run fencing rejects the stale owner.
                logger.exception(
                    "run task lease renewal failed",
                    extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
                )
                if metrics is not None:
                    metrics.increment(
                        "harness_worker_queue_failures_total",
                        labels={"operation": "extend_lease"},
                    )


async def _renew_sandbox_lease(leases: SandboxLeaseService, task: RunTask) -> None:
    """Keep the durable sandbox lease alive while the Run is still executing.

    A failure here is logged rather than raised: the Run can still finish
    correctly, and an un-renewed lease only means the reaper will question the
    sandbox after it expires.
    """

    try:
        lease = await leases.for_run(task.tenant_id, task.run_id)
        if lease is None or lease.state is not SandboxLeaseState.ACTIVE:
            return
        await leases.renew(task.tenant_id, lease.lease_id, epoch=lease.epoch)
    except Exception:  # noqa: BLE001 - renewal must never kill the worker
        logger.exception(
            "sandbox lease renewal failed",
            extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
        )


async def _target_is_gone(
    probe: Callable[[str, str], Awaitable[object]] | None, task: RunTask
) -> bool:
    """Whether the task's target is *known* to be absent.

    Only a definitive "not found" counts. A probe that fails for any other
    reason - the database is down, credentials are rejected, the read timed out
    - must not be read as "deleted", because retiring the task on an
    infrastructure failure would silently drop work that still exists.
    """

    if probe is None:
        return False
    try:
        await probe(task.tenant_id, task.run_id)
    except NotFoundError:
        return True
    except Exception:
        logger.warning(
            "could not confirm whether the run target still exists",
            extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
            exc_info=True,
        )
        return False
    return False


async def worker_loop(
    queue: TaskQueue,
    executor: RunExecutor,
    *,
    stop: asyncio.Event,
    poll_interval: float,
    lease_heartbeat_interval: float = 20,
    sandbox_leases: SandboxLeaseService | None = None,
    concurrency: int = 1,
    maintenance: Callable[[], Awaitable[object]] | None = None,
    metrics: ReliabilityMetrics | None = None,
    session_gate: SessionGate | None = None,
    run_target: Callable[[str, str], Awaitable[object]] | None = None,
) -> None:
    """Consume durable run tasks until shutdown is requested.

    A task is requeued only when execution escapes with an infrastructure error.
    Domain/runtime failures are terminal run results handled by the orchestrator.
    """

    if concurrency < 1:
        raise ValueError("worker concurrency must be at least 1")

    active: set[asyncio.Task[None]] = set()
    gate = session_gate if session_gate is not None else _LocalSessionGate()

    async def execute_task(task: RunTask, session_key: tuple[str, str]) -> None:
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            _renew_task_lease(
                queue,
                task,
                stop=heartbeat_stop,
                interval=lease_heartbeat_interval,
                metrics=metrics,
                sandbox_leases=sandbox_leases,
            )
        )
        try:
            # Different sessions may use the worker concurrently. Runs belonging
            # to one session remain ordered so workspace snapshots and the
            # provider conversation cannot race each other. The gate must be
            # shared across replicas (Redis) for that ordering to hold cluster-wide.
            async with gate.acquire(session_key):
                await executor.execute(task.tenant_id, task.run_id)
        except Exception:
            logger.exception(
                "run task execution escaped unexpectedly",
                extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
            )
            if await _target_is_gone(run_target, task):
                # The accepted target was deleted while its task was in flight
                # (or already was when the task arrived). Retrying can only
                # repeat the same lookup, so the task is retired here.
                logger.warning(
                    "run task target no longer exists; retiring the task",
                    extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
                )
                if metrics is not None:
                    metrics.increment(
                        "harness_worker_queue_failures_total",
                        labels={"operation": "target_gone"},
                    )
                try:
                    await queue.acknowledge(task)
                except Exception:
                    logger.exception(
                        "run task acknowledge for a missing target failed",
                        extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
                    )
            else:
                try:
                    await queue.retry(task)
                except Exception:
                    # Keep the processing lease intact. Visibility-timeout recovery
                    # will make the task eligible again without terminating this
                    # worker or blocking unrelated ready tasks.
                    logger.exception(
                        "run task retry failed",
                        extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
                    )
                    if metrics is not None:
                        metrics.increment(
                            "harness_worker_queue_failures_total",
                            labels={"operation": "retry"},
                        )
            await _wait_for_work(stop, poll_interval)
        else:
            try:
                await queue.acknowledge(task)
            except Exception:
                # A terminal Run is idempotent. If the queue acknowledge fails,
                # leave the lease for redelivery; the next executor observes the
                # terminal Run and acknowledges it without repeating business work.
                logger.exception(
                    "run task acknowledge failed",
                    extra={"tenant_id": task.tenant_id, "run_id": task.run_id},
                )
                if metrics is not None:
                    metrics.increment(
                        "harness_worker_queue_failures_total",
                        labels={"operation": "acknowledge"},
                    )
        finally:
            heartbeat_stop.set()
            await heartbeat

    while not stop.is_set():
        if maintenance is not None:
            try:
                await maintenance()
            except Exception:
                logger.exception("worker maintenance failed")
        if len(active) >= concurrency:
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            active.difference_update(done)
            continue
        try:
            task: RunTask | None = await queue.dequeue()
        except Exception:
            # Redis/network failures and malformed leased payloads are scoped to
            # this poll. The worker remains alive and continues with later tasks.
            logger.exception("run task dequeue failed")
            if metrics is not None:
                metrics.increment(
                    "harness_worker_queue_failures_total",
                    labels={"operation": "dequeue"},
                )
            await _wait_for_work(stop, poll_interval)
            continue
        if task is None:
            done = {child for child in active if child.done()}
            active.difference_update(done)
            await _wait_for_work(stop, poll_interval)
            continue
        session_key = (task.tenant_id, task.session_id or task.run_id)
        active.add(asyncio.create_task(execute_task(task, session_key)))

    if active:
        await asyncio.gather(*active)


async def maintenance_loop(
    maintenance: Callable[[], Awaitable[object]],
    *,
    stop: asyncio.Event,
    poll_interval: float,
    label: str,
) -> None:
    """Run an independent control-plane reconciler beside Run execution."""

    while not stop.is_set():
        try:
            await maintenance()
        except Exception:
            logger.exception("%s maintenance failed", label)
        await _wait_for_work(stop, poll_interval)


async def _write_metrics_response(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    metrics: ReliabilityMetrics,
) -> None:
    try:
        request = await reader.read(8192)
        first_line = request.split(b"\r\n", 1)[0]
        if first_line.startswith(b"GET /metrics "):
            body = metrics.render_prometheus().encode()
            status = b"200 OK"
            content_type = b"text/plain; version=0.0.4; charset=utf-8"
        else:
            body = b"not found\n"
            status = b"404 Not Found"
            content_type = b"text/plain; charset=utf-8"
        writer.write(
            b"HTTP/1.1 "
            + status
            + b"\r\nContent-Type: "
            + content_type
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def start_metrics_server(
    metrics: ReliabilityMetrics,
    *,
    host: str = "0.0.0.0",
    port: int = 8001,
) -> asyncio.Server:
    """Expose process-local worker metrics on an internal-only HTTP port."""

    return await asyncio.start_server(
        lambda reader, writer: _write_metrics_response(reader, writer, metrics),
        host,
        port,
    )


async def serve(settings: Settings) -> None:
    """Compose and run the production worker until SIGINT or SIGTERM."""

    from harness.composition import build_production_container

    container = build_production_container(settings)
    if container.sandbox_startup is not None:
        # A sandbox backend that cannot serve this deployment configuration
        # should stop the Worker, not the first Run that needs it.
        await container.sandbox_startup()
    metrics_server = await start_metrics_server(
        container.reliability_metrics,
        port=settings.worker_metrics_port,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(shutdown_signal, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows event loop
            pass
    # Accepted Runs carry a durable dispatch obligation, so this loop is what
    # turns "accepted" into "executing somewhere". The lifecycle stops it and
    # waits for it before the container releases Redis and the database.
    async with worker_lifecycle(
        container, dispatch_enabled=settings.worker_dispatch_enabled
    ):
        try:

            async def preview_maintenance() -> None:
                await container.preview_controller.process_once()

            async def eval_maintenance() -> None:
                await container.eval_controller.process_once()

            async def deployment_maintenance() -> None:
                await container.deployment_controller.process_once()

            async def reliability_maintenance() -> None:
                await container.reliability_controller.process_once()

            async def trigger_maintenance() -> None:
                await container.triggers.dispatch_due()

            async def automation_maintenance() -> None:
                await container.automations.dispatch_due()

            control_tasks = [
                asyncio.create_task(
                    maintenance_loop(
                        preview_maintenance,
                        stop=stop,
                        poll_interval=settings.worker_poll_interval_seconds,
                        label="preview",
                    )
                ),
                asyncio.create_task(
                    maintenance_loop(
                        eval_maintenance,
                        stop=stop,
                        poll_interval=settings.worker_poll_interval_seconds,
                        label="eval",
                    )
                ),
                asyncio.create_task(
                    maintenance_loop(
                        deployment_maintenance,
                        stop=stop,
                        poll_interval=settings.worker_poll_interval_seconds,
                        label="deployment",
                    )
                ),
                asyncio.create_task(
                    maintenance_loop(
                        reliability_maintenance,
                        stop=stop,
                        poll_interval=settings.reliability_reaper_interval_seconds,
                        label="reliability",
                    )
                ),
                asyncio.create_task(
                    maintenance_loop(
                        trigger_maintenance,
                        stop=stop,
                        poll_interval=1.0,
                        label="triggers",
                    )
                ),
                asyncio.create_task(
                    maintenance_loop(
                        automation_maintenance,
                        stop=stop,
                        poll_interval=1.0,
                        label="automations",
                    )
                ),
            ]
            try:
                await worker_loop(
                    container.task_queue,
                    container.worker,
                    stop=stop,
                    poll_interval=settings.worker_poll_interval_seconds,
                    lease_heartbeat_interval=settings.worker_task_heartbeat_seconds,
                    concurrency=settings.worker_concurrency,
                    metrics=container.reliability_metrics,
                    session_gate=getattr(container, "session_gate", None),
                    sandbox_leases=getattr(container, "sandbox_leases", None),
                    run_target=container.runs.get,
                )
            finally:
                stop.set()
                await asyncio.gather(*control_tasks)
        finally:
            # Only the metrics listener is closed here. Redis, the database and
            # the rest of the shared resources belong to `worker_lifecycle`,
            # which releases them after the Dispatcher has stopped.
            metrics_server.close()
            await metrics_server.wait_closed()


def entrypoint() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(Settings()))
