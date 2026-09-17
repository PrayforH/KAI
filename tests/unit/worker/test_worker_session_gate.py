"""Two worker_loop instances sharing one queue must serialize same-session Runs.

The in-process lock only orders Runs inside one process. This test drives two
loops against a shared in-memory queue with a shared RedisSessionGate (fake
client) and asserts the session contract holds across the replica boundary.
"""

import asyncio

import pytest

from harness.core.ports import RunTask
from harness.storage.redis import RedisSessionGate
from harness.worker.main import worker_loop


def make_task(tenant_id: str, session_id: str, run_id: str) -> RunTask:
    return RunTask(
        tenant_id=tenant_id,
        session_id=session_id,
        run_id=run_id,
    )


class FakeGateRedis:
    """Minimal gate-script semantics; no TTL bookkeeping needed here."""

    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object:
        key, token = keys_and_args[0], keys_and_args[1]
        if "PEXPIRE" in script:
            return 1 if self.held.get(key) == token else 0
        if "DEL" in script:
            if self.held.get(key) == token:
                self.held.pop(key, None)
                return 1
            return 0
        if key not in self.held:
            self.held[key] = token
            return 1
        return 0


class SharedQueue:
    """Hands each task to exactly one dequeue caller."""

    def __init__(self) -> None:
        self._items: list[RunTask] = []
        self._event = asyncio.Event()
        self._awaiting: list[asyncio.Future[RunTask | None]] = []
        self.dequeued: list[str] = []

    async def enqueue(self, task: RunTask) -> None:
        for future in self._awaiting:
            if not future.done():
                self._awaiting.remove(future)
                future.set_result(task)
                return
        self._items.append(task)
        self._event.set()

    async def dequeue(self) -> RunTask | None:
        if self._items:
            task = self._items.pop(0)
        else:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[RunTask | None] = loop.create_future()
            self._awaiting.append(future)
            try:
                # Mirror the Redis queue: a poll that yields None instead of
                # blocking forever, so worker_loop can observe shutdown.
                task = await asyncio.wait_for(future, timeout=0.05)
            except TimeoutError:
                self._awaiting.remove(future)
                return None
        if task is not None:
            self.dequeued.append(task.run_id)
        return task

    async def acknowledge(self, task: RunTask) -> None: ...

    async def retry(self, task: RunTask) -> None: ...

    async def extend_lease(self, task: RunTask) -> None: ...


@pytest.mark.asyncio
async def test_two_worker_loops_serialize_same_session_runs() -> None:
    queue = SharedQueue()
    gate = RedisSessionGate(
        FakeGateRedis(),  # type: ignore[arg-type]
        ttl_seconds=60,
        refresh_interval_seconds=10_000,  # no refresh interference in test time
        poll_interval_seconds=0.01,
        acquire_timeout_seconds=5,
    )

    entry_order: list[str] = []
    exit_order: list[str] = []
    a_started = asyncio.Event()
    a_finish = asyncio.Event()
    overlap_detected: list[str] = []
    in_flight: set[str] = set()

    class Executor:
        async def execute(self, tenant_id: str, run_id: str) -> None:
            if in_flight:
                overlap_detected.append(run_id)
            in_flight.add(run_id)
            entry_order.append(run_id)
            try:
                if run_id == "run-a":
                    a_started.set()
                    await a_finish.wait()
                else:
                    await asyncio.sleep(0.01)
            finally:
                in_flight.discard(run_id)
                exit_order.append(run_id)

    stop = asyncio.Event()
    loops = [
        asyncio.create_task(
            worker_loop(
                queue,  # type: ignore[arg-type]
                Executor(),  # type: ignore[arg-type]
                stop=stop,
                poll_interval=0.01,
                concurrency=1,
                session_gate=gate,
            )
        )
        for _ in range(2)
    ]

    await queue.enqueue(make_task("t", "session-1", "run-a"))
    await a_started.wait()
    # Loop 1 is busy inside run-a, so loop 2 must take run-b of the same
    # session and block on the shared gate instead of entering execution.
    await queue.enqueue(make_task("t", "session-1", "run-b"))
    await asyncio.sleep(0.2)
    assert entry_order == ["run-a"], entry_order
    assert queue.dequeued == ["run-a", "run-b"]

    a_finish.set()
    for _ in range(100):
        if "run-b" in entry_order:
            break
        await asyncio.sleep(0.02)
    assert entry_order == ["run-a", "run-b"], entry_order
    assert exit_order.index("run-a") < entry_order.index("run-b")

    stop.set()
    await asyncio.gather(*loops)


@pytest.mark.asyncio
async def test_independent_sessions_run_concurrently_across_loops() -> None:
    queue = SharedQueue()
    gate = RedisSessionGate(
        FakeGateRedis(),  # type: ignore[arg-type]
        ttl_seconds=60,
        refresh_interval_seconds=10_000,
        poll_interval_seconds=0.01,
        acquire_timeout_seconds=5,
    )

    both_started = asyncio.Event()
    started: set[str] = set()
    release = asyncio.Event()

    class Executor:
        async def execute(self, tenant_id: str, run_id: str) -> None:
            started.add(run_id)
            if len(started) == 2:
                both_started.set()
            await release.wait()

    stop = asyncio.Event()
    loops = [
        asyncio.create_task(
            worker_loop(
                queue,  # type: ignore[arg-type]
                Executor(),  # type: ignore[arg-type]
                stop=stop,
                poll_interval=0.01,
                concurrency=1,
                session_gate=gate,
            )
        )
        for _ in range(2)
    ]

    await queue.enqueue(make_task("t", "session-1", "run-a"))
    await queue.enqueue(make_task("t", "session-2", "run-b"))
    await asyncio.wait_for(both_started.wait(), timeout=5)

    release.set()
    stop.set()
    await asyncio.gather(*loops)
