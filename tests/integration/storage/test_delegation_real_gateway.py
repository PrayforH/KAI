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
from pydantic import SecretStr

from harness.assistant.delegation import AssistantDelegationGateway, assistant_delegation_overlay
from harness.assistant.lifecycle import next_check
from harness.assistant.models import (
    ManagedTask,
    Objective,
    ObjectiveBrief,
    ObjectiveExecutionStatus,
    ObjectiveSchedule,
    ObjectiveStatus,
)
from harness.assistant.repository import PostgresAssistantRepository
from harness.assistant.service import AssistantService
from harness.core.errors import ConflictError
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
    from unittest.mock import AsyncMock

    # The stubs implement exactly the service surface the delegation path
    # touches; full AgentService/AuthService collaborators are out of scope.
    return AssistantService(
        repo,
        agents=cast(Any, _StubAgents()),
        auth=cast(Any, _StubAuth()),
        projects=cast(Any, _StubProjects()),
        agui=cast(Any, None),
        runs=cast(Any, None),
        events=cast(Any, SimpleNamespace(list_after=AsyncMock(return_value=[]))),
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


class _StubAgui:
    """Binding lookups for managed threads."""

    def __init__(self, agent_name: str = "audit-agent", agent_version: str = "1.0.0") -> None:
        self.agent_name = agent_name
        self.agent_version = agent_version
        self.active_session_runs: list[Any] = []

    async def get_binding(self, *, tenant_id: str, user_id: str, thread_id: str):
        return SimpleNamespace(
            session_id=f"session-for-{thread_id}",
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            project_id=None,
        )

    async def list_bindings(
        self, tenant_id: str, user_id: str, *, limit: int = 50
    ) -> list[SimpleNamespace]:
        return []

    async def create_run_with_result(self, **_kwargs: Any):  # pragma: no cover - mutex test only
        raise AssertionError("run creation must not happen while the thread is busy")


class _StubRuns:
    def __init__(self, active: list[Any] | None = None) -> None:
        self._active = active or []
        self.steer_calls: list[tuple[str, str]] = []

    async def list_for_sessions(self, _tenant_id: str, _session_ids: list[str], *, limit: int):
        return self._active

    async def find_by_idempotency_key(self, *_args: Any, **_kwargs: Any):
        return None

    async def steer(self, tenant_id: str, run_id: str, request_id: str, text: str):
        self.steer_calls.append((run_id, request_id))
        return {"request_id": request_id, "status": "requested"}


@pytest.mark.asyncio
async def test_managed_binding_is_unique_per_thread_and_objective(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    managed = ManagedTask(
        managedId="managed-1",
        objectiveId="objective-m",
        tenantId="tenant-a",
        userId="user-a",
        threadId="thread-taken",
        createdAt=NOW,
    )
    await service.repository.create(
        Objective(
            objectiveId="objective-m",
            tenantId="tenant-a",
            userId="user-a",
            title="跟进",
            instructions="i",
            agentName="audit-agent",
            agentVersion="1.0.0",
            managedThreadId="thread-taken",
            createdAt=NOW,
            updatedAt=NOW,
        )
    )
    created, is_new = await service.repository.bind_managed(managed)
    assert is_new and created.managed_id == "managed-1"
    same, is_new_again = await service.repository.bind_managed(
        managed.model_copy(update={"managedId": "managed-2"})
    )
    assert not is_new_again and same.managed_id == "managed-1"


@pytest.mark.asyncio
async def test_managed_objective_executes_on_the_original_thread(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    objective = await service.create(
        "tenant-a",
        "user-a",
        brief(),
        managed_thread_id="original-thread",
    )
    assert objective.managed_thread_id == "original-thread"
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="m1", now=NOW
    )
    assert execution.thread_id == "original-thread"
    bindings = await service.repository.managed_bindings("tenant-a", "user-a")
    assert len(bindings) == 1 and bindings[0].objective_id == objective.objective_id


@pytest.mark.asyncio
async def test_follow_only_objective_never_starts_executions(
    database: DatabaseFixture,
) -> None:
    from harness.core.errors import ConflictError

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    objective = await service.create(
        "tenant-a",
        "user-a",
        brief(),
        managed_thread_id="thread-o",
        mode=cast(Any, "follow_only"),
    )
    assert objective.mode.value == "follow_only"
    with pytest.raises(ConflictError, match="follow-only"):
        await service.repository.enqueue(
            "tenant-a", "user-a", objective.objective_id, trigger_key="x", now=NOW
        )


@pytest.mark.asyncio
async def test_finished_execution_writes_a_structured_checkpoint(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    objective = await service.create("tenant-a", "user-a", brief(), managed_thread_id="thread-c")
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="c1", now=NOW
    )
    claim = await service.repository.claim(NOW)
    assert claim is not None
    finished = execution.model_copy(
        update={
            "status": ObjectiveExecutionStatus.SUCCEEDED,
            "summary": "已完成数据校验，下一步生成图表",
            "finished_at": NOW,
        }
    )
    assert await service.repository.save_execution(finished, claim[1], NOW)
    checkpoints = await service.repository.checkpoints(
        "tenant-a", "user-a", objective.objective_id
    )
    assert len(checkpoints) == 1
    assert checkpoints[0].summary == "已完成数据校验，下一步生成图表"
    assert checkpoints[0].status is ObjectiveExecutionStatus.SUCCEEDED
    notices = await service.repository.inbox("tenant-a", "user-a")
    assert notices[0].thread_id == "thread-c"


@pytest.mark.asyncio
async def test_instruct_queues_without_a_running_execution(database: DatabaseFixture) -> None:
    service = make_service(database)
    runs = _StubRuns()
    service._runs = cast(Any, runs)  # pyright: ignore[reportPrivateUsage]
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    objective = await service.create("tenant-a", "user-a", brief())
    outcome = await service.instruct(
        "tenant-a", "user-a", objective.objective_id, "改用 CSV", request_id="r1"
    )
    assert outcome["status"] == "queued" and runs.steer_calls == []
    events = await service.repository.events("tenant-a", "user-a", objective.objective_id)
    assert [item.content for item in events if item.kind == "note"] == ["改用 CSV"]


@pytest.mark.asyncio
async def test_instruct_steers_a_running_managed_execution(database: DatabaseFixture) -> None:
    service = make_service(database)
    objective = await service.create("tenant-a", "user-a", brief())
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="s1", now=NOW
    )
    claim = await service.repository.claim(NOW)
    assert claim is not None
    await service.repository.save_execution(
        execution.model_copy(
            update={"status": ObjectiveExecutionStatus.RUNNING, "run_id": "run-9"}
        ),
        claim[1],
        NOW,
    )
    runs = _StubRuns()
    service._runs = cast(Any, runs)  # pyright: ignore[reportPrivateUsage]
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    outcome = await service.instruct(
        "tenant-a", "user-a", objective.objective_id, "优先导出", request_id="r2"
    )
    assert outcome["status"] == "accepted" and runs.steer_calls == [("run-9", "r2")]


@pytest.mark.asyncio
async def test_reconcile_holds_pending_while_the_original_thread_is_busy(
    database: DatabaseFixture,
) -> None:

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    service._runs = cast(Any, _StubRuns(active=[SimpleNamespace(status=RunStatus.RUNNING)]))  # pyright: ignore[reportPrivateUsage]
    objective = await service.create("tenant-a", "user-a", brief(), managed_thread_id="thread-busy")
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="b1", now=NOW
    )
    held = await service._reconcile(execution)  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    assert held is execution


