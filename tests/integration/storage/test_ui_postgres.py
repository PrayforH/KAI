"""Cross-process admission and publication, using the real PostgreSQL locks."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import InMemoryEventBus
from harness.application.events import EventService
from harness.core.errors import ConflictError
from harness.presentation.models import ActionRequest, Publication
from harness.presentation.schema import Presentation
from harness.storage.database import SessionFactory
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository
from harness.storage.ui_repository import PostgresPresentationRepository
from tests.integration.agui.test_ui_actions import setup
from tests.integration.storage.conftest import DatabaseFixture


async def seed(database: DatabaseFixture) -> tuple[SessionFactory, Publication, Presentation]:
    _, sessions = database
    memory, p = await setup()
    run = await memory.runs.get("tenant-a", p.run_id)
    await PostgresRunRepository(sessions).add(run)
    publication = await memory.presentations.repository.for_run("tenant-a", p.run_id)
    assert publication
    return sessions, publication, p


@pytest.mark.asyncio
async def test_concurrent_outbox_drains_keep_one_canonical_event(database: DatabaseFixture) -> None:
    sessions, publication, p = await seed(database)
    repo = PostgresEventRepository(sessions)
    bus = InMemoryEventBus()

    async def drain():
        service = EventService(
            PostgresEventRepository(sessions),
            bus,
            clock=lambda: publication.created_at,
            id_generator=lambda prefix: prefix,
        )
        return await service.append_once(
            event_id="ui-drain-event",
            tenant_id="tenant-a",
            run_id=p.run_id,
            session_id=publication.session_id,
            event_type="ui.presentation.ready",
            timestamp=publication.created_at.isoformat(),
            payload={"presentation_id": p.presentation_id},
        )

    results = await asyncio.gather(*(drain() for _ in range(16)))
    assert all(event == results[0] for event in results)
    assert await repo.list_after("tenant-a", p.run_id, 0) == [results[0]]
    assert await bus.read("tenant-a", p.run_id) == [results[0]]


def request(p: Presentation, key: str) -> ActionRequest:
    now = datetime.now(UTC)
    return ActionRequest(
        request_id=f"req-{key}",
        tenant_id="tenant-a",
        actor_user_id="owner",
        presentation_id=p.presentation_id,
        action_id=p.actions[0]["action_id"],
        revision=1,
        idempotency_key=key,
        payload_hash="same-input",
        values={"project": ["测试"]},
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_concurrent_publication_freezes_one_snapshot_and_outbox(
    database: DatabaseFixture,
) -> None:
    sessions, publication, p = await seed(database)
    repo = PostgresPresentationRepository(sessions)
    with pytest.raises(ConflictError):
        await repo.publish(
            publication.model_copy(update={"fencing_token": publication.fencing_token - 1})
        )
    assert await repo.for_run("tenant-a", p.run_id) is None
    results = await asyncio.gather(
        *(PostgresPresentationRepository(sessions).publish(publication) for _ in range(12))
    )
    assert all(r == publication for r in results)
    assert len(await repo.outbox()) == 1
    # Later policy/renderer changes cannot silently rewrite the published data.
    changed = publication.model_copy(
        update={"presentations": (p.model_copy(update={"title": "changed"}),)}
    )
    assert await repo.publish(changed) == publication
    assert (await PostgresPresentationRepository(sessions).get("tenant-a", p.presentation_id))[
        1
    ] == p


@pytest.mark.asyncio
async def test_two_api_processes_reserve_one_write_and_one_dispatch_lease(
    database: DatabaseFixture,
) -> None:
    sessions, publication, p = await seed(database)
    repo = PostgresPresentationRepository(sessions)
    await repo.publish(publication)
    admitted = await asyncio.gather(
        *(
            PostgresPresentationRepository(sessions).reserve(request(p, str(i)), single_use=True)
            for i in range(16)
        )
    )
    assert len({r.request_id for r in admitted}) == 1
    assert len(await repo.requests_for_presentation("tenant-a", "owner", p.presentation_id)) == 1
    with pytest.raises(ConflictError):
        await repo.reserve(
            request(p, "changed").model_copy(update={"payload_hash": "different"}), single_use=True
        )
    claims = await asyncio.gather(
        *(
            PostgresPresentationRepository(sessions).claim(admitted[0], datetime.now(UTC))
            for _ in range(12)
        )
    )
    claimed = [r for r in claims if r]
    assert len(claimed) == 1
    first = claimed[0]
    recovered = await repo.claim(first, datetime.now(UTC) + timedelta(seconds=31))
    assert recovered and recovered.fencing_token == first.fencing_token + 1
    assert not await repo.save_request(
        first.model_copy(update={"status": "failed"}), first.fencing_token
    )
    assert await repo.save_request(
        recovered.model_copy(update={"status": "submitted", "run_id": "child"}),
        recovered.fencing_token,
    )
    assert (await repo.request("tenant-a", first.request_id)).run_id == "child"
    assert len(await repo.outbox()) == 2  # ready + one requested; no duplicate facts


@pytest.mark.asyncio
async def test_view_and_revision_cas_across_processes(database: DatabaseFixture) -> None:
    sessions, publication, p = await seed(database)
    repo = PostgresPresentationRepository(sessions)
    await repo.publish(publication)
    states = await asyncio.gather(
        *(
            PostgresPresentationRepository(sessions).save_view(
                "tenant-a", "owner", p.presentation_id, 1, 0, {"values": {"project": [str(i)]}}
            )
            for i in range(8)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in states) == 1
    assert sum(isinstance(r, ConflictError) for r in states) == 7
    revision = p.model_copy(update={"revision": 2})
    revisions = await asyncio.gather(
        *(
            PostgresPresentationRepository(sessions).revise(
                "tenant-a", p.presentation_id, 1, revision
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in revisions) == 1
    assert (await repo.get("tenant-a", p.presentation_id, 1))[1].revision == 1
    assert (await repo.get("tenant-a", p.presentation_id))[1].revision == 2
    with pytest.raises(ConflictError):
        await repo.reserve(request(p, "stale"), single_use=True)
