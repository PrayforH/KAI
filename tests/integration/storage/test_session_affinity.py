"""Real Redis coverage for bounded worker preference and safe lease failover."""
import asyncio
import os
from typing import cast
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from harness.core.ports import RunTask
from harness.storage.redis import AsyncRedisClient, RedisTaskQueue


@pytest.mark.asyncio
async def test_affinity_reuses_worker_without_blocking_other_sessions_or_failover() -> None:
    client = Redis.from_url(os.getenv('HARNESS_TEST_REDIS_BASE_URL', 'redis://localhost:6379'))
    namespace = f'affinity-test-{uuid4().hex}'
    owner = RedisTaskQueue(cast(AsyncRedisClient, client), namespace=namespace,
                          session_affinity_seconds=.15, session_affinity_ttl_seconds=1)
    peer = RedisTaskQueue(cast(AsyncRedisClient, client), namespace=namespace,
                         session_affinity_seconds=.15, session_affinity_ttl_seconds=1)
    def task(run: str, session: str = 'session', tenant: str = 'tenant') -> RunTask:
        return RunTask(tenant_id=tenant, run_id=run, session_id=session)
    try:
        first = task('first')
        await owner.enqueue(first)
        assert await owner.dequeue() == first
        await owner.acknowledge(first)
        second = task('second')
        await peer.enqueue(second)
        assert await peer.dequeue() is None
        unrelated = task('unrelated', tenant='other-tenant')
        await peer.enqueue(unrelated)
        assert await peer.dequeue() == unrelated
        await peer.acknowledge(unrelated)
        assert await owner.dequeue() == second
        await owner.acknowledge(second)
        third = task('owner-down')
        await owner.enqueue(third)
        assert await peer.dequeue() is None
        await asyncio.sleep(.18)
        assert await peer.dequeue() == third
        await peer.acknowledge(third)
        fourth = task('new-owner')
        await owner.enqueue(fourth)
        assert await owner.dequeue() is None
        assert await peer.dequeue() == fourth
        await peer.acknowledge(fourth)
        assert await peer.stats() == {'ready': 0, 'processing': 0}
    finally:
        keys = [key async for key in client.scan_iter(match=f'{namespace}:*')]
        if keys:
            await client.delete(*keys)
        await client.aclose()


@pytest.mark.asyncio
async def test_affinity_preserves_visibility_recovery_and_stale_ack_fencing() -> None:
    client = Redis.from_url(os.getenv('HARNESS_TEST_REDIS_BASE_URL', 'redis://localhost:6379'))
    namespace = f'affinity-test-{uuid4().hex}'
    settings = dict(namespace=namespace, visibility_timeout_seconds=.1,
                    session_affinity_seconds=.04, session_affinity_ttl_seconds=.1)
    owner = RedisTaskQueue(cast(AsyncRedisClient, client), **settings)
    peer = RedisTaskQueue(cast(AsyncRedisClient, client), **settings)
    task = RunTask(tenant_id='tenant', run_id='lost', session_id='session')
    try:
        await owner.enqueue(task)
        assert await owner.dequeue() == task
        await asyncio.sleep(.15)
        assert await peer.dequeue() == task
        await owner.acknowledge(task)
        assert await peer.stats() == {'ready': 0, 'processing': 1}
        await peer.acknowledge(task)
        assert await peer.stats() == {'ready': 0, 'processing': 0}
        legacy = RunTask(tenant_id='tenant', run_id='legacy')
        await peer.enqueue(legacy)
        assert await owner.dequeue() == legacy
        await owner.acknowledge(legacy)
    finally:
        keys = [key async for key in client.scan_iter(match=f'{namespace}:*')]
        if keys:
            await client.delete(*keys)
        await client.aclose()
