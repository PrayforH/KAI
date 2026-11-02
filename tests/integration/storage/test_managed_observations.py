"""Read-only subscriptions consume source events, never dispatch replacement Runs."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from harness.assistant.models import (
    DelegationMode,
    ManagedTask,
    ManagedTaskRole,
    ObjectiveAction,
    ObjectiveStatus,
)
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.events import RunEvent
from harness.core.models import Run, RunStatus, Session
from harness.storage.models import AguiThreadBindingRow, RunRow, SessionRow
from harness.storage.repositories import PostgresEventRepository
from tests.integration.storage.conftest import DatabaseFixture
from tests.integration.storage.test_delegation_real_gateway import _StubAgui, brief, make_service


async def prepare(database: DatabaseFixture, *, subscribe: bool = True):
    service = make_service(database)
    agui = _StubAgui()
    agui.get_thread_record = AsyncMock(return_value=SimpleNamespace(title="原任务成果"))
    service._agui = cast(Any, agui)
    service._events = PostgresEventRepository(database[1])
    obj = await service.create(
        "tenant-a",
        "user-a",
        brief(schedule=None),
        managed_thread_id="original",
        mode=DelegationMode.FOLLOW_ONLY if subscribe else DelegationMode.AUTONOMOUS,
    )
    task = (await service.repository.managed_bindings("tenant-a", "user-a"))[0]
    return service, obj, task


async def source(database: DatabaseFixture, run_id: str, *, owner: str = "user-a"):
    now = datetime.now(UTC)
    session = Session(
        session_id="session-for-original",
        tenant_id="tenant-a",
        user_id=owner,
        agent_name="audit-agent",
        agent_version="1.0.0",
        created_at=now,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    run = Run(
        run_id=run_id,
        tenant_id="tenant-a",
        session_id=session.session_id,
        status=RunStatus.SUCCEEDED,
        idempotency_key=run_id,
        input={"prompt": "请完成尚未做的事"},
        created_at=now,
        updated_at=now,
    )
    async with database[1]() as db, db.begin():
        await db.merge(
            SessionRow(
                tenant_id="tenant-a",
                session_id=session.session_id,
                user_id=owner,
                payload=session.model_dump(mode="json"),
            )
        )
        await db.merge(
            AguiThreadBindingRow(
                tenant_id="tenant-a",
                user_id=owner,
                thread_id="original",
                session_id=session.session_id,
                payload={},
            )
        )
        db.add(
            RunRow(
                tenant_id="tenant-a",
                run_id=run_id,
                session_id=session.session_id,
                idempotency_key=run_id,
                status=run.status.value,
                updated_at=now,
                payload=run.model_dump(mode="json"),
            )
        )
    return run


def event(run: Run, seq: int, kind: str, at: datetime, **payload):
    return RunEvent(
        event_id=f"{run.run_id}-{seq}",
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        session_id=run.session_id,
        sequence=seq,
        type=kind,
        timestamp=at,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_result_and_artifact_events_coalesce_and_duplicate_delivery_keeps_read_state(
    database,
):
    service, obj, task = await prepare(database)
    assert task.role is ManagedTaskRole.OBSERVE and task.notify_on_progress
    run = await source(database, "run-original")
    at = datetime.now(UTC) + timedelta(seconds=1)
    ready = event(run, 1, "artifact.ready", at, artifact_id="file-one")
    await service._events.append(ready)
    await service.observe_run_event(ready)
    first = (await service.repository.inbox("tenant-a", "user-a"))[0]
    assert first.kind == "artifact_ready" and first.run_id == run.run_id
    answer = event(run, 2, "message.delta", at, text="真实结果：文件已生成。")
    done = event(run, 3, "run.succeeded", at)
    await service._events.append(answer)
    await service._events.append(done)
    await service.observe_run_event(done)
    notices = await service.repository.inbox("tenant-a", "user-a")
    assert len(notices) == 1 and notices[0].item_id == first.item_id
    assert notices[0].content == "真实结果：文件已生成。"
    assert notices[0].source_sequence == 3 and notices[0].source_event_id == done.event_id
    await service.repository.read_notice("tenant-a", "user-a", first.item_id, now=at)
    await service.observe_run_event(ready)
    assert (await service.repository.inbox("tenant-a", "user-a"))[0].read_at == at
    assert await service.repository.pending_managed_events() == []
    assert await service.repository.executions("tenant-a", "user-a", obj.objective_id) == []


@pytest.mark.asyncio
async def test_source_log_repairs_missed_fanout_and_late_commit_in_another_run(database):
    service, obj, _ = await prepare(database)
    at = datetime.now(UTC) + timedelta(seconds=1)
    newer = await source(database, "run-newer")
    done = event(newer, 1, "run.failed", at + timedelta(seconds=2))
    await service._events.append(done)
    await service.process_once()
    assert len(await service.repository.inbox("tenant-a", "user-a")) == 1
    older = await source(database, "run-late-commit")
    late = event(older, 1, "run.succeeded", at)
    await service._events.append(late)
    await service.process_once()
    assert len(await service.repository.inbox("tenant-a", "user-a")) == 2
    await service.process_once()
    assert len(await service.repository.inbox("tenant-a", "user-a")) == 2
    assert await service.repository.executions("tenant-a", "user-a", obj.objective_id) == []


@pytest.mark.asyncio
async def test_legacy_opt_out_and_explicit_enable_has_cas_and_no_historical_backfill(database):
    service, obj, task = await prepare(database, subscribe=False)
    run = await source(database, "run-old")
    old = event(run, 1, "run.succeeded", datetime.now(UTC))
    await service._events.append(old)
    await service.observe_run_event(old)
    assert await service.repository.pending_managed_events() == []
    assert await service.repository.inbox("tenant-a", "user-a") == []
    now = old.timestamp + timedelta(seconds=1)
    updated = await service.repository.configure_managed_notifications(
        "tenant-a",
        "user-a",
        obj.objective_id,
        task.managed_id,
        enabled=True,
        expected_revision=obj.revision,
        now=now,
    )
    assert updated.notify_since == now
    await service.observe_run_event(old)
    assert await service.repository.pending_managed_events() == []
    with pytest.raises(ConflictError):
        await service.repository.configure_managed_notifications(
            "tenant-a",
            "user-a",
            obj.objective_id,
            task.managed_id,
            enabled=False,
            expected_revision=obj.revision,
            now=now,
        )
    with pytest.raises(NotFoundError):
        await service.repository.configure_managed_notifications(
            "tenant-a",
            "other",
            obj.objective_id,
            task.managed_id,
            enabled=False,
            expected_revision=obj.revision + 1,
            now=now,
        )
    fresh = event(run, 2, "artifact.ready", now + timedelta(seconds=1))
    await service._events.append(fresh)
    await service.observe_run_event(fresh)
    # A later artifact must not downgrade a known terminal result.
    notice = (await service.repository.inbox("tenant-a", "user-a"))[0]
    assert notice.kind == "succeeded" and notice.source_sequence == 2


@pytest.mark.asyncio
async def test_paused_objective_and_revoked_access_never_dispatch_or_leak_results(database):
    service, obj, task = await prepare(database)
    run = await source(database, "run-permission")
    at = datetime.now(UTC) + timedelta(seconds=1)
    done = event(run, 1, "run.succeeded", at)
    await service._events.append(done)
    paused = await service.repository.update(
        "tenant-a",
        "user-a",
        obj.objective_id,
        expected_revision=obj.revision,
        now=at,
        action=ObjectiveAction.PAUSE,
    )
    assert paused.status is ObjectiveStatus.PAUSED
    assert await service.repository.pending_managed_events() == []
    await service.observe_run_event(done)
    assert await service.repository.inbox("tenant-a", "user-a") == []
    await service.repository.update(
        "tenant-a",
        "user-a",
        obj.objective_id,
        expected_revision=paused.revision,
        now=at,
        action=ObjectiveAction.RESUME,
    )
    service._auth = cast(
        Any, SimpleNamespace(profile=AsyncMock(side_effect=PermissionDeniedError("revoked")))
    )
    await service.observe_run_event(done)
    assert await service.repository.inbox("tenant-a", "user-a") == []
    saved = (await service.repository.managed_bindings("tenant-a", "user-a"))[0]
    assert not saved.notify_on_progress and saved.notification_error
    assert await service.repository.pending_managed_events() == []
    assert await service.repository.executions("tenant-a", "user-a", obj.objective_id) == []


@pytest.mark.asyncio
async def test_source_session_owner_must_match_subscription_and_multiple_observers_are_independent(
    database,
):
    service, obj, task = await prepare(database)
    run = await source(database, "run-other-user", owner="other")
    done = event(run, 1, "run.succeeded", datetime.now(UTC) + timedelta(seconds=1))
    await service._events.append(done)
    assert await service.repository.managed_event_targets(done) == []
    assert await service.repository.pending_managed_events() == []
    await service.observe_run_event(done)
    assert await service.repository.inbox("tenant-a", "user-a") == []
    second = await service.create("tenant-a", "user-a", brief(schedule=None))
    another, created = await service.repository.bind_managed(
        ManagedTask(
            **(
                task.model_dump()
                | {"managed_id": "second-watch", "objective_id": second.objective_id}
            )
        )
    )
    assert created and another.objective_id != obj.objective_id


@pytest.mark.asyncio
async def test_concurrent_live_and_repair_consumers_produce_one_card(database):
    import asyncio

    service, obj, _ = await prepare(database)
    run = await source(database, "run-concurrent")
    done = event(run, 1, "run.succeeded", datetime.now(UTC) + timedelta(seconds=1))
    await service._events.append(done)
    await asyncio.gather(*(service.observe_run_event(done) for _ in range(8)))
    assert len(await service.repository.inbox("tenant-a", "user-a")) == 1
    assert await service.repository.pending_managed_events() == []
    assert await service.repository.executions("tenant-a", "user-a", obj.objective_id) == []


@pytest.mark.asyncio
async def test_retained_owned_session_events_follow_the_original_thread_after_rebase(database):
    service, _, _ = await prepare(database)
    run = await source(database, "run-retained")
    async with database[1]() as db, db.begin():
        row = await db.get(AguiThreadBindingRow, ("tenant-a", "user-a", "original"))
        row.session_id = "session-rebased"
        row.payload = {"previous_session_ids": [run.session_id]}
    done = event(run, 1, "run.succeeded", datetime.now(UTC) + timedelta(seconds=1))
    await service._events.append(done)
    assert await service.repository.pending_managed_events() == [done]
    await service.observe_run_event(done)
    notice = (await service.repository.inbox("tenant-a", "user-a"))[0]
    assert notice.thread_id == "original" and notice.run_id == run.run_id


@pytest.mark.asyncio
async def test_own_objective_execution_keeps_its_existing_canonical_notice(database):
    from harness.assistant.models import ObjectiveExecutionStatus

    service, obj, task = await prepare(database, subscribe=False)
    await service.repository.configure_managed_notifications(
        "tenant-a",
        "user-a",
        obj.objective_id,
        task.managed_id,
        enabled=True,
        expected_revision=obj.revision,
        now=datetime.now(UTC),
    )
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", obj.objective_id, trigger_key="explicit-once", now=datetime.now(UTC)
    )
    claim = await service.repository.claim(datetime.now(UTC))
    run = await source(database, "run-objective-owned")
    linked = execution.model_copy(update={"run_id": run.run_id})
    assert await service.repository.save_execution(linked, claim[1], datetime.now(UTC))
    done = event(run, 1, "run.succeeded", datetime.now(UTC) + timedelta(seconds=1))
    await service._events.append(done)
    await service.observe_run_event(done)
    assert await service.repository.pending_managed_events() == []
    assert await service.repository.inbox("tenant-a", "user-a") == []
    claim = await service.repository.claim(datetime.now(UTC) + timedelta(seconds=6))
    finished = linked.model_copy(
        update={
            "status": ObjectiveExecutionStatus.SUCCEEDED,
            "finished_at": datetime.now(UTC),
        }
    )
    assert await service.repository.save_execution(
        finished, claim[1], datetime.now(UTC) + timedelta(seconds=6)
    )
    notices = await service.repository.inbox("tenant-a", "user-a")
    assert len(notices) == 1 and notices[0].execution_id == execution.execution_id
