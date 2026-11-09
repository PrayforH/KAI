"""The drained migration moves live Bot pointers and retains the old Run."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from harness.adapters.memory import InMemoryAgentRegistry
from harness.application.agents import AgentService
from harness.application.sessions import SessionService
from harness.assistant.models import Objective, Specialist
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.errors import ConflictError
from harness.core.models import AguiThreadBinding, Run, RunStatus, Session
from harness.storage.platform_repositories import (
    PostgresAguiThreadBindingRepository,
    PostgresSessionRepository,
)
from harness.storage.repositories import PostgresRunRepository
from scripts.migrate_bot_default import MigrationCounts, migrate
from tests.integration.storage.conftest import DatabaseFixture


@pytest.mark.asyncio
async def test_migration_preserves_history_and_refuses_active_work(database: DatabaseFixture):
    _, db = database
    now = datetime.now(UTC)
    owner = ("tenant", "user")
    contacts = PostgresAssistantRepository(db)
    binding_repo = PostgresAguiThreadBindingRepository(db)
    session_repo = PostgresSessionRepository(db)
    run_repo = PostgresRunRepository(db)
    registry = InMemoryAgentRegistry()
    agents = AgentService(
        registry, clock=lambda: now, default_manifest_path="agents/lead-agent/agent.yaml"
    )
    sessions = SessionService(
        registry, session_repo, clock=lambda: now, id_generator=lambda _: uuid4().hex
    )

    bot = await contacts.create_specialist(Specialist(
        specialistId="specialist_legacy", tenantId=owner[0], userId=owner[1],
        name="旧 Bot", slug="legacy", agentName="vertical", agentVersion="1",
        createdAt=now, updatedAt=now,
    ))
    await contacts.create(Objective(
        objectiveId="objective_duty", tenantId=owner[0], userId=owner[1],
        specialistId=bot.specialist_id, title="核对", instructions="核对记录",
        agentName="vertical", agentVersion="1", createdAt=now, updatedAt=now,
    ))
    for thread_id, kind in (("assistant_specialist_legacy", "bot"), ("project-task", "task")):
        await session_repo.add(Session(
            tenant_id=owner[0], user_id=owner[1], session_id=f"s-{thread_id}",
            agent_name="vertical", agent_version="1", created_at=now,
        ))
        await binding_repo.add(AguiThreadBinding(
            tenant_id=owner[0], user_id=owner[1], thread_id=thread_id,
            session_id=f"s-{thread_id}", kind=kind,
            specialist_id=bot.specialist_id if kind == "bot" else None,
            created_at=now, updated_at=now,
        ))
    old = await binding_repo.get_by_thread(*owner, "assistant_specialist_legacy")
    run = Run(
        tenant_id=owner[0], run_id="original", session_id=old.session_id,
        idempotency_key="original", status=RunStatus.RUNNING, created_at=now, updated_at=now,
    )
    await run_repo.add(run)

    assert await migrate(db, *owner) == MigrationCounts(1, 1, 1)
    with pytest.raises(ConflictError, match="active Run"):
        await migrate(db, *owner, services=(agents, sessions))
    assert await binding_repo.get_by_thread(*owner, old.thread_id) == old

    assert await run_repo.compare_and_set(
        RunStatus.RUNNING,
        run.model_copy(update={"status": RunStatus.SUCCEEDED, "fencing_token": 1}),
    )
    assert await migrate(db, *owner, services=(agents, sessions)) == MigrationCounts(1, 1, 1)
    current = await binding_repo.get_by_thread(*owner, old.thread_id)
    assert current.previous_session_ids == (old.session_id,)
    assert (await sessions.get(owner[0], current.session_id)).agent_name == "lead-agent"
    assert (await run_repo.get(owner[0], "original")).session_id == old.session_id
    assert (await contacts.get_specialist(*owner, bot.specialist_id)).agent_name == "lead-agent"
    assert (await contacts.get(*owner, "objective_duty")).agent_name == "lead-agent"
    assert (await binding_repo.get_by_thread(*owner, "project-task")).session_id == "s-project-task"
    assert await migrate(db, *owner, services=(agents, sessions)) == MigrationCounts(0, 0, 0)
