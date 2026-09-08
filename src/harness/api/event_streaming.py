"""Shared event-stream waiting policy with a safe polling fallback."""

import asyncio
import logging

from harness.core.ports import EventWakeup

logger = logging.getLogger(__name__)


async def wait_for_run_event(
    wakeup: EventWakeup | None,
    tenant_id: str,
    run_id: str,
    after_sequence: int,
    *,
    fallback_poll_seconds: float,
    wakeup_timeout_seconds: float = 1.0,
) -> None:
    """Wait for an event signal, periodically falling back to durable storage.

    Notification loss is harmless: the timeout causes the stream to query the
    durable repository again. A Redis outage also degrades to the previous
    short polling behaviour instead of terminating an active client stream.
    """

    if wakeup is None:
        await asyncio.sleep(fallback_poll_seconds)
        return
    try:
        await wakeup.wait(
            tenant_id,
            run_id,
            after_sequence,
            timeout_seconds=wakeup_timeout_seconds,
        )
    except Exception:
        logger.debug("event wakeup failed; using durable polling fallback", exc_info=True)
        await asyncio.sleep(fallback_poll_seconds)
