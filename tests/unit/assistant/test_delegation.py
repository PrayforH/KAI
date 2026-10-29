"""Delegation tool behaviour against a typed in-memory gateway."""

from datetime import UTC, datetime, timedelta
from typing import Any

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
        self.enqueue_error: Exception | None = None

    async def create(
        self, tenant_id: str, user_id: str, brief: ObjectiveBrief, *, idempotency_key: str | None
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
        execution = ObjectiveExecution(
            executionId=f"objective_run_{trigger_key}",
            objectiveId=objective_id,
            tenantId=tenant_id,
            userId=user_id,
            triggerKey=trigger_key,
            objectiveRevision=1,
            brief=self.objectives[objective_id],
            threadId=f"assistant_{objective_id}",
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
