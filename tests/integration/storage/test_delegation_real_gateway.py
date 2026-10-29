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
    ManagedTask,
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
async def test_specialist_create_is_deduped_and_config_gates_status(
    database: DatabaseFixture,
) -> None:
    from harness.assistant.models import SpecialistStatus

    service = make_service(database)
    first, created = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="费用监控",
        description="长期关注账号花费",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    assert created and first.status is SpecialistStatus.NEEDS_INPUT
    again, created_again = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="费用监控",
        description="长期关注账号花费",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    assert not created_again and again.specialist_id == first.specialist_id

    # 保存不等于验证：缺项时保持 needs_input；补齐后转 ready。
    updated = await service.configure_specialist(
        "tenant-a",
        "user-a",
        first.specialist_id,
        config={"schedule_minutes": 60},
        expected_revision=first.revision,
    )
    assert updated.status is SpecialistStatus.NEEDS_INPUT
    ready = await service.configure_specialist(
        "tenant-a",
        "user-a",
        first.specialist_id,
        config={"notify_channel": "inbox"},
        expected_revision=updated.revision,
    )
    assert ready.status is SpecialistStatus.READY

    # 缺项不启用：新建的第二个实例在补齐配置前不能进入 active。
    from harness.core.errors import ConflictError

    second, second_created = await service.create_specialist(
        "tenant-a",
        "user-a",
        name="project-patrol",
        description="巡检项目状态",
        agent_name="audit-agent",
        agent_version="1.0.0",
    )
    assert second_created
    with pytest.raises(ConflictError, match="缺项"):
        await service.set_specialist_status(
            "tenant-a",
            "user-a",
            second.specialist_id,
            status=SpecialistStatus.ACTIVE,
            expected_revision=second.revision,
        )


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
    # 配置缺项：职责已登记但回执为 pending_input，不启动执行。
    assert outcome["status"] == "pending_input"
    assert outcome["missing"] == ["schedule_minutes", "notify_channel"]
    specialist = await service.repository.get_specialist(
        "tenant-a", "user-a", str(outcome["specialist_id"])
    )
    objectives = await service.repository.list("tenant-a", "user-a")
    assert len(objectives) == 1 and objectives[0].specialist_id == specialist.specialist_id

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
    assert await service.repository.list("tenant-a", "user-a") == objectives


@pytest.mark.asyncio
async def test_completed_specialist_config_enables_first_execution(
    database: DatabaseFixture,
) -> None:
    from harness.assistant.models import SpecialistStatus

    service = make_service(database)
    service._agui = cast(Any, _StubAgui())  # pyright: ignore[reportPrivateUsage]
    outcome = await service.delegate_to_specialist(
        "tenant-a",
        "user-a",
        name="行业研究",
        description="每日行业快报",
        agent_name="audit-agent",
        agent_version="1.0.0",
        task="每天整理行业动态",
        mode=cast(Any, 'autonomous'),
        message_id="delegate-specialist:run-2:行业研究",
    )
    specialist = await service.repository.get_specialist(
        "tenant-a", "user-a", str(outcome["specialist_id"])
    )
    configured = await service.configure_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        config={"schedule_minutes": 1440, "notify_channel": "inbox"},
        expected_revision=specialist.revision,
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
