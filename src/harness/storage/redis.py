"""Redis transient queue and event fan-out adapters."""

import asyncio
import json
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager, suppress
from typing import Protocol
from uuid import uuid4

from harness.core.events import RunEvent
from harness.core.ports import RunTask


class AsyncRedisClient(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Awaitable[object]: ...

    def get(self, name: str) -> Awaitable[bytes | str | None]: ...

    def zadd(self, name: str, mapping: dict[str, int]) -> Awaitable[object]: ...

    def zrangebyscore(
        self, name: str, minimum: str, maximum: str
    ) -> Awaitable[list[bytes | str]]: ...

    def zcard(self, name: str) -> Awaitable[int]: ...

    def pubsub(self) -> "AsyncRedisPubSub": ...


class AsyncRedisPubSub(Protocol):
    def subscribe(self, *channels: str) -> Awaitable[object]: ...

    def unsubscribe(self, *channels: str) -> Awaitable[object]: ...

    def get_message(
        self,
        ignore_subscribe_messages: bool = False,
        timeout: float = 0.0,
    ) -> Awaitable[dict[str, object] | None]: ...

    def aclose(self) -> Awaitable[None]: ...


_ENQUEUE = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + tonumber(current[2]) / 1000000
if redis.call('SADD', KEYS[1], ARGV[1]) == 1 then
  redis.call('ZADD', KEYS[2], now, ARGV[1])
  return 1
end
return 0
"""

_DEQUEUE = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + tonumber(current[2]) / 1000000
local expired = redis.call('ZRANGEBYSCORE', KEYS[3], '-inf', now)
for _, item in ipairs(expired) do
  redis.call('ZREM', KEYS[3], item)
  redis.call('HDEL', KEYS[4], item)
  redis.call('ZADD', KEYS[2], now, item)
end
local items = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now, 'LIMIT', 0, 1)
local item = items[1]
if item then
  redis.call('ZREM', KEYS[2], item)
  redis.call('ZADD', KEYS[3], now + tonumber(ARGV[1]), item)
  redis.call('HSET', KEYS[4], item, ARGV[2])
end
return item
"""

_ACKNOWLEDGE = """
if redis.call('HGET', KEYS[3], ARGV[1]) == ARGV[2] then
  redis.call('ZREM', KEYS[2], ARGV[1])
  redis.call('HDEL', KEYS[3], ARGV[1])
  return redis.call('SREM', KEYS[1], ARGV[1])
end
return 0
"""

_RETRY = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + tonumber(current[2]) / 1000000
if redis.call('HGET', KEYS[3], ARGV[1]) == ARGV[3]
  and redis.call('ZREM', KEYS[2], ARGV[1]) == 1 then
  redis.call('HDEL', KEYS[3], ARGV[1])
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
  return 1
end
return 0
"""

_EXTEND_LEASE = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + tonumber(current[2]) / 1000000
if redis.call('HGET', KEYS[2], ARGV[1]) == ARGV[3]
  and redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[1])
  return 1
end
return 0
"""

_PUBLISH_EVENT = """
redis.call('ZADD', KEYS[1], ARGV[1], ARGV[2])
return redis.call('PUBLISH', KEYS[2], ARGV[1])
"""

_PUBLISH_CANCELLATION = """
local current = redis.call('GET', KEYS[1])
if not current or tonumber(ARGV[1]) >= tonumber(current) then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
end
return redis.call('PUBLISH', KEYS[2], ARGV[1])
"""


class RedisTaskQueue:
    def __init__(
        self,
        client: AsyncRedisClient,
        *,
        namespace: str = "harness",
        visibility_timeout_seconds: float = 60,
        retry_delay_seconds: float = 1,
    ) -> None:
        if visibility_timeout_seconds <= 0 or retry_delay_seconds < 0:
            raise ValueError("queue visibility must be positive and retry delay non-negative")
        self._client = client
        self._pending = f"{namespace}:queue:pending"
        self._ready = f"{namespace}:queue:ready"
        self._processing = f"{namespace}:queue:processing"
        self._receipt_key = f"{namespace}:queue:receipts"
        self._receipts: dict[str, str] = {}
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._retry_delay_seconds = retry_delay_seconds

    async def enqueue(self, task: RunTask) -> None:
        payload = task.model_dump_json()
        await self._client.eval(
            _ENQUEUE,
            2,
            self._pending,
            self._ready,
            payload,
        )

    async def dequeue(self) -> RunTask | None:
        receipt = uuid4().hex
        value = await self._client.eval(
            _DEQUEUE,
            4,
            self._pending,
            self._ready,
            self._processing,
            self._receipt_key,
            str(self._visibility_timeout_seconds),
            receipt,
        )
        if value is None:
            return None
        payload = value.decode() if isinstance(value, bytes) else str(value)
        self._receipts[payload] = receipt
        return RunTask.model_validate_json(payload)

    async def acknowledge(self, task: RunTask) -> None:
        payload = task.model_dump_json()
        receipt = self._receipts.pop(payload, "")
        await self._client.eval(
            _ACKNOWLEDGE,
            3,
            self._pending,
            self._processing,
            self._receipt_key,
            payload,
            receipt,
        )

    async def retry(self, task: RunTask) -> None:
        payload = task.model_dump_json()
        receipt = self._receipts.pop(payload, "")
        await self._client.eval(
            _RETRY,
            3,
            self._ready,
            self._processing,
            self._receipt_key,
            payload,
            str(self._retry_delay_seconds),
            receipt,
        )

    async def extend_lease(self, task: RunTask) -> None:
        payload = task.model_dump_json()
        receipt = self._receipts.get(payload, "")
        await self._client.eval(
            _EXTEND_LEASE,
            2,
            self._processing,
            self._receipt_key,
            payload,
            str(self._visibility_timeout_seconds),
            receipt,
        )

    async def stats(self) -> dict[str, int]:
        ready = await self._client.zcard(self._ready)
        processing = await self._client.zcard(self._processing)
        return {"ready": int(ready), "processing": int(processing)}


class RedisEventBus:
    def __init__(self, client: AsyncRedisClient, *, namespace: str = "harness") -> None:
        self._client = client
        self._namespace = namespace

    def _key(self, tenant_id: str, run_id: str) -> str:
        return f"{self._namespace}:events:{tenant_id}:{run_id}"

    def _channel(self, tenant_id: str, run_id: str) -> str:
        return f"{self._key(tenant_id, run_id)}:notify"

    async def publish(self, event: RunEvent) -> None:
        await self._client.eval(
            _PUBLISH_EVENT,
            2,
            self._key(event.tenant_id, event.run_id),
            self._channel(event.tenant_id, event.run_id),
            str(event.sequence),
            json.dumps(event.model_dump(mode="json")),
        )

    async def read(self, tenant_id: str, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
        values = await self._client.zrangebyscore(
            self._key(tenant_id, run_id), f"({after_sequence}", "+inf"
        )
        return [RunEvent.model_validate_json(value) for value in values]

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float,
    ) -> bool:
        """Wait for a signal without treating Redis as the durable event source.

        The sorted-set checks close both sides of the subscribe race. Callers
        still re-read PostgreSQL after this returns; Redis only prevents idle
        streams from polling the database continuously.
        """

        if await self.read(tenant_id, run_id, after_sequence):
            return True
        channel = self._channel(tenant_id, run_id)
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(channel)
            if await self.read(tenant_id, run_id, after_sequence):
                return True
            deadline = time.monotonic() + timeout_seconds
            while (remaining := deadline - time.monotonic()) > 0:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=remaining,
                )
                if message is not None:
                    return True
            return False
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


