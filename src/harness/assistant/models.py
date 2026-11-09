"""Channel-independent contracts for the personal assistant.

An objective survives individual runs. Its lifecycle must never be inferred
from a run's terminal status: successful execution does not prove completion
of an ongoing responsibility.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from pydantic import AwareDatetime, Field, field_validator, model_validator

from harness.core.errors import ConflictError
from harness.scheduling import Timing, TimingKind
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
    # 共享可见桌面单执行者：声明需要桌面的职责按优先级+FIFO 排队，等待时不空耗模型。
    requires_desktop: bool = Field(default=False, alias="requiresDesktop")
    # 桌面排队优先级：同用户队列内高者先于低者 acquire（不抢占活 holder）；缺省 0 即纯 FIFO。
    desktop_priority: int = Field(default=0, alias="desktopPriority", ge=0, le=100)
    # 新建任务授权范围：协调职责最多可派生多少个新任务；None 表示无权新建。
    max_new_tasks: int | None = Field(default=None, alias="maxNewTasks", ge=0, le=100)
    # 计量上限：只在平台能给出真实用量时生效；无计量时诚实报告不可用。
    max_tokens: int | None = Field(default=None, alias="maxTokens", ge=1)
    max_cost_usd: float | None = Field(default=None, alias="maxCostUsd", gt=0)


class ObjectiveSchedule(StudioModel):
    """One saved rule. Historic intervalMinutes payloads default to every."""

    kind: TimingKind = TimingKind.EVERY
    interval_minutes: int | None = Field(default=None, alias="intervalMinutes", ge=15, le=525_600)
    at: AwareDatetime | None = None
    expression: str | None = Field(default=None, max_length=256)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    meeting_starts_at: AwareDatetime | None = Field(default=None, alias="meetingStartsAt")
    minutes_before: int | None = Field(default=None, alias="minutesBefore", strict=True, ge=1, le=10_080)

    def timing(self) -> Timing:
        return Timing(
            self.kind,
            at=self.at,
            seconds=self.interval_minutes * 60 if self.interval_minutes is not None else None,
            expression=self.expression,
            timezone=self.timezone,
        )

    @model_validator(mode="after")
    def valid_rule(self) -> ObjectiveSchedule:
        if self.meeting_starts_at is not None or self.minutes_before is not None:
            if self.kind is not TimingKind.ONCE or self.meeting_starts_at is None or self.minutes_before is None:
                raise ValueError("会议提醒需要会议开始时间和提前分钟数，按一次性规则执行")
            reminder = self.meeting_starts_at.astimezone(UTC) - timedelta(minutes=self.minutes_before)
            if self.at is not None and self.at != reminder:
                raise ValueError("提醒时间与会议开始时间及提前量不一致")
            object.__setattr__(self, "at", reminder)
        try:
            self.timing()
        except ConflictError as error:
            raise ValueError(str(error)) from error
        return self


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
    schedule_version: int = Field(default=1, alias="scheduleVersion", ge=1)
    # 接管既有会话时绑定原 thread：执行在该线程续跑，而不是替身线程。
    managed_thread_id: str | None = Field(default=None, alias="managedThreadId", max_length=128)
    # 由专职子助理承接的职责：定时能力属于职责，不属于头像或名称。
    specialist_id: str | None = Field(default=None, alias="specialistId", max_length=128)
    # 协调职责派生的子任务：用于按授权范围计数，不构成隐式子职责树。
    parent_objective_id: str | None = Field(default=None, alias="parentObjectiveId", max_length=128)
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
    schedule_version: int | None = Field(default=None, alias="scheduleVersion", ge=1)
    scheduled_for: AwareDatetime | None = Field(default=None, alias="scheduledFor")
    brief: ObjectiveBrief
    context: str = ""
    status: ObjectiveExecutionStatus = ObjectiveExecutionStatus.PENDING
    thread_id: str = Field(alias="threadId")
    run_id: str | None = Field(default=None, alias="runId")
    summary: str = ""
    error_code: str | None = Field(default=None, alias="errorCode")
    # skipUnchanged 早退：成功但没有唤起模型，计量语义是"不适用"而不是"未知"。
    skipped_no_model: bool = Field(default=False, alias="skippedNoModel")
    created_at: AwareDatetime = Field(alias="createdAt")
    finished_at: AwareDatetime | None = Field(default=None, alias="finishedAt")

    def matches_schedule(self, objective: Objective) -> bool:
        if not self.trigger_key.startswith("schedule:"):
            return True
        return (self.schedule_version or 1) == objective.schedule_version


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


class AssistantInboxMetrics(StudioModel):
    """一次执行的结算计量：与同次 checkpoint 同口径（unknown 时不冒充 0）。"""

    tokens: int | None = None
    cost_usd: float | None = Field(default=None, alias="costUsd")
    unknown_usage: bool = Field(default=False, alias="unknownUsage")
    skipped_no_model: bool = Field(default=False, alias="skippedNoModel")


class AssistantInboxItem(StudioModel):
    item_id: str = Field(alias="itemId")
    objective_id: str = Field(alias="objectiveId")
    execution_id: str | None = Field(default=None, alias="executionId")
    kind: str
    title: str
    content: str
    thread_id: str | None = Field(default=None, alias="threadId")
    # 失败回放：终态 notice 带上本次执行的 Run，前端可深链到失败检查点；
    # 旧行没有该字段，仍按 None 读取（只回原线程）。
    run_id: str | None = Field(default=None, alias="runId")
    # 结算结果可见：终态 notice 带同次 checkpoint 的 tokens/cost 与执行耗时；
    # 旧行没有这些字段，仍按 None 读取（不渲染计量行）。
    metrics: AssistantInboxMetrics | None = None
    duration_seconds: int | None = Field(default=None, alias="durationSeconds")
    created_at: AwareDatetime = Field(alias="createdAt")
    read_at: AwareDatetime | None = Field(default=None, alias="readAt")
    source_event_id: str | None = Field(default=None, alias="sourceEventId")
    source_sequence: int | None = Field(default=None, alias="sourceSequence", ge=1)


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
    # Explicit read-only subscription. Legacy bindings never opt in on read.
    notify_on_progress: bool = Field(default=False, alias="notifyOnProgress")
    notify_since: AwareDatetime | None = Field(default=None, alias="notifySince")
    notification_error: str | None = Field(default=None, alias="notificationError")
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
    skipped_no_model: bool = Field(default=False, alias="skippedNoModel")
    # 未唤起模型的定时跳过：没有用量可言，UI 不应显示"未计量"。
    skipped_no_model: bool = Field(default=False, alias="skippedNoModel")
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
    # 例行程序闸门（对齐"无人查看就暂停"）：最近一次打开该 Bot 会话的时间；
    # None 表示本功能上线前的存量 Bot，视为从未查看。运行态字段，不参与 config CAS。
    last_viewed_at: AwareDatetime | None = Field(default=None, alias="lastViewedAt")
    routine_paused_reason: dict[str, object] | None = Field(
        default=None, alias="routinePausedReason"
    )
    deleted_at: AwareDatetime | None = Field(default=None, alias="deletedAt")
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")


class SpecialistActivity(StudioModel):
    """Read-only owned conversation projection; never persisted as Bot config."""

    thread_id: str = Field(alias="threadId")
    run_id: str | None = Field(default=None, alias="runId")
    status: str = "idle"


class SpecialistDirectoryItem(Specialist):
    activity: SpecialistActivity


class HandoverStatus(StrEnum):
    """8.11 转交回执：发送≠接收≠执行。"""

    SENT = "sent"
    RECEIVED = "received"
    APPLIED = "applied"
    EXECUTING = "executing"
    PENDING_INPUT = "pending_input"
    FAILED = "failed"


class SpecialistConversation(StudioModel):
    """Owned conversation coordinates, separate from a Bot's default configuration."""

    thread_id: str = Field(alias="threadId")
    agent_name: str = Field(alias="agentName")
    agent_version: str = Field(alias="agentVersion")
    agent_owner_user_id: str = Field(alias="agentOwnerUserId")
    space_id: str | None = Field(default=None, alias="spaceId")


