"""RedisSessionGate mutual exclusion against a script-faithful fake client."""

import asyncio
from typing import Protocol

import pytest

from harness.storage.redis import RedisSessionGate, SessionGateError


class AsyncRedisClientLike(Protocol):
    pass


class FakeRedisClient:
    """Implements the three Lua scripts' semantics with real TTL expiry."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[str, float]] = {}
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds * 1000
        expired = [k for k, (_, exp) in self.store.items() if exp <= self.now]
        for key in expired:
            self.store.pop(key, None)

    async def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object:
        key, token, ttl_ms = keys_and_args[0], keys_and_args[1], keys_and_args[2]
        current = self.store.get(key)
        if "PEXPIRE" in script:
            if current is not None and current[0] == token:
                self.store[key] = (token, self.now + float(ttl_ms))
                return 1
            return 0
        if "DEL" in script:
            if current is not None and current[0] == token:
                self.store.pop(key, None)
                return 1
            return 0
        # acquire
        if current is None or current[1] <= self.now:
            self.store[key] = (token, self.now + float(ttl_ms))
            return 1
        return 0


def make_gate(client: FakeRedisClient, **overrides: float) -> RedisSessionGate:
    defaults: dict[str, float] = {
        "ttl_seconds": 90,
        "refresh_interval_seconds": 30,
        "poll_interval_seconds": 0.01,
        "acquire_timeout_seconds": 1,
    }
    defaults.update(overrides)
    return RedisSessionGate(client, **defaults)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_second_acquire_waits_until_release() -> None:
    client = FakeRedisClient()
    gate = make_gate(client)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with gate.acquire(("t", "s")):
            entered.set()
            await release.wait()

    first = asyncio.create_task(holder())
    await entered.wait()
    assert client.store  # key held

    waiter = gate.acquire(("t", "s"))
    second = asyncio.create_task(waiter.__aenter__())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(second), timeout=0.1)

    release.set()
    await first
    await asyncio.wait_for(second, timeout=2)
    await waiter.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_release_is_token_checked() -> None:
    client = FakeRedisClient()
    gate = make_gate(client)
    async with gate.acquire(("t", "s")):
        # A foreign token must not be able to release or steal the key.
        release_script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] then "
            "redis.call('DEL', KEYS[1]) return 1 end return 0"
        )
        stolen = await client.eval(
            release_script,
            1,
            "harness:session-gate:t:s",
            "foreign-token",
            "90000",
        )
        assert stolen == 0
        assert client.store


@pytest.mark.asyncio
async def test_busy_gate_times_out_with_session_gate_error() -> None:
    client = FakeRedisClient()
    gate = make_gate(client)
    async with gate.acquire(("t", "s")):
        with pytest.raises(SessionGateError):
            async with gate.acquire(("t", "s")):
                pass


@pytest.mark.asyncio
async def test_ttl_expiry_releases_dead_holder() -> None:
    client = FakeRedisClient()
    gate = make_gate(client, refresh_interval_seconds=10_000)
    async with gate.acquire(("t", "s")):
        client.advance(120)  # beyond the 90s TTL with no refresh
        assert "harness:session-gate:t:s" not in client.store
    # The stale release is a no-op; a fresh acquire succeeds.
    async with gate.acquire(("t", "s")):
        assert "harness:session-gate:t:s" in client.store


@pytest.mark.asyncio
async def test_refresh_extends_ttl_for_live_holder() -> None:
    client = FakeRedisClient()
    gate = make_gate(client, ttl_seconds=0.5, refresh_interval_seconds=0.05)
    async with gate.acquire(("t", "s")):
        # Fake-elapsed 1.0s > 0.5s TTL; only the live refresh keeps the key.
        for _ in range(20):
            client.advance(0.05)
            await asyncio.sleep(0.01)
        assert "harness:session-gate:t:s" in client.store
