"""Tests for the automation scheduling service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from harness.automations.models import (
    AutomationPermission,
    AutomationRecordTrigger,
    AutomationSchedule,
    AutomationScheduleType,
    AutomationStatus,
    AutomationValidity,
    CreateAutomationTaskRequest,
    UpdateAutomationTaskRequest,
)
from harness.automations.repositories import (
    InMemoryAutomationRecordRepository,
    InMemoryAutomationTaskRepository,
)
from harness.automations.service import AutomationService
from harness.core.errors import ConflictError
from harness.core.models import RunStatus

ZONE = "Asia/Shanghai"


@dataclass
class FakeSession:
    session_id: str = "session-1"
    deployment_snapshot_id: str = "snapshot-1"
    agent_version: str = "1.0.2+platform.test"


class FakeBindings:
    def __init__(self) -> None:
        self.by_thread: dict[tuple[str, str, str], Any] = {}
        self.rebinds: list[str] = []

    async def get_by_thread(self, tenant_id: str, user_id: str, thread_id: str) -> Any:
        binding = self.by_thread.get((tenant_id, user_id, thread_id))
        if binding is None:
            from harness.core.errors import NotFoundError

            raise NotFoundError(f"AG-UI thread binding not found: {thread_id}")
        return binding

    async def add(self, binding: Any) -> None:
        self.by_thread[(binding.tenant_id, binding.user_id, binding.thread_id)] = binding

    async def rebind_session(
        self,
        tenant_id: str,
        user_id: str,
        thread_id: str,
        *,
        expected_session_id: str,
        session_id: str,
        updated_at: datetime,
    ) -> Any:
        self.rebinds.append(thread_id)
        binding = self.by_thread[(tenant_id, user_id, thread_id)]
        assert binding.session_id == expected_session_id
        updated = binding.model_copy(update={"session_id": session_id})
        self.by_thread[(tenant_id, user_id, thread_id)] = updated
        return updated


class FakeEvents:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    async def list_after(self, _tenant_id: str, _run_id: str, _sequence: int) -> list[Any]:
        return self.events


@dataclass
class FakeRun:
    run_id: str
    input: dict[str, object] = field(default_factory=dict)
    status: RunStatus = RunStatus.RUNNING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_code: str | None = None


class FakeVersion:
    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self.status = type("Status", (), {"value": "published"})()
        self.created_at = datetime.now(UTC)


class FakeRegistry:
    def __init__(self, versions: list[FakeVersion]) -> None:
        self.versions = versions

    async def list_catalog_for_user(self, _tenant_id: str, _user_id: str) -> list[FakeVersion]:
        return self.versions


class FakeSessions:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.sessions: dict[str, FakeSession] = {}

    async def create(
        self, tenant_id: str, user_id: str, agent_name: str, *_args: Any, **kwargs: Any
    ) -> FakeSession:
        self.created.append((user_id, agent_name))
        session = FakeSession(session_id=kwargs.get("session_id", f"session-{len(self.created)}"))
        self.sessions[session.session_id] = session
        return session

    async def get(self, _tenant_id: str, session_id: str) -> FakeSession:
        from harness.core.errors import NotFoundError

        if session_id not in self.sessions:
            raise NotFoundError(f"Session not found: {session_id}")
        return self.sessions[session_id]


class FakeRuns:
    def __init__(self) -> None:
        self.runs: dict[str, FakeRun] = {}
        self.inputs: list[dict[str, object]] = []

    async def create(
        self,
        _tenant_id: str,
        _session_id: str,
        idempotency_key: str,
        *,
        input: dict[str, object] | None = None,
    ) -> FakeRun:
        run = FakeRun(run_id=f"run-{len(self.runs) + 1}", input=input or {})
        self.runs[idempotency_key] = run
        self.inputs.append(input or {})
        return run

    async def get(self, _tenant_id: str, run_id: str) -> FakeRun:
        for run in self.runs.values():
            if run.run_id == run_id:
                return run
        raise KeyError(run_id)


def build_service(clock=None) -> tuple[AutomationService, FakeSessions, FakeRuns]:
    sessions = FakeSessions()
    runs = FakeRuns()
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    bindings = FakeBindings()
    service = AutomationService(
        InMemoryAutomationTaskRepository(),
        InMemoryAutomationRecordRepository(),
        sessions=sessions,  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        agent_name="lead-agent",
        registry=FakeRegistry([FakeVersion("lead-agent", "1.0.2+platform.test")]),
        executor=None,
        bindings=bindings,  # type: ignore[arg-type]
        events=FakeEvents([]),  # type: ignore[arg-type]
        clock=clock or (lambda: now),
        id_generator=lambda prefix: f"{prefix}-1",
    )
    return service, sessions, runs


def once_request(at: datetime) -> CreateAutomationTaskRequest:
    return CreateAutomationTaskRequest(
        name="体检预约提醒",
        prompt="提醒我确认体检时间",
        schedule=AutomationSchedule(type=AutomationScheduleType.ONCE, at=at, timezone=ZONE),
    )


def cron_request(cron: str, **kwargs: Any) -> CreateAutomationTaskRequest:
    timezone = kwargs.pop("timezone", "UTC")
    return CreateAutomationTaskRequest(
        name=kwargs.pop("name", "每日 AI 新闻推送"),
        prompt=kwargs.pop("prompt", "关注当天 AI 领域的重要动态"),
        schedule=AutomationSchedule(
            type=AutomationScheduleType.CRON, cron=cron, timezone=timezone
        ),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_once_task_fires_once_and_expires() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, sessions, runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1",
        user_id="user-1",
        request=once_request(now + timedelta(minutes=6)),
    )
    assert task.next_run_at == now + timedelta(minutes=6)

    # Not due yet.
    assert await service.dispatch_due() == 0
    # Due.
    now += timedelta(minutes=7)
    service._clock = lambda: now
    assert await service.dispatch_due() == 1
    assert len(runs.runs) == 1
    reloaded = await service.get("tenant-1", "user-1", task.task_id)
    assert reloaded.status is AutomationStatus.EXPIRED
    assert reloaded.next_run_at is None
    # Expired tasks never fire again.
    now += timedelta(minutes=10)
    service._clock = lambda: now
    assert await service.dispatch_due() == 0
    assert sessions.created and sessions.created[0][1] == "lead-agent"


@pytest.mark.asyncio
async def test_cron_task_advances_next_run() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1",
        user_id="user-1",
        request=cron_request("0 8 * * *"),
    )
    assert task.next_run_at == datetime(2026, 9, 20, 8, 0, tzinfo=UTC)

    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    service._clock = lambda: now
    assert await service.dispatch_due() == 1
    reloaded = await service.get("tenant-1", "user-1", task.task_id)
    assert reloaded.status is AutomationStatus.ACTIVE
    assert reloaded.next_run_at == datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    assert len(runs.runs) == 1
    # The same slot is not fired twice.
    assert await service.dispatch_due() == 0


@pytest.mark.asyncio
async def test_dispatch_is_idempotent_across_workers() -> None:
    created_at = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, runs = build_service(clock=lambda: created_at)
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    now = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    service._clock = lambda: now
    rival, _rival_sessions, rival_runs = build_service(clock=lambda: now)
    rival._tasks = service._tasks
    # A second worker racing on the same slot claims nothing.
    assert await service.dispatch_due() == 1
    assert await rival.dispatch_due() == 0
    assert len(runs.runs) == 1
    assert len(rival_runs.runs) == 0
    assert task.task_id


@pytest.mark.asyncio
async def test_run_now_creates_manual_record() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, sessions, _runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    record = await service.run_now(
        tenant_id="tenant-1", user_id="user-1", task_id=task.task_id
    )
    assert record.trigger is AutomationRecordTrigger.MANUAL
    assert record.status.value == "running"
    assert record.task_name == "每日 AI 新闻推送"
    assert sessions.created and sessions.created[0][1] == "lead-agent"


@pytest.mark.asyncio
async def test_records_derive_terminal_status_from_run() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    await service.run_now(tenant_id="tenant-1", user_id="user-1", task_id=task.task_id)
    run = next(iter(runs.runs.values()))
    run.status = RunStatus.FAILED
    run.error_code = "agent_runtime_error"
    records = await service.records(tenant_id="tenant-1", user_id="user-1")
    assert records[0].status.value == "failed"
    assert records[0].error == "agent_runtime_error"
    assert records[0].duration_ms is not None


@pytest.mark.asyncio
async def test_prompt_includes_model_override_when_pinned() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1",
        user_id="user-1",
        request=cron_request("0 8 * * *", model="glm-5"),
    )
    await service.run_now(tenant_id="tenant-1", user_id="user-1", task_id=task.task_id)
    assert isinstance(runs.inputs[0]["prompt"], str)
    assert str(runs.inputs[0]["prompt"]).startswith("[model:glm-5]")


@pytest.mark.asyncio
async def test_update_requires_matching_revision() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, _runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    request = UpdateAutomationTaskRequest(
        expectedRevision=task.revision + 1,
        name="改名",
        prompt="新提示词",
        schedule=AutomationSchedule(
            type=AutomationScheduleType.CRON, cron="0 9 * * *", timezone="UTC"
        ),
    )
    with pytest.raises(ConflictError):
        await service.update(
            tenant_id="tenant-1", user_id="user-1", task_id=task.task_id, request=request
        )
    request = request.model_copy(update={"expected_revision": task.revision})
    updated = await service.update(
        tenant_id="tenant-1", user_id="user-1", task_id=task.task_id, request=request
    )
    assert updated.name == "改名"
    assert updated.next_run_at == datetime(2026, 9, 19, 9, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_pause_and_resume() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, _runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    paused = await service.set_enabled(
        tenant_id="tenant-1",
        user_id="user-1",
        task_id=task.task_id,
        expected_revision=task.revision,
        enabled=False,
    )
    assert paused.status is AutomationStatus.PAUSED
    assert await service.dispatch_due() == 0
    resumed = await service.set_enabled(
        tenant_id="tenant-1",
        user_id="user-1",
        task_id=task.task_id,
        expected_revision=paused.revision,
        enabled=True,
    )
    assert resumed.status is AutomationStatus.ACTIVE


@pytest.mark.asyncio
async def test_validity_until_expires_task_after_date() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, _runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1",
        user_id="user-1",
        request=CreateAutomationTaskRequest(
            name="短期任务",
            prompt="内容",
            schedule=AutomationSchedule(
                type=AutomationScheduleType.CRON, cron="0 7 * * *", timezone=ZONE
            ),
            validity=AutomationValidity(
                type="until", until=datetime(2026, 9, 19, 9, 0, tzinfo=UTC)
            ),
        ),
    )
    # The next slot (7:00 next day) is beyond validity, so the task expires immediately.
    assert task.status is AutomationStatus.EXPIRED
    assert task.next_run_at is None


@pytest.mark.asyncio
async def test_result_delivery_binds_thread_with_task_title() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, sessions, runs = build_service(clock=lambda: now)
    bindings = service._bindings
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    record = await service.run_now(
        tenant_id="tenant-1", user_id="user-1", task_id=task.task_id
    )
    binding = bindings.by_thread[("tenant-1", "user-1", f"automation_{task.task_id}")]
    assert binding.title == "每日 AI 新闻推送"
    assert binding.title_source == "user"
    assert binding.session_id == record.session_id
    # The session is stable per (task, agent version): a second run reuses it.
    record2 = await service.run_now(
        tenant_id="tenant-1", user_id="user-1", task_id=task.task_id
    )
    assert record2.session_id == record.session_id
    assert bindings.rebinds == []


@pytest.mark.asyncio
async def test_record_extracts_output_summary_from_events() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, runs = build_service(clock=lambda: now)
    deltas = [
        type("Event", (), {"type": "message.delta", "payload": {"text": "今日 AI 要点：…"}, "event_id": "1"})(),
    ]
    service._events = FakeEvents(deltas)
    task = await service.create(
        tenant_id="tenant-1", user_id="user-1", request=cron_request("0 8 * * *")
    )
    await service.run_now(tenant_id="tenant-1", user_id="user-1", task_id=task.task_id)
    run = next(iter(runs.runs.values()))
    run.status = RunStatus.SUCCEEDED
    records = await service.records(tenant_id="tenant-1", user_id="user-1")
    assert records[0].status.value == "success"
    assert records[0].output_summary == "今日 AI 要点：…"


class FailingSessions:
    """Simulates deterministic dispatch failures (missing Agent, etc.)."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def get(self, _tenant_id: str, _session_id: str) -> FakeSession:
        raise self.error

    async def create(self, *_args: Any, **_kwargs: Any) -> FakeSession:
        raise self.error


