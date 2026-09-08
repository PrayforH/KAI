import os

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.storage.catalog_repository import PostgresCapabilityCatalogRepository
from harness.storage.database import SessionFactory, create_database
from tests.contracts.capability_catalog_repository import (
    exercise_catalog_concurrent_replace,
    exercise_catalog_repository_contract,
    record,
)

DatabaseFixture = tuple[AsyncEngine, SessionFactory]
DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest.mark.asyncio
async def test_postgres_capability_catalog_repository_contract(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    await exercise_catalog_repository_contract(PostgresCapabilityCatalogRepository(sessions))


@pytest.mark.asyncio
async def test_postgres_capability_catalog_concurrent_replace(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    await exercise_catalog_concurrent_replace(PostgresCapabilityCatalogRepository(sessions))


@pytest.mark.asyncio
async def test_postgres_catalog_survives_engine_restart(
    database: DatabaseFixture,
) -> None:
    first_engine, sessions = database
    original = record()
    repository = PostgresCapabilityCatalogRepository(sessions)
    await repository.seed(original)
    await first_engine.dispose()

    second_engine, second_sessions = create_database(DATABASE_URL)
    try:
        restored = await PostgresCapabilityCatalogRepository(second_sessions).get(
            original.tenant_id
        )
    finally:
        await second_engine.dispose()

    assert restored == original