@pytest.mark.asyncio
async def test_specialist_identity_is_ready_without_a_schedule_and_permissions_still_gate(
    database: DatabaseFixture,
) -> None:
    from harness.assistant.models import SpecialistStatus
    service = make_service(database)
    first, created = await service.create_specialist(
        "tenant-a", "user-a", name="编码协同", description="按需编码",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    assert created and first.status is SpecialistStatus.READY
    assert "schedule_minutes" not in first.config
    again, created_again = await service.create_specialist(
        "tenant-a", "user-a", name="编码协同", description="重复",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    assert not created_again and again.specialist_id == first.specialist_id
    pending = await service.configure_specialist(
        "tenant-a", "user-a", first.specialist_id,
        config={"required_local_tools": ["run_commands"]}, expected_revision=first.revision,
    )
    assert pending.status is SpecialistStatus.NEEDS_INPUT
    with pytest.raises(ConflictError, match="run_commands"):
        await service.set_specialist_status(
            "tenant-a", "user-a", first.specialist_id,
            status=SpecialistStatus.ACTIVE, expected_revision=pending.revision,
        )
    ready = await service.configure_specialist(
        "tenant-a", "user-a", first.specialist_id,
        config={"local_tool_permissions": ["run_commands"]}, expected_revision=pending.revision,
    )
    assert ready.status is SpecialistStatus.READY


@pytest.mark.asyncio
async def test_delegate_to_specialist_creates_deduped_handover_and_objective(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    outcome = await service.delegate_to_specialist(
        "tenant-a",
        "user-a",
        name="费用监控",
        description="长期关注账号花费",
        agent_name="audit-agent",
        agent_version="1.0.0",
        task="长期关注某账号花费",
        mode=cast(Any, 'autonomous'),
        message_id="delegate-specialist:run-1:费用监控",
    )
    # Default handover is one authorized execution, without a periodic trigger.
    assert outcome["status"] == "applied"
    assert outcome["missing"] == []
    specialist = await service.repository.get_specialist(
        "tenant-a", "user-a", str(outcome["specialist_id"])
    )
    objectives = await service.repository.list("tenant-a", "user-a")
    assert len(objectives) == 1 and objectives[0].specialist_id == specialist.specialist_id
    assert objectives[0].schedule is None and objectives[0].next_check_at is None
    executions = await service.repository.executions(
        "tenant-a", "user-a", objectives[0].objective_id
    )
    assert len(executions) == 1

    replay = await service.delegate_to_specialist(
        "tenant-a",
        "user-a",
        name="费用监控",
        description="长期关注账号花费",
        agent_name="audit-agent",
        agent_version="1.0.0",
        task="长期关注某账号花费",
        mode=cast(Any, 'autonomous'),
        message_id="delegate-specialist:run-1:费用监控",
    )
    assert replay["handover_id"] == outcome["handover_id"]
    stored = (await service.repository.handovers("tenant-a", "user-a"))[0]
    assert outcome["created_at"] == replay["created_at"] == stored.created_at.isoformat()
    assert outcome["updated_at"] == replay["updated_at"] == stored.updated_at.isoformat()
    assert await service.repository.list("tenant-a", "user-a") == objectives


@pytest.mark.asyncio
async def test_applied_delegate_enqueues_exactly_one_first_execution(
    database: DatabaseFixture,
) -> None:
    """配置完整时 applied 必须落一条 queued execution，而不是只写回执。"""
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="ready-duty",
        description="已配置职责",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    await service.configure_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        config={"schedule_minutes": 60, "notify_channel": "inbox"},
        expected_revision=specialist.revision,
    )
    outcome = await service.delegate_to_specialist(
        "tenant-a",
        "user-a",
        name="ready-duty",
        description="已配置职责",
        agent_name="audit-agent",
        agent_version="1.0.0",
        task="每天检查一次",
        mode=cast(Any, "autonomous"),
        message_id="delegate-specialist:run-ready:ready-duty",
    )
    assert outcome["status"] == "applied" and outcome["missing"] == []
    objective_id = str(outcome["objective_id"])
    executions = await service.repository.executions("tenant-a", "user-a", objective_id)
    assert len(executions) == 1
    assert executions[0].status is ObjectiveExecutionStatus.PENDING
    assert executions[0].trigger_key.startswith("delegate-specialist:")
    assert outcome["execution_id"] == executions[0].execution_id
    assert outcome["thread_id"] == executions[0].thread_id


@pytest.mark.asyncio
async def test_enable_does_not_restart_old_handovers_and_explicit_start_targets_one(
    database: DatabaseFixture,
) -> None:
    from harness.api.dependencies import Identity
    from harness.assistant import api
    from harness.assistant.models import HandoverStatus, SpecialistHandover, SpecialistStatus
    service = make_service(database)
    bot, _ = await service.create_specialist(
        "tenant-a", "user-a", name="old-coding", description="existing blocked identity",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    pending = await service.repository.update_specialist(
        "tenant-a", "user-a", bot.specialist_id, expected_revision=bot.revision,
        status=SpecialistStatus.NEEDS_INPUT, now=NOW,
    )
    duties = [await service.create(
        "tenant-a", "user-a", brief(schedule=None, title=f"old task {i}"),
        specialist_id=bot.specialist_id,
    ) for i in range(2)]
    receipts = [await service.repository.record_handover(SpecialistHandover(
        handoverId=f"old-handover-{i}", tenantId="tenant-a", userId="user-a",
        specialistId=bot.specialist_id, objectiveId=duty.objective_id,
        messageId=f"old-message-{i}", status=HandoverStatus.PENDING_INPUT,
        createdAt=NOW, updatedAt=NOW,
    )) for i, duty in enumerate(duties)]
    configured = await service.configure_specialist(
        "tenant-a", "user-a", bot.specialist_id,
        config={"notify_channel": "inbox"}, expected_revision=pending.revision,
    )
    await service.set_specialist_status(
        "tenant-a", "user-a", bot.specialist_id,
        status=SpecialistStatus.ACTIVE, expected_revision=configured.revision,
    )
    for duty in duties:
        assert not await service.repository.executions("tenant-a", "user-a", duty.objective_id)
    identity = Identity(tenant_id="tenant-a", user_id="user-a")
    first = await api.execute(duties[0].objective_id, identity, service, "first-handover:one")
    replay = await api.execute(duties[0].objective_id, identity, service, "first-handover:one")
    assert replay.execution_id == first.execution_id
    assert len(await service.repository.executions(
        "tenant-a", "user-a", duties[0].objective_id
    )) == 1
    assert not await service.repository.executions("tenant-a", "user-a", duties[1].objective_id)
    stored = await service.repository.handovers("tenant-a", "user-a")
    states = {item.handover_id: item.status for item in stored}
    assert states[receipts[0].handover_id] is HandoverStatus.APPLIED
    assert states[receipts[1].handover_id] is HandoverStatus.PENDING_INPUT


@pytest.mark.asyncio
async def test_budget_stops_dispatch_when_limits_are_reached(database: DatabaseFixture) -> None:
    from harness.core.errors import ConflictError

    service = make_service(database)
    objective = await service.create(
        "tenant-a",
        "user-a",
        ObjectiveBrief.model_validate(
            {
                "title": "预算受限职责",
                "instructions": "受预算约束的检查",
                "agentName": "audit-agent",
                "agentVersion": "1.0.0",
                "budget": {"maxRunsPerDay": 2, "maxTotalRuns": 3},
            }
        ),
    )
    for index in range(2):
        execution = await service.repository.enqueue(
            "tenant-a", "user-a", objective.objective_id, trigger_key=f"b{index}", now=NOW
        )
        claim = await service.repository.claim(NOW)
        assert claim is not None
        await service.repository.save_execution(
            execution.model_copy(
                update={
                    "status": ObjectiveExecutionStatus.SUCCEEDED,
                    "finished_at": NOW,
                }
            ),
            claim[1],
            NOW,
        )
    with pytest.raises(ConflictError, match="当日运行次数上限"):
        await service.repository.enqueue(
            "tenant-a", "user-a", objective.objective_id, trigger_key="b2", now=NOW
        )
    # 次日恢复派发；总上限仍然生效。
    from datetime import timedelta

    third = await service.repository.enqueue(
        "tenant-a",
        "user-a",
        objective.objective_id,
        trigger_key="b3",
        now=NOW + timedelta(days=1),
    )
    assert third is not None
    claim = await service.repository.claim(NOW + timedelta(days=1))
    assert claim is not None
    await service.repository.save_execution(
        third.model_copy(
            update={"status": ObjectiveExecutionStatus.SUCCEEDED, "finished_at": NOW}
        ),
        claim[1],
        NOW + timedelta(days=1),
    )
    with pytest.raises(ConflictError, match="总运行次数上限"):
        await service.repository.enqueue(
            "tenant-a",
            "user-a",
            objective.objective_id,
            trigger_key="b4",
            now=NOW + timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_task_dependencies_must_not_close_a_cycle(database: DatabaseFixture) -> None:
    from harness.core.errors import ConflictError

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]

    async def make(number: str) -> Any:
        # plain objectives: the dependency edges are bound explicitly below
        return await service.create("tenant-a", "user-a", brief())

    first = await make("dep-a")
    second = await make("dep-b")
    for objective, thread, depends_on in (
        (first, "thread-dep-a", None),
        (second, "thread-dep-b", first.objective_id),
    ):
        await service.repository.bind_managed(
            ManagedTask(
                managedId=f"managed-{thread}",
                objectiveId=objective.objective_id,
                tenantId="tenant-a",
                userId="user-a",
                threadId=thread,
                dependsOnObjectiveId=depends_on,
                createdAt=NOW,
            )
        )
    await service.repository.bind_managed(
        ManagedTask(
            managedId="managed-dep-b",
            objectiveId=second.objective_id,
            tenantId="tenant-a",
            userId="user-a",
            threadId="thread-dep-b",
            dependsOnObjectiveId=first.objective_id,
            createdAt=NOW,
        )
    )
    # a chain is fine; a cycle is not
    with pytest.raises(ConflictError, match="依赖成环"):
        await service.repository.bind_managed(
            ManagedTask(
                managedId="managed-dep-a",
                objectiveId=first.objective_id,
                tenantId="tenant-a",
                userId="user-a",
                threadId="thread-dep-a",
                dependsOnObjectiveId=second.objective_id,
                createdAt=NOW,
            )
        )


@pytest.mark.asyncio
async def test_project_takeover_collects_threads_under_one_objective(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    main = await service.create("tenant-a", "user-a", brief(), managed_thread_id="thread-main")
    await service.bind_thread("tenant-a", "user-a", main.objective_id, "thread-extra-1")
    await service.bind_thread("tenant-a", "user-a", main.objective_id, "thread-extra-2")
    bindings = await service.repository.managed_bindings(
        "tenant-a", "user-a", objective_id=main.objective_id
    )
    roles = sorted(item.role.value for item in bindings)
    threads = sorted(item.thread_id for item in bindings)
    assert roles == ["execute", "observe", "observe"]
    assert threads == ["thread-extra-1", "thread-extra-2", "thread-main"]


@pytest.mark.asyncio
async def test_specialist_credential_stores_ciphertext_and_reference_only(
    database: DatabaseFixture,
) -> None:
    from sqlalchemy import select

    from harness.assistant.storage import SpecialistCredentialRow
    from harness.studio.mcp_credential_store import McpCredentialCipher

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    service.set_cipher(McpCredentialCipher(SecretStr("unit-test-secret")))  # pyright: ignore[reportPrivateUsage]
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="cost-monitor",
        description="费用监控",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    secret = "sk-live-DO-NOT-LOG-0123456789"
    updated = await service.save_specialist_credential(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        name="vendor_api",
        secret=secret,
        expected_revision=specialist.revision,
    )
    # config 只出现引用
    ref = updated.config.get("credential_ref:vendor_api")
    assert isinstance(ref, str) and ref.startswith("specialist_credential_")
    assert secret not in str(updated.config)
    # 库里是密文：明文既不在 ciphertext 也不在 objectives payload
    async with database[1]() as session:
        row = (
            await session.scalars(
                select(SpecialistCredentialRow).where(
                    SpecialistCredentialRow.reference == ref
                )
            )
        ).first()
        assert row is not None and secret not in row.ciphertext
    objectives_payload = await service.repository.list("tenant-a", "user-a")
    assert all(secret not in str(item.model_dump()) for item in objectives_payload)
    # 无 cipher 时拒绝；密文不可回读给模型（没有读取接口）
    bare = make_service(database)
    with pytest.raises(ConflictError, match="凭据存储未配置"):
        await bare.save_specialist_credential(
            "tenant-a",
            "user-a",
            specialist.specialist_id,
            name="x",
            secret="y",
            expected_revision=updated.revision,
        )


@pytest.mark.asyncio
async def test_chinese_duty_names_dedupe_by_name_not_into_one_bucket(
    database: DatabaseFixture,
) -> None:
    """中文名不能在 slug 规整后互相混桶：同名复用、异名各建。"""
    first, created_first = await service_create_specialist(database, "费用监控")
    assert created_first
    same, created_again = await service_create_specialist(database, "费用监控")
    assert not created_again and same.specialist_id == first.specialist_id
    other, created_other = await service_create_specialist(database, "成本监控")
    assert created_other and other.specialist_id != first.specialist_id


async def service_create_specialist(database: DatabaseFixture, name: str) -> tuple[Any, bool]:
    service = make_service(database)
    return await service.create_specialist(
        "tenant-a",
        "user-a",
        name=name,
        description="中文职责",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )


@pytest.mark.asyncio
async def test_managed_overview_projects_each_thread_and_renders_summary(
    database: DatabaseFixture,
) -> None:
    from harness.core.models import RunStatus

    service = make_service(database)
    agui = _StubAgui()
    runs = _StubRuns()
    service._agui = cast(Any, agui)  # pyright: ignore[reportPrivateUsage]
    service._runs = cast(Any, runs)  # pyright: ignore[reportPrivateUsage]
    coordinator = await service.create(
        "tenant-a", "user-a", brief(), managed_thread_id="thread-main"
    )
    await service.bind_thread("tenant-a", "user-a", coordinator.objective_id, "thread-extra")

    def binding_with(thread_id: str, title: str) -> Any:
        return SimpleNamespace(
            session_id=f"session-for-{thread_id}",
            agent_name="audit-agent",
            agent_version="1.0.0",
            project_id=None,
            title=title,
        )

    registry = {
        "thread-main": binding_with("thread-main", "主任务"),
        "thread-extra": binding_with("thread-extra", "配套任务"),
    }

    def _binding_result(thread_id: str) -> Any:
        return registry[thread_id]

    async def get_binding(**kwargs: Any) -> Any:
        return _binding_result(kwargs["thread_id"])

    agui.get_binding = get_binding  # type: ignore[method-assign]

    async def get_thread_record(**kwargs: Any) -> Any:
        return _binding_result(kwargs["thread_id"])

    agui.get_thread_record = get_thread_record  # type: ignore[method-assign]

    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    stamp = _dt.now(_UTC)

    def run_stub(status, prompt, run_id):
        # managed_overview 现在投影 run_id 与耗时：stub 需要 created/updated。
        return SimpleNamespace(
            status=status,
            input={"prompt": prompt},
            run_id=run_id,
            created_at=stamp,
            updated_at=stamp,
        )

    async def runs_for(_tenant: str, session_ids: list[str], *, limit: int) -> list[Any]:
        if session_ids == ["session-for-thread-extra"]:
            return [run_stub(RunStatus.SUCCEEDED, "已完成配套交付", "run-extra-1")]
        return [run_stub(RunStatus.RUNNING, "正在整理主任务证据", "run-main-1")]

    runs.list_for_sessions = runs_for  # type: ignore[method-assign]
    from harness.core.events import RunEvent
    async def result_events(_tenant: str, run_id: str, _after: int):
        return [RunEvent(
            event_id=f"{run_id}-result", tenant_id="tenant-a", run_id=run_id,
            session_id="session-a", sequence=1, timestamp=stamp, type="message.delta",
            payload={"text": "配套真实结果" if run_id == "run-extra-1" else "主任务当前结果"},
        )]
    service._events = cast(Any, SimpleNamespace(list_after=result_events))
    overview = await service.managed_overview(
        "tenant-a", "user-a", coordinator.objective_id
    )
    assert len(overview) == 2
    rendered = service.render_managed_overview(overview)
    assert "纳管任务进展" in rendered
    assert "主任务" in rendered and "配套任务" in rendered
    assert "配套真实结果" in rendered and "主任务当前结果" in rendered
    assert "已完成配套交付" not in rendered and "正在整理主任务证据" not in rendered


@pytest.mark.asyncio
async def test_measured_usage_lands_on_checkpoints_and_gates_token_budget(
    database: DatabaseFixture,
) -> None:

    service = make_service(database)
    objective = await service.create(
        "tenant-a",
        "user-a",
        ObjectiveBrief.model_validate(
            {
                "title": "计量职责",
                "instructions": "按 token 预算推进",
                "agentName": "audit-agent",
                "agentVersion": "1.0.0",
                "budget": {"maxTokens": 1_000},
            }
        ),
    )
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="u1", now=NOW
    )
    claim = await service.repository.claim(NOW)
    assert claim is not None
    finished = execution.model_copy(
        update={"status": ObjectiveExecutionStatus.SUCCEEDED, "finished_at": NOW}
    )
    # 真实计量：token 与 micro-USD 费用都记到检查点
    assert await service.repository.save_execution(
        finished, claim[1], NOW, usage_tokens=800, usage_cost=0.25
    )
    checkpoints = await service.repository.checkpoints(
        "tenant-a", "user-a", objective.objective_id
    )
    assert checkpoints[0].tokens == 800
    assert checkpoints[0].cost_usd == pytest.approx(0.25)
    assert checkpoints[0].unknown_usage is False

    # 第二次执行把总量推到 1600 > 1000：拒派并说明是 token 上限
    second = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="u2", now=NOW
    )
    claim = await service.repository.claim(NOW)
    assert claim is not None
    await service.repository.save_execution(
        second.model_copy(
            update={"status": ObjectiveExecutionStatus.SUCCEEDED, "finished_at": NOW}
        ),
        claim[1],
        NOW,
        usage_tokens=800,
    )
    with pytest.raises(ConflictError, match="token 上限"):
        await service.repository.enqueue(
            "tenant-a", "user-a", objective.objective_id, trigger_key="u3", now=NOW
        )


@pytest.mark.asyncio
async def test_unmetered_execution_is_reported_not_treated_as_zero(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    objective = await service.create(
        "tenant-a",
        "user-a",
        ObjectiveBrief.model_validate(
            {
                "title": "无计量职责",
                "instructions": "计量不可用时必须如实报告",
                "agentName": "audit-agent",
                "agentVersion": "1.0.0",
                "budget": {"maxTokens": 100},
            }
        ),
    )
    execution = await service.repository.enqueue(
        "tenant-a", "user-a", objective.objective_id, trigger_key="n1", now=NOW
    )
    claim = await service.repository.claim(NOW)
    assert claim is not None
    await service.repository.save_execution(
        execution.model_copy(
            update={"status": ObjectiveExecutionStatus.SUCCEEDED, "finished_at": NOW}
        ),
        claim[1],
        NOW,
    )
    checkpoint = (
        await service.repository.checkpoints("tenant-a", "user-a", objective.objective_id)
    )[0]
    assert checkpoint.unknown_usage is True and checkpoint.tokens is None
    # 有未计量历史时不能声称仍在 token 额度内
    with pytest.raises(ConflictError, match="无法核实"):
        await service.repository.enqueue(
            "tenant-a", "user-a", objective.objective_id, trigger_key="n2", now=NOW
        )


@pytest.mark.asyncio
async def test_unchanged_schedule_skips_the_model_call(database: DatabaseFixture) -> None:
    """定时检查在纳管任务无变化时跳过，不创建 Run。"""
    from types import SimpleNamespace as _Ns

    from harness.core.models import RunStatus

    service = make_service(database)
    agui = _StubAgui()
    runs = _StubRuns()
    service._agui = cast(Any, agui)  # pyright: ignore[reportPrivateUsage]
    service._runs = cast(Any, runs)  # pyright: ignore[reportPrivateUsage]
    created_runs: list[str] = []

    async def create_run(**kwargs: Any) -> Any:
        created_runs.append("created")
        raise AssertionError("model must not be invoked when nothing changed")

    agui.create_run_with_result = create_run  # type: ignore[method-assign]

    objective = await service.create(
        "tenant-a",
        "user-a",
        ObjectiveBrief.model_validate(
            {
                "title": "巡检职责",
                "instructions": "定时巡检纳管任务",
                "agentName": "audit-agent",
                "agentVersion": "1.0.0",
                "budget": {"skipUnchanged": True},
            }
        ),
        managed_thread_id="thread-watch",
    )
    registry = {
        "thread-watch": _Ns(
            session_id="session-for-thread-watch",
            title="巡检对象",
            agent_name="audit-agent",
            agent_version="1.0.0",
            project_id=None,
        )
    }

    async def get_binding(**kwargs: Any) -> Any:
        return registry[kwargs["thread_id"]]

    async def get_thread_record(**kwargs: Any) -> Any:
        return registry[kwargs["thread_id"]]

    from datetime import UTC as _UTC2
    from datetime import datetime as _dt2

    async def list_runs(*_args: Any, **_kwargs: Any) -> list[Any]:
        stamp = _dt2.now(_UTC2)
        return [
            _Ns(
                status=RunStatus.SUCCEEDED,
                input={"prompt": "已完成"},
                run_id="run-watch-1",
                created_at=stamp,
                updated_at=stamp,
            )
        ]

    agui.get_binding = get_binding  # type: ignore[method-assign]
    agui.get_thread_record = get_thread_record  # type: ignore[method-assign]
    runs.list_for_sessions = list_runs  # type: ignore[method-assign]

    fingerprint = await service.progress_fingerprint(
        "tenant-a", "user-a", objective.objective_id
    )
    assert fingerprint
    # 上一轮检查点记录了同一指纹
    await service.repository.save_checkpoint(
        __import__(
            "harness.assistant.models", fromlist=["ObjectiveCheckpoint"]
        ).ObjectiveCheckpoint(
            checkpointId="checkpoint-prev",
            objectiveId=objective.objective_id,
            executionId="execution-prev",
            tenantId="tenant-a",
            userId="user-a",
            revision=1,
            status=ObjectiveExecutionStatus.SUCCEEDED,
            summary=f"本次定时检查未发现变化，未唤起模型。未变化指纹 {fingerprint[:16]}",
            createdAt=NOW,
        )
    )
    execution = await service.repository.enqueue(
        "tenant-a",
        "user-a",
        objective.objective_id,
        trigger_key=f"schedule:{NOW}",
        now=NOW,
    )
    result = await service._reconcile(execution)  # pyright: ignore[reportPrivateUsage]
    assert created_runs == []
    assert result.status is ObjectiveExecutionStatus.SUCCEEDED
    assert "未唤起模型" in result.summary
    assert result.skipped_no_model is True

    # 收尾后：inbox 与 checkpoint 都明确"未唤起模型"，不能冒充 tokens 未计量。
    claim = await service.repository.claim(NOW)
    assert claim is not None
    assert await service.repository.save_execution(result, claim[1], NOW)
    notice = (await service.repository.inbox("tenant-a", "user-a"))[0]
    assert notice.metrics is not None
    assert notice.metrics.skipped_no_model is True
    assert notice.metrics.unknown_usage is False
    assert notice.metrics.tokens is None
    checkpoint = (
        await service.repository.checkpoints(
            "tenant-a", "user-a", objective.objective_id, limit=1
        )
    )[0]
    # checkpoint 是"用量审计"：没有用量就是 unknown；面向用户的 inbox 才区分跳过。
    assert checkpoint.tokens is None
    assert checkpoint.unknown_usage is True


@pytest.mark.asyncio
async def test_shared_desktop_is_single_executor_with_queueing(
    database: DatabaseFixture,
) -> None:
    """共享桌面：一项职责持有时另一项排队等待（不创建 Run），释放后可接手。"""
    from datetime import timedelta

    service = make_service(database)
    first = await service.repository.acquire_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        objective_id="objective-one",
        execution_id="execution-one",
        now=NOW,
    )
    assert first is not None and first.objective_id == "objective-one"
    blocked = await service.repository.acquire_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        objective_id="objective-two",
        execution_id="execution-two",
        now=NOW + timedelta(seconds=10),
    )
    assert blocked is None
    # 同一执行重复领取是幂等的（不重复排队、不自我死锁）
    again = await service.repository.acquire_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        objective_id="objective-one",
        execution_id="execution-one",
        now=NOW + timedelta(seconds=20),
    )
    assert again is not None and again.desktop_turn_id == first.desktop_turn_id
    await service.repository.release_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        execution_id="execution-one",
        now=NOW + timedelta(seconds=30),
    )
    second = await service.repository.acquire_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        objective_id="objective-two",
        execution_id="execution-two",
        now=NOW + timedelta(seconds=40),
    )
    assert second is not None and second.objective_id == "objective-two"
    # 超过 TTL 的持有会被回收，不会永久堵住桌面
    stale = await service.repository.acquire_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        objective_id="objective-three",
        execution_id="execution-three",
        now=NOW + timedelta(seconds=40 + 901),
    )
    assert stale is not None and stale.objective_id == "objective-three"


