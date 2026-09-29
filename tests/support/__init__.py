"""Shared test support helpers."""

from harness.api.dependencies import ApiContainer
from harness.worker.dispatcher import ExecutionCommandDispatcher


def dispatcher_of(container: ApiContainer) -> ExecutionCommandDispatcher:
    """Return the composed Dispatcher, failing loudly when it is missing.

    Acceptance records a durable obligation and the Dispatcher delivers it, so
    a test that expects a dequeueable Run task must run one dispatch pass. This
    accessor keeps that step explicit instead of silently skipping it.
    """

    dispatcher = container.dispatcher
    assert dispatcher is not None, "container has no execution command dispatcher"
    return dispatcher


async def deliver_pending_tasks(container: ApiContainer) -> int:
    """Publish every currently due dispatch obligation; return how many were sent."""

    return (await dispatcher_of(container).run_once()).dispatched
