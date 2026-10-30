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

    async def runs_for(_tenant: str, session_ids: list[str], *, limit: int) -> list[Any]:
        if session_ids == ["session-for-thread-extra"]:
            return [
                SimpleNamespace(
                    status=RunStatus.SUCCEEDED,
                    input={"prompt": "已完成配套交付"},
                )
            ]
        return [
            SimpleNamespace(
                status=RunStatus.RUNNING,
                input={"prompt": "正在整理主任务证据"},
            )
        ]

    runs.list_for_sessions = runs_for  # type: ignore[method-assign]
    overview = await service.managed_overview(
        "tenant-a", "user-a", coordinator.objective_id
    )
    assert len(overview) == 2
    rendered = service.render_managed_overview(overview)
    assert "纳管任务进展" in rendered
    assert "主任务" in rendered and "配套任务" in rendered
    assert "已完成配套交付" in rendered and "正在整理主任务证据" in rendered


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

    async def list_runs(*_args: Any, **_kwargs: Any) -> list[Any]:
        return [_Ns(status=RunStatus.SUCCEEDED, input={"prompt": "已完成"})]

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
        config={"schedule_minutes": 60, "notify_channel": "inbox"},
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
async def test_specialist_without_a_duty_cannot_be_activated(database: DatabaseFixture) -> None:
    """没有职责就没有调度对象：启用必须拒绝，并说明缺的是职责。"""
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
    with pytest.raises(ConflictError, match="先转交一项职责"):
        await service.set_specialist_status(
            "tenant-a",
            "user-a",
            specialist.specialist_id,
            status=SpecialistStatus.ACTIVE,
            expected_revision=configured.revision,
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
    # 已挂职责 → 允许启用
    enabled = await service.set_specialist_status(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        status=SpecialistStatus.ACTIVE,
        expected_revision=attached.revision,
    )
    assert enabled.status is SpecialistStatus.ACTIVE
    trial = await service.trial_specialist(
        "tenant-a",
        "user-a",
        specialist.specialist_id,
        expected_revision=enabled.revision,
    )
    assert trial["objectiveId"] == duty.objective_id
    assert trial["status"] == "pending"