@pytest.mark.asyncio
async def test_child_task_creation_respects_the_authorization_scope(
    database: DatabaseFixture,
) -> None:
    """新建任务范围：无权时拒绝、超出上限时拒绝、限额内成功并计入父职责。"""
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    plain = await service.create("tenant-a", "user-a", brief())
    with pytest.raises(ConflictError, match="未被授权"):
        await service.create(
            "tenant-a", "user-a", brief(), parent_objective_id=plain.objective_id
        )

    parent = await service.create(
        "tenant-a",
        "user-a",
        ObjectiveBrief.model_validate(
            {
                "title": "项目协调",
                "instructions": "协调项目内后续任务",
                "agentName": "audit-agent",
                "agentVersion": "1.0.0",
                "budget": {"maxNewTasks": 1},
            }
        ),
        managed_thread_id="thread-project",
    )
    child = await service.create(
        "tenant-a", "user-a", brief(), parent_objective_id=parent.objective_id
    )
    assert child.parent_objective_id == parent.objective_id
    assert await service.repository.count_children(
        "tenant-a", "user-a", parent.objective_id
    ) == 1
    with pytest.raises(ConflictError, match="授权上限"):
        await service.create(
            "tenant-a", "user-a", brief(), parent_objective_id=parent.objective_id
        )


