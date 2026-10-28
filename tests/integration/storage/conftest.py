import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.storage.database import (
    SessionFactory,
    create_database,
    create_schema,
)

DatabaseFixture = tuple[AsyncEngine, SessionFactory]


@pytest_asyncio.fixture
async def database() -> AsyncIterator[DatabaseFixture]:
    engine, sessions = create_database(
        os.getenv(
            "HARNESS_TEST_DATABASE_URL",
            "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
        )
    )
    # Cross-suite schemas (feishu, evolution) carry FKs into assistant
    # tables; a metadata-ordered drop cannot cover tables outside this
    # metadata, so the test schema resets with a cascade instead.
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await create_schema(engine)
    try:
        yield engine, sessions
    finally:
        await engine.dispose()
