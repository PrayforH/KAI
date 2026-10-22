"""Owner-scoped assistant API, shared by Web and authenticated channel adapters."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.assistant.models import (
    AssistantInboxItem,
    Objective,
    ObjectiveActionRequest,
    ObjectiveBrief,
    ObjectiveEvent,
    ObjectiveExecution,
    ObjectiveNoteRequest,
    UpdateObjectiveRequest,
)
from harness.assistant.service import AssistantService

router = APIRouter(prefix="/v1/studio/assistant", tags=["assistant"])


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
