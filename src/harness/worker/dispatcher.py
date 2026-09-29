"""Deliver accepted Runs to the queue, exactly as often as it takes.

Acceptance commits the Run, its event and one dispatch obligation in a single
database transaction. Redis is a transport for that obligation, not the
obligation itself, so this Dispatcher is what turns "accepted" into "running
somewhere" without ever making the HTTP answer depend on Redis.

Delivery is at-least-once. A Dispatcher that dies after publishing but before
recording the hand-off leaves the obligation leased; the lease expires and the
task is published again. Duplicates are harmless because the Worker's existing
idempotency, status validation and fencing decide who may execute a Run, and
because external model and tool side effects are not transactional with a
queue message.
"""

import asyncio
import logging
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from harness.application.types import Clock
from harness.core.ports import (
    RunExecutionCommand,
    RunExecutionCommandBacklog,
    RunExecutionCommandRepository,
    TaskQueue,
)
from harness.reliability.metrics import ReliabilityMetrics

logger = logging.getLogger(__name__)

# `scheme://user:secret@host` appears in Redis and HTTP client errors. The
# command diagnostic is bounded and scrubbed here so a credential can never be
# written to the obligation table or to the log line beside it.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s@]*@")
_AUTHORIZATION = re.compile(r"(?i)\b(authorization\s*[:=]\s*)[^\s,;]+")
_MAX_ERROR_LENGTH = 200

# One hand-off must not pin a Dispatcher pass: a Redis that accepts the
# connection and then stops answering would otherwise stall the whole batch.
DEFAULT_ENQUEUE_TIMEOUT_SECONDS = 5.0


def safe_error(error: BaseException) -> str:
    """Describe an exception for storage without leaking credentials."""

    message = str(error).replace("\n", " ")
    message = _URL_USERINFO.sub(r"\1[REDACTED]@", message)
    message = _AUTHORIZATION.sub(r"\1[REDACTED]", message)
    prefix = f"{type(error).__name__}: " if message else type(error).__name__
    budget = _MAX_ERROR_LENGTH - len(prefix)
    if budget <= 0:
        return prefix[:_MAX_ERROR_LENGTH]
    if len(message) > budget:
        message = f"{message[: budget - 1]}…" if budget > 1 else ""
    return f"{prefix}{message}"


@dataclass(frozen=True)
class DispatchCycle:
    """What one dispatch pass did, for tests and for the health snapshot."""

    claimed: int = 0
    dispatched: int = 0
    rescheduled: int = 0
    lost_lease: int = 0


@dataclass(frozen=True)
class DispatcherHealth:
    """Local view of the Dispatcher plus the durable backlog behind it."""

    running: bool
    cycles: int
    last_claimed: int
    last_dispatched: int
    last_rescheduled: int
    delivery_failures: int
    backlog: RunExecutionCommandBacklog


