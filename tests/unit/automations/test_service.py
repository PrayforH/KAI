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


@dataclass
class FakeRun:
    run_id: str
    input: dict[str, object] = field(default_factory=dict)
    status: RunStatus = RunStatus.RUNNING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_code: str | None = None


class FakeSessions:
    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []

    async def create(
        self, tenant_id: str, user_id: str, agent_name: str, *_args: Any, **_kwargs: Any
    ) -> FakeSession:
        self.created.append((user_id, agent_name))
        return FakeSession()


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
    service = AutomationService(
        InMemoryAutomationTaskRepository(),
        InMemoryAutomationRecordRepository(),
        sessions=sessions,  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        agent_name="lead-agent",
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
