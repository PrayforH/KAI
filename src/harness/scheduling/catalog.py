"""One read model for every schedule a person can see.

Objectives and automation tasks persist different shapes because they execute
differently, but to a person they are the same thing: something that runs later.
This module translates both into one ``ScheduleTask`` value so the Web client has
a single catalog to read, sort and filter. It owns no writes and no execution.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import AwareDatetime, Field

from harness.assistant.models import Objective
from harness.automations.models import AutomationScheduleType, AutomationTask
from harness.scheduling.timing import Timing, TimingKind
from harness.studio.models import StudioModel


class ScheduleSource(StrEnum):
    OBJECTIVE = "objective"
    AUTOMATION = "automation"


class ScheduleTiming(StudioModel):
    """The timing fields a client needs to render and edit a schedule."""

    kind: TimingKind
    at: AwareDatetime | None = None
    seconds: int | None = None
    expression: str | None = None
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_timing(cls, timing: Timing) -> ScheduleTiming:
        return cls(
            kind=timing.kind,
            at=timing.at,
            seconds=timing.seconds,
            expression=timing.expression,
            timezone=timing.timezone,
        )


class ScheduleTask(StudioModel):
    """A schedule as the catalog presents it, independent of who executes it."""

    schedule_id: str = Field(alias="scheduleId")
    source: ScheduleSource
    title: str
    status: str
    timing: ScheduleTiming
    specialist_id: str | None = Field(default=None, alias="specialistId")
    agent_name: str | None = Field(default=None, alias="agentName")
    next_run_at: AwareDatetime | None = Field(default=None, alias="nextRunAt")
    last_run_at: AwareDatetime | None = Field(default=None, alias="lastRunAt")
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")

    @property
    def sort_key(self) -> tuple[bool, datetime, str]:
        return (self.next_run_at is None, self.next_run_at or self.created_at, self.schedule_id)


class ObjectiveSource(Protocol):
    async def list(self, tenant_id: str, user_id: str, *, limit: int = 100) -> list[Objective]: ...


class AutomationSource(Protocol):
    async def list(self, tenant_id: str, user_id: str) -> list[AutomationTask]: ...


def _automation_timing(task: AutomationTask) -> ScheduleTiming:
    if task.schedule.type is AutomationScheduleType.ONCE:
        assert task.schedule.at is not None
        return ScheduleTiming.from_timing(Timing.once(task.schedule.at))
    return ScheduleTiming.from_timing(
        Timing.cron(task.schedule.cron or "", task.schedule.timezone)
    )


def from_objective(objective: Objective) -> ScheduleTask | None:
    """Project one objective; manual-only objectives have no schedule to show."""

    if objective.schedule is None:
        return None
    return ScheduleTask(
        scheduleId=objective.objective_id,
        source=ScheduleSource.OBJECTIVE,
        title=objective.title,
        status=objective.status.value,
        timing=ScheduleTiming(
            kind=TimingKind.EVERY,
            seconds=objective.schedule.interval_minutes * 60,
            timezone=objective.schedule.timezone,
        ),
        specialistId=objective.specialist_id,
        agentName=objective.agent_name,
        nextRunAt=objective.next_check_at,
        createdAt=objective.created_at,
        updatedAt=objective.updated_at,
    )


def from_automation(task: AutomationTask) -> ScheduleTask:
    return ScheduleTask(
        scheduleId=task.task_id,
        source=ScheduleSource.AUTOMATION,
        title=task.name,
        status=task.status.value,
        timing=_automation_timing(task),
        nextRunAt=task.next_run_at,
        lastRunAt=task.last_run_at,
        createdAt=task.created_at,
        updatedAt=task.updated_at,
    )


class ScheduleCatalog:
    """Read both schedule stores and answer with one ordered list.

    ``specialist_id`` narrows the result to objectives owned by one specialist;
    automation tasks have no specialist, so they are omitted under that filter.
    """

    def __init__(self, objectives: ObjectiveSource, automations: AutomationSource) -> None:
        self._objectives = objectives
        self._automations = automations

    async def list(
        self,
        tenant_id: str,
        user_id: str,
        *,
        specialist_id: str | None = None,
        source: ScheduleSource | None = None,
    ) -> list[ScheduleTask]:
        tasks: list[ScheduleTask] = []
        if source in (None, ScheduleSource.OBJECTIVE):
            for objective in await self._objectives.list(tenant_id, user_id):
                if specialist_id is not None and objective.specialist_id != specialist_id:
                    continue
                projected = from_objective(objective)
                if projected is not None:
                    tasks.append(projected)
        if source in (None, ScheduleSource.AUTOMATION) and specialist_id is None:
            tasks.extend(
                from_automation(task)
                for task in await self._automations.list(tenant_id, user_id)
            )
        return sorted(tasks, key=lambda task: task.sort_key)

