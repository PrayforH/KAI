"""Studio management and public invocation routes for Agent triggers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse

from harness.api.dependencies import Identity, ensure_permission, require_identity
from harness.api.downloads import attachment_content_disposition
from harness.api.event_streaming import wait_for_run_event
from harness.core.errors import NotFoundError
from harness.core.models import ArtifactStatus, Run
from harness.triggers.models import (
    AgentTrigger,
    CreateAgentTriggerRequest,
    CreatedAgentTrigger,
    InvokeAgentTriggerRequest,
    InvokeChatOpsTriggerRequest,
    RotateAgentTriggerSecretRequest,
    TriggerInvocation,
    TriggerKind,
    UpdateAgentTriggerRequest,
)
from harness.triggers.service import (
    AgentTriggerService,
    TriggerAuthenticationError,
    TriggerTaskNotFoundError,
)

_TERMINAL_EVENTS = {
    "run.cancelled",
    "run.failed",
    "run.rejected",
    "run.succeeded",
    "run.timed_out",
}


def get_trigger_service(request: Request) -> AgentTriggerService:
    container = getattr(request.app.state, "container", None)
    service = getattr(container, "triggers", None)
    if not isinstance(service, AgentTriggerService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "trigger_control_plane_not_configured",
                "message": "Agent Trigger control plane is not configured",
            },
        )
    return service


def require_trigger_admin(
    identity: Annotated[Identity, Depends(require_identity)],
) -> Identity:
    ensure_permission(identity, "studio:triggers:write")
    return identity


studio_router = APIRouter(prefix="/v1/studio", tags=["agent-triggers"])
public_router = APIRouter(prefix="/webhooks/agent-triggers", tags=["agent-trigger-invocation"])
chatops_router = APIRouter(prefix="/chatops/agent-triggers", tags=["chatops-invocation"])


@studio_router.get(
    "/agents/{agent_name}/triggers",
    response_model=list[AgentTrigger],
)
async def list_agent_triggers(
    agent_name: str,
    identity: Annotated[Identity, Depends(require_identity)],
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
) -> list[AgentTrigger]:
    ensure_permission(identity, "studio:read")
    return await service.list(identity.tenant_id, identity.user_id, agent_name)


@studio_router.post(
    "/agents/{agent_name}/triggers",
    response_model=CreatedAgentTrigger,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_trigger(
    agent_name: str,
    body: CreateAgentTriggerRequest,
    identity: Annotated[Identity, Depends(require_trigger_admin)],
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
) -> CreatedAgentTrigger:
    return await service.create(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        agent_name=agent_name,
        request=body,
    )


@studio_router.put(
    "/triggers/{trigger_id}",
    response_model=AgentTrigger,
)
async def update_agent_trigger(
    trigger_id: str,
    body: UpdateAgentTriggerRequest,
    identity: Annotated[Identity, Depends(require_trigger_admin)],
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
) -> AgentTrigger:
    return await service.update(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        trigger_id=trigger_id,
        request=body,
    )


@studio_router.post(
    "/triggers/{trigger_id}/rotate-secret",
    response_model=CreatedAgentTrigger,
)
async def rotate_agent_trigger_secret(
    trigger_id: str,
    body: RotateAgentTriggerSecretRequest,
    identity: Annotated[Identity, Depends(require_trigger_admin)],
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
) -> CreatedAgentTrigger:
    return await service.rotate_secret(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        trigger_id=trigger_id,
        expected_revision=body.expected_revision,
    )


@public_router.post(
    "/{trigger_id}",
    response_model=TriggerInvocation,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invoke_agent_trigger(
    trigger_id: str,
    body: InvokeAgentTriggerRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
    authorization: Annotated[str, Header(alias="Authorization")],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1)],
) -> TriggerInvocation:
    invocation, run = await _invoke(
        service,
        trigger_id=trigger_id,
        authorization=authorization,
        idempotency_key=idempotency_key,
        prompt=body.prompt,
    )
    container = request.app.state.container
    if container.auto_execute:
        background_tasks.add_task(container.worker.execute, run.tenant_id, run.run_id)
    return invocation


@public_router.get("/{trigger_id}/openapi.json", include_in_schema=False)
async def agent_trigger_openapi(trigger_id: str, request: Request) -> Response:
    descriptor = await get_trigger_service(request).public_descriptor(
        trigger_id,
        kind=TriggerKind.WEBHOOK,
    )
    base = str(request.base_url).rstrip("/")
    endpoint = f"{base}/webhooks/agent-triggers/{quote(trigger_id, safe='')}"
    document: dict[str, object] = {
        "openapi": "3.1.0",
        "info": {
            "title": descriptor.display_name,
            "description": descriptor.description,
            "version": descriptor.agent_version,
        },
        "servers": [{"url": endpoint}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/": {
                "post": {
                    "operationId": "invokeAgent",
                    "summary": "Create an Agent run",
                    "parameters": [
                        {
                            "name": "Idempotency-Key",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string", "minLength": 1},
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/InvokeRequest"}
                            }
                        },
                    },
                    "responses": {
                        "202": {
                            "description": "Run accepted",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Invocation"}
                                }
                            },
                        }
                    },
                }
            },
            "/runs/{runId}": {
                "get": {
                    "operationId": "getAgentRun",
                    "summary": "Get run state",
                    "parameters": [{"$ref": "#/components/parameters/RunId"}],
                    "responses": {
                        "200": {
                            "description": "Current run",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/Run"}}
                            },
                        }
                    },
                }
            },
            "/runs/{runId}:cancel": {
                "post": {
                    "operationId": "cancelAgentRun",
                    "summary": "Cancel a run",
                    "parameters": [{"$ref": "#/components/parameters/RunId"}],
                    "responses": {
                        "200": {
                            "description": "Cancellation state",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/Run"}}
                            },
                        }
                    },
                }
            },
            "/runs/{runId}/events": {
                "get": {
                    "operationId": "subscribeAgentRun",
                    "summary": "Subscribe to durable run events",
                    "parameters": [
                        {"$ref": "#/components/parameters/RunId"},
                        {
                            "name": "Last-Event-ID",
                            "in": "header",
                            "required": False,
                            "schema": {"type": "integer", "minimum": 0},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Server-sent event stream",
                            "content": {"text/event-stream": {}},
                        }
                    },
                }
            },
            "/runs/{runId}/artifacts/{artifactId}/content": {
                "get": {
                    "operationId": "downloadAgentArtifact",
                    "summary": "Download a run artifact",
                    "parameters": [
                        {"$ref": "#/components/parameters/RunId"},
                        {
                            "name": "artifactId",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"200": {"description": "Artifact bytes"}},
                }
            },
        },
        "components": {
            "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
            "parameters": {
                "RunId": {
                    "name": "runId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            },
            "schemas": {
                "InvokeRequest": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["prompt"],
                    "properties": {
                        "prompt": {"type": "string", "minLength": 1, "maxLength": 200000}
                    },
                },
                "Invocation": {
                    "type": "object",
                    "required": [
                        "triggerId",
                        "sessionId",
                        "runId",
                        "status",
                        "environment",
                        "agentName",
                        "agentVersion",
                        "deploymentSnapshotId",
                    ],
                    "properties": {
                        "triggerId": {"type": "string"},
                        "sessionId": {"type": "string"},
                        "runId": {"type": "string"},
                        "status": {"type": "string"},
                        "environment": {"type": "string"},
                        "agentName": {"type": "string"},
                        "agentVersion": {"type": "string"},
                        "deploymentSnapshotId": {"type": "string"},
                    },
                },
                "Run": {
                    "type": "object",
                    "required": [
                        "run_id",
                        "session_id",
                        "tenant_id",
                        "status",
                        "idempotency_key",
                        "created_at",
                        "updated_at",
                    ],
                    "properties": {
                        "run_id": {"type": "string"},
                        "session_id": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "status": {"type": "string"},
                        "idempotency_key": {"type": "string"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                        "error_code": {"type": ["string", "null"]},
                        "input": {"type": "object"},
                    },
                },
            },
        },
        "x-agent-studio": {
            "triggerId": descriptor.trigger.trigger_id,
            "agentName": descriptor.trigger.agent_name,
            "agentVersion": descriptor.agent_version,
            "environment": descriptor.trigger.environment.value,
            "lifecycle": "Session -> Run -> Event -> Artifact",
        },
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(canonical).hexdigest()}"'
    headers = {
        "Cache-Control": "public, max-age=300, must-revalidate",
        "ETag": etag,
    }
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return JSONResponse(document, headers=headers)


@public_router.get(
    "/{trigger_id}/runs/{run_id}",
    response_model=Run,
)
async def get_agent_trigger_run(
    trigger_id: str,
    run_id: str,
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
    authorization: Annotated[str, Header(alias="Authorization")],
) -> Run:
    return await _authorized_webhook_run(
        service,
        trigger_id=trigger_id,
        authorization=authorization,
        run_id=run_id,
    )


@public_router.post("/{trigger_id}/runs/{run_id}:cancel", response_model=Run)
async def cancel_agent_trigger_run(
    trigger_id: str,
    run_id: str,
    request: Request,
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
    authorization: Annotated[str, Header(alias="Authorization")],
) -> Run:
    run = await _authorized_webhook_run(
        service,
        trigger_id=trigger_id,
        authorization=authorization,
        run_id=run_id,
    )
    return await request.app.state.container.runs.cancel(run.tenant_id, run.run_id)


@public_router.get("/{trigger_id}/runs/{run_id}/events")
async def subscribe_agent_trigger_run(
    trigger_id: str,
    run_id: str,
    request: Request,
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
    authorization: Annotated[str, Header(alias="Authorization")],
) -> StreamingResponse:
    run = await _authorized_webhook_run(
        service,
        trigger_id=trigger_id,
        authorization=authorization,
        run_id=run_id,
    )
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is not None and not last_event_id.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "event_cursor_invalid",
                "message": "Last-Event-ID must be a durable event sequence",
            },
        )
    return StreamingResponse(
        _run_event_stream(request, run, int(last_event_id or "0")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@public_router.get("/{trigger_id}/runs/{run_id}/artifacts/{artifact_id}/content")
async def download_agent_trigger_artifact(
    trigger_id: str,
    run_id: str,
    artifact_id: str,
    request: Request,
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
    authorization: Annotated[str, Header(alias="Authorization")],
) -> Response:
    run = await _authorized_webhook_run(
        service,
        trigger_id=trigger_id,
        authorization=authorization,
        run_id=run_id,
    )
    try:
        artifact = await request.app.state.container.artifacts.get(
            run.tenant_id,
            artifact_id,
        )
    except NotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "artifact_not_found", "message": "Artifact not found"},
        ) from error
    if artifact.run_id != run.run_id or artifact.status is not ArtifactStatus.READY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "artifact_not_found", "message": "Artifact not found"},
        )
    artifact, content = await request.app.state.container.artifacts.download(
        run.tenant_id,
        artifact_id,
    )
    return Response(
        content=content,
        media_type=artifact.media_type,
        headers={"Content-Disposition": attachment_content_disposition(artifact.name)},
    )


@chatops_router.post(
    "/{trigger_id}",
    response_model=TriggerInvocation,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invoke_chatops_trigger(
    trigger_id: str,
    body: InvokeChatOpsTriggerRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    service: Annotated[AgentTriggerService, Depends(get_trigger_service)],
    authorization: Annotated[str, Header(alias="Authorization")],
) -> TriggerInvocation:
    try:
        invocation, run = await service.invoke_chatops(
            trigger_id=trigger_id,
            secret=_bearer_secret(authorization),
            message_id=body.message_id,
            channel_id=body.channel_id,
            prompt=body.text,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error
    container = request.app.state.container
    if container.auto_execute:
        background_tasks.add_task(container.worker.execute, run.tenant_id, run.run_id)
    return invocation


async def _invoke(
    service: AgentTriggerService,
    *,
    trigger_id: str,
    authorization: str,
    idempotency_key: str,
    prompt: str,
) -> tuple[TriggerInvocation, Run]:
    try:
        return await service.invoke(
            trigger_id=trigger_id,
            secret=_bearer_secret(authorization),
            idempotency_key=idempotency_key,
            prompt=prompt,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error


async def _authorized_webhook_run(
    service: AgentTriggerService,
    *,
    trigger_id: str,
    authorization: str,
    run_id: str,
) -> Run:
    try:
        return await service.run(
            trigger_id=trigger_id,
            secret=_bearer_secret(authorization),
            run_id=run_id,
            kind=TriggerKind.WEBHOOK,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error
    except TriggerTaskNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "trigger_run_not_found", "message": "Run not found"},
        ) from error


async def _run_event_stream(
    request: Request,
    run: Run,
    sequence: int,
) -> AsyncIterator[str]:
    snapshot = {
        "run": run.model_dump(mode="json"),
        "cursor": sequence,
    }
    yield f"event: run.snapshot\ndata: {json.dumps(snapshot, separators=(',', ':'))}\n\n"
    terminal = False
    while not terminal:
        events = await request.app.state.container.observed_events.list_after(
            run.tenant_id,
            run.run_id,
            sequence,
        )
        for event in events:
            sequence = event.sequence
            payload = event.model_dump(mode="json")
            yield (
                f"id: {sequence}\n"
                f"event: {event.type}\n"
                f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
            )
            if event.type in _TERMINAL_EVENTS:
                terminal = True
        if terminal:
            continue
        current = await request.app.state.container.runs.get(run.tenant_id, run.run_id)
        if current.status.is_terminal:
            terminal = True
        else:
            await wait_for_run_event(
                request.app.state.container.event_wakeup,
                run.tenant_id,
                run.run_id,
                sequence,
                fallback_poll_seconds=0.05,
            )


def bearer_secret(authorization: str) -> str:
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not credential:
        raise _authentication_error()
    return credential


def authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "trigger_authentication_failed",
            "message": "Trigger authentication failed",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


_bearer_secret = bearer_secret
_authentication_error = authentication_error
