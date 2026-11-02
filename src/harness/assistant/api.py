"""Owner-scoped assistant API, shared by Web and authenticated channel adapters."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.assistant.models import (
    AssistantInboxItem,
    HandoverStatus,
    ManagedTask,
    Objective,
    ObjectiveActionRequest,
    ObjectiveBrief,
    ObjectiveEvent,
    ObjectiveExecution,
    ObjectiveExecutionStatus,
    ObjectiveNoteRequest,
    ObjectiveSchedule,
    Specialist,
    SpecialistHandover,
    SpecialistStatus,
    UpdateObjectiveRequest,
)
from harness.assistant.service import AssistantService
from harness.core.errors import NotFoundError
from harness.studio.models import StudioModel

router = APIRouter(prefix="/v1/studio/assistant", tags=["assistant"])


class SpecialistConfigRequest(StudioModel):
    config: dict[str, object]
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class SpecialistStatusRequest(StudioModel):
    status: SpecialistStatus
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class SpecialistCredentialRequest(StudioModel):
    name: str = Field(min_length=1, max_length=64)
    secret: str = Field(min_length=1, max_length=8_000)
    expected_revision: int = Field(alias="expectedRevision", ge=1)


def service(request: Request) -> AssistantService:
    value = getattr(request.app.state.container, "assistant", None)
    if not isinstance(value, AssistantService):
        raise HTTPException(503, "Assistant is not configured")
    return value


async def reader(identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
    ensure_permission(identity, "tasks:read")
    return identity


async def writer(identity: Annotated[Identity, Depends(require_identity)]) -> Identity:
    ensure_permission(identity, "tasks:write")
    return identity


Service = Annotated[AssistantService, Depends(service)]
Reader = Annotated[Identity, Depends(reader)]
Writer = Annotated[Identity, Depends(writer)]


@router.get("/objectives")
async def list_objectives(identity: Reader, assistant: Service) -> list[Objective]:
    return await assistant.repository.list(identity.tenant_id, identity.user_id)


@router.post("/objectives", status_code=201)
async def create_objective(body: ObjectiveBrief, identity: Writer, assistant: Service) -> Objective:
    return await assistant.create(identity.tenant_id, identity.user_id, body)


@router.get("/objectives/{objective_id}")
async def get_objective(objective_id: str, identity: Reader, assistant: Service) -> Objective:
    return await assistant.repository.get(identity.tenant_id, identity.user_id, objective_id)


@router.put("/objectives/{objective_id}")
async def update_objective(
    objective_id: str, body: UpdateObjectiveRequest, identity: Writer, assistant: Service
) -> Objective:
    await assistant.repository.get(identity.tenant_id, identity.user_id, objective_id)
    brief = ObjectiveBrief.model_validate(body.model_dump(exclude={"expected_revision"}))
    await assistant.validate(identity.tenant_id, identity.user_id, brief)
    return await assistant.repository.update(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        expected_revision=body.expected_revision,
        now=datetime.now(UTC),
        brief=brief,
    )


class ObjectiveScheduleRequest(StudioModel):
    schedule: ObjectiveSchedule | None
    expected_revision: int = Field(alias="expectedRevision", ge=1)


@router.patch("/objectives/{objective_id}/schedule")
async def update_objective_schedule(
    objective_id: str, body: ObjectiveScheduleRequest, identity: Writer, assistant: Service
) -> Objective:
    current = await assistant.repository.get(identity.tenant_id, identity.user_id, objective_id)
    brief = ObjectiveBrief.model_validate(
        current.model_dump(include=set(ObjectiveBrief.model_fields)) | {"schedule": body.schedule}
    )
    await assistant.validate(identity.tenant_id, identity.user_id, brief)
    return await assistant.repository.update(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        expected_revision=body.expected_revision,
        now=datetime.now(UTC),
        brief=brief,
    )


@router.post("/objectives/{objective_id}/actions")
async def action(
    objective_id: str, body: ObjectiveActionRequest, identity: Writer, assistant: Service
) -> Objective:
    return await assistant.repository.update(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        expected_revision=body.expected_revision,
        now=datetime.now(UTC),
        action=body.action,
    )


@router.post("/objectives/{objective_id}/notes")
async def note(
    objective_id: str, body: ObjectiveNoteRequest, identity: Writer, assistant: Service
) -> Objective:
    return await assistant.repository.update(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        expected_revision=body.expected_revision,
        now=datetime.now(UTC),
        note=body.content,
    )


@router.post("/objectives/{objective_id}/executions", status_code=202)
async def execute(
    objective_id: str,
    identity: Writer,
    assistant: Service,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
) -> ObjectiveExecution:
    objective = await assistant.repository.get(identity.tenant_id, identity.user_id, objective_id)
    await assistant.validate(identity.tenant_id, identity.user_id, objective)
    execution = await assistant.repository.enqueue(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        trigger_key=f"manual:{idempotency_key}",
        now=datetime.now(UTC),
    )
    if objective.specialist_id and execution.status in {
        ObjectiveExecutionStatus.PENDING,
        ObjectiveExecutionStatus.RUNNING,
    }:
        for handover in await assistant.repository.handovers(
            identity.tenant_id, identity.user_id, specialist_id=objective.specialist_id
        ):
            if handover.objective_id == objective_id and handover.status in {
                HandoverStatus.SENT,
                HandoverStatus.RECEIVED,
                HandoverStatus.PENDING_INPUT,
            }:
                await assistant.repository.update_handover(
                    identity.tenant_id,
                    identity.user_id,
                    handover.handover_id,
                    status=HandoverStatus.APPLIED,
                    now=datetime.now(UTC),
                )
    return execution


@router.get("/objectives/{objective_id}/executions")
async def executions(
    objective_id: str, identity: Reader, assistant: Service
) -> list[ObjectiveExecution]:
    return await assistant.repository.executions(identity.tenant_id, identity.user_id, objective_id)


@router.get("/objectives/{objective_id}/events")
async def events(objective_id: str, identity: Reader, assistant: Service) -> list[ObjectiveEvent]:
    return await assistant.repository.events(identity.tenant_id, identity.user_id, objective_id)


@router.get("/inbox")
async def inbox(identity: Reader, assistant: Service) -> list[AssistantInboxItem]:
    return await assistant.repository.inbox(identity.tenant_id, identity.user_id)


class NoticeReadRequest(StudioModel):
    expected_source_sequence: int | None = Field(default=None, alias="expectedSourceSequence", ge=1)


@router.post("/inbox/{item_id}/read", status_code=204)
async def read_notice(
    item_id: str, identity: Reader, assistant: Service,
    body: NoticeReadRequest | None = None,
) -> None:
    await assistant.repository.read_notice(
        identity.tenant_id, identity.user_id, item_id, datetime.now(UTC),
        expected_source_sequence=body.expected_source_sequence if body else None,
    )


@router.get("/specialists")
async def list_specialists(identity: Reader, assistant: Service) -> list[Specialist]:
    return await assistant.repository.list_specialists(identity.tenant_id, identity.user_id)


class SpecialistAppearance(StudioModel):
    """Bot 的外观选择（对齐客户端的新建 Bot 弹窗）：只影响呈现。"""

    color: str = Field(default="#126fd4", pattern=r"^#[0-9a-fA-F]{6}$")
    avatar: str = Field(default="blob", pattern=r"^[a-z0-9-]{1,24}$")


class SpecialistCreateRequest(StudioModel):
    """创建请求只接受职责与能力来源；租户与用户由身份推导，绝不接受客户端伪造。"""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2_000)
    # 新建不强制绑定智能体：缺省时沿用已有 Bot 的智能体或本人第一份已发布版本。
    agent_name: str | None = Field(
        default=None, alias="agentName", pattern=r"^[a-z][a-z0-9-]{0,127}$"
    )
    agent_version: str | None = Field(
        default=None, alias="agentVersion", min_length=1, max_length=128
    )
    appearance: SpecialistAppearance | None = None


@router.post("/specialists", status_code=201)
async def create_specialist(
    body: SpecialistCreateRequest, identity: Writer, assistant: Service
) -> Specialist:
    """创建专职子助理；同名职责复用既有实例，不会重复创建。"""
    created, _ = await assistant.create_specialist(
        identity.tenant_id,
        identity.user_id,
        name=body.name,
        description=body.description,
        agent_name=body.agent_name,
        agent_version=body.agent_version,
        appearance=body.appearance.model_dump() if body.appearance else None,
    )
    return created


@router.get("/specialists/{specialist_id}")
async def get_specialist(specialist_id: str, identity: Reader, assistant: Service):
    """子助理详情 + 它的会话入口。

    子助理独立会话与旧职责入口使用同一只读解析，不迁移或重开线程。
    """
    specialist = await assistant.repository.get_specialist(
        identity.tenant_id, identity.user_id, specialist_id
    )
    duties = [
        item
        for item in await assistant.repository.list(identity.tenant_id, identity.user_id)
        if item.specialist_id == specialist_id
    ]
    thread_id = await assistant.specialist_thread(
        identity.tenant_id, identity.user_id, specialist, duties=duties
    )
    # 纯读：详情/编辑弹窗不算"查看"，不能在这里清掉 routinePausedReason。
    # 真正的查看走 POST /specialists/{id}/viewed（进入该 Bot 会话时调用一次）。
    return {
        "specialist": specialist,
        "threadId": thread_id,
        "primaryObjectiveId": duties[0].objective_id if duties else None,
        "duties": [
            {
                "objectiveId": item.objective_id,
                "title": item.title,
                "status": item.status.value,
                "revision": item.revision,
                "hasExecutions": bool(
                    await assistant.repository.executions(
                        identity.tenant_id, identity.user_id, item.objective_id
                    )
                ),
                "schedule": item.schedule.model_dump(by_alias=True) if item.schedule else None,
            }
            for item in duties
        ],
        "agentName": specialist.agent_name,
        "agentVersion": specialist.agent_version,
    }


@router.post("/specialists/{specialist_id}/viewed")
async def mark_specialist_viewed(
    specialist_id: str, identity: Writer, assistant: Service
) -> Specialist:
    """进入该 Bot 会话时的一次显式"查看"。

    刷新 last_viewed_at 并清除 routinePausedReason（无人查看暂停据此恢复）；
    不递增 revision。GET 详情与编辑弹窗都不算查看，必须由前端在这个入口调用。
    """
    viewed = await assistant.touch_specialist_viewed(
        identity.tenant_id, identity.user_id, specialist_id
    )
    if not viewed:
        raise NotFoundError("Specialist not found")
    return await assistant.repository.get_specialist(
        identity.tenant_id, identity.user_id, specialist_id
    )


class SpecialistSidebar(StudioModel):
    pinned: bool = False
    hidden: bool = False
    unread: bool = False
    group: str = Field(default="", max_length=40)


class SpecialistProfileRequest(StudioModel):
    """编辑 Bot：名称/标题/描述/外观，纯呈现信息。"""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    title: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2_000)
    appearance: SpecialistAppearance | None = None
    sidebar: SpecialistSidebar | None = None
    expected_revision: int = Field(alias="expectedRevision", ge=1)


@router.post("/specialists/{specialist_id}/profile")
async def update_specialist_profile(
    specialist_id: str, body: SpecialistProfileRequest, identity: Writer, assistant: Service
):
    return await assistant.update_specialist_profile(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        name=body.name,
        title=body.title,
        description=body.description,
        appearance=body.appearance.model_dump() if body.appearance else None,
        **({"sidebar": body.sidebar.model_dump()} if body.sidebar is not None else {}),
        expected_revision=body.expected_revision,
    )


@router.post("/specialists/{specialist_id}/config")
async def configure_specialist(
    specialist_id: str,
    body: SpecialistConfigRequest,
    identity: Writer,
    assistant: Service,
) -> Specialist:
    return await assistant.configure_specialist(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        config=body.config,
        expected_revision=body.expected_revision,
    )


@router.post("/specialists/{specialist_id}/status")
async def set_specialist_status(
    specialist_id: str,
    body: SpecialistStatusRequest,
    identity: Writer,
    assistant: Service,
) -> Specialist:
    return await assistant.set_specialist_status(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        status=body.status,
        expected_revision=body.expected_revision,
    )


@router.post("/specialists/{specialist_id}/credentials", status_code=200)
async def save_specialist_credential(
    specialist_id: str,
    body: SpecialistCredentialRequest,
    identity: Writer,
    assistant: Service,
) -> Specialist:
    """专用凭据入口：密文入库，模型与日志只允许出现 credential_ref。"""
    return await assistant.save_specialist_credential(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        name=body.name,
        secret=body.secret,
        expected_revision=body.expected_revision,
    )


class ManagedBindingRequest(StudioModel):
    thread_id: str = Field(alias="threadId", min_length=1, max_length=128)
    depends_on_objective_id: str | None = Field(
        default=None, alias="dependsOnObjectiveId", max_length=128
    )
    notify_on_progress: bool = Field(default=False, alias="notifyOnProgress")


@router.get("/objectives/{objective_id}/managed")
async def managed_overview(objective_id: str, identity: Reader, assistant: Service):
    """项目托管面板：纳管绑定 + 每个任务的进展与检查点（只读投影）。"""
    objective = await assistant.repository.get(identity.tenant_id, identity.user_id, objective_id)
    bindings = await assistant.repository.managed_bindings(
        identity.tenant_id, identity.user_id, objective_id=objective_id
    )
    overview = await assistant.managed_overview(identity.tenant_id, identity.user_id, objective_id)
    by_thread = {str(item["thread_id"]): item for item in overview}
    # 检查点计量投影：run_id → 最新检查点的 tokens/cost/unknown_usage。
    checkpoints = await assistant.repository.checkpoints(
        identity.tenant_id, identity.user_id, objective_id, limit=50
    )
    metrics_by_run: dict[str, dict[str, object]] = {}
    # 失败回放只需定位：检查点 id 与摘要随 overview 一起投影，避免前端二次查询。
    checkpoint_ids_by_run: dict[str, str] = {}
    checkpoint_summaries_by_run: dict[str, str] = {}
    for checkpoint in checkpoints:
        if checkpoint.run_id is None or checkpoint.run_id in metrics_by_run:
            continue
        checkpoint_ids_by_run[checkpoint.run_id] = checkpoint.checkpoint_id
        # 只做失败卡摘要预览，截断避免一次返回整段执行总结。
        checkpoint_summaries_by_run[checkpoint.run_id] = checkpoint.summary[:300]
        if checkpoint.unknown_usage or checkpoint.tokens is None:
            metrics_by_run[checkpoint.run_id] = {
                "tokens": None,
                "costUsd": checkpoint.cost_usd,
                "unknownUsage": True,
            }
        else:
            metrics_by_run[checkpoint.run_id] = {
                "tokens": checkpoint.tokens,
                "costUsd": checkpoint.cost_usd,
                "unknownUsage": False,
            }
    for item in overview:
        run_id = item.get("run_id")
        run_key = str(run_id) if run_id else None
        item["metrics"] = metrics_by_run.get(run_key) if run_key else None
        item["checkpoint_id"] = checkpoint_ids_by_run.get(run_key) if run_key else None
        item["checkpoint_summary"] = checkpoint_summaries_by_run.get(run_key) if run_key else None
    return {
        "objectiveId": objective.objective_id,
        "revision": objective.revision,
        "budget": objective.budget.model_dump(by_alias=True) if objective.budget else None,
        "managed": [
            {
                "threadId": task.thread_id,
                "managedId": task.managed_id,
                "role": task.role.value,
                "notifyOnProgress": task.notify_on_progress,
                "notificationError": task.notification_error,
                "projectId": task.project_id,
                "dependsOnObjectiveId": task.depends_on_objective_id,
                "progress": by_thread.get(task.thread_id),
            }
            for task in bindings
        ],
        "overview": overview,
        "aggregate": assistant.render_managed_overview(overview),
    }


@router.post("/objectives/{objective_id}/managed", status_code=201)
async def bind_managed_thread(
    objective_id: str, body: ManagedBindingRequest, identity: Writer, assistant: Service
):
    """把一个既有线程纳入职责的纳管集合；依赖成环由服务端拒绝。"""
    await assistant.bind_thread(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        body.thread_id,
        notify_on_progress=body.notify_on_progress,
    )
    return {"objectiveId": objective_id, "threadId": body.thread_id, "bound": True}


class ManagedNotificationRequest(StudioModel):
    enabled: bool
    expected_revision: int = Field(alias="expectedRevision", ge=1)


@router.patch("/objectives/{objective_id}/managed/{managed_id}/notifications")
async def configure_managed_notifications(
    objective_id: str,
    managed_id: str,
    body: ManagedNotificationRequest,
    identity: Writer,
    assistant: Service,
) -> ManagedTask:
    return await assistant.configure_managed_notifications(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        managed_id,
        enabled=body.enabled,
        expected_revision=body.expected_revision,
    )


class SpecialistTrialRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class SpecialistDutyRequest(StudioModel):
    objective_id: str = Field(alias="objectiveId", min_length=1, max_length=128)
    expected_revision: int = Field(alias="expectedRevision", ge=1)


@router.post("/specialists/{specialist_id}/duties", status_code=201)
async def attach_specialist_duty(
    specialist_id: str, body: SpecialistDutyRequest, identity: Writer, assistant: Service
) -> Specialist:
    """把既有职责挂到子助理；职责本身与承接者正交。"""
    return await assistant.attach_duty(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        objective_id=body.objective_id,
        expected_revision=body.expected_revision,
    )


@router.post("/specialists/{specialist_id}/trial", status_code=201)
async def trial_specialist(
    specialist_id: str, body: SpecialistTrialRequest, identity: Writer, assistant: Service
):
    """首次试运行：配置缺项时拒绝；成功只返回真实执行，不宣称持续运行已启用。"""
    return await assistant.trial_specialist(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        expected_revision=body.expected_revision,
    )


@router.get("/desktop/occupancy")
async def desktop_occupancy(identity: Reader, assistant: Service) -> dict[str, object]:
    """共享桌面占用/排队的只读投影：谁在占用、等待者位置与原因（中文可读）。"""
    return await assistant.desktop_occupancy(identity.tenant_id, identity.user_id)


@router.get("/handovers")
async def list_handovers(
    identity: Reader,
    assistant: Service,
    specialist_id: str | None = Query(default=None),
) -> list[SpecialistHandover]:
    return await assistant.repository.handovers(
        identity.tenant_id, identity.user_id, specialist_id=specialist_id
    )
