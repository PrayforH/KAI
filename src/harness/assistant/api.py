"""Owner-scoped assistant API, shared by Web and authenticated channel adapters."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.assistant.constants import primary_specialist_id
from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupMessage,
    AssistantInboxItem,
    GroupPostResult,
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
    SpecialistDirectoryItem,
    SpecialistHandover,
    SpecialistStatus,
    UpdateObjectiveRequest,
)
from harness.assistant.service import AssistantService
from harness.assistant.skill_models import PersonalSkill, SkillProposal
from harness.assistant.vault import (
    VaultDeleteRequest,
    VaultGrantRequest,
    VaultItem,
    VaultItemUpdate,
    VaultItemWrite,
)
from harness.core.errors import NotFoundError
from harness.studio.models import StudioModel

router = APIRouter(prefix="/v1/studio/assistant", tags=["assistant"])


class SpecialistConfigRequest(StudioModel):
    config: dict[str, object]
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class SpecialistStatusRequest(StudioModel):
    status: SpecialistStatus
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class SpecialistDeleteRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    confirm_name: str = Field(alias="confirmName", min_length=1, max_length=80)


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
    return await assistant.set_schedule(
        identity.tenant_id, identity.user_id, objective_id,
        expected_revision=body.expected_revision,
        schedule=body.schedule,
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


@router.get("/primary")
async def primary_profile(identity: Reader, assistant: Service) -> Specialist | None:
    return await assistant.primary_profile(identity.tenant_id, identity.user_id)


@router.post("/primary")
async def initialize_primary_profile(identity: Writer, assistant: Service) -> Specialist | None:
    return await assistant.primary_profile(identity.tenant_id, identity.user_id, initialize=True)


@router.get("/primary/duties")
async def primary_duties(identity: Reader, assistant: Service):
    duties = await assistant.repository.list(identity.tenant_id, identity.user_id, limit=None)
    primary_id = primary_specialist_id(identity.tenant_id, identity.user_id)
    return {"duties": await _duty_summaries(
        assistant, identity, [duty for duty in duties if duty.specialist_id in (None, primary_id)]
    )}


async def _duty_summaries(assistant: AssistantService, identity: Identity, duties: list[Objective]):
    plans = []
    for item in duties:
        recent = await assistant.repository.executions(
            identity.tenant_id, identity.user_id, item.objective_id, limit=1
        )
        latest = recent[0] if recent else None
        plans.append({
            "objectiveId": item.objective_id,
            "title": item.title,
            "status": item.status.value,
            "revision": item.revision,
            "hasExecutions": latest is not None,
            "latestExecution": latest.model_dump(
                mode="json", by_alias=True,
                include={"status", "summary", "run_id", "thread_id", "finished_at"},
            ) if latest else None,
            "schedule": item.schedule.model_dump(by_alias=True) if item.schedule else None,
            "nextCheckAt": item.next_check_at,
        })
    return plans


@router.get("/specialists")
async def list_specialists(identity: Reader, assistant: Service) -> list[SpecialistDirectoryItem]:
    return await assistant.specialist_directory(identity.tenant_id, identity.user_id)


@router.get("/specialists/appearance/palette")
async def appearance_palette(identity: Reader, assistant: Service) -> dict[str, list[str]]:
    """外观清单由服务端给出：前端不再各写一份取色表，新建时也不会漏项。"""
    return assistant.appearance_palette()


@router.delete("/specialists/{specialist_id}", status_code=204)
async def delete_specialist(
    specialist_id: str, body: SpecialistDeleteRequest, identity: Writer, assistant: Service,
) -> None:
    await assistant.repository.delete_specialist(
        identity.tenant_id, identity.user_id, specialist_id,
        expected_revision=body.expected_revision,
        confirm_name=body.confirm_name,
        now=datetime.now(UTC),
    )


class SpecialistAppearance(StudioModel):
    """Bot 的外观选择（对齐客户端的新建 Bot 弹窗）：只影响呈现。"""

    color: str = Field(default="#126fd4", pattern=r"^#[0-9a-fA-F]{6}$")
    avatar: str = Field(default="blob", pattern=r"^[a-z0-9-]{1,24}$")


class SpecialistCreateRequest(StudioModel):
    """创建请求只接受职责与能力来源；租户与用户由身份推导，绝不接受客户端伪造。"""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2_000)
    # 省略智能体坐标时，使用当前账号的平台通用助手。
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
async def get_specialist(
    specialist_id: str, identity: Reader, assistant: Service, *, include_duties: bool = True,
):
    """子助理详情 + 它的会话入口。

    子助理独立会话与旧职责入口使用同一只读解析，不迁移或重开线程。
    """
    specialist = await assistant.repository.get_specialist(
        identity.tenant_id, identity.user_id, specialist_id
    )
    duties = await assistant.repository.list(
        identity.tenant_id, identity.user_id, limit=None, specialist_id=specialist_id
    )
    conversation = await assistant.specialist_conversation(
        identity.tenant_id, identity.user_id, specialist, duties=duties
    )
    plans = await _duty_summaries(assistant, identity, duties) if include_duties else []
    return {
        "specialist": specialist,
        **conversation.model_dump(by_alias=True),
        "primaryObjectiveId": duties[0].objective_id if duties else None,
        "duties": plans,
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


class SpecialistMessageRequest(StudioModel):
    """给某个 Bot 发一句话：只提交正文，目标与线程由服务端解析。"""

    text: str = Field(min_length=1, max_length=20_000)
    source_thread_id: str | None = Field(
        default=None, alias="sourceThreadId", min_length=1, max_length=128
    )
    model_route: str | None = Field(default=None, alias="modelRoute", min_length=1, max_length=128)
    reply_to_run_id: str | None = Field(
        default=None, alias="replyToRunId", min_length=1, max_length=128)
    reply_message_id: str | None = Field(
        default=None, alias="replyMessageId", min_length=1, max_length=200)


@router.get("/vault")
async def list_vault_items(identity: Reader, assistant: Service) -> list[VaultItem]:
    """助理资料库：只回脱敏元数据，敏感值不进接口。"""
    return await assistant.vault_items(identity.tenant_id, identity.user_id)


@router.post("/vault", status_code=201)
async def create_vault_item(
    body: VaultItemWrite, identity: Writer, assistant: Service
) -> VaultItem:
    """新建一条资料：值进加密存储，列表只见标题与末四位。"""
    return await assistant.create_vault_item(
        identity.tenant_id,
        identity.user_id,
        category=body.category,
        title=body.title,
        value=body.value,
        last4=body.last4,
    )


@router.put("/vault/{item_id}")
async def update_vault_item(
    item_id: str, body: VaultItemUpdate, identity: Writer, assistant: Service
) -> VaultItem:
    return await assistant.update_vault_item(
        identity.tenant_id,
        identity.user_id,
        item_id,
        expected_revision=body.expected_revision,
        title=body.title,
        value=body.value,
        last4=body.last4,
    )


@router.delete("/vault/{item_id}")
async def delete_vault_item(
    item_id: str, body: VaultDeleteRequest, identity: Writer, assistant: Service
) -> dict[str, object]:
    """删除一条资料：必须带标题确认，密文一起清掉。"""
    deleted = await assistant.delete_vault_item(
        identity.tenant_id, identity.user_id, item_id, confirm_title=body.confirm_title
    )
    if not deleted:
        raise HTTPException(404, "资料不存在")
    return {"itemId": item_id, "deleted": True}


@router.post("/vault/{item_id}/grant")
async def grant_vault_item(
    item_id: str, body: VaultGrantRequest, identity: Writer, assistant: Service
) -> Specialist:
    """把资料授权给某个 Bot：写 credential_ref；撤销时移除。"""
    return await assistant.grant_vault_item(
        identity.tenant_id,
        identity.user_id,
        body.specialist_id,
        item_id=item_id,
        granted=body.granted,
        expected_revision=body.expected_revision,
    )


@router.post("/specialists/{specialist_id}/messages", status_code=202)
async def send_specialist_message(
    specialist_id: str,
    body: SpecialistMessageRequest,
    request: Request,
    identity: Writer,
    assistant: Service,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
) -> dict[str, object]:
    """在目标原会话执行消息，并将幂等回执和结果关联到拥有者的来源会话。

    无来源的旧客户端仍只登记回执；新客户端由服务端复用既有 AG-UI 执行管线。
    """
    quoted_message_context = ""
    if body.reply_message_id and body.source_thread_id:
        # The quote is resolved against the conversation it was made in, not
        # the Bot's thread that will receive it. The client excerpt is display
        # only: the text the model sees always comes from the durable Run.
        from harness.agui.routes import _quoted_context_from_reference

        quoted_message_context = await _quoted_context_from_reference(
            request.app.state.container,
            identity.tenant_id,
            identity.user_id,
            body.source_thread_id,
            {"messageId": body.reply_message_id},
        )
    send = (
        assistant.send_mention_message
        if body.source_thread_id
        else assistant.record_mention_handover
    )
    handover = await send(
        identity.tenant_id,
        identity.user_id,
        specialist_id,
        text=body.text,
        idempotency_key=idempotency_key,
        **(
            {"source_thread_id": body.source_thread_id, "model_route": body.model_route,
             **({"reply_to_run_id": body.reply_to_run_id}
                if body.reply_to_run_id else {}),
             **({"quoted_message_context": quoted_message_context}
                if quoted_message_context else {})}
            if body.source_thread_id
            else {}
        ),
    )
    return {
        "handoverId": handover.handover_id,
        "specialistId": handover.specialist_id,
        "messageId": handover.message_id,
        "status": handover.status.value,
        "summary": handover.summary,
        "createdAt": handover.created_at.isoformat(),
        "sourceThreadId": handover.source_thread_id,
        "targetThreadId": handover.target_thread_id,
        "runId": handover.run_id,
    }


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


class SkillProposalGenerateRequest(StudioModel):
    model_route: str | None = Field(default=None, alias="modelRoute", max_length=100)


class SkillProposalConfirmRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class PersonalSkillEnabledRequest(StudioModel):
    enabled: bool
    expected_revision: int = Field(alias="expectedRevision", ge=1)


@router.get("/skill-proposals")
async def list_skill_proposals(
    identity: Reader,
    assistant: Service,
    status: str | None = Query(default=None),
) -> list[SkillProposal]:
    return await assistant.list_skill_proposals(
        identity.tenant_id, identity.user_id, status=status
    )


@router.post("/skill-proposals/{proposal_id}/generate")
async def generate_skill_proposal(
    proposal_id: str,
    body: SkillProposalGenerateRequest,
    identity: Writer,
    assistant: Service,
) -> SkillProposal:
    return await assistant.generate_skill_proposal(
        identity.tenant_id,
        identity.user_id,
        proposal_id,
        model_route=body.model_route,
    )


@router.post("/skill-proposals/{proposal_id}/save")
async def save_skill_proposal(
    proposal_id: str,
    body: SkillProposalConfirmRequest,
    identity: Writer,
    assistant: Service,
) -> PersonalSkill:
    """Save a generated draft disabled; enabling is a separate explicit action."""
    return await assistant.save_skill_proposal(
        identity.tenant_id,
        identity.user_id,
        proposal_id,
        expected_revision=body.expected_revision,
    )


@router.post("/skill-proposals/{proposal_id}/dismiss")
async def dismiss_skill_proposal(
    proposal_id: str,
    body: SkillProposalConfirmRequest,
    identity: Writer,
    assistant: Service,
) -> SkillProposal:
    return await assistant.dismiss_skill_proposal(
        identity.tenant_id,
        identity.user_id,
        proposal_id,
        expected_revision=body.expected_revision,
    )


@router.get("/personal-skills")
async def list_personal_skills(
    identity: Reader, assistant: Service
) -> list[PersonalSkill]:
    return await assistant.list_personal_skills(identity.tenant_id, identity.user_id)


@router.post("/personal-skills/{skill_id}/enabled")
async def set_personal_skill_enabled(
    skill_id: str,
    body: PersonalSkillEnabledRequest,
    identity: Writer,
    assistant: Service,
) -> PersonalSkill:
    return await assistant.set_personal_skill_enabled(
        identity.tenant_id,
        identity.user_id,
        skill_id,
        enabled=body.enabled,
        expected_revision=body.expected_revision,
    )


@router.delete("/personal-skills/{skill_id}")
async def delete_personal_skill(
    skill_id: str, identity: Writer, assistant: Service
) -> dict[str, bool]:
    return {
        "deleted": await assistant.delete_personal_skill(
            identity.tenant_id, identity.user_id, skill_id
        )
    }


class GroupCreateRequest(StudioModel):
    """群名是唯一的必填项；创建者与主助理由服务端按身份与默认规则落位。"""

    name: str = Field(min_length=1, max_length=80)


class GroupMemberRequest(StudioModel):
    specialist_id: str = Field(alias="specialistId", min_length=1, max_length=128)


class GroupReplyAsRequest(StudioModel):
    """None 表示默认主助理；其他值必须是当前群成员。"""

    reply_as: str | None = Field(default=None, alias="replyAs", max_length=128)


class GroupMessageRequest(StudioModel):
    text: str = Field(min_length=1, max_length=8_000)
    client_message_id: str | None = Field(
        default=None, alias="clientMessageId", max_length=128
    )


class GroupReactionRequest(StudioModel):
    emoji: str = Field(min_length=1, max_length=16)


@router.get("/groups")
async def list_assistant_groups(identity: Reader, assistant: Service) -> list[AssistantGroup]:
    return await assistant.list_assistant_groups(identity.tenant_id, identity.user_id)


@router.post("/groups", status_code=201)
async def create_assistant_group(
    body: GroupCreateRequest, identity: Writer, assistant: Service
) -> AssistantGroup:
    return await assistant.create_assistant_group(
        identity.tenant_id, identity.user_id, name=body.name
    )


@router.get("/groups/{group_id}")
async def get_assistant_group(
    group_id: str, identity: Reader, assistant: Service
) -> AssistantGroup:
    return await assistant.get_assistant_group(
        identity.tenant_id, identity.user_id, group_id
    )


@router.patch("/groups/{group_id}")
async def update_assistant_group(
    group_id: str, body: GroupReplyAsRequest, identity: Writer, assistant: Service
) -> AssistantGroup:
    return await assistant.set_assistant_group_reply_as(
        identity.tenant_id, identity.user_id, group_id, reply_as=body.reply_as
    )


@router.post("/groups/{group_id}/read")
async def mark_assistant_group_read(
    group_id: str, identity: Writer, assistant: Service
) -> AssistantGroup:
    return await assistant.mark_assistant_group_read(
        identity.tenant_id, identity.user_id, group_id
    )


@router.post("/groups/{group_id}/messages/{message_id}/reactions")
async def toggle_assistant_group_reaction(
    group_id: str,
    message_id: str,
    body: GroupReactionRequest,
    identity: Writer,
    assistant: Service,
) -> dict[str, list[str]]:
    reactions = await assistant.toggle_assistant_group_reaction(
        identity.tenant_id,
        identity.user_id,
        group_id,
        message_id,
        emoji=body.emoji,
    )
    return {"reactions": list(reactions)}


@router.post("/groups/{group_id}/members", status_code=201)
async def add_assistant_group_member(
    group_id: str, body: GroupMemberRequest, identity: Writer, assistant: Service
) -> AssistantGroup:
    return await assistant.add_assistant_group_member(
        identity.tenant_id, identity.user_id, group_id, specialist_id=body.specialist_id
    )


@router.delete("/groups/{group_id}/members/{member_id}")
async def remove_assistant_group_member(
    group_id: str, member_id: str, identity: Writer, assistant: Service
) -> AssistantGroup:
    return await assistant.remove_assistant_group_member(
        identity.tenant_id, identity.user_id, group_id, member_id=member_id
    )


@router.get("/groups/{group_id}/messages")
async def list_assistant_group_messages(
    group_id: str, identity: Reader, assistant: Service
) -> list[AssistantGroupMessage]:
    return await assistant.list_assistant_group_messages(
        identity.tenant_id, identity.user_id, group_id
    )


@router.post("/groups/{group_id}/messages", status_code=201)
async def post_assistant_group_message(
    group_id: str, body: GroupMessageRequest, identity: Writer, assistant: Service
) -> GroupPostResult:
    return await assistant.post_assistant_group_message(
        identity.tenant_id,
        identity.user_id,
        group_id,
        text=body.text,
        client_message_id=body.client_message_id,
    )
