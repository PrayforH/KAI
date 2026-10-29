"""Delegation tools against the real assistant service and repository.

The unit suite runs against a typed fake; these tests pin the two real-adapter
behaviours the spec calls out: ``add_note`` must supply a real revision (an
un-awaited coroutine made every note conflict), and ``task_status`` must read
the newest execution from the newest-first repository ordering.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from harness.assistant.delegation import AssistantDelegationGateway, assistant_delegation_overlay
from harness.assistant.lifecycle import next_check
from harness.assistant.models import (
    Objective,
    ObjectiveBrief,
    ObjectiveExecutionStatus,
    ObjectiveSchedule,
    ObjectiveStatus,
)
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.service import AssistantService
from harness.core.models import Run, RunStatus, Session
from tests.integration.storage.conftest import DatabaseFixture

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


class _StubAgents:
    async def get_published(self, *_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(name="audit-agent")


class _StubAuth:
    async def profile(self, *_args: Any, **_kwargs: Any) -> tuple[SimpleNamespace, SimpleNamespace]:
        return SimpleNamespace(disabled=False), SimpleNamespace(role="owner")


class _StubProjects:
    async def get(self, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(project_id="project-a")


def brief(**overrides: Any) -> ObjectiveBrief:
    fields = {
        "title": "整改跟进",
        "instructions": "检查新上传的整改材料，列出待补证据。",
        "agentName": "audit-agent",
        "agentVersion": "1.0.0",
        "schedule": ObjectiveSchedule(intervalMinutes=60),
    }
    return ObjectiveBrief.model_validate(fields | overrides)


def make_service(database: DatabaseFixture) -> AssistantService:
    repo = PostgresAssistantRepository(database[1])
    # The stubs implement exactly the service surface the delegation path
    # touches; full AgentService/AuthService collaborators are out of scope.
    return AssistantService(
        repo,
        agents=cast(Any, _StubAgents()),
        auth=cast(Any, _StubAuth()),
        projects=cast(Any, _StubProjects()),
        agui=cast(Any, None),
        runs=cast(Any, None),
        events=cast(Any, None),
    )


def make_run(session: Session) -> Run:
    return Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.RUNNING,
        idempotency_key="delegate-key",
        created_at=NOW,
        updated_at=NOW,
    )


def make_session() -> Session:
    return Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="audit-agent",
        agent_version="1.0.0",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )


def overlay_for(service: AssistantService) -> Any:
    gateway = AssistantDelegationGateway(service)
    session = make_session()
    return assistant_delegation_overlay(
        gateway, make_run(session), session, coordinator_thread=True
    )


def text_of(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_create_then_status_then_update_round_trip(database: DatabaseFixture) -> None:
    service = make_service(database)
    tools = {tool.name: tool.handler for tool in overlay_for(service).tools}
    created = await tools["assistant_create_task"]({"task": "整理季度报告初稿"})
    assert "已安排" in text_of(created)
    objectives = await service.repository.list("tenant-a", "user-a")
    assert len(objectives) == 1

    status = await tools["assistant_task_status"]({})
    body = text_of(status)
    assert "revision=1" in body and "最近执行：pending" in body
    assert "expected_revision" in body

    updated = await tools["assistant_update_task"](
        {"objective_id": objectives[0].objective_id, "action": "pause", "expected_revision": 1}
    )
    assert "revision=2" in text_of(updated)
    final = await service.repository.get("tenant-a", "user-a", objectives[0].objective_id)
    assert final.status is ObjectiveStatus.PAUSED


@pytest.mark.asyncio
async def test_repeated_create_with_same_key_returns_the_existing_objective(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    tools = {tool.name: tool.handler for tool in overlay_for(service).tools}
    await tools["assistant_create_task"]({"task": "整理季度报告初稿"})
    await tools["assistant_create_task"]({"task": "整理季度报告初稿"})
    assert len(await service.repository.list("tenant-a", "user-a")) == 1


@pytest.mark.asyncio
async def test_add_note_supplies_a_real_revision_to_the_repository(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    objective = await service.create("tenant-a", "user-a", brief())
    gateway = AssistantDelegationGateway(service)
    updated = await gateway.add_note(
        "tenant-a", "user-a", objective.objective_id, note="优先检查采购制度"
    )
    assert updated.revision == 2
    events = await service.repository.events("tenant-a", "user-a", objective.objective_id)
    assert [item.content for item in events if item.kind == "note"] == ["优先检查采购制度"]


@pytest.mark.asyncio
async def test_repository_returns_executions_newest_first_and_create_is_idempotent(
    database: DatabaseFixture,
) -> None:
    repo: PostgresAssistantRepository = make_service(database).repository
    first = Objective(
        objectiveId="objective-a",
        tenantId="tenant-a",
        userId="user-a",
        title="整改跟进",
        instructions="检查新上传的整改材料，列出待补证据。",
        agentName="audit-agent",
        agentVersion="1.0.0",
        nextCheckAt=next_check(ObjectiveSchedule(intervalMinutes=60), after=NOW, ends_at=None),
        createdAt=NOW,
        updatedAt=NOW,
    )
    created = await repo.create(first, idempotency_key="delegate:run-1:abc")
    again = await repo.create(first, idempotency_key="delegate:run-1:abc")
    assert again.objective_id == created.objective_id
    second = Objective(
        **first.model_dump(include=set(ObjectiveBrief.model_fields)),
        objectiveId="objective-b",
        tenantId=first.tenant_id,
        userId=first.user_id,
        createdAt=NOW,
        updatedAt=NOW,
    )
    different = await repo.create(second, idempotency_key="delegate:run-2:abc")
    assert different.objective_id == "objective-b"

    older = await repo.enqueue(
        "tenant-a", "user-a", "objective-a", trigger_key="event-1", now=NOW
    )
    finished = older.model_copy(
        update={
            "status": ObjectiveExecutionStatus.SUCCEEDED,
            "finished_at": NOW,
        }
    )
    claim = await repo.claim(NOW)
    assert claim is not None and claim[0].execution_id == older.execution_id
    await repo.save_execution(finished, claim[1], NOW)
    newer = await repo.enqueue(
        "tenant-a", "user-a", "objective-a", trigger_key="event-2", now=NOW + timedelta(hours=1)
    )
    executions = await repo.executions("tenant-a", "user-a", "objective-a")
    assert [item.execution_id for item in executions] == [newer.execution_id, older.execution_id]