@pytest.mark.asyncio
async def test_specialist_trial_run_requires_complete_config_and_reports_scheduling(
    database: DatabaseFixture,
) -> None:
    from harness.assistant.models import SpecialistStatus

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    prepared, _ = await service.create_specialist(
        "tenant-a", "user-a", name="trial-duty", description="trial",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    await service.configure_specialist(
        "tenant-a", "user-a", prepared.specialist_id,
        config={"required_local_tools": ["run_commands"]}, expected_revision=prepared.revision,
    )
    outcome = await service.delegate_to_specialist(
        "tenant-a",
        "user-a",
        name="trial-duty",
        description="试运行职责",
        agent_name="audit-agent",
        agent_version="1.0.0",
        task="做一次检查",
        mode=cast(Any, "autonomous"),
        message_id="delegate-specialist:run-trial:trial-duty",
    )
    specialist = await service.repository.get_specialist(
        "tenant-a", "user-a", str(outcome["specialist_id"])
    )
    # 缺项：拒绝试运行
    with pytest.raises(ConflictError, match="配置缺项"):
        await service.trial_specialist(
            "tenant-a", "user-a", specialist.specialist_id, expected_revision=specialist.revision
        )
    configured = await service.configure_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        config={"local_tool_permissions": ["run_commands"]},
        expected_revision=specialist.revision,
    )
    assert configured.status is SpecialistStatus.READY
    trial = await service.trial_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        expected_revision=configured.revision,
    )
    assert trial["status"] == "pending"
    assert "试运行" in str(trial["scheduled"])
    # 试用后仍是 ready：试运行不冒充"已启用持续运行"
    after = await service.repository.get_specialist(
        "tenant-a", "user-a", specialist.specialist_id
    )
    assert after.status is SpecialistStatus.READY


