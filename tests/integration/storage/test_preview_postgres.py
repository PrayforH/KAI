import os

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.storage.database import SessionFactory, create_database
from harness.storage.preview_repository import PostgresPreviewRepository
from tests.contracts.preview_repository import (
    exercise_concurrent_cas,
    exercise_repository_contract,
    preview,
)

DatabaseFixture = tuple[AsyncEngine, SessionFactory]
DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest.mark.asyncio
async def test_postgres_preview_repository_contract(
    database: DatabaseFixture,
) -> None:
    _engine, sessions = database
    await exercise_repository_contract(PostgresPreviewRepository(sessions))


@pytest.mark.asyncio
async def test_postgres_preview_concurrent_cas(database: DatabaseFixture) -> None:
    _engine, sessions = database
    await exercise_concurrent_cas(PostgresPreviewRepository(sessions))


@pytest.mark.asyncio
async def test_postgres_preview_survives_repository_and_engine_restart(
    database: DatabaseFixture,
) -> None:
    first_engine, first_sessions = database
    original = preview(preview_id="preview-durable", idempotency_key="durable")
    await PostgresPreviewRepository(first_sessions).add(original)

    await first_engine.dispose()
    second_engine, second_sessions = create_database(DATABASE_URL)
    try:
        restored = await PostgresPreviewRepository(second_sessions).get(
            original.tenant_id, original.preview_id
        )
    finally:
        await second_engine.dispose()

    assert restored == original
