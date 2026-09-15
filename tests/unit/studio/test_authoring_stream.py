import asyncio
import json

import pytest

from harness.studio.authoring_stream import authoring_stream


@pytest.mark.asyncio
async def test_disconnect_cancels_producer_after_incremental_progress() -> None:
    stopped = asyncio.Event()

    async def operation(emit):
        try:
            await emit({"type": "progress", "text": "first"})
            await asyncio.Event().wait()
        finally:
            stopped.set()

    response = authoring_stream(operation)
    first = await anext(response.body_iterator)
    assert json.loads(first.removeprefix("data: "))["text"] == "first"
    assert not stopped.is_set()
    await response.body_iterator.aclose()
    assert stopped.is_set()
