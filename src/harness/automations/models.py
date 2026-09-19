"""Automation task and run record contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from harness.studio.models import StudioModel


class AutomationScheduleType(StrEnum):
    ONCE = "once"
    CRON = "cron"


class AutomationStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"


class AutomationValidityType(StrEnum):
    FOREVER = "forever"
    UNTIL = "until"


class AutomationPermission(StrEnum):
    FULL = "full"
    RESTRICTED = "restricted"
    READONLY = "readonly"


class AutomationRecordTrigger(StrEnum):
    SCHEDULED = "scheduled"
    MANUAL = "manual"


class AutomationRecordStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AutomationSchedule(StudioModel):
    type: AutomationScheduleType = AutomationScheduleType.ONCE
    at: datetime | None = None
    cron: str | None = Field(default=None, max_length=120)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)

    @model_validator(mode="after")
    def type_configuration(self) -> AutomationSchedule:
        if self.type is AutomationScheduleType.ONCE and self.at is None:
            raise ValueError("once schedules require an 'at' timestamp")
        if self.type is AutomationScheduleType.CRON:
            if not self.cron:
                raise ValueError("cron schedules require a cron expression")
            from harness.automations.cronexpr import parse_cron

            parse_cron(self.cron)
        return self


class AutomationValidity(StudioModel):
    type: AutomationValidityType = AutomationValidityType.FOREVER
    until: datetime | None = None

    @model_validator(mode="after")
    def type_configuration(self) -> AutomationValidity:
        if self.type is AutomationValidityType.UNTIL and self.until is None:
            raise ValueError("until validity requires an 'until' timestamp")
        return self


class AutomationTask(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    task_id: str = Field(alias="taskId", min_length=1)
    user_id: str = Field(alias="userId", min_length=1)
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=200_000)
    model: str = Field(default="auto", max_length=120)
    workspace_id: str | None = Field(default=None, alias="workspaceId", max_length=256)
    permission: AutomationPermission = AutomationPermission.FULL
    schedule: AutomationSchedule
    validity: AutomationValidity = Field(default_factory=AutomationValidity)
    status: AutomationStatus = AutomationStatus.ACTIVE
    next_run_at: datetime | None = Field(default=None, alias="nextRunAt")
    last_run_at: datetime | None = Field(default=None, alias="lastRunAt")
    revision: int = Field(ge=1)
    source_template_id: str | None = Field(
        default=None, alias="sourceTemplateId", max_length=120
    )
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class CreateAutomationTaskRequest(StudioModel):
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=200_000)
    model: str = Field(default="auto", max_length=120)
    workspace_id: str | None = Field(default=None, alias="workspaceId", max_length=256)
    permission: AutomationPermission = AutomationPermission.FULL
    schedule: AutomationSchedule
    validity: AutomationValidity = Field(default_factory=AutomationValidity)
    source_template_id: str | None = Field(
        default=None, alias="sourceTemplateId", max_length=120
    )


class UpdateAutomationTaskRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    name: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1, max_length=200_000)
    model: str = Field(default="auto", max_length=120)
    workspace_id: str | None = Field(default=None, alias="workspaceId", max_length=256)
    permission: AutomationPermission = AutomationPermission.FULL
    schedule: AutomationSchedule
    validity: AutomationValidity = Field(default_factory=AutomationValidity)


class AutomationRunRecord(StudioModel):
    tenant_id: str = Field(alias="tenantId")
    record_id: str = Field(alias="recordId")
    task_id: str = Field(alias="taskId")
    task_name: str = Field(alias="taskName", max_length=120)
    user_id: str = Field(alias="userId", min_length=1)
    trigger: AutomationRecordTrigger
    status: AutomationRecordStatus = AutomationRecordStatus.RUNNING
    session_id: str = Field(alias="sessionId")
    run_id: str = Field(alias="runId")
    scheduled_at: datetime | None = Field(default=None, alias="scheduledAt")
    started_at: datetime = Field(alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    duration_ms: int | None = Field(default=None, alias="durationMs")
    error: str | None = None
