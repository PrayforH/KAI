"""Bound text batching latency without cancelling a slow SDK read."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress

FLUSH_TEXT = object()
TEXT_FLUSH_SECONDS = 0.05


async def with_flush_deadline(
    source: AsyncIterator[object],
    has_pending_text: Callable[[], bool],
    *,
    interval: float = TEXT_FLUSH_SECONDS,
) -> AsyncGenerator[object, None]:
    """Yield a flush marker within one interval of an outstanding text batch.

    All source reads and cleanup run in the same task: SDK cancel scopes and
    tracing ContextVars cannot safely move between per-read tasks. A one-item
    queue preserves backpressure; timeouts cancel only the queue read.
    """
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=1)

    async def produce() -> None:
        try:
            async for item in source:
                await queue.put(item)
        finally:
            close = getattr(source, "aclose", None)
            if close is not None:
                await close()

    producer = asyncio.create_task(produce())
    deadline: float | None = None
    loop = asyncio.get_running_loop()
    try:
        while True:
            if producer.done() and queue.empty():
                if has_pending_text():
                    yield FLUSH_TEXT
                producer.result()  # Propagate source errors, including cancellation.
                return
            if not has_pending_text():
                deadline = None
            elif deadline is None:
                deadline = loop.time() + interval
            if deadline is not None and loop.time() >= deadline:
                deadline = None
                yield FLUSH_TEXT
                continue
            read = asyncio.create_task(queue.get())
            try:
                done, _ = await asyncio.wait(
                    {read, producer},
                    timeout=None if deadline is None else max(0, deadline - loop.time()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if read in done:
                    yield read.result()
                elif producer not in done:
                    deadline = None
                    yield FLUSH_TEXT
            finally:
                read.cancel()
                with suppress(asyncio.CancelledError):
                    await read
    finally:
        producer.cancel()
        with suppress(asyncio.CancelledError):
            await producer
