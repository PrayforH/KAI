"""Delegation tool behaviour against a typed in-memory gateway."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from harness.assistant.delegation import (
    assistant_delegation_overlay,
    coordinator_session,
)
from harness.assistant.models import (
    Objective,
    ObjectiveAction,
    ObjectiveBrief,
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    ObjectiveStatus,
)
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import Run, RunStatus, Session

NOW = datetime(2026, 10, 28, 3, 0, tzinfo=UTC)


class FakeGateway:
    """Records calls and stores objectives in memory.

    Mirrors the real repository contract: ``executions`` returns newest first,
    and ``create`` is idempotent on the caller's key.
    """

    def __init__(self) -> None:
        self.objectives: dict[str, Objective] = {}
        self.records: dict[str, list[ObjectiveExecution]] = {}
        self.created: list[ObjectiveBrief] = []
        self.idempotency_keys: dict[str, str | None] = {}
        self.notes: list[tuple[str, str]] = []
        self.bindings: list[dict[str, Any]] = []
        self.resolved_queries: list[str] = []
        self.delegate_calls: list[dict[str, Any]] = []
        self.bound_threads: list[tuple[str, str]] = []
        self.instruct_outcome: dict[str, Any] = {
            "status": "queued",
            "message": "当前没有进行中的执行",
        }
        self.checkpoint_summaries: list[Any] = []
        self.specialists: list[Any] = []
        self.handovers: list[Any] = []
        self.delegate_outcome: dict[str, Any] = {
            "handover_id": "handover-1",
            "specialist_id": "specialist-1",
            "objective_id": "objective-9",
            "status": "pending_input",
            "missing": ["schedule_minutes", "notify_channel"],
            "created_specialist": True,
        }
        self.enqueue_error: Exception | None = None

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        brief: ObjectiveBrief,
        *,
        idempotency_key: str | None,
        managed_thread_id: str | None = None,
        mode: str | None = None,
        parent_objective_id: str | None = None,
    ) -> Objective:
        for objective_id, key in self.idempotency_keys.items():
            if key is not None and key == idempotency_key:
                return self.objectives[objective_id]
        self.created.append(brief)
        objective = Objective(
            **brief.model_dump(),
            objectiveId=f"objective_{len(self.created):04d}",
            tenantId=tenant_id,
            userId=user_id,
            status=ObjectiveStatus.ACTIVE,
            managedThreadId=managed_thread_id,
            parentObjectiveId=parent_objective_id,
            mode=cast("Any", mode or "autonomous"),
            createdAt=NOW,
            updatedAt=NOW,
        )
        self.objectives[objective.objective_id] = objective
        self.idempotency_keys[objective.objective_id] = idempotency_key
        return objective

    async def enqueue(
        self, tenant_id: str, user_id: str, objective_id: str, *, trigger_key: str
    ) -> ObjectiveExecution:
        if self.enqueue_error is not None:
            raise self.enqueue_error
        for existing in self.records.get(objective_id, []):
            if existing.trigger_key == trigger_key:
                return existing
        managed = self.objectives[objective_id].managed_thread_id
        execution = ObjectiveExecution(
            executionId=f"objective_run_{trigger_key}",
            objectiveId=objective_id,
            tenantId=tenant_id,
            userId=user_id,
            triggerKey=trigger_key,
            objectiveRevision=1,
            brief=self.objectives[objective_id],
            threadId=managed or f"assistant_{objective_id}",
            status=ObjectiveExecutionStatus.PENDING,
            createdAt=NOW + timedelta(minutes=len(self.records.get(objective_id, []))),
        )
        self.records.setdefault(objective_id, []).insert(0, execution)
        return execution

    async def list_objectives(self, tenant_id: str, user_id: str) -> list[Objective]:
        return list(self.objectives.values())

    async def executions(
        self, tenant_id: str, user_id: str, objective_id: str
    ) -> list[ObjectiveExecution]:
        return self.records.get(objective_id, [])

    async def resolve_targets(
        self, tenant_id: str, user_id: str, query: str, *, limit: int = 5
    ) -> list[dict[str, Any]]:
        self.resolved_queries.append(query)
        needle = query.strip().lower()
        matches = [
            item
            for item in self.bindings
            if needle in item["thread_id"].lower()
            or needle in (item["title"] or "").lower()
        ]
        return matches[:limit]

    async def instruct(
        self, tenant_id: str, user_id: str, objective_id: str, text: str, *, request_id: str
    ) -> dict[str, Any]:
        await self.add_note(tenant_id, user_id, objective_id, note=text)
        return self.instruct_outcome

    async def checkpoints(
        self, tenant_id: str, user_id: str, objective_id: str, *, limit: int = 3
    ) -> list[Any]:
        return self.checkpoint_summaries

    async def delegate_to_specialist(
        self,
        tenant_id: str,
        user_id: str,
        *,
        name: str,
        description: str,
        agent_name: str,
        agent_version: str,
        task: str,
        mode: str,
        message_id: str,
    ) -> dict[str, Any]:
        self.delegate_calls.append(
            {"name": name, "task": task, "mode": mode, "message_id": message_id}
        )
        return self.delegate_outcome

    async def bind_thread(
        self, tenant_id: str, user_id: str, objective_id: str, thread_id: str
    ) -> None:
        self.bound_threads.append((objective_id, thread_id))

    async def list_specialists(self, tenant_id: str, user_id: str) -> list[Any]:
        return self.specialists

    async def specialist_handovers(
        self, tenant_id: str, user_id: str, *, specialist_id: str | None = None
    ) -> list[Any]:
        return self.handovers

    async def add_note(
        self, tenant_id: str, user_id: str, objective_id: str, *, note: str
    ) -> Objective:
        objective = self.objectives[objective_id]
        updated = objective.model_copy(update={"revision": objective.revision + 1})
        self.objectives[objective_id] = updated
        self.notes.append((objective_id, note))
        return updated

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        objective_id: str,
        *,
        expected_revision: int,
        action: ObjectiveAction,
    ) -> Objective:
        objective = self.objectives[objective_id]
        if objective.revision != expected_revision:
            raise ConflictError("Objective changed; reload before applying this change")
        from harness.assistant.lifecycle import transition

        updated = objective.model_copy(
            update={
                "revision": objective.revision + 1,
                "status": transition(objective.status, action),
            }
        )
        self.objectives[objective_id] = updated
        return updated


def overlay(gateway: FakeGateway, *, coordinator_thread: bool = True) -> Any:
    run = Run(
        run_id="run-1",
        session_id="session-1",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="k",
        created_at=NOW,
        updated_at=NOW,
    )
    session = Session(
        session_id="session-1",
        tenant_id="t",
        user_id="u",
        agent_name="kai-personal-assistant",
        agent_version="1",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    return assistant_delegation_overlay(
        gateway, run, session, coordinator_thread=coordinator_thread
    )


def text_of(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_create_task_persists_and_enqueues_one_execution():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_create_task"]({"task": "整理季度报告初稿"})
    assert "已安排" in text_of(result)
    assert "已创建并开始执行" not in text_of(result)
    assert len(gateway.created) == 1
    brief = gateway.created[0]
    assert brief.agent_name == "kai-personal-assistant" and brief.schedule is None
    # The trigger key carries the delegating run: retries cannot re-dispatch.
    execution = gateway.records["objective_0001"][0]
    assert execution.trigger_key == "delegate:run-1:objective_0001"
    assert execution.thread_id == "assistant_objective_0001"
    assert gateway.idempotency_keys["objective_0001"] is not None
    assert gateway.idempotency_keys["objective_0001"].startswith("delegate:run-1:")


@pytest.mark.asyncio
async def test_repeated_tool_call_reuses_the_same_objective():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    first = await tools["assistant_create_task"]({"task": "整理季度报告初稿"})
    second = await tools["assistant_create_task"]({"task": "整理季度报告初稿"})
    assert len(gateway.created) == 1
    assert "objective_0001" in text_of(second)
    assert "objective_0001" in text_of(first)
    # Only one durable execution intent exists for the repeated request.
    assert len(gateway.records["objective_0001"]) == 1


@pytest.mark.asyncio
async def test_delegation_tools_only_exist_for_the_coordinator_thread():
    gateway = FakeGateway()
    overlay_off_coordinator = overlay(gateway, coordinator_thread=False)
    assert overlay_off_coordinator.tools == () and not overlay_off_coordinator.instructions


@pytest.mark.asyncio
async def test_coordinator_session_resolves_the_personal_thread():
    from types import SimpleNamespace

    from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD

    class Agui:
        def __init__(self, thread_id: str | None) -> None:
            self._thread_id = thread_id

        async def get_thread_record_for_session(self, **_kwargs: Any):
            if self._thread_id is None:
                raise NotFoundError("no binding")
            return SimpleNamespace(thread_id=self._thread_id)

    session = Session(
        session_id="session-1",
        tenant_id="t",
        user_id="u",
        agent_name="kai-personal-assistant",
        agent_version="1",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    assert await coordinator_session(Agui(PERSONAL_ASSISTANT_THREAD), session)
    assert not await coordinator_session(Agui("assistant_objective_x"), session)
    assert not await coordinator_session(Agui(None), session)


@pytest.mark.asyncio
async def test_create_task_supports_periodic_schedule():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_create_task"](
        {"task": "每小时检查页面", "interval_minutes": 60}
    )
    assert "已安排" in text_of(result)
    assert gateway.created[0].schedule is not None
    assert gateway.created[0].schedule.interval_minutes == 60


@pytest.mark.asyncio
async def test_create_task_rejects_bad_interval():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_create_task"]({"task": "x", "interval_minutes": 5})
    assert "15" in text_of(result)
    assert gateway.created == []


@pytest.mark.asyncio
async def test_status_lists_objectives_with_revision_and_latest_execution():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    await tools["assistant_create_task"]({"task": "观察某页面变化"})
    # The newest execution wins: the gateway returns newest first, so an older
    # queued execution must not shadow a newer failure.
    pending = gateway.records["objective_0001"][0]
    failed = pending.model_copy(
        update={"status": ObjectiveExecutionStatus.FAILED, "summary": "失败"}
    )
    gateway.records["objective_0001"] = [failed, pending]
    status = await tools["assistant_task_status"]({})
    body = text_of(status)
    assert "[active]" in body and "最近执行：failed" in body
    assert "revision=1" in body
    assert "expected_revision" in body


@pytest.mark.asyncio
async def test_update_task_reports_the_new_revision():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    await tools["assistant_create_task"]({"task": "任务"})
    ok = await tools["assistant_update_task"](
        {"objective_id": "objective_0001", "action": "pause", "expected_revision": 1}
    )
    assert "revision=2" in text_of(ok)


@pytest.mark.asyncio
async def test_update_task_requires_fresh_revision():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    await tools["assistant_create_task"]({"task": "任务"})
    stale = await tools["assistant_update_task"](
        {"objective_id": "objective_0001", "action": "pause", "expected_revision": 9}
    )
    assert "失败" in text_of(stale)
    ok = await tools["assistant_update_task"](
        {"objective_id": "objective_0001", "action": "pause", "expected_revision": 1}
    )
    assert "paused" in text_of(ok)


@pytest.mark.asyncio
async def test_add_context_records_a_note_for_the_next_execution():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    await tools["assistant_create_task"]({"task": "任务"})
    result = await tools["assistant_add_context"](
        {"objective_id": "objective_0001", "context": "优先用 CSV 格式"}
    )
    assert "已补充" in text_of(result)
    assert gateway.notes == [("objective_0001", "优先用 CSV 格式")]
    empty = await tools["assistant_add_context"]({"objective_id": "objective_0001", "context": " "})
    assert "1–8000" in text_of(empty)


@pytest.mark.asyncio
async def test_enqueue_failure_reports_instead_of_raising():
    gateway = FakeGateway()
    gateway.enqueue_error = ConflictError("already running")
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_create_task"]({"task": "任务"})
    assert "未能创建" in text_of(result)


@pytest.mark.asyncio
async def test_manage_work_binds_the_original_thread():
    gateway = FakeGateway()
    gateway.bindings = [
        {
            "thread_id": "task-thread-1",
            "title": "季度报告",
            "agent_name": "kai-personal-assistant",
            "agent_version": "1",
            "project_id": None,
            "managed_objective_id": None,
        }
    ]
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_manage_work"]({"thread_id": "task-thread-1"})
    body = text_of(result)
    assert "已接管原任务" in body and "task-thread-1" in body
    objective = gateway.objectives["objective_0001"]
    assert objective.managed_thread_id == "task-thread-1"
    # The execution continues on the original thread, not a stand-in.
    assert gateway.records["objective_0001"][0].thread_id == "task-thread-1"
    # A second takeover reports the existing coordinator instead of duplicating.
    gateway.bindings[0]["managed_objective_id"] = "objective_0001"
    again = await tools["assistant_manage_work"]({"thread_id": "task-thread-1"})
    assert "不重复接管" in text_of(again)


@pytest.mark.asyncio
async def test_manage_work_follow_only_does_not_execute():
    gateway = FakeGateway()
    gateway.bindings = [
        {
            "thread_id": "task-thread-2",
            "title": "采购跟进",
            "agent_name": "kai-personal-assistant",
            "agent_version": "1",
            "project_id": None,
            "managed_objective_id": None,
        }
    ]
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_manage_work"](
        {"thread_id": "task-thread-2", "mode": "follow_only"}
    )
    assert "只跟进" in text_of(result)
    assert gateway.records.get("objective_0001") is None


@pytest.mark.asyncio
async def test_resolve_target_lists_candidates_for_ambiguous_names():
    gateway = FakeGateway()
    gateway.bindings = [
        {
            "thread_id": "thread-a",
            "title": "季度报告",
            "agent_name": "agent-a",
            "agent_version": "1",
            "project_id": None,
            "managed_objective_id": None,
        },
        {
            "thread_id": "thread-b",
            "title": "季度报告 二稿",
            "agent_name": "agent-a",
            "agent_version": "1",
            "project_id": None,
            "managed_objective_id": None,
        },
    ]
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_resolve_target"]({"query": "季度报告"})
    body = text_of(result)
    assert "thread-a" in body and "thread-b" in body


@pytest.mark.asyncio
async def test_send_instruction_reports_the_delivery_status():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    await tools["assistant_create_task"]({"task": "任务"})
    gateway.instruct_outcome = {"status": "accepted", "message": "已提交当前运行"}
    accepted = await tools["assistant_send_instruction"](
        {"objective_id": "objective_0001", "context": "改用 CSV"}
    )
    assert "accepted" in text_of(accepted) and "已送达当前执行" in text_of(accepted)
    gateway.instruct_outcome = {"status": "queued", "message": "当前执行未就绪"}
    queued = await tools["assistant_send_instruction"](
        {"objective_id": "objective_0001", "context": "改用 CSV"}
    )
    assert "queued" in text_of(queued) and "下一次执行" in text_of(queued)


@pytest.mark.asyncio
async def test_status_includes_the_latest_checkpoint():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    await tools["assistant_create_task"]({"task": "任务"})
    from types import SimpleNamespace

    gateway.checkpoint_summaries = [SimpleNamespace(summary="已完成数据校验，下一步生成图表")]
    status = await tools["assistant_task_status"]({})
    assert "已完成数据校验" in text_of(status)


@pytest.mark.asyncio
async def test_delegate_to_specialist_reports_missing_config_without_starting():
    gateway = FakeGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_delegate_to_specialist"](
        {"name": "费用监控", "task": "长期关注某账号花费"}
    )
    body = text_of(result)
    assert "pending_input" in body and "schedule_minutes" in body and "缺项不启动" in body
    call = gateway.delegate_calls[0]
    assert call["mode"] == "autonomous"


@pytest.mark.asyncio
async def test_delegate_replay_reports_receipt_without_recreating():
    gateway = FakeGateway()
    gateway.delegate_outcome = {
        "handover_id": "handover-1",
        "specialist_id": "specialist-1",
        "objective_id": "objective-9",
        "status": "sent",
        "missing": [],
        "created_specialist": False,
    }
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_delegate_to_specialist"](
        {"name": "费用监控", "task": "长期关注花费"}
    )
    assert "复用已有实例" in text_of(result)


@pytest.mark.asyncio
async def test_specialist_status_lists_instances_and_receipts():
    gateway = FakeGateway()
    from types import SimpleNamespace

    gateway.specialists = [
        SimpleNamespace(
            specialist_id="specialist-1",
            name="费用监控",
            status=SimpleNamespace(value="needs_input"),
            revision=2,
        )
    ]
    gateway.handovers = [
        SimpleNamespace(specialist_id="specialist-1", status=SimpleNamespace(value="pending_input"))
    ]
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_specialist_status"]({})
    body = text_of(result)
    assert "费用监控" in body and "pending_input" in body


@pytest.mark.asyncio
async def test_read_only_observer_overlay_has_no_delegation_rights():
    gateway = FakeGateway()
    overlay_observer = assistant_delegation_overlay(
        gateway,
        Run(
            run_id="run-obs",
            session_id="session-1",
            tenant_id="t",
            status=RunStatus.RUNNING,
            idempotency_key="k",
            created_at=NOW,
            updated_at=NOW,
        ),
        Session(
            session_id="session-1",
            tenant_id="t",
            user_id="u",
            agent_name="kai-personal-assistant",
            agent_version="1",
            created_at=NOW,
            environment="production",
            runtime_type="claude-agent-sdk",
        ),
        coordinator_thread=True,
        read_only=True,
    )
    names = {tool.name for tool in overlay_observer.tools}
    assert names == {"assistant_task_status"}
    assert "不得创建新任务" in (overlay_observer.instructions or "")


@pytest.mark.asyncio
async def test_memory_gateway_scopes_to_specialist_when_set():
    from harness.assistant.memory_tools import MemoryBankGateway

    class Bank:
        def __init__(self) -> None:
            self.seen: list[str] = []

        async def search(
            self, tenant_id: str, user_id: str, agent_name: str, query: str
        ) -> list[object]:
            self.seen.append(agent_name)
            return []

    bank = Bank()
    await MemoryBankGateway(bank).search("t", "u", "audit-agent", "q")
    assert bank.seen == ["audit-agent"]
    await MemoryBankGateway(bank, scope="specialist:cost-monitor").search(
        "t", "u", "audit-agent", "q"
    )
    assert bank.seen[-1] == "specialist:cost-monitor"


@pytest.mark.asyncio
async def test_specialist_memory_scope_resolves_duty_threads():
    from types import SimpleNamespace as _Ns

    from harness.assistant.delegation import specialist_memory_scope

    class Assistant:
        class repository:  # noqa: N801
            @staticmethod
            async def get(tenant_id: str, user_id: str, objective_id: str):
                if objective_id == "objective-spec":
                    return _Ns(specialist_id="specialist-1")
                return _Ns(specialist_id=None)

            @staticmethod
            async def get_specialist(tenant_id: str, user_id: str, specialist_id: str):
                return _Ns(slug="cost-monitor")

    class Agui:
        async def get_thread_record_for_session(self, **kwargs: Any) -> Any:
            thread = str(kwargs["session_id"])
            if thread == "no-binding":
                raise NotFoundError("none")
            return _Ns(thread_id=thread)

    session = Session(
        session_id="assistant_objective-spec",
        tenant_id="t",
        user_id="u",
        agent_name="audit-agent",
        agent_version="1",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    assert await specialist_memory_scope(Assistant(), Agui(), session) == (
        "specialist:cost-monitor"
    )
    plain = session.model_copy(update={"session_id": "assistant_objective-plain"})
    assert await specialist_memory_scope(Assistant(), Agui(), plain) is None
    unbound = session.model_copy(update={"session_id": "no-binding"})
    assert await specialist_memory_scope(Assistant(), Agui(), unbound) is None