@pytest.mark.asyncio
async def test_deterministic_dispatch_failure_records_failure_without_retry_spin() -> None:
    created_at = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    runs = FakeRuns()
    now = datetime(2026, 9, 19, 8, 6, tzinfo=UTC)
    service = AutomationService(
        InMemoryAutomationTaskRepository(),
        InMemoryAutomationRecordRepository(),
        sessions=FailingSessions(ConflictError("automation agent has no published version: x")),  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        agent_name="lead-agent",
        registry=FakeRegistry([FakeVersion("lead-agent", "1.0.2")]),
        clock=lambda: now,
        id_generator=lambda prefix: f"{prefix}-1",
    )
    task = await service.create(
        tenant_id="tenant-1",
        user_id="user-1",
        request=once_request(datetime(2026, 9, 19, 8, 5, tzinfo=UTC)),
    )
    assert await service.dispatch_due() == 1
    reloaded = await service.get("tenant-1", "user-1", task.task_id)
    assert reloaded.status is AutomationStatus.EXPIRED
    records = await service.records(tenant_id="tenant-1", user_id="user-1")
    assert records[0].status.value == "failed"
    assert "no published version" in (records[0].error or "")
    assert len(runs.runs) == 0
    # No retry spin: expired once tasks never fire again.
    now = datetime(2026, 9, 19, 8, 10, tzinfo=UTC)
    service._clock = lambda: now
    assert await service.dispatch_due() == 0


@pytest.mark.asyncio
async def test_permission_defaults_to_full_with_explicit_value() -> None:
    now = datetime(2026, 9, 19, 8, 0, tzinfo=UTC)
    service, _sessions, _runs = build_service(clock=lambda: now)
    task = await service.create(
        tenant_id="tenant-1",
        user_id="user-1",
        request=CreateAutomationTaskRequest(
            name="受限任务",
            prompt="内容",
            permission=AutomationPermission.READONLY,
            schedule=AutomationSchedule(
                type=AutomationScheduleType.CRON, cron="0 7 * * *", timezone=ZONE
            ),
        ),
    )
    assert task.permission is AutomationPermission.READONLY
