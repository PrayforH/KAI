"""Real PostgreSQL broadcasts, membership, read-only artifact pagination and retry recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.group_delivery import GROUP_FANOUT_LIMIT, reconcile_group_summaries
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.service import AssistantService
from harness.core.errors import PermissionDeniedError
from harness.core.models import Artifact, ArtifactStatus, Run, RunStatus
from harness.storage.models import SessionRow
from harness.storage.platform_repositories import PostgresArtifactRepository
from harness.storage.repositories import PostgresRunRepository
from tests.integration.storage.test_assistant_group_delegate import setup_pg
from tests.unit.assistant.test_group_chat import _service, _specialist


async def broadcast_pg(database, count=2):
    service, _, agui = _service()
    service.repository = PostgresAssistantRepository(database[1])
    group = await service.create_assistant_group("t", "u", name="广播实库")
    for i in range(count):
        await service.repository.create_specialist(_specialist(f"specialist_{i}", f"专家{i}"))
        await service.add_assistant_group_member(
            "t", "u", group.group_id, specialist_id=f"specialist_{i}"
        )
    runs = PostgresRunRepository(database[1])
    service._runs = runs

    async def create(**kwargs):
        key = kwargs["request"].run_id
        run = Run(
            run_id=key,
            tenant_id="t",
            session_id=kwargs["request"].thread_id,
            idempotency_key=key,
            status=RunStatus.RUNNING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            input={"group_context": kwargs["group_context"]},
        )
        previous = await runs.find_by_idempotency_key("t", run.session_id, key)
        if previous is None:
            async with database[1]() as db:
                if await db.get(SessionRow, ("t", run.session_id)) is None:
                    db.add(
                        SessionRow(
                            tenant_id="t", session_id=run.session_id, user_id="u", payload={}
                        )
                    )
                    await db.commit()
            await runs.add(run)
        return SimpleNamespace(run=previous or run)

    agui.create_run_with_result.side_effect = create
    return service, group, agui


@pytest.mark.asyncio
async def test_broadcast_two_services_concurrent_replay(database):
    service, group, agui = await broadcast_pg(database)
    other = service.with_repository(PostgresAssistantRepository(database[1]))
    results = await asyncio.gather(
        *[
            s.post_assistant_group_message(
                "t",
                "u",
                group.group_id,
                text="＠ 所有人 @everyone 请给结论",
                client_message_id="one-message",
            )
            for s in (service, other)
        ]
    )
    assert results[0].message.message_id == results[1].message.message_id
    assert agui.create_run_with_result.await_count == 3
    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    assert len([m for m in messages if m.run_id]) == 3
    assert len([m for m in messages if m.delivery_batch_id and m.delivery_order is not None]) == 3
    batch = await other.get_assistant_group_delivery(
        "t", "u", group.group_id, results[0].message.message_id
    )
    assert len(batch.targets) == 3
    assert all(t.state.value == "running" for t in batch.targets)


@pytest.mark.asyncio
async def test_broadcast_limit_and_removed_paused_members(database):
    service, group, agui = await broadcast_pg(database, GROUP_FANOUT_LIMIT)
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@everyone 做事"
    )
    assert "整批未派发" in result.notices[-1]
    agui.create_run_with_result.assert_not_awaited()
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="specialist_0")
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@everyone 做事"
    )
    assert agui.create_run_with_result.await_count == GROUP_FANOUT_LIMIT
    batch = await service.get_assistant_group_delivery(
        "t", "u", group.group_id, result.message.message_id
    )
    assert "specialist_0" not in [t.member.member_id for t in batch.targets]


@pytest.mark.asyncio
async def test_crash_after_run_acceptance_replays_same_id(database):
    service, group, agui = await broadcast_pg(database, 1)
    original_save = PostgresAssistantRepository.save_group_delivery

    async def fail(*args, **kwargs):
        raise RuntimeError("crash before commit")

    PostgresAssistantRepository.save_group_delivery = fail
    try:
        with pytest.raises(RuntimeError):
            await service.post_assistant_group_message(
                "t", "u", group.group_id, text="@所有人 做事", client_message_id="crash"
            )
    finally:
        PostgresAssistantRepository.save_group_delivery = original_save
    assert await service.repository.list_group_messages("t", "u", group.group_id) == []
    await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 做事", client_message_id="crash"
    )
    runs = await service._runs.list_for_sessions(
        "t", [f"assistant_group_{group.group_id}_specialist_0"], limit=100
    )
    assert len(runs) == 1


@pytest.mark.asyncio
async def test_delegate_batch_persists_results_and_membership_denials(database):
    service, group, posted, run, session, events = await setup_pg(database)
    delivery = await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    service._events = events
    batch = await service.get_assistant_group_delivery(
        "t", "u", group.group_id, posted.message.message_id
    )
    assert batch.batch_id == delivery["deliveryBatchId"]
    assert batch.targets[0].result.delivery_member_id == "specialist_pm"
    timeline = await service.list_assistant_group_messages("t", "u", group.group_id)
    header = next(m for m in timeline if m.author.value == "primary")
    child = next(m for m in timeline if m.author.value == "specialist")
    assert header.delivery_batch_id == child.delivery_batch_id == batch.batch_id
    assert child.delivery_order == 0
    for tenant, user in [("t", "outsider"), ("other", "u")]:
        with pytest.raises(PermissionDeniedError):
            await service.get_assistant_group_delivery(
                tenant, user, group.group_id, posted.message.message_id
            )
        with pytest.raises(PermissionDeniedError):
            await service.list_assistant_group_artifacts(tenant, user, group.group_id)


@pytest.mark.asyncio
async def test_group_artifacts_page_time_and_no_private_fields(database):
    service, group, posted, run, session, events = await setup_pg(database)
    delivery = await service.delegate_assistant_group(run, session, specialist_id="specialist_pm")
    child = await service._runs.get("t", delivery["runId"])
    now = datetime.now(UTC)
    async with database[1]() as db:
        db.add(SessionRow(tenant_id="t", session_id=child.session_id, user_id="u", payload={}))
        await db.commit()
    artifacts = PostgresArtifactRepository(database[1])
    for i in range(3):
        artifact = Artifact(
            artifact_id=f"artifact_{i}",
            run_id=child.run_id,
            tenant_id="t",
            name=f"产物{i}.pdf",
            media_type="application/pdf",
            status=ArtifactStatus.READY,
            object_key=f"private/{i}",
            size_bytes=i + 10,
        )
        await artifacts.add(artifact)
        await events.append(
            tenant_id="t",
            run_id=child.run_id,
            session_id=child.session_id,
            event_type="artifact.ready",
            payload=artifact.model_dump(mode="json"),
        )
    page = await service.list_assistant_group_artifacts("t", "u", group.group_id, limit=1)
    assert len(page.items) == 1
    assert page.next_offset == 1
    second = await service.list_assistant_group_artifacts(
        "t", "u", group.group_id, limit=2, offset=1
    )
    assert len(second.items) == 2 and second.next_offset is None
    assert len({page.items[0].artifact_id, *(i.artifact_id for i in second.items)}) == 3
    assert all(i.member_id == "specialist_pm" for i in second.items)
    assert all(i.producer_name == "项目助理" for i in second.items)
    assert all(i.created_at is not None for i in second.items)
    assert "object_key" not in page.model_dump_json() and "private/" not in page.model_dump_json()
    assert (
        await service.list_assistant_group_artifacts(
            "t", "u", group.group_id, since=now + timedelta(days=1)
        )
    ).items == ()
    # Removing the producing assistant does not delete existing shared products.
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="specialist_pm")
    assert len((await service.list_assistant_group_artifacts("t", "u", group.group_id)).items) == 3
    # A personal run must not leak into group history.
    private = child.model_copy(
        update={"run_id": "private-run", "idempotency_key": "private", "input": {}}
    )
    await service._runs.add(private)
    await artifacts.add(
        Artifact(
            artifact_id="private-artifact",
            run_id=private.run_id,
            tenant_id="t",
            name="secret.txt",
            media_type="text/plain",
            status=ArtifactStatus.READY,
            object_key="private",
        )
    )
    assert len((await service.list_assistant_group_artifacts("t", "u", group.group_id)).items) == 3


@pytest.mark.asyncio
async def test_delivery_migration_upgrade_downgrade_roundtrip(database):
    import importlib

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text

    revision = importlib.import_module("migrations.versions.0077_group_deliveries")

    def check(connection):
        connection.execute(text("DROP TABLE assistant_group_deliveries"))
        with Operations.context(MigrationContext.configure(connection)):
            revision.upgrade()
            indexes = {
                i["name"] for i in inspect(connection).get_indexes("assistant_group_deliveries")
            }
            assert indexes == {"ix_group_deliveries_source", "ix_group_deliveries_due"}
            revision.downgrade()
            assert "assistant_group_deliveries" not in inspect(connection).get_table_names()
            revision.upgrade()

    async with database[0].begin() as connection:
        await connection.run_sync(check)


@pytest.mark.asyncio
async def test_summary_failures_rotate_past_25_batches(database, monkeypatch):
    service, group, agui = await broadcast_pg(database, 1)
    posted = [
        await service.post_assistant_group_message(
            "t", "u", group.group_id, text="@所有人 检查", client_message_id=f"rotate-{i}"
        )
        for i in range(26)
    ]
    service._runs = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake"))
    )
    service._events.list_after.return_value = []
    original = AssistantService._dispatch_group_reply
    attempted = set()

    async def dispatch(self, *args, **kwargs):
        attempted.add(args[3].message_id)
        if args[3].message_id != posted[-1].message.message_id:
            raise TimeoutError("persistent per-batch admission outage")
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(AssistantService, "_dispatch_group_reply", dispatch)
    await reconcile_group_summaries(service)
    await reconcile_group_summaries(service)
    assert len(attempted) == 26
    healthy = await service.repository.get_group_delivery(
        "t", "u", group.group_id, posted[-1].message.delivery_batch_id
    )
    assert healthy.summary_state == "queued"
    assert agui.create_run_with_result.await_count == 53


@pytest.mark.asyncio
@pytest.mark.parametrize("replay", [False, True])
async def test_lost_ack_recovers_original_run_and_timeline_without_redispatch(database, replay):
    service, group, agui = await broadcast_pg(database, 1)
    original = agui.create_run_with_result.side_effect
    accepted = []

    async def create(**kwargs):
        result = await original(**kwargs)
        if kwargs["group_context"]["memberId"] == "specialist_0":
            accepted.append(result.run)
            raise TimeoutError("accepted but acknowledgement lost")
        return result

    agui.create_run_with_result.side_effect = create
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 检查", client_message_id="lost-ack"
    )
    child = accepted[0]
    finished = child.model_copy(
        update={"status": RunStatus.SUCCEEDED, "fencing_token": child.fencing_token + 1}
    )
    assert await service._runs.compare_and_set(RunStatus.RUNNING, finished)
    service._events.list_after.return_value = []
    if replay:
        repeated = await service.post_assistant_group_message(
            "t", "u", group.group_id, text="@所有人 检查", client_message_id="lost-ack"
        )
        assert repeated.message.delivery_batch_id == posted.message.delivery_batch_id
        assert all(
            m.delivery_batch_id == posted.message.delivery_batch_id for m in repeated.replies
        )
        assert any(m.run_id == child.run_id for m in repeated.replies)
    batch = await service.get_assistant_group_delivery(
        "t", "u", group.group_id, posted.message.message_id
    )
    target = next(t for t in batch.targets if t.member.member_id == "specialist_0")
    assert target.state == "succeeded" and target.run_id == child.run_id
    assert target.detail is None
    assert target.result.run_id == child.run_id
    timeline = await service.repository.list_group_messages("t", "u", group.group_id)
    assert len([m for m in timeline if m.run_id == child.run_id]) == 1
    assert agui.create_run_with_result.await_count == 2


@pytest.mark.asyncio
async def test_replay_preserves_delivery_metadata(database):
    service, group, agui = await broadcast_pg(database, 1)
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 检查", client_message_id="repeat-fields"
    )
    second = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 检查", client_message_id="repeat-fields"
    )
    assert second.message.delivery_batch_id == first.message.delivery_batch_id
    assert {
        m.message_id: (m.delivery_batch_id, m.delivery_member_id, m.delivery_order)
        for m in second.replies
    } == {
        m.message_id: (m.delivery_batch_id, m.delivery_member_id, m.delivery_order)
        for m in first.replies
    }
    assert agui.create_run_with_result.await_count == 2


@pytest.mark.asyncio
async def test_cancelled_summary_attempt_joins_children_and_does_not_starve_next(
    database, monkeypatch
):
    service, group, _ = await broadcast_pg(database, 1)
    posted = [
        await service.post_assistant_group_message(
            "t", "u", group.group_id, text="@所有人 检查", client_message_id=f"cancel-{i}"
        )
        for i in range(2)
    ]
    service._runs = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=RunStatus.SUCCEEDED, run_id="fake"))
    )
    service._events.list_after.return_value = []
    scan = PostgresAssistantRepository.pending_group_summaries

    async def one_at_a_time(self, **kwargs):
        return await scan(self, limit=1)

    monkeypatch.setattr(PostgresAssistantRepository, "pending_group_summaries", one_at_a_time)
    entered = asyncio.Event()
    exited = asyncio.Event()
    dispatch = AssistantService._dispatch_group_reply

    async def suspended(self, *args, **kwargs):
        if args[3].message_id == posted[0].message.message_id:
            entered.set()
            try:
                await asyncio.Future()
            finally:
                exited.set()
        return await dispatch(self, *args, **kwargs)

    monkeypatch.setattr(AssistantService, "_dispatch_group_reply", suspended)
    task = asyncio.create_task(reconcile_group_summaries(service))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()
    await reconcile_group_summaries(service)
    healthy = await service.repository.get_group_delivery(
        "t", "u", group.group_id, posted[1].message.delivery_batch_id
    )
    assert healthy.summary_state == "queued"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["not_accepted", "owner", "group", "member", "ambiguous"])
async def test_unknown_recovery_does_not_guess_or_redispatch(database, boundary):
    service, group, agui = await broadcast_pg(database, 1)
    original = agui.create_run_with_result.side_effect
    accepted = []

    async def create(**kwargs):
        if kwargs["group_context"]["memberId"] != "specialist_0":
            return await original(**kwargs)
        if boundary != "not_accepted":
            accepted.append((await original(**kwargs)).run)
        raise TimeoutError("unknown admission")

    agui.create_run_with_result.side_effect = create
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@所有人 检查"
    )
    if accepted:
        child = accepted[0]
        if boundary == "owner":
            async with database[1]() as db:
                session = await db.get(SessionRow, ("t", child.session_id))
                session.user_id = "outsider"
                await db.commit()
        elif boundary == "ambiguous":
            async with database[1]() as db:
                db.add(
                    SessionRow(tenant_id="t", session_id="rotated-session", user_id="u", payload={})
                )
                await db.commit()
            await service._runs.add(
                child.model_copy(
                    update={"run_id": "ambiguous-run", "session_id": "rotated-session"}
                )
            )
        else:
            context = dict(child.input["group_context"])
            context["groupId" if boundary == "group" else "memberId"] = "other"
            changed = child.model_copy(
                update={
                    "input": {"group_context": context},
                    "fencing_token": child.fencing_token + 1,
                }
            )
            assert await service._runs.compare_and_set(RunStatus.RUNNING, changed)
    batch = await service.get_assistant_group_delivery(
        "t", "u", group.group_id, posted.message.message_id
    )
    target = batch.targets[1]
    assert target.state == "unknown" and target.run_id is None and target.result is None
    assert agui.create_run_with_result.await_count == 2
