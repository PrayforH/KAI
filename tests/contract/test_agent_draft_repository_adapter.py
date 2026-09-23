import pytest

from harness.studio.repositories import InMemoryAgentDraftRepository
from tests.contracts.agent_draft_repository import (
    exercise_concurrent_replace,
    exercise_repository_contract,
)


@pytest.mark.asyncio
async def test_in_memory_agent_draft_repository_contract() -> None:
    await exercise_repository_contract(InMemoryAgentDraftRepository())


@pytest.mark.asyncio
async def test_in_memory_agent_draft_concurrent_replace() -> None:
    await exercise_concurrent_replace(InMemoryAgentDraftRepository())


@pytest.mark.asyncio
async def test_in_memory_revision_history() -> None:
    from tests.contracts.agent_draft_repository import exercise_revision_history

    await exercise_revision_history(InMemoryAgentDraftRepository())
