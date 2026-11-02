import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.assistant.lifecycle import next_check
from harness.assistant.models import (
    Objective,
    ObjectiveAction,
    ObjectiveBrief,
    ObjectiveExecutionStatus,
    ObjectiveSchedule,
    ObjectiveStatus,
    Specialist,
)
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.errors import ConflictError, NotFoundError
from tests.integration.storage.conftest import DatabaseFixture

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_declared_capabilities_gate_durable_intent_creation(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    bot = await repo.create_specialist(Specialist(
        specialistId="bot", tenantId="tenant-a", userId="user-a", slug="bot", name="Bot",
        description="test", agentName="audit-agent", agentVersion="1.0.0",
        config={"required_local_tools": ["run_commands"]}, createdAt=NOW, updatedAt=NOW,
    ))
    await repo.create(objective().model_copy(update={"specialist_id": bot.specialist_id}))
    with pytest.raises(ConflictError, match="待授权"):
        await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="grant-test", now=NOW)
    assert await repo.executions("tenant-a", "user-a", "objective-a") == []
    await repo.update_specialist(
        "tenant-a", "user-a", "bot", expected_revision=bot.revision,
        config={"local_tool_permissions": ["run_commands"]}, now=NOW,
    )
    execution = await repo.enqueue(
        "tenant-a", "user-a", "objective-a", trigger_key="grant-test", now=NOW
    )
    assert execution.status is ObjectiveExecutionStatus.PENDING


def objective() -> Objective:
    schedule = ObjectiveSchedule(intervalMinutes=60)
    return Objective(
        objectiveId="objective-a",
        tenantId="tenant-a",
        userId="user-a",
        title="整改跟进",
        instructions="检查新上传的整改材料，列出待补证据。",
        agentName="audit-agent",
        agentVersion="1.0.0",
        schedule=schedule,
        nextCheckAt=next_check(schedule, after=NOW, ends_at=None),
        createdAt=NOW,
        updatedAt=NOW,
    )


@pytest.mark.asyncio
async def test_objective_is_private_in_every_read_and_write(database: DatabaseFixture) -> None:
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    for tenant, user in (("tenant-b", "user-a"), ("tenant-a", "user-b")):
        assert await repo.list(tenant, user) == []
        for read in (repo.get, repo.events, repo.executions):
            with pytest.raises(NotFoundError):
                await read(tenant, user, "objective-a")
        with pytest.raises(NotFoundError):
            await repo.update(
                tenant,
                user,
                "objective-a",
                expected_revision=1,
                now=NOW,
                action=ObjectiveAction.STOP,
            )
        with pytest.raises(NotFoundError):
            await repo.enqueue(tenant, user, "objective-a", trigger_key="forged", now=NOW)
    assert (await repo.get("tenant-a", "user-a", "objective-a")).status is ObjectiveStatus.ACTIVE


@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_share_one_durable_intent(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    attempts = await asyncio.gather(
        *[
            repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="same-event", now=NOW)
            for _ in range(8)
        ]
    )
    assert len({item.execution_id for item in attempts}) == 1
    assert len(await repo.executions("tenant-a", "user-a", "objective-a")) == 1
    with pytest.raises(ConflictError, match="active execution"):
        await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="another", now=NOW)