class RedisCancellationWakeup:
    """Low-latency cancellation notification; PostgreSQL remains authoritative."""

    def __init__(
        self,
        client: AsyncRedisClient,
        *,
        namespace: str = "harness",
        ttl_seconds: int = 86_400,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("cancellation wakeup TTL must be positive")
        self._client = client
        self._namespace = namespace
        self._ttl_seconds = ttl_seconds

    def _key(self, tenant_id: str, run_id: str) -> str:
        return f"{self._namespace}:cancel:{tenant_id}:{run_id}"

    def _channel(self, tenant_id: str, run_id: str) -> str:
        return f"{self._key(tenant_id, run_id)}:notify"

    async def _token(self, tenant_id: str, run_id: str) -> int:
        value = await self._client.get(self._key(tenant_id, run_id))
        if value is None:
            return 0
        decoded = value.decode() if isinstance(value, bytes) else value
        try:
            return int(decoded)
        except ValueError:
            return 0

    async def publish(self, tenant_id: str, run_id: str, fencing_token: int) -> None:
        await self._client.eval(
            _PUBLISH_CANCELLATION,
            2,
            self._key(tenant_id, run_id),
            self._channel(tenant_id, run_id),
            str(fencing_token),
            str(self._ttl_seconds),
        )

    async def wait(
        self,
        tenant_id: str,
        run_id: str,
        after_fencing_token: int,
        *,
        timeout_seconds: float,
    ) -> bool:
        if await self._token(tenant_id, run_id) > after_fencing_token:
            return True
        channel = self._channel(tenant_id, run_id)
        pubsub = self._client.pubsub()
        try:
            await pubsub.subscribe(channel)
            if await self._token(tenant_id, run_id) > after_fencing_token:
                return True
            deadline = time.monotonic() + timeout_seconds
            while (remaining := deadline - time.monotonic()) > 0:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=remaining,
                )
                if message is not None:
                    return True
            return False
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


