"""Real PostgreSQL broadcasts, membership, read-only artifact pagination and retry recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from harness.assistant.group_delivery import GROUP_FANOUT_LIMIT
from harness.assistant.repository import PostgresAssistantRepository
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
