import os

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.storage.database import SessionFactory, create_database
from harness.storage.models import AgentDraftRow
from harness.storage.studio_repository import PostgresAgentDraftRepository
from tests.contracts.agent_draft_repository import (
    draft,
    exercise_concurrent_replace,
    exercise_repository_contract,
)

DatabaseFixture = tuple[AsyncEngine, SessionFactory]
DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)


@pytest.mark.asyncio
async def test_postgres_agent_draft_repository_contract(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    await exercise_repository_contract(PostgresAgentDraftRepository(sessions))


@pytest.mark.asyncio
async def test_postgres_agent_draft_concurrent_replace(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    await exercise_concurrent_replace(PostgresAgentDraftRepository(sessions))


@pytest.mark.asyncio
async def test_postgres_agent_draft_survives_repository_and_engine_restart(
    database: DatabaseFixture,
) -> None:
    first_engine, first_sessions = database
    original = draft(draft_id="draft-durable")
    await PostgresAgentDraftRepository(first_sessions).add(original)

    await first_engine.dispose()
    second_engine, second_sessions = create_database(DATABASE_URL)
    try:
        restored = await PostgresAgentDraftRepository(second_sessions).get(
            original.tenant_id, original.created_by, original.draft_id
        )
    finally:
        await second_engine.dispose()

    assert restored == original


@pytest.mark.asyncio
async def test_postgres_agent_draft_rejects_unknown_payload_schema(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    original = draft(draft_id="draft-future-schema")
    repository = PostgresAgentDraftRepository(sessions)
    await repository.add(original)
    async with sessions() as session:
        await session.execute(
            update(AgentDraftRow)
            .where(
                AgentDraftRow.tenant_id == original.tenant_id,
                AgentDraftRow.draft_id == original.draft_id,
            )
            .values(schema_version=999)
        )
        await session.commit()

    with pytest.raises(ValueError, match="Unsupported Agent Draft schema version"):
        await repository.get(original.tenant_id, original.created_by, original.draft_id)
