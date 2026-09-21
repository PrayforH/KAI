"""Bounded SSE transport for control-plane work, cancelled on disconnect."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any

from fastapi.responses import StreamingResponse

from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError

Progress = Callable[[dict[str, Any]], Awaitable[None]]


def authoring_stream(
    operation: Callable[[Progress], Awaitable[dict[str, Any]]],
) -> StreamingResponse:
    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)

        async def produce() -> None:
            try:
                result = await operation(queue.put)
                await queue.put({"type": "result", "result": result})
            except (ConflictError, NotFoundError, PermissionDeniedError) as error:
                # The message only reaches the client inside the SSE body, so
                # without this line a rejected builder turn leaves no server-side
                # trace at all and the failure cannot be told apart from any
                # other gate in the chain.
                logging.getLogger(__name__).warning(
                    "authoring stream rejected error_type=%s message=%s",
                    type(error).__name__,
                    error,
                )
                await queue.put({"type": "error", "message": str(error)})
            except Exception:
                logging.getLogger(__name__).exception("authoring stream failed")
                await queue.put({"type": "error", "message": "处理失败，请刷新确认当前状态后重试"})

        task = asyncio.create_task(produce())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                if event["type"] in {"result", "error"}:
                    break
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })
