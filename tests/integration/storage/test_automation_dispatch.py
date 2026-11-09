"""Atomic scheduling and recovery on PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.automations.models import AutomationDispatch, AutomationSchedule, AutomationTask
from harness.automations.pg_repositories import PostgresAutomationTaskRepository
from tests.integration.storage.conftest import DatabaseFixture


@pytest.mark.asyncio
async def test_due_claim_persists_one_dispatch_across_worker_restart(
    database: DatabaseFixture,
) -> None:
    now = datetime(2026, 11, 9, 8, tzinfo=UTC)
    tasks = PostgresAutomationTaskRepository(database[1])
    task = AutomationTask(
        tenantId="tenant", userId="owner", taskId="automation-a", name="独立计划", prompt="运行",
        schedule=AutomationSchedule(at=now), nextRunAt=now, revision=1,
        createdAt=now - timedelta(hours=1), updatedAt=now - timedelta(hours=1),
    )
    await tasks.add(task)
    edited = task.model_copy(update={"revision": 2, "prompt": "新计划", "updated_at": now})
    await tasks.replace(1, edited)

    async def enqueue(snapshot: AutomationTask) -> bool:
        return await tasks.claim_due(
            AutomationDispatch(
                dispatch_id="automationrun-one", task=snapshot, scheduled_at=now,
                agent_version="1.2.3",
            ),
            next_run_at=None, status="expired", last_run_at=now,
        )

    assert not await enqueue(task)
    assert sum(await asyncio.gather(*(enqueue(edited) for _ in range(6)))) == 1
    assert await tasks.list_due(now, limit=20) == []
    lease = await tasks.claim_dispatch(now)
    assert lease is not None and lease[0].task == edited
    assert lease[0].agent_version == "1.2.3"

    restarted = PostgresAutomationTaskRepository(database[1])
    assert await restarted.claim_dispatch(now + timedelta(seconds=30)) is None
    later = now + timedelta(seconds=61)
    recovered = await restarted.claim_dispatch(later)
    assert recovered is not None and recovered[0] == lease[0] and recovered[1] != lease[1]
    await tasks.finish_dispatch(lease[0].dispatch_id, lease[1])
    assert await restarted.claim_dispatch(later + timedelta(seconds=30)) is None
    final_time = later + timedelta(seconds=61)
    final = await restarted.claim_dispatch(final_time)
    assert final is not None and final[0] == recovered[0]
    await restarted.finish_dispatch(final[0].dispatch_id, final[1])
    assert await restarted.claim_dispatch(final_time + timedelta(minutes=2)) is None
