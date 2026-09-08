from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from claude_agent_sdk import SessionKey, SessionStoreEntry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.context.models import ContextDigestCreator, ContextDigestSource
from harness.context.service import ContextService
from harness.core.errors import NotFoundError
from harness.policy.models import ContextTrust
from harness.runtime.session_store import PostgresSessionStore
from harness.storage.context_repository import PostgresContextRepository
from harness.storage.database import SessionFactory
from harness.storage.models import SessionContextDigestRow
from harness.storage.transcript_checkpoint import PostgresTranscriptCheckpointProvider

DatabaseFixture = tuple[AsyncEngine, SessionFactory]
NOW = datetime(2026, 8, 9, tzinfo=UTC)


def _ids() -> Callable[[str], str]:
    current = 0

    def generate(prefix: str) -> str:
        nonlocal current
        current += 1
        return f"{prefix}-{current}"

    return generate


def _service(sessions: SessionFactory) -> ContextService:
    return ContextService(
        PostgresContextRepository(sessions),
        clock=lambda: NOW,
        id_generator=_ids(),
    )


def _source(value: str) -> ContextDigestSource:
    return ContextDigestSource(
        sdk_session_id_hash=f"sha256:{'a' * 64}",
        through_run_id="run-a",
        through_event_sequence=9,
        transcript_checkpoint_hash=f"sha256:{value * 64}",
    )


@pytest.mark.asyncio
async def test_postgres_context_state_and_digest_are_durable_and_owner_scoped(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    first = _service(sessions)
    digest = await first.create_digest(
        tenant_id="tenant-a",
        owner_user_id="owner-a",
        session_id="session-a",
        source=_source("1"),
        created_by=ContextDigestCreator(
            route_id="context-digest-v1",
            model="deterministic",
            prompt_revision="v1",
        ),
    )

    restarted = _service(sessions)
    state = await restarted.state("tenant-a", "owner-a", "session-a")
    restored = await restarted.latest_digest("tenant-a", "owner-a", "session-a")

    assert state.latest_digest_id == digest.digest_id
    assert restored == digest
    with pytest.raises(NotFoundError):
        await restarted.state("tenant-a", "owner-b", "session-a")


@pytest.mark.asyncio
async def test_postgres_context_cas_converges_and_digest_versions_are_immutable(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    service = _service(sessions)

    await asyncio.gather(
        service.promote_trust("tenant-a", "owner-a", "session-a", ContextTrust.SENSITIVE),
        service.promote_trust("tenant-a", "owner-a", "session-a", ContextTrust.UNTRUSTED),
    )
    first, repeated = await asyncio.gather(
        service.create_digest(
            tenant_id="tenant-a",
            owner_user_id="owner-a",
            session_id="session-a",
            source=_source("1"),
            created_by=ContextDigestCreator(
                route_id="context-digest-v1",
                model="deterministic",
                prompt_revision="v1",
            ),
        ),
        service.create_digest(
            tenant_id="tenant-a",
            owner_user_id="owner-a",
            session_id="session-a",
            source=_source("1"),
            created_by=ContextDigestCreator(
                route_id="context-digest-v1",
                model="deterministic",
                prompt_revision="v1",
            ),
        ),
    )

    assert first == repeated
    assert first.version == 1
    state = await service.state("tenant-a", "owner-a", "session-a")
    assert state.trust_high_watermark is ContextTrust.UNTRUSTED
    async with sessions() as db:
        count = await db.scalar(select(func.count()).select_from(SessionContextDigestRow))
    assert count == 1


@pytest.mark.asyncio
async def test_postgres_context_overview_is_descending_and_cursor_paginated(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    service = _service(sessions)
    creator = ContextDigestCreator(
        route_id="context-digest-v1",
        model="deterministic",
        prompt_revision="v1",
    )
    for checkpoint in ("1", "2", "3"):
        await service.create_digest(
            tenant_id="tenant-a",
            owner_user_id="owner-a",
            session_id="session-a",
            source=_source(checkpoint),
            created_by=creator,
        )

    first = await service.overview("tenant-a", "owner-a", "session-a", limit=2)
    second = await service.overview(
        "tenant-a",
        "owner-a",
        "session-a",
        before_version=first.next_before_version,
        limit=2,
    )

    assert [item.version for item in first.digests] == [3, 2]
    assert first.next_before_version == 2
    assert [item.version for item in second.digests] == [1]
    assert second.next_before_version is None


@pytest.mark.asyncio
async def test_postgres_transcript_checkpoint_is_stable_and_changes_with_entries(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    key: SessionKey = {
        "project_key": "temporary-workspace",
        "session_id": "sdk-session-a",
    }
    store = PostgresSessionStore(
        sessions,
        tenant_id="tenant-a",
        project_id="session-a",
    )
    entries = [
        cast(SessionStoreEntry, {"type": "user", "uuid": "entry-1", "message": "hello"}),
    ]
    await store.append(key, entries)
    provider = PostgresTranscriptCheckpointProvider(sessions)

    first = await provider.checkpoint("tenant-a", "session-a", "sdk-session-a")
    repeated = await provider.checkpoint("tenant-a", "session-a", "sdk-session-a")
    await store.append(
        key,
        [
            cast(
                SessionStoreEntry,
                {"type": "assistant", "uuid": "entry-2", "message": "world"},
            )
        ],
    )
    changed = await provider.checkpoint("tenant-a", "session-a", "sdk-session-a")

    assert first is not None
    assert repeated == first
    assert changed is not None
    assert changed.entry_count == 2
    assert changed.transcript_checkpoint_hash != first.transcript_checkpoint_hash