@pytest.mark.asyncio
async def test_specialist_can_be_available_without_inventing_a_duty(
    database: DatabaseFixture,
) -> None:
    """Availability alone creates neither a duty nor an execution."""
    from harness.assistant.models import SpecialistStatus

    service = make_service(database)
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="duty-less",
        description="尚未转交职责",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    configured = await service.configure_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        config={"schedule_minutes": 60, "notify_channel": "inbox"},
        expected_revision=specialist.revision,
    )
    assert configured.status is SpecialistStatus.READY
    enabled = await service.set_specialist_status(
        "tenant-a", "user-a", specialist.specialist_id,
        status=SpecialistStatus.ACTIVE, expected_revision=configured.revision,
    )
    assert enabled.status is SpecialistStatus.ACTIVE
    assert await service.repository.list("tenant-a", "user-a") == []


@pytest.mark.asyncio
async def test_paused_specialist_resumes_without_duplicate_first_run(
    database: DatabaseFixture,
) -> None:
    """显式暂停的专职子助理可以恢复为 active；已有执行历史的职责不会重复补跑。"""
    from harness.assistant.models import SpecialistStatus

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    duty = await service.create("tenant-a", "user-a", brief())
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="resume",
        description="暂停后可恢复",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    attached = await service.attach_duty(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        objective_id=duty.objective_id,
        expected_revision=specialist.revision,
    )
    configured = await service.configure_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        config={"schedule_minutes": 60, "notify_channel": "inbox"},
        expected_revision=attached.revision,
    )
    assert configured.status is SpecialistStatus.READY

    enabled = await service.set_specialist_status(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        status=SpecialistStatus.ACTIVE,
        expected_revision=configured.revision,
    )
    assert enabled.status is SpecialistStatus.ACTIVE
    await service.trial_specialist(
        "tenant-a", "user-a", specialist.specialist_id, expected_revision=enabled.revision,
    )
    first_executions = await service.repository.executions(
        "tenant-a", "user-a", duty.objective_id
    )
    assert len(first_executions) == 1

    paused = await service.set_specialist_status(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        status=SpecialistStatus.PAUSED,
        expected_revision=enabled.revision,
    )
    assert paused.status is SpecialistStatus.PAUSED

    resumed = await service.set_specialist_status(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        status=SpecialistStatus.ACTIVE,
        expected_revision=paused.revision,
    )
    assert resumed.status is SpecialistStatus.ACTIVE
    # 已有执行历史：恢复不会重复塞一条首跑。
    assert (
        len(await service.repository.executions("tenant-a", "user-a", duty.objective_id))
        == 1
    )

    # 其它状态仍拒绝：先把一个完整配置的实例直接置为 blocked，再尝试激活。
    other, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="blocked",
        description="其它状态",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    other_configured = await service.configure_specialist(
        "tenant-a",
        "user-a",
        other.specialist_id,
        config={"schedule_minutes": 60, "notify_channel": "inbox"},
        expected_revision=other.revision,
    )
    blocked = await service.repository.update_specialist(
        "tenant-a",
        "user-a",
        other.specialist_id,
        expected_revision=other_configured.revision,
        status=SpecialistStatus.BLOCKED,
        now=NOW,
    )
    from harness.core.errors import ConflictError

    with pytest.raises(ConflictError, match="ready 或 paused"):
        await service.set_specialist_status(
            "tenant-a",
            "user-a",
            other.specialist_id,
            status=SpecialistStatus.ACTIVE,
            expected_revision=blocked.revision,
        )


