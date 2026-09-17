import asyncio
from contextlib import aclosing
from contextvars import ContextVar

import pytest

from harness.runtime.stream_flush import FLUSH_TEXT, with_flush_deadline


@pytest.mark.asyncio
async def test_flushes_small_batch_while_provider_is_waiting_without_cancelling_it():
    resume = asyncio.Event()
    closed = asyncio.Event()
    pending = False
    marker = ContextVar("stream-test", default=False)

    async def source():
        token = marker.set(True)
        try:
            yield "first"
            yield "small"
            await resume.wait()
            assert marker.get()
            yield "tail"
        finally:
            marker.reset(token)  # Opening, reads and cleanup must share one context.
            closed.set()

    async with aclosing(with_flush_deadline(source(), lambda: pending, interval=0.01)) as stream:
        assert await anext(stream) == "first"
        assert await anext(stream) == "small"
        pending = True
        assert await asyncio.wait_for(anext(stream), 0.3) is FLUSH_TEXT
        assert not closed.is_set()
        pending = False
        resume.set()
        assert await anext(stream) == "tail"
        pending = True
        assert await anext(stream) is FLUSH_TEXT
        pending = False
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    assert closed.is_set()
    assert marker.get() is False


@pytest.mark.asyncio
async def test_flush_deadline_is_not_postponed_by_continuous_small_deltas():
    pending = False

    async def source():
        while True:
            yield "字"
            await asyncio.sleep(0.001)

    async with aclosing(with_flush_deadline(source(), lambda: pending, interval=0.02)) as stream:
        assert await anext(stream) == "字"
        pending = True
        async with asyncio.timeout(0.3):
            seen = 0
            async for item in stream:
                if item is FLUSH_TEXT:
                    break
                seen += 1
        assert seen > 0


@pytest.mark.asyncio
async def test_early_close_cancels_source_and_releases_context():
    closed = asyncio.Event()

    async def source():
        try:
            yield "first"
            await asyncio.Event().wait()
        finally:
            closed.set()

    async with aclosing(with_flush_deadline(source(), lambda: False)) as stream:
        assert await anext(stream) == "first"
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("provider failed"), asyncio.CancelledError()])
async def test_source_error_or_cancellation_does_not_hang_and_flushes_tail(error):
    pending = False

    async def source():
        yield "tail"
        raise error

    async with aclosing(with_flush_deadline(source(), lambda: pending)) as stream:
        assert await anext(stream) == "tail"
        pending = True
        assert await asyncio.wait_for(anext(stream), 0.3) is FLUSH_TEXT
        pending = False
        with pytest.raises(type(error)):
            await anext(stream)
