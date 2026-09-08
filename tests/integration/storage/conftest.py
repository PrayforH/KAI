import os
from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.storage.database import (
    SessionFactory,
    create_database,
    create_schema,
    drop_schema,
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
    await drop_schema(engine)
    await create_schema(engine)
    try:
        yield engine, sessions
    finally:
        await engine.dispose()