_SESSION_GATE_ACQUIRE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
  return 1
end
return 0
"""

_SESSION_GATE_REFRESH_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('PEXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

_SESSION_GATE_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""


class SessionGateError(TimeoutError):
    """Raised when a session gate cannot be acquired within its wait budget."""


class RedisSessionGate:
    """Serialize same-session Runs across every worker replica.

    The in-process lock in ``worker_loop`` only orders Runs sharing one
    process. Replicas dequeue from the same Redis queue, so ordering needs a
    shared gate. Holders renew a short TTL; a dead worker's gate expires and
    the redelivered Run reclaims it after the visibility timeout, staying
    consistent with at-least-once delivery plus Run fencing.
    """

    def __init__(
        self,
        client: AsyncRedisClient,
        *,
        namespace: str = "harness",
        ttl_seconds: float = 90,
        refresh_interval_seconds: float = 30,
        poll_interval_seconds: float = 0.5,
        acquire_timeout_seconds: float = 900,
    ) -> None:
        if ttl_seconds <= 0 or refresh_interval_seconds <= 0:
            raise ValueError("session gate TTL and refresh interval must be positive")
        if poll_interval_seconds <= 0 or acquire_timeout_seconds <= 0:
            raise ValueError("session gate poll interval and timeout must be positive")
        self._client = client
        self._prefix = f"{namespace}:session-gate"
        self._ttl_ms = str(int(ttl_seconds * 1000))
        self._refresh_interval_seconds = refresh_interval_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._acquire_timeout_seconds = acquire_timeout_seconds

    def _key(self, session_key: tuple[str, str]) -> str:
        tenant_id, session_id = session_key
        return f"{self._prefix}:{tenant_id}:{session_id}"

    async def _run_script(self, script: str, key: str, token: str) -> int:
        result = await self._client.eval(script, 1, key, token, self._ttl_ms)
        return int(result)

    @asynccontextmanager
    async def acquire(self, session_key: tuple[str, str]):
        key = self._key(session_key)
        token = uuid4().hex
        deadline = time.monotonic() + self._acquire_timeout_seconds
        while True:
            if await self._run_script(_SESSION_GATE_ACQUIRE_SCRIPT, key, token):
                break
            if time.monotonic() >= deadline:
                raise SessionGateError(
                    f"session gate busy beyond {self._acquire_timeout_seconds}s: {key}"
                )
            await asyncio.sleep(self._poll_interval_seconds)

        refresh_task = asyncio.create_task(self._refresh_loop(key, token))
        try:
            yield
        finally:
            refresh_task.cancel()
            with suppress(asyncio.CancelledError):
                await refresh_task
            try:
                await self._run_script(_SESSION_GATE_RELEASE_SCRIPT, key, token)
            except Exception:  # noqa: BLE001 - release is best-effort; TTL reaps
                pass

    async def _refresh_loop(self, key: str, token: str) -> None:
        while True:
            await asyncio.sleep(self._refresh_interval_seconds)
            await self._run_script(_SESSION_GATE_REFRESH_SCRIPT, key, token)