@pytest.mark.asyncio
async def test_stale_revision_cannot_overwrite_user_changes(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    updated = await repo.update(
        "tenant-a", "user-a", "objective-a", expected_revision=1, now=NOW, note="优先检查采购制度"
    )
    assert updated.revision == 2
    with pytest.raises(ConflictError, match="changed"):
        await repo.update(
            "tenant-a", "user-a", "objective-a", expected_revision=1, now=NOW, note="旧页面要求"
        )
    events = await repo.events("tenant-a", "user-a", "objective-a")
    assert [item.content for item in events if item.kind == "note"] == ["优先检查采购制度"]


@pytest.mark.asyncio
async def test_execution_keeps_its_original_brief_and_notes(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    original = objective()
    await repo.create(original)
    await repo.update(
        "tenant-a", "user-a", "objective-a", expected_revision=1, now=NOW, note="只处理采购制度"
    )
    first = await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="event-1", now=NOW)
    brief = ObjectiveBrief.model_validate(
        original.model_dump(include=set(ObjectiveBrief.model_fields))
        | {"instructions": "改为检查全部制度"}
    )
    await repo.update(
        "tenant-a", "user-a", "objective-a", expected_revision=2, now=NOW, brief=brief
    )
    recovered = (await repo.executions("tenant-a", "user-a", "objective-a"))[0]
    assert recovered == first
    assert recovered.objective_revision == 2
    assert recovered.brief.instructions == original.instructions
    assert recovered.context == "只处理采购制度"


@pytest.mark.asyncio
async def test_pause_and_stop_remove_future_wakeups(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    paused = await repo.update(
        "tenant-a",
        "user-a",
        "objective-a",
        expected_revision=1,
        now=NOW,
        action=ObjectiveAction.PAUSE,
    )
    assert paused.next_check_at is None
    with pytest.raises(ConflictError, match="active"):
        await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="paused", now=NOW)
    resumed = await repo.update(
        "tenant-a",
        "user-a",
        "objective-a",
        expected_revision=2,
        now=NOW + timedelta(days=2),
        action=ObjectiveAction.RESUME,
    )
    assert resumed.next_check_at == NOW + timedelta(days=2, hours=1)
    stopped = await repo.update(
        "tenant-a",
        "user-a",
        "objective-a",
        expected_revision=3,
        now=NOW + timedelta(days=2),
        action=ObjectiveAction.STOP,
    )
    assert stopped.status is ObjectiveStatus.CANCELLED
    assert stopped.next_check_at is None
    with pytest.raises(ConflictError):
        await repo.update(
            "tenant-a",
            "user-a",
            "objective-a",
            expected_revision=4,
            now=NOW,
            action=ObjectiveAction.RESUME,
        )


@pytest.mark.asyncio
async def test_scheduler_workers_enqueue_once_and_skip_missed_intervals(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    later = NOW + timedelta(days=2)
    await asyncio.gather(*[repo.schedule_due(later) for _ in range(8)])
    runs = await repo.executions("tenant-a", "user-a", "objective-a")
    assert len(runs) == 1
    assert (await repo.get("tenant-a", "user-a", "objective-a")).next_check_at == later + timedelta(
        hours=1
    )
    await repo.schedule_due(later + timedelta(hours=1))
    assert len(await repo.executions("tenant-a", "user-a", "objective-a")) == 1


@pytest.mark.asyncio
async def test_expired_worker_cannot_write_and_result_is_private(database: DatabaseFixture):
    from harness.assistant.models import ObjectiveExecutionStatus

    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())
    await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="test", now=NOW)
    first = await repo.claim(NOW)
    assert first is not None
    assert await repo.claim(NOW) is None
    later = NOW + timedelta(seconds=61)
    second = await repo.claim(later)
    assert second is not None and second[1] != first[1]
    finished = second[0].model_copy(
        update={
            "status": ObjectiveExecutionStatus.SUCCEEDED,
            "finished_at": later,
            "summary": "已完成本次检查",
        }
    )
    assert not await repo.save_execution(finished, first[1], later)
    assert await repo.save_execution(finished, second[1], later)
    assert await repo.claim(later) is None
    notices = await repo.inbox("tenant-a", "user-a")
    assert len(notices) == 1
    assert await repo.inbox("tenant-a", "user-b") == []
    with pytest.raises(NotFoundError):
        await repo.read_notice("tenant-a", "user-b", notices[0].item_id, later)
    await repo.read_notice("tenant-a", "user-a", notices[0].item_id, later)
    assert (await repo.inbox("tenant-a", "user-a"))[0].read_at == later
    assert (await repo.get("tenant-a", "user-a", "objective-a")).status is ObjectiveStatus.ACTIVE


