"""Worker entry helpers."""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from harness.config import Settings
from harness.core.models import Run
from harness.core.ports import RunTask, TaskQueue
from harness.reliability.metrics import ReliabilityMetrics
from harness.sandbox.lease import SandboxLeaseService, SandboxLeaseState
from harness.worker.orchestrator import RunOrchestrator

logger = logging.getLogger(__name__)


class RunExecutor(Protocol):
    async def execute(self, tenant_id: str, run_id: str) -> Run: ...


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
            )
        finally:
            stop.set()
            await asyncio.gather(*control_tasks)
    finally:
        metrics_server.close()
        await metrics_server.wait_closed()
        if container.close is not None:
            await container.close()


def entrypoint() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(Settings()))