class ExecutionCommandDispatcher:
    """Lease pending dispatch commands and publish them to the Run queue.

    Redis is never called while a database transaction is open: a claim
    commits, the publish happens, and only then is the claim closed. That is
    the ordering that lets a crash anywhere in the middle stay recoverable.
    """

    def __init__(
        self,
        commands: RunExecutionCommandRepository,
        queue: TaskQueue,
        *,
        clock: Clock,
        owner: str,
        lease_seconds: float = 60,
        batch_size: int = 50,
        interval_seconds: float = 0.5,
        retry_base_seconds: float = 1,
        retry_max_seconds: float = 60,
        enqueue_timeout_seconds: float = DEFAULT_ENQUEUE_TIMEOUT_SECONDS,
        metrics: ReliabilityMetrics | None = None,
    ) -> None:
        if lease_seconds <= 0 or interval_seconds <= 0 or batch_size < 1:
            raise ValueError("dispatcher lease, interval and batch size must be positive")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("dispatcher retry backoff must be positive and ordered")
        if enqueue_timeout_seconds <= 0:
            raise ValueError("dispatcher enqueue timeout must be positive")
        self._commands = commands
        self._queue = queue
        self._clock = clock
        self._owner = owner
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._enqueue_timeout_seconds = enqueue_timeout_seconds
        self._metrics = metrics
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._cycles = 0
        self._last = DispatchCycle()
        self._delivery_failures = 0
        self._observed_at = 0.0
        # Backlog aggregation runs at most this often, so an idle Dispatcher
        # does not count the table on every poll.
        self._observation_interval_seconds = max(5.0, interval_seconds)

    async def start(self) -> None:
        """Begin dispatching in the background; safe to call once."""

        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(self._stop))

    async def stop(self, *, grace_seconds: float = 5) -> None:
        """Stop dispatching and let the in-flight pass settle.

        A pass that published but has not closed its claim yet is left to the
        lease expiry rather than trusted, which is why cancelling after the
        grace period stays safe.
        """

        if self._task is None:
            return
        assert self._stop is not None
        task = self._task
        self._stop.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=grace_seconds)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None
            self._stop = None

    async def run_once(self) -> DispatchCycle:
        """Claim one batch, publish outside the transaction, close the leases."""

        now = self._clock()
        claimed = await self._commands.claim_pending(
            owner=self._owner,
            lease_seconds=self._lease_seconds,
            limit=self._batch_size,
            now=now,
        )
        dispatched = 0
        rescheduled = 0
        lost = 0
        for command in claimed:
            if not await self._publish(command):
                rescheduled += 1
                continue
            if await self._commands.mark_dispatched(command, now=self._clock()):
                dispatched += 1
            elif await self._commands.get(command.tenant_id, command.run_id) is None:
                # The obligation is gone, which means the Run and its command
                # were deleted while this pass held the lease. Nothing is owed,
                # and the delivered task is absorbed by the consumer's "target
                # no longer exists" convergence.
                logger.info(
                    "dispatch obligation was removed while publishing",
                    extra={
                        "tenant_id": command.tenant_id,
                        "run_id": command.run_id,
                        "command_id": command.command_id,
                    },
                )
            else:
                # The lease was reclaimed while publishing. The command stays
                # pending, so the task will be published again; that duplicate
                # is absorbed by the Worker's idempotency.
                lost += 1
                logger.warning(
                    "dispatch lease was reclaimed while publishing",
                    extra={
                        "tenant_id": command.tenant_id,
                        "run_id": command.run_id,
                        "command_id": command.command_id,
                    },
                )
        cycle = DispatchCycle(
            claimed=len(claimed),
            dispatched=dispatched,
            rescheduled=rescheduled,
            lost_lease=lost,
        )
        self._cycles += 1
        self._last = cycle
        await self._observe(cycle)
        return cycle

    async def _publish(self, command: RunExecutionCommand) -> bool:
        """Hand one obligation to the queue; True only when it was accepted.

        Every failure path leaves the obligation pending for a backed-off
        retry, because a failed publish is never proof that Redis did not
        receive the command: a timeout or a broken connection can land after
        the server already queued it. A duplicate is therefore possible by
        design, and the Worker's idempotency, status validation and fencing are
        what keep it from becoming a second execution.
        """

        try:
            async with asyncio.timeout(self._enqueue_timeout_seconds):
                await self._queue.enqueue(command.to_task())
        except asyncio.CancelledError:
            # Shutdown or an explicit cancel owns this: the lease stays held and
            # expires, so the obligation is recovered rather than lost.
            raise
        except TimeoutError:
            await self._reschedule(
                command, None, operation="enqueue_timeout", uncertain=True
            )
            return False
        except Exception as error:
            await self._reschedule(command, error, operation="enqueue", uncertain=True)
            return False
        return True

    def _retry_delay(self, attempts: int) -> float:
        """Exponential backoff from the first retry, capped so a long outage
        still retries steadily instead of waiting out a runaway exponent."""

        return min(
            self._retry_max_seconds,
            self._retry_base_seconds * (2 ** max(0, min(attempts - 1, 16))),
        )

    async def _reschedule(
        self,
        command: RunExecutionCommand,
        error: BaseException | None,
        *,
        operation: str,
        uncertain: bool = False,
    ) -> None:
        available_at = self._clock() + timedelta(seconds=self._retry_delay(command.attempts))
        detail = (
            f"TimeoutError: publish exceeded {self._enqueue_timeout_seconds}s"
            if error is None
            else safe_error(error)
        )
        if uncertain:
            # Recorded so an operator reading the table knows a duplicate is
            # possible for this command, not that the publish definitely failed.
            detail = f"unconfirmed:{detail}"
        await self._commands.reschedule(
            command, available_at=available_at, error=detail
        )
        self._delivery_failures += 1
        if self._metrics is not None:
            self._metrics.increment(
                "harness_queue_dispatch_failures_total",
                labels={"operation": operation},
            )
        logger.warning(
            "run dispatch publish did not confirm; retrying with backoff",
            extra={
                "tenant_id": command.tenant_id,
                "run_id": command.run_id,
                "command_id": command.command_id,
                "attempts": command.attempts,
                "failure": detail,
            },
        )

    async def _observe(self, cycle: DispatchCycle) -> None:
        if self._metrics is None:
            return
        now = time.monotonic()
        stale = now - self._observed_at >= self._observation_interval_seconds
        if not (cycle.claimed or stale):
            return
        self._observed_at = now
        backlog = await self._commands.backlog(now=self._clock())
        self._metrics.gauge(
            "harness_dispatch_commands", backlog.pending, labels={"state": "pending"}
        )
        self._metrics.gauge(
            "harness_dispatch_commands", backlog.leased, labels={"state": "leased"}
        )
        self._metrics.gauge(
            "harness_dispatch_commands", backlog.dispatched, labels={"state": "dispatched"}
        )
        if backlog.oldest_pending_age_seconds is None:
            # An empty backlog is the recovered state, so the gauge has to say
            # zero rather than keep reporting the age of a backlog that is gone.
            self._metrics.gauge("harness_dispatch_pending_age_seconds", 0.0)
        else:
            # A pending obligation that keeps ageing is work nobody is
            # delivering; it must be visible rather than silently accumulating.
            self._metrics.gauge(
                "harness_dispatch_pending_age_seconds",
                backlog.oldest_pending_age_seconds,
            )
        if cycle.dispatched:
            self._metrics.increment("harness_queue_dispatch_total", cycle.dispatched)

    async def health(self) -> DispatcherHealth:
        """Expose backlog and delivery state for the worker metrics port."""

        return DispatcherHealth(
            running=self._task is not None and not self._task.done(),
            cycles=self._cycles,
            last_claimed=self._last.claimed,
            last_dispatched=self._last.dispatched,
            last_rescheduled=self._last.rescheduled,
            delivery_failures=self._delivery_failures,
            backlog=await self._commands.backlog(now=self._clock()),
        )

    async def _loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A database or Redis outage must not kill the Dispatcher: the
                # obligations are durable and the next pass retries them.
                logger.exception("dispatcher pass failed")
                if self._metrics is not None:
                    self._metrics.increment(
                        "harness_queue_dispatch_failures_total",
                        labels={"operation": "claim"},
                    )
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                pass


@asynccontextmanager
async def running_dispatcher(
    dispatcher: "ExecutionCommandDispatcher | None",
    *,
    enabled: bool,
    grace_seconds: float = 5,
) -> AsyncGenerator["ExecutionCommandDispatcher | None", None]:
    """Run the Dispatcher for the duration of the block, when it is enabled.

    The Worker owns this lifecycle: the API process only writes obligations.
    A Dispatcher that is stopped or never started leaves every obligation
    pending, which is visible in the backlog gauge rather than silent.

    ``grace_seconds`` bounds the wait for an in-flight pass, so a publish that
    is stuck against an unresponsive Redis cannot hold shutdown open.
    """

    if dispatcher is None or not enabled:
        yield None
        return
    await dispatcher.start()
    try:
        yield dispatcher
    finally:
        await dispatcher.stop(grace_seconds=grace_seconds)
