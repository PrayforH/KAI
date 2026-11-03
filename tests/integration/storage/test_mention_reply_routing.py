"""Durable @ replies retain origin and ownership without synthetic duties."""

from datetime import timedelta
from uuid import uuid4

import pytest

from harness.assistant.models import HandoverStatus, SpecialistHandover
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.errors import ConflictError
from harness.core.models import RunStatus
from harness.storage.models import RunRow, SessionRow
from tests.integration.storage.test_delegation_real_gateway import NOW, make_run, make_session
from tests.unit.assistant.test_mention_message import _specialist


@pytest.mark.asyncio
async def test_results_and_pending_approvals_are_origin_owned_and_cursor_stable(database):
    repo = PostgresAssistantRepository(database[1])
    await repo.create_specialist(
        _specialist().model_copy(update={"tenant_id": "tenant-a", "user_id": "user-a"})
    )
    session = make_session()
    async with database[1]() as db, db.begin():
        db.add(
            SessionRow(
                tenant_id=session.tenant_id,
                session_id=session.session_id,
                user_id=session.user_id,
                payload=session.model_dump(mode="json"),
            )
        )
        for index, (_origin, status) in enumerate(
            [
                ("main", RunStatus.SUCCEEDED),
                ("rest", RunStatus.SUCCEEDED),
                ("main", RunStatus.RUNNING),
                ("main", RunStatus.FAILED),
                ("main", RunStatus.WAITING_APPROVAL),
            ]
        ):
            run = make_run(session).model_copy(
                update={
                    "run_id": f"run-{index}",
                    "idempotency_key": str(index),
                    "status": status,
                    "updated_at": NOW + timedelta(seconds=index // 2),
                }
            )
            db.add(
                RunRow(
                    tenant_id=run.tenant_id,
                    run_id=run.run_id,
                    session_id=run.session_id,
                    idempotency_key=run.idempotency_key,
                    status=status.value,
                    updated_at=run.updated_at,
                    payload=run.model_dump(mode="json"),
                )
            )
    for index, origin in enumerate(["main", "rest", "main", "main", "main"]):
        await repo.record_handover(
            SpecialistHandover(
                handoverId=f"handover-{index}",
                tenantId="tenant-a",
                userId="user-a",
                specialistId="specialist_a",
                messageId=f"mention-specialist:key-{index}:specialist_a",
                summary="检查",
                sourceThreadId=origin,
                targetThreadId="assistant_specialist_a",
                runId=f"run-{index}",
                requestHash="hash",
                sentAt=NOW + timedelta(seconds=index + 2),
                status=HandoverStatus.SENT,
                createdAt=NOW,
                updatedAt=NOW,
            )
        )
    rows, count = await repo.mention_message_runs("tenant-a", "user-a", "main", limit=1)
    assert count == 3 and rows[0][1].run_id == "run-4"
    cursor = (rows[0][1].updated_at, "mention-result-" + rows[0][0].handover_id)
    more, _ = await repo.mention_message_runs("tenant-a", "user-a", "main", before=cursor)
    assert [row[1].run_id for row in more] == ["run-3", "run-0"]
    rest, count = await repo.mention_message_runs("tenant-a", "user-a", "rest")
    assert count == 1 and rest[0][1].run_id == "run-1"
    for tenant, user in [("other", "user-a"), ("tenant-a", "other")]:
        assert await repo.mention_message_runs(tenant, user, "main") == ([], 0)
    assert await repo.mention_message_runs("tenant-a", "user-a", "task-unrelated") == ([], 0)
    assert await repo.mention_message_runs("tenant-a", "user-a", "assistant_specialist_a") == (
        [],
        0,
    )

    sent, count = await repo.mention_message_runs(
        "tenant-a", "user-a", "main", direction="sent", limit=2
    )
    assert count == 4  # Running and waiting children both have genuine sent events.
    cursor = (
        sent[-1][0].sent_at or sent[-1][0].created_at,
        "mention-sent-" + sent[-1][0].handover_id,
    )
    more, _ = await repo.mention_message_runs(
        "tenant-a", "user-a", "main", direction="sent", before=cursor
    )
    assert len(more) == 2
    assert len({item[0].handover_id for item in [*sent, *more]}) == 4
    assert await repo.mention_message_runs("tenant-a", "other", "main", direction="sent") == ([], 0)


@pytest.mark.asyncio
async def test_receipt_idempotency_rejects_changed_origin_and_body(database):
    repo = PostgresAssistantRepository(database[1])
    await repo.create_specialist(_specialist())
    first = SpecialistHandover(
        handoverId="handover-one",
        tenantId="t",
        userId="u",
        specialistId="specialist_a",
        messageId="mention-specialist:stable-key:specialist_a",
        summary="检查",
        sourceThreadId="main",
        targetThreadId="assistant_specialist_a",
        requestHash="hash",
        status=HandoverStatus.SENT,
        createdAt=NOW,
        updatedAt=NOW,
    )
    await repo.record_handover(first)
    for changes in ({"source_thread_id": "rest"}, {"request_hash": "other"},
                    {"reply_to_run_id": "another-question"}):
        with pytest.raises(ConflictError):
            await repo.record_handover(
                first.model_copy(update={"handover_id": uuid4().hex, **changes})
            )
    again = await repo.record_handover(first.model_copy(update={"handover_id": "retry"}))
    assert again.handover_id == first.handover_id
    linked = await repo.link_mention_run("t", "u", first.handover_id, "actual-run")
    assert linked.run_id == "actual-run" and linked.sent_at is not None
    replayed = await repo.link_mention_run("t", "u", first.handover_id, "actual-run")
    assert replayed.sent_at == linked.sent_at
    with pytest.raises(ConflictError):
        await repo.link_mention_run("t", "u", first.handover_id, "wrong-run")