@pytest.mark.asyncio
async def test_attach_existing_duty_to_a_specialist_and_trial_it(
    database: DatabaseFixture,
) -> None:
    """既有职责可挂到子助理；绑定后首次试运行才被允许并真实排入执行。"""
    from harness.assistant.models import SpecialistStatus

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    duty = await service.create("tenant-a", "user-a", brief())
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="attach-duty",
        description="承接既有职责",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    configured = await service.configure_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        config={"schedule_minutes": 1440, "notify_channel": "inbox"},
        expected_revision=specialist.revision,
    )
    attached = await service.attach_duty(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        objective_id=duty.objective_id,
        expected_revision=configured.revision,
    )
    bound = await service.repository.get("tenant-a", "user-a", duty.objective_id)
    assert bound.specialist_id == attached.specialist_id
    # 已挂职责 → 允许试运行，并真实排入一条执行
    trial = await service.trial_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        expected_revision=attached.revision,
    )
    assert trial["objectiveId"] == duty.objective_id
    assert trial["status"] == "pending"
    # 试运行已占住首次执行；启用不会再重复入队。
    enabled = await service.set_specialist_status(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        status=SpecialistStatus.ACTIVE,
        expected_revision=attached.revision,
    )
    assert enabled.status is SpecialistStatus.ACTIVE
    executions = await service.repository.executions("tenant-a", "user-a", duty.objective_id)
    assert len(executions) == 1


@pytest.mark.asyncio
async def test_specialist_conversation_is_the_duty_thread(database: DatabaseFixture) -> None:
    """子助理没有替身会话：它的对话就是承接职责的原线程。"""
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    duty = await service.create("tenant-a", "user-a", brief(), managed_thread_id="thread-duty")
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="chat-duty",
        description="承接职责的子助理",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    await service.attach_duty(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        objective_id=duty.objective_id,
        expected_revision=specialist.revision,
    )
    bindings = await service.repository.managed_bindings(
        "tenant-a", "user-a", objective_id=duty.objective_id
    )
    # 面板解析会话时用的就是这条原线程，不是新开的 assistant_<objective>
    assert bindings and bindings[0].thread_id == "thread-duty"
    bound = await service.repository.get("tenant-a", "user-a", duty.objective_id)
    assert bound.specialist_id == specialist.specialist_id