class SpecialistHandover(StudioModel):
    handover_id: str = Field(alias="handoverId")
    tenant_id: str = Field(alias="tenantId")
    user_id: str = Field(alias="userId")
    specialist_id: str = Field(alias="specialistId")
    objective_id: str | None = Field(default=None, alias="objectiveId")
    message_id: str = Field(alias="messageId")
    summary: str = ""
    source_text: str | None = Field(default=None, alias="sourceText")
    source_thread_id: str | None = Field(default=None, alias="sourceThreadId")
    target_thread_id: str | None = Field(default=None, alias="targetThreadId")
    run_id: str | None = Field(default=None, alias="runId")
    request_hash: str | None = Field(default=None, alias="requestHash")
    reply_to_run_id: str | None = Field(default=None, alias="replyToRunId")
    sent_at: AwareDatetime | None = Field(default=None, alias="sentAt")
    status: HandoverStatus = HandoverStatus.SENT
    created_at: AwareDatetime = Field(alias="createdAt")
    updated_at: AwareDatetime = Field(alias="updatedAt")


class MentionMessage(StudioModel):
    """Read projection of a sent message or child result in its originating chat."""

    message_id: str
    text: str
    source: dict[str, str]
    created_at: AwareDatetime
    run_id: str
    target_thread_id: str
