"""Contracts for invoking a deployed Agent through external triggers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from harness.core.errors import ConflictError
from harness.core.models import RunStatus
from harness.deployments.models import EnvironmentName
from harness.scheduling import Timing
from harness.studio.models import StudioModel


class TriggerKind(StrEnum):
    WEBHOOK = "webhook"
    A2A = "a2a"
    SCHEDULE = "schedule"
    CHATOPS = "chatops"


class TriggerSchedule(StudioModel):
    interval_seconds: int | None = Field(
        default=None, alias="intervalSeconds", ge=60, le=31_536_000
    )
    cron: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str = Field(default="UTC", min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=200_000)

    @model_validator(mode="after")
    def valid_schedule(self) -> TriggerSchedule:
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("Choose exactly one of intervalSeconds or cron")
        try:
            if self.cron:
                Timing.cron(self.cron, self.timezone)
            else:
                assert self.interval_seconds is not None
                Timing.every(self.interval_seconds)
        except (ValueError, ConflictError) as error:
            raise ValueError(str(error)) from error
        return self

    def next_after(self, after: datetime) -> datetime:
        timing = (
            Timing.cron(self.cron, self.timezone)
            if self.cron
            else Timing.every(self.interval_seconds or 0)
        )
        occurrence = timing.next_occurrence(after)
        if occurrence is None:
            raise ConflictError("trigger schedule has no future occurrence")
        return occurrence


class TriggerChatOps(StudioModel):
    provider: Literal["slack", "teams", "email", "generic"] = "generic"
    allowed_channel_ids: tuple[str, ...] = Field(default=(), alias="allowedChannelIds")


class AgentTrigger(StudioModel):
    tenant_id: str = Field(alias="tenantId", min_length=1)
    trigger_id: str = Field(alias="triggerId", min_length=1)
    kind: TriggerKind = TriggerKind.WEBHOOK
    name: str = Field(min_length=1, max_length=120)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]*$")
    environment: EnvironmentName
    enabled: bool = True
    revision: int = Field(ge=1)
    created_by: str = Field(alias="createdBy", min_length=1)
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    last_invoked_at: datetime | None = Field(default=None, alias="lastInvokedAt")
    schedule: TriggerSchedule | None = None
    chatops: TriggerChatOps | None = None
    next_fire_at: datetime | None = Field(default=None, alias="nextFireAt")

    @model_validator(mode="after")
    def kind_configuration(self) -> AgentTrigger:
        if (self.kind is TriggerKind.SCHEDULE) != (self.schedule is not None):
            raise ValueError("schedule triggers require exactly one schedule configuration")
        if (self.kind is TriggerKind.CHATOPS) != (self.chatops is not None):
            raise ValueError("chatops triggers require exactly one chatops configuration")
        return self


class StoredAgentTrigger(AgentTrigger):
    secret_digest: str = Field(alias="secretDigest", pattern=r"^[a-f0-9]{64}$")

    def public(self) -> AgentTrigger:
        return AgentTrigger.model_validate(
            self.model_dump(mode="json", by_alias=True, exclude={"secret_digest"})
        )


class CreateAgentTriggerRequest(StudioModel):
    name: str = Field(min_length=1, max_length=120)
    environment: EnvironmentName
    kind: TriggerKind = TriggerKind.WEBHOOK
    schedule: TriggerSchedule | None = None
    chatops: TriggerChatOps | None = None

    @model_validator(mode="after")
    def kind_configuration(self) -> CreateAgentTriggerRequest:
        if (self.kind is TriggerKind.SCHEDULE) != (self.schedule is not None):
            raise ValueError("schedule triggers require exactly one schedule configuration")
        if (self.kind is TriggerKind.CHATOPS) != (self.chatops is not None):
            raise ValueError("chatops triggers require exactly one chatops configuration")
        return self


class UpdateAgentTriggerRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    name: str = Field(min_length=1, max_length=120)
    enabled: bool


class RotateAgentTriggerSecretRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class CreatedAgentTrigger(StudioModel):
    trigger: AgentTrigger
    secret: str = Field(min_length=32)


class InvokeAgentTriggerRequest(StudioModel):
    prompt: str = Field(min_length=1, max_length=200_000)


class InvokeChatOpsTriggerRequest(StudioModel):
    message_id: str = Field(alias="messageId", min_length=1, max_length=256)
    channel_id: str = Field(alias="channelId", min_length=1, max_length=256)
    thread_id: str | None = Field(default=None, alias="threadId", max_length=256)
    actor_id: str = Field(alias="actorId", min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=200_000)


class TriggerInvocation(StudioModel):
    trigger_id: str = Field(alias="triggerId")
    session_id: str = Field(alias="sessionId")
    run_id: str = Field(alias="runId")
    status: RunStatus
    environment: EnvironmentName
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    deployment_snapshot_id: str = Field(alias="deploymentSnapshotId")


class AgentExposureSkill(StudioModel):
    skill_id: str = Field(alias="skillId", min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tags: tuple[str, ...] = ()


class AgentExposureDescriptor(StudioModel):
    trigger: AgentTrigger
    agent_version: str = Field(alias="agentVersion", min_length=1)
    display_name: str = Field(alias="displayName", min_length=1)
    description: str = Field(min_length=1)
    skills: tuple[AgentExposureSkill, ...]
