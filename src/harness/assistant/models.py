"""Channel-independent contracts for the personal assistant.

An objective survives individual runs. Its lifecycle must never be inferred
from a run's terminal status: successful execution does not prove completion
of an ongoing responsibility.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator

from harness.studio.models import StudioModel


class ObjectiveStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.CANCELLED}


class ObjectiveAction(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    COMPLETE = "complete"
    STOP = "stop"


class NotificationMode(StrEnum):
    IMPORTANT = "important"
    ALL_RESULTS = "all_results"
    NONE = "none"


class DelegationMode(StrEnum):
    """只跟进读取并汇报差距；自主推进在授权范围内执行下一步。"""

    AUTONOMOUS = "autonomous"
    FOLLOW_ONLY = "follow_only"


class ManagedTaskRole(StrEnum):
    """同一用户同一 thread 至多一个主动执行托管者；关注者只读。"""

    EXECUTE = "execute"
    OBSERVE = "observe"


class ManagedTaskSource(StrEnum):
    WEB = "web"
    FEISHU = "feishu"
    WEIXIN = "weixin"
    API = "api"


class ObjectiveBudget(StudioModel):
    """派发闸门：达到任一上限后不再派发新工作（8.6）。

    token/费用上限需要真实计量对接后才允许配置；本字段先承载运行次数与并发。
    """

    max_runs_per_day: int | None = Field(default=None, alias="maxRunsPerDay", ge=1, le=10_000)
    max_total_runs: int | None = Field(default=None, alias="maxTotalRuns", ge=1, le=100_000)
    # 无变化不唤起模型：按纳管任务的进展指纹比较，相同则跳过本次检查。
    skip_unchanged: bool = Field(default=False, alias="skipUnchanged")
    # 共享可见桌面单执行者：声明需要桌面的职责按 FIFO 排队，等待时不空耗模型。
    requires_desktop: bool = Field(default=False, alias="requiresDesktop")
    # 计量上限：只在平台能给出真实用量时生效；无计量时诚实报告不可用。
    max_tokens: int | None = Field(default=None, alias="maxTokens", ge=1)
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd", gt=0)


class ObjectiveSchedule(StudioModel):
    """A bounded periodic check; None on the objective means manual only."""

    interval_minutes: int = Field(alias="intervalMinutes", ge=15, le=525_600)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("unknown time zone") from error
        return value


class ObjectiveBrief(StudioModel):
    title: str = Field(min_length=1, max_length=120)
    instructions: str = Field(min_length=1, max_length=20_000)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]{0,127}$")
    agent_version: str = Field(alias="agentVersion", min_length=1, max_length=128)
    project_id: str | None = Field(default=None, alias="projectId", max_length=128)
    schedule: ObjectiveSchedule | None = None
    ends_at: AwareDatetime | None = Field(default=None, alias="endsAt")
    completion_criteria: str = Field(default="", alias="completionCriteria", max_length=2_000)
    notification_mode: NotificationMode = Field(
        default=NotificationMode.IMPORTANT, alias="notificationMode"
    )
    budget: ObjectiveBudget | None = None

    @field_validator("title", "instructions", "agent_version")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class Objective(ObjectiveBrief):
    objective_id: str = Field(alias="objectiveId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    status: ObjectiveStatus = ObjectiveStatus.ACTIVE
    revision: int = Field(default=1, ge=1)
    # 接管既有会话时绑定原 thread：执行在该线程续跑，而不是替身线程。
    managed_thread_id: str | None = Field(default=None, alias="managedThreadId", max_length=128)
    # 由专职子助理承接的职责：定时能力属于职责，不属于头像或名称。
    specialist_id: str | None = Field(default=None, alias="specialistId", max_length=128)
    mode: DelegationMode = DelegationMode.AUTONOMOUS
    next_check_at: AwareDatetime | None = Field(default=None, alias="nextCheckAt")
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")


class UpdateObjectiveRequest(ObjectiveBrief):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class ObjectiveActionRequest(StudioModel):
    action: ObjectiveAction
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class ObjectiveExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ObjectiveExecution(StudioModel):
    execution_id: str = Field(alias="executionId")
    objective_id: str = Field(alias="objectiveId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    trigger_key: str = Field(alias="triggerKey")
    objective_revision: int = Field(alias="objectiveRevision", ge=1)
    brief: ObjectiveBrief
    context: str = ""
    status: ObjectiveExecutionStatus = ObjectiveExecutionStatus.PENDING
    thread_id: str = Field(alias="threadId")
    run_id: str | None = Field(default=None, alias="runId")
    summary: str = ""
    error_code: str | None = Field(default=None, alias="errorCode")
    created_at: AwareDatetime = Field(alias="createdAt")
    finished_at: AwareDatetime | None = Field(default=None, alias="finishedAt")


class ObjectiveNoteRequest(StudioModel):
    content: str = Field(min_length=1, max_length=8_000)
    expected_revision: int = Field(alias="expectedRevision", ge=1)

    @field_validator("content")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        return ObjectiveBrief.meaningful_text(value)


class ObjectiveEvent(StudioModel):
    event_id: str = Field(alias="eventId")
    objective_id: str = Field(alias="objectiveId")
    kind: str
    content: str
    created_at: datetime = Field(alias="createdAt")


class AssistantInboxItem(StudioModel):
    item_id: str = Field(alias="itemId")
    objective_id: str = Field(alias="objectiveId")
    execution_id: str | None = Field(default=None, alias="executionId")
    kind: str
    title: str
    content: str
    thread_id: str | None = Field(default=None, alias="threadId")
    created_at: AwareDatetime = Field(alias="createdAt")
    read_at: AwareDatetime | None = Field(default=None, alias="readAt")


class ManagedTask(StudioModel):
    """托管关联：objective 与原 thread 的稳定绑定，带授权来源。

    depends_on_objective_id 声明依赖职责；绑定与改绑时服务端校验无环。
    """

    managed_id: str = Field(alias="managedId")
    objective_id: str = Field(alias="objectiveId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    thread_id: str = Field(alias="threadId")
    project_id: str | None = Field(default=None, alias="projectId", max_length=128)
    depends_on_objective_id: str | None = Field(
        default=None, alias="dependsOnObjectiveId", max_length=128
    )
    role: ManagedTaskRole = ManagedTaskRole.EXECUTE
    source: ManagedTaskSource = ManagedTaskSource.API
    created_at: AwareDatetime = Field(alias="createdAt")


class ObjectiveCheckpoint(StudioModel):
    """一次执行结束后的结构化检查点，可追溯且支撑恢复。"""

    checkpoint_id: str = Field(alias="checkpointId")
    objective_id: str = Field(alias="objectiveId")
    execution_id: str = Field(alias="executionId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    run_id: str | None = Field(default=None, alias="runId")
    revision: int = Field(ge=1)
    status: ObjectiveExecutionStatus
    summary: str = ""
    # 真实计量：unknown_usage=True 表示平台未提供可靠用量，不能当 0 处理。
    tokens: int | None = None
    cost_usd: float | None = Field(default=None, alias="costUsd")
    unknown_usage: bool = Field(default=False, alias="unknownUsage")
    created_at: AwareDatetime = Field(alias="createdAt")


class DesktopTurn(StudioModel):
    """共享桌面的一次执行权：同一用户同一时刻只有一个执行者。"""

    desktop_turn_id: str = Field(alias="desktopTurnId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    objective_id: str = Field(alias="objectiveId")
    execution_id: str = Field(alias="executionId")
    acquired_at: AwareDatetime = Field(alias="acquiredAt")
    expires_at: AwareDatetime = Field(alias="expiresAt")
    released_at: AwareDatetime | None = Field(default=None, alias="releasedAt")


class SpecialistStatus(StrEnum):
    """8.10 配置状态机：保存不等于验证，缺项不启动。"""

    DRAFT = "draft"
    NEEDS_INPUT = "needs_input"
    VALIDATING = "validating"
    READY = "ready"
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    ARCHIVED = "archived"


class Specialist(StudioModel):
    """持久专职子助理：复用已发布能力模板的实例配置，不引入第二套发布系统。"""

    specialist_id: str = Field(alias="specialistId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    description: str = Field(default="", max_length=2_000)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]{0,127}$")
    agent_version: str = Field(alias="agentVersion", min_length=1, max_length=128)
    status: SpecialistStatus = SpecialistStatus.DRAFT
    revision: int = Field(default=1, ge=1)
    # 结构化运行配置：调度、阈值、通知等；凭据只存引用（credential_ref）。
    config: dict[str, object] = Field(default_factory=dict)
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")


class HandoverStatus(StrEnum):
    """8.11 转交回执：发送≠接收≠执行。"""

    SENT = "sent"
    RECEIVED = "received"
    APPLIED = "applied"
    EXECUTING = "executing"
    PENDING_INPUT = "pending_input"
    FAILED = "failed"


class SpecialistHandover(StudioModel):
    handover_id: str = Field(alias="handoverId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    specialist_id: str = Field(alias="specialistId")
    objective_id: str | None = Field(default=None, alias="objectiveId")
    message_id: str = Field(alias="messageId")
    summary: str = ""
    status: HandoverStatus = HandoverStatus.SENT
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")


# 首次配置的最小完整集：缺任一项不得启用持续调度。
SPECIALIST_REQUIRED_CONFIG = ("schedule_minutes", "notify_channel")
