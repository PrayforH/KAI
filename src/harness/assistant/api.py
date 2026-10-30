"""Owner-scoped assistant API, shared by Web and authenticated channel adapters."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.assistant.models import (
    AssistantInboxItem,
    Objective,
    ObjectiveActionRequest,
    ObjectiveBrief,
    ObjectiveEvent,
    ObjectiveExecution,
    ObjectiveNoteRequest,
    Specialist,
    SpecialistHandover,
    SpecialistStatus,
    UpdateObjectiveRequest,
)
from harness.assistant.service import AssistantService
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
    return await assistant.repository.enqueue(
        identity.tenant_id,
        identity.user_id,
        objective_id,
        trigger_key=f"manual:{idempotency_key}",
        now=datetime.now(UTC),
    )


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


@router.post("/inbox/{item_id}/read", status_code=204)
async def read_notice(item_id: str, identity: Reader, assistant: Service) -> None:
    await assistant.repository.read_notice(
        identity.tenant_id, identity.user_id, item_id, datetime.now(UTC)
    )


@router.get("/specialists")
async def list_specialists(identity: Reader, assistant: Service) -> list[Specialist]:
    return await assistant.repository.list_specialists(identity.tenant_id, identity.user_id)


class SpecialistCreateRequest(StudioModel):
    """创建请求只接受职责与能力来源；租户与用户由身份推导，绝不接受客户端伪造。"""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2_000)
    agent_name: str = Field(alias="agentName", pattern=r"^[a-z][a-z0-9-]{0,127}$")
    agent_version: str = Field(alias="agentVersion", min_length=1, max_length=128)


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
    )
    return created


@router.get("/specialists/{specialist_id}")
async def get_specialist(specialist_id: str, identity: Reader, assistant: Service) -> Specialist:
    return await assistant.repository.get_specialist(
        identity.tenant_id, identity.user_id, specialist_id
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


@router.get("/objectives/{objective_id}/managed")
async def managed_overview(objective_id: str, identity: Reader, assistant: Service):
    """项目托管面板：纳管绑定 + 每个任务的进展与检查点（只读投影）。"""
    objective = await assistant.repository.get(
        identity.tenant_id, identity.user_id, objective_id
    )
    bindings = await assistant.repository.managed_bindings(
        identity.tenant_id, identity.user_id, objective_id=objective_id
    )
    overview = await assistant.managed_overview(
        identity.tenant_id, identity.user_id, objective_id
    )
    by_thread = {str(item["thread_id"]): item for item in overview}
    return {
        "objectiveId": objective.objective_id,
        "revision": objective.revision,
        "budget": objective.budget.model_dump(by_alias=True) if objective.budget else None,
        "managed": [
            {
                "threadId": task.thread_id,
                "role": task.role.value,
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
        identity.tenant_id, identity.user_id, objective_id, body.thread_id
    )
    return {"objectiveId": objective_id, "threadId": body.thread_id, "bound": True}


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


@router.get("/handovers")
async def list_handovers(
    identity: Reader,
    assistant: Service,
    specialist_id: str | None = Query(default=None),
) -> list[SpecialistHandover]:
    return await assistant.repository.handovers(
        identity.tenant_id, identity.user_id, specialist_id=specialist_id
    )