@pytest.mark.asyncio
async def test_unviewed_specialist_routine_is_skipped_then_recovers(
    database: DatabaseFixture,
) -> None:
    """无人查看超阈值的例行程序：跳过一次调度并留下可读的暂停标记；查看后恢复。"""
    from sqlalchemy import select

    from harness.assistant.storage import SpecialistRow

    service = make_service(database)
    duty = await service.create("tenant-a", "user-a", brief())
    specialist, _ = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="pause-duty",
        description="例行闸门验收",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    await service.attach_duty(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        objective_id=duty.objective_id,
        expected_revision=specialist.revision,
    )

    # 创建即视为查看；下一次检查点由服务用真实时钟写入，各次调度都对齐它。
    fresh = await service.repository.get_specialist(
        "tenant-a", "user-a", specialist.specialist_id
    )
    assert fresh.last_viewed_at is not None
    first = await service.repository.get("tenant-a", "user-a", duty.objective_id)
    assert first.next_check_at is not None
    first_due = first.next_check_at + timedelta(seconds=1)
    await service.repository.schedule_due(first_due, unviewed_pause_hours=24)
    enqueued = await service.repository.executions(
        "tenant-a", "user-a", duty.objective_id
    )
    assert len(enqueued) == 1, "freshly viewed bot must still be scheduled"
    # 同一职责同一时刻只允许一个在途执行：把首条收尾（直改行，避免 token 校验），后续调度才能入队。
    async with database[1].begin() as session:  # type: ignore[operator]
        from sqlalchemy import select

        from harness.assistant.storage import ObjectiveExecutionRow

        exec_row = await session.scalar(
            select(ObjectiveExecutionRow).where(
                ObjectiveExecutionRow.objective_id == duty.objective_id
            )
        )
        assert exec_row is not None
        payload = dict(exec_row.payload)
        payload["status"] = "succeeded"
        payload["finishedAt"] = NOW.isoformat()
        exec_row.payload = payload
        exec_row.status = "succeeded"
    clean = await service.repository.get_specialist(
        "tenant-a", "user-a", specialist.specialist_id
    )
    assert clean.routine_paused_reason is None

    # 把 last_viewed_at 拨回 25 小时前，模拟"此后无人打开"；下一次到期即被闸门拦下。
    second = await service.repository.get("tenant-a", "user-a", duty.objective_id)
    assert second.next_check_at is not None
    second_due = second.next_check_at + timedelta(seconds=1)
    async with database[1].begin() as session:  # type: ignore[operator]
        row = await session.scalar(
            select(SpecialistRow).where(
                SpecialistRow.specialist_id == specialist.specialist_id
            )
        )
        assert row is not None
        payload = dict(row.payload)
        payload["lastViewedAt"] = (second_due - timedelta(hours=25)).isoformat()
        row.payload = payload

    await service.repository.schedule_due(second_due, unviewed_pause_hours=24)
    paused = await service.repository.get_specialist(
        "tenant-a", "user-a", specialist.specialist_id
    )
    assert paused.routine_paused_reason is not None
    assert paused.routine_paused_reason["reason"] == "unviewed"
    assert paused.routine_paused_reason["hours_since_view"] >= 24
    skipped = await service.repository.executions(
        "tenant-a", "user-a", duty.objective_id
    )
    assert len(skipped) == 1, "the unviewed interval must not enqueue an execution"

    # 打开会话（touch）→ 恢复：下一次到期放行，暂停标记清除。
    await service.touch_specialist_viewed(
        "tenant-a", "user-a", specialist.specialist_id
    )
    recovered = await service.repository.get_specialist(
        "tenant-a", "user-a", specialist.specialist_id
    )
    assert recovered.routine_paused_reason is None
    third = await service.repository.get("tenant-a", "user-a", duty.objective_id)
    assert third.next_check_at is not None
    third_due = third.next_check_at + timedelta(seconds=1)
    await service.repository.schedule_due(third_due, unviewed_pause_hours=24)
    resumed = await service.repository.executions(
        "tenant-a", "user-a", duty.objective_id
    )
    assert len(resumed) == 2


@pytest.mark.asyncio
async def test_desktop_occupancy_is_observable_and_waiting_reasons_are_readable(
    database: DatabaseFixture,
) -> None:
    """只读投影：占用者可见；等待者带排队位置与「单执行者占用中」中文原因。"""
    from datetime import timedelta

    service = make_service(database)
    # 占用者：objective-one（带标题）
    holder = await service.repository.acquire_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        objective_id="objective-one",
        execution_id="execution-one",
        now=NOW,
    )
    assert holder is not None
    # 两条 pending 执行：一条属于桌面职责（应出现在 waiting），一条不涉及桌面（不出现）
    brief_desktop = brief(
        **{"title": "每天截图检查", "budget": {"requiresDesktop": True, "maxRunsPerDay": 10}}
    )
    duty_desktop = await service.create("tenant-a", "user-a", brief_desktop)
    brief_plain = brief(**{"title": "普通汇总", "budget": {"maxRunsPerDay": 10}})
    duty_plain = await service.create("tenant-a", "user-a", brief_plain)
    # 两条职责都带 60 分钟 schedule：到期调度入队 pending 执行（排队投影的来源）。
    # service.create 用真实时钟写 next_check_at，这里也对齐真实时钟。
    from datetime import UTC as _UTC

    await service.repository.schedule_due(
        datetime.now(_UTC) + timedelta(minutes=61), unviewed_pause_hours=None
    )
    occupancy = await service.desktop_occupancy(
        "tenant-a", "user-a", now=NOW + timedelta(seconds=60)
    )

    holder_info = occupancy["holder"]
    assert holder_info is not None
    assert holder_info["objective_id"] == "objective-one"
    assert holder_info["execution_id"] == "execution-one"

    waiting = occupancy["waiting"]
    assert all(item["objective_id"] != "objective-one" for item in waiting)
    desktop_waiters = [
        item for item in waiting if item["objective_id"] == duty_desktop.objective_id
    ]
    assert desktop_waiters, "desktop-bound pending execution must be visible in the queue"
    first = desktop_waiters[0]
    assert first["position"] == 1
    assert first["wait_reason"] == "单执行者占用中（另一项职责正在使用共享桌面）"
    plain_waiters = [item for item in waiting if item["objective_id"] == duty_plain.objective_id]
    assert not plain_waiters, "non-desktop executions must not appear in the desktop queue"

    # 占用释放后：无 holder，等待原因回到「等待桌面就绪」。
    await service.repository.release_desktop_turn(
        tenant_id="tenant-a",
        user_id="user-a",
        execution_id="execution-one",
        now=NOW + timedelta(seconds=30),
    )
    released = await service.desktop_occupancy(
        "tenant-a", "user-a", now=NOW + timedelta(seconds=60)
    )
    assert released["holder"] is None
    desktop_waiters_after = [
        item
        for item in released["waiting"]
        if item["objective_id"] == duty_desktop.objective_id
    ]
    assert desktop_waiters_after[0]["wait_reason"] == "等待桌面就绪"