@pytest.mark.asyncio
async def test_terminal_notice_carries_checkpoint_metering(database: DatabaseFixture) -> None:
    """终态 inbox 与同次 checkpoint 同口径：有量带 tokens/cost/耗时，未知不冒充 0。"""
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())

    await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="measured", now=NOW)
    claim = await repo.claim(NOW)
    assert claim is not None
    finished = claim[0].model_copy(
        update={
            "status": ObjectiveExecutionStatus.SUCCEEDED,
            "finished_at": NOW + timedelta(seconds=45),
            "summary": "已完成本次检查",
        }
    )
    assert await repo.save_execution(
        finished,
        claim[1],
        NOW + timedelta(seconds=45),
        usage_tokens=1234,
        usage_cost=0.02,
    )

    # 第二次执行没有任何用量：unknownUsage=True，tokens/cost 保持 None。
    await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="unknown", now=NOW)
    claim_unknown = await repo.claim(NOW + timedelta(seconds=61))
    assert claim_unknown is not None
    finished_unknown = claim_unknown[0].model_copy(
        update={
            "status": ObjectiveExecutionStatus.SUCCEEDED,
            "finished_at": NOW + timedelta(seconds=100),
            "summary": "本次未提供用量",
        }
    )
    assert await repo.save_execution(
        finished_unknown, claim_unknown[1], NOW + timedelta(seconds=100)
    )

    notices = {notice.execution_id: notice for notice in await repo.inbox("tenant-a", "user-a")}
    measured = notices[finished.execution_id]
    assert measured.run_id is None  # 没有 Run 的结算不伪造 runId
    assert measured.metrics is not None
    assert measured.metrics.tokens == 1234
    assert measured.metrics.cost_usd == 0.02
    assert measured.metrics.unknown_usage is False
    assert measured.duration_seconds == 45

    unknown = notices[finished_unknown.execution_id]
    assert unknown.metrics is not None
    assert unknown.metrics.tokens is None
    assert unknown.metrics.cost_usd is None
    assert unknown.metrics.unknown_usage is True
    assert unknown.duration_seconds == 100


@pytest.mark.asyncio
async def test_failed_notice_projects_run_and_execution_for_replay(
    database: DatabaseFixture,
) -> None:
    """失败 notice 带 runId/executionId，前端才能深链到失败检查点；无 run 时不伪造。"""
    repo = PostgresAssistantRepository(database[1])
    await repo.create(objective())

    await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="replay", now=NOW)
    claim = await repo.claim(NOW)
    assert claim is not None
    finished = claim[0].model_copy(
        update={
            "status": ObjectiveExecutionStatus.FAILED,
            "run_id": "run-replay-1",
            "finished_at": NOW + timedelta(seconds=30),
            "summary": "沙箱启动失败，未执行任何步骤",
        }
    )
    assert await repo.save_execution(finished, claim[1], NOW + timedelta(seconds=30))
    notice = (await repo.inbox("tenant-a", "user-a"))[0]
    assert notice.run_id == "run-replay-1"
    assert notice.execution_id == finished.execution_id
    assert notice.thread_id == finished.thread_id
    assert notice.kind == "failed"


@pytest.mark.asyncio
async def test_agent_or_project_cannot_change_under_existing_thread(database: DatabaseFixture):
    repo = PostgresAssistantRepository(database[1])
    current = objective()
    await repo.create(current)
    await repo.enqueue("tenant-a", "user-a", "objective-a", trigger_key="test", now=NOW)
    changed = ObjectiveBrief.model_validate(
        current.model_dump(include=set(ObjectiveBrief.model_fields)) | {"project_id": "new-project"}
    )
    with pytest.raises(ConflictError, match="agent or project"):
        await repo.update(
            "tenant-a", "user-a", "objective-a", expected_revision=1, now=NOW, brief=changed
        )
