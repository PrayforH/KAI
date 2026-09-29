import os
from typing import cast

import pytest
from claude_agent_sdk import SessionStore
from claude_agent_sdk.testing import run_session_store_conformance
from sqlalchemy import delete

from harness.runtime.session_store import PostgresSessionStore
from harness.storage.database import create_database, create_schema, drop_schema
from harness.storage.models import SdkSessionEntryRow

DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest.mark.asyncio
async def test_postgres_sdk_session_store_conformance() -> None:
    engine, sessions = create_database(DATABASE_URL)
    await drop_schema(engine)
    await create_schema(engine)

    async def fresh() -> SessionStore:
        async with sessions() as session:
            await session.execute(delete(SdkSessionEntryRow))
            await session.commit()
        return cast(SessionStore, PostgresSessionStore(sessions, tenant_id="tenant-a"))

    try:
        await run_session_store_conformance(
            fresh, skip_optional=frozenset({"list_session_summaries"})
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_warm_revision_tracks_child_writes_and_deletion_but_isolates_sessions() -> None:
    engine, sessions = create_database(DATABASE_URL)
    await drop_schema(engine)
    await create_schema(engine)
    store = PostgresSessionStore(sessions, tenant_id="a", project_id="session-a")
    other = PostgresSessionStore(sessions, tenant_id="a", project_id="session-b")
    try:
        initial = await store.revision()
        await store.append(
            {"session_id": "native", "project_key": "cwd"},
            [{"uuid": "one", "type": "user", "message": {"content": "one"}}],
        )
        first = await store.revision()
        assert first != initial
        await other.append(
            {"session_id": "native", "project_key": "cwd"},
            [{"uuid": "other", "type": "user", "message": {"content": "other"}}],
        )
        assert await store.revision() == first
        await store.append(
            {"session_id": "native", "project_key": "cwd", "subpath": "child"},
            [{"uuid": "two", "type": "user", "message": {"content": "two"}}],
        )
        child = await store.revision()
        assert child != first
        await store.delete({"session_id": "native", "project_key": "cwd", "subpath": "child"})
        assert await store.revision() != child
    finally:
        await engine.dispose()