@pytest.mark.asyncio
async def test_trigger_rules_are_owned_by_one_duty_not_the_bot(database: DatabaseFixture) -> None:
    from harness.api.dependencies import Identity
    from harness.assistant.api import ObjectiveScheduleRequest, update_objective_schedule
    from harness.core.errors import NotFoundError

    service = make_service(database)
    bot, _ = await service.create_specialist(
        "tenant-a", "user-a", name="coding-triggers", description="on demand",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    first = await service.create(
        "tenant-a", "user-a", brief(schedule=None, title="first"), specialist_id=bot.specialist_id,
    )
    second = await service.create(
        "tenant-a", "user-a", brief(title="second"), specialist_id=bot.specialist_id,
    )
    # A legacy Bot config edit must not change either duty's explicit trigger.
    await service.configure_specialist(
        "tenant-a", "user-a", bot.specialist_id,
        config={"schedule_minutes": 1440}, expected_revision=bot.revision,
    )
    unchanged = await service.repository.get("tenant-a", "user-a", first.objective_id)
    sibling = await service.repository.get("tenant-a", "user-a", second.objective_id)
    assert unchanged.schedule is None and unchanged.next_check_at is None
    assert sibling.schedule == second.schedule and sibling.revision == second.revision
    identity = Identity(tenant_id="tenant-a", user_id="user-a")
    request = ObjectiveScheduleRequest(schedule=ObjectiveSchedule(intervalMinutes=15),
                                       expectedRevision=first.revision)
    updated = await update_objective_schedule(first.objective_id, request, identity, service)
    assert updated.schedule and updated.schedule.interval_minutes == 15
    assert updated.next_check_at is not None
    assert not await service.repository.executions("tenant-a", "user-a", first.objective_id)
    with pytest.raises(ConflictError):
        await update_objective_schedule(first.objective_id, request, identity, service)
    with pytest.raises(NotFoundError):
        await update_objective_schedule(
            first.objective_id, request, Identity(tenant_id="tenant-a", user_id="other"), service,
        )
    cleared = await update_objective_schedule(
        first.objective_id,
        ObjectiveScheduleRequest(schedule=None, expectedRevision=updated.revision),
        identity, service,
    )
    assert cleared.schedule is None and cleared.next_check_at is None
    assert await service.repository.get("tenant-a", "user-a", second.objective_id) == sibling


@pytest.mark.asyncio
async def test_finished_on_demand_handover_never_becomes_a_periodic_job(
    database: DatabaseFixture,
) -> None:
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())
    outcome = await service.delegate_to_specialist(
        "tenant-a", "user-a", name="coding-once", description="coding",
        agent_name="audit-agent", agent_version="1.0.0", task="检查一次改动",
        mode=cast(Any, "autonomous"), message_id="on-demand-once",
    )
    oid = str(outcome["objective_id"])
    claim = await service.repository.claim(datetime.now(UTC))
    assert claim is not None
    finished = claim[0].model_copy(update={
        "status": ObjectiveExecutionStatus.SUCCEEDED, "finished_at": datetime.now(UTC),
    })
    assert await service.repository.save_execution(finished, claim[1], datetime.now(UTC))
    await service.repository.schedule_due(datetime.now(UTC) + timedelta(days=30))
    assert len(await service.repository.executions("tenant-a", "user-a", oid)) == 1
    objective = await service.repository.get("tenant-a", "user-a", oid)
    assert objective.schedule is None and objective.next_check_at is None


@pytest.mark.asyncio
async def test_new_bot_conversation_exists_without_a_duty_and_does_not_move_on_handover(
    database: DatabaseFixture,
) -> None:
    from harness.api.dependencies import Identity
    from harness.assistant.api import get_specialist
    service = make_service(database)
    bot, _ = await service.create_specialist(
        "tenant-a", "user-a", name="standalone-coding", description="coding",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    identity = Identity(tenant_id="tenant-a", user_id="user-a")
    first = await get_specialist(bot.specialist_id, identity, service)
    assert first["threadId"] == f"assistant_{bot.specialist_id}"
    assert first["duties"] == []
    duty = await service.create(
        "tenant-a", "user-a", brief(schedule=None), specialist_id=bot.specialist_id,
    )
    second = await get_specialist(bot.specialist_id, identity, service)
    assert second["threadId"] == first["threadId"]
    assert second["duties"][0]["hasExecutions"] is False
    assert second["duties"][0]["revision"] == duty.revision
    assert second["duties"][0]["schedule"] is None


@pytest.mark.asyncio
async def test_explicit_periodic_handover_runs_again_and_respects_a_paused_bot(
    database: DatabaseFixture,
) -> None:
    from harness.assistant.models import SpecialistStatus
    service = make_service(database)
    service._agui = cast(Any, _StubAgui())
    result = await service.delegate_to_specialist(
        "tenant-a", "user-a", name="periodic-proof", description="explicit checks",
        agent_name="audit-agent", agent_version="1.0.0", task="每15分钟检查",
        mode=cast(Any, "autonomous"), message_id="explicit-periodic-proof",
        schedule=ObjectiveSchedule(intervalMinutes=15),
    )
    oid = str(result["objective_id"])
    base = datetime.now(UTC)
    async def finish(at: datetime) -> None:
        claim = await service.repository.claim(at)
        assert claim is not None
        done = claim[0].model_copy(update={
            "status": ObjectiveExecutionStatus.SUCCEEDED, "finished_at": at,
        })
        assert await service.repository.save_execution(done, claim[1], at)
    await finish(base)
    await service.repository.schedule_due(base + timedelta(minutes=16))
    assert len(await service.repository.executions("tenant-a", "user-a", oid)) == 2
    await finish(base + timedelta(minutes=16))
    bot = await service.repository.get_specialist(
        "tenant-a", "user-a", str(result["specialist_id"])
    )
    await service.set_specialist_status(
        "tenant-a", "user-a", bot.specialist_id,
        expected_revision=bot.revision, status=SpecialistStatus.PAUSED,
    )
    await service.repository.schedule_due(base + timedelta(minutes=35))
    assert len(await service.repository.executions("tenant-a", "user-a", oid)) == 2


@pytest.mark.asyncio
async def test_legacy_bot_preserves_its_duty_thread_until_standalone_chat_exists(
    database: DatabaseFixture,
) -> None:
    from unittest.mock import AsyncMock

    from harness.api.dependencies import Identity
    from harness.assistant.api import get_specialist
    from harness.core.errors import NotFoundError

    service = make_service(database)
    bot, _ = await service.create_specialist(
        "tenant-a", "user-a", name="legacy-coding", description="coding",
        agent_name="audit-agent", agent_version="1.0.0",
    )
    # Simulate a pre-existing Bot; GET must not migrate or activate it.
    bot = await service.repository.update_specialist(
        "tenant-a", "user-a", bot.specialist_id, expected_revision=bot.revision,
        config={"conversation_thread_id": None}, now=NOW,
    )
    duty = await service.create(
        "tenant-a", "user-a", brief(schedule=None), specialist_id=bot.specialist_id,
    )
    lookup = AsyncMock(side_effect=NotFoundError("not found"))
    service._agui = cast(Any, SimpleNamespace(get_thread_record=lookup))
    identity = Identity(tenant_id="tenant-a", user_id="user-a")
    legacy = await get_specialist(bot.specialist_id, identity, service)
    assert legacy["threadId"] == f"assistant_{duty.objective_id}"
    lookup.assert_awaited_once_with(
        tenant_id="tenant-a", user_id="user-a", thread_id=f"assistant_{bot.specialist_id}"
    )
    lookup.side_effect = None
    lookup.return_value = SimpleNamespace(thread_id=f"assistant_{bot.specialist_id}")
    standalone = await get_specialist(bot.specialist_id, identity, service)
    assert standalone["threadId"] == f"assistant_{bot.specialist_id}"
    assert await service.repository.get_specialist("tenant-a", "user-a", bot.specialist_id) == bot
    assert not await service.repository.executions("tenant-a", "user-a", duty.objective_id)
