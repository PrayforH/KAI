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
