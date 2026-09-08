"""A2A 1.0 HTTP+JSON adapter over durable Harness Runs."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from urllib.parse import quote

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response, StreamingResponse

from harness.api.downloads import attachment_content_disposition
from harness.api.event_streaming import wait_for_run_event
from harness.core.errors import ConflictError, NotFoundError
from harness.core.events import RunEvent
from harness.core.models import ArtifactStatus, Run, RunStatus
from harness.quota.repositories import QuotaExceededError
from harness.runtime.message_mapper import safe_model_text
from harness.triggers.api import bearer_secret, get_trigger_service
from harness.triggers.models import TriggerKind
from harness.triggers.service import (
    AgentTriggerService,
    TriggerAuthenticationError,
    TriggerTaskNotFoundError,
)

router = APIRouter(
    prefix="/a2a/agent-triggers/{trigger_id}",
    tags=["a2a"],
)

_A2A_MEDIA_TYPE = "application/a2a+json"
_SUPPORTED_VERSION = "1.0"
_OUTPUT_MODES = frozenset({"text/plain", "application/json"})
_TERMINAL_EVENTS = {
    "run.cancelled",
    "run.failed",
    "run.rejected",
    "run.succeeded",
    "run.timed_out",
}
_STATUS_EVENTS = {
    "approval.cancelled",
    "approval.expired",
    "approval.requested",
    "run.cancelling",
    "run.provisioning",
    "run.queued",
    "run.recovered",
    "run.resumed",
    "run.running",
    *_TERMINAL_EVENTS,
}
_TASK_STATES = frozenset(
    {
        "TASK_STATE_SUBMITTED",
        "TASK_STATE_WORKING",
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
        "TASK_STATE_INPUT_REQUIRED",
        "TASK_STATE_AUTH_REQUIRED",
    }
)


class A2AProtocolError(Exception):
    """HTTP+JSON error carrying the canonical A2A/Google status mapping."""

    def __init__(
        self,
        status_code: int,
        rpc_status: str,
        message: str,
        *,
        reason: str | None = None,
        metadata: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rpc_status = rpc_status
        self.message = message
        self.reason = reason
        self.metadata = metadata or {}
        self.headers = headers or {}


async def a2a_protocol_error_response(
    _request: Request,
    error: Exception,
) -> JSONResponse:
    assert isinstance(error, A2AProtocolError)
    value: dict[str, object] = {
        "code": error.status_code,
        "status": error.rpc_status,
        "message": error.message,
    }
    if error.reason is not None:
        detail: dict[str, object] = {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": error.reason,
            "domain": "a2a-protocol.org",
        }
        if error.metadata:
            detail["metadata"] = error.metadata
        value["details"] = [detail]
    return JSONResponse(
        status_code=error.status_code,
        content={"error": value},
        headers=error.headers,
        media_type=_A2A_MEDIA_TYPE,
    )


@dataclass(frozen=True)
class A2AMessageInput:
    message_id: str
    prompt: str
    context_id: str | None
    task_id: str | None
    return_immediately: bool
    history_length: int | None


def _protocol_error(
    reason: str,
    message: str,
    *,
    metadata: dict[str, str] | None = None,
) -> A2AProtocolError:
    mapping = {
        "CONTENT_TYPE_NOT_SUPPORTED": (400, "INVALID_ARGUMENT"),
        "PUSH_NOTIFICATION_NOT_SUPPORTED": (400, "FAILED_PRECONDITION"),
        "TASK_NOT_CANCELABLE": (400, "FAILED_PRECONDITION"),
        "TASK_NOT_FOUND": (404, "NOT_FOUND"),
        "UNSUPPORTED_OPERATION": (400, "FAILED_PRECONDITION"),
        "VERSION_NOT_SUPPORTED": (400, "FAILED_PRECONDITION"),
    }
    status_code, rpc_status = mapping[reason]
    return A2AProtocolError(
        status_code,
        rpc_status,
        message,
        reason=reason,
        metadata=metadata,
    )


def _invalid(message: str, *, field: str | None = None) -> A2AProtocolError:
    metadata = {"field": field} if field is not None else None
    return A2AProtocolError(
        status.HTTP_400_BAD_REQUEST,
        "INVALID_ARGUMENT",
        message,
        metadata=metadata,
    )


def _authentication_error() -> A2AProtocolError:
    return A2AProtocolError(
        status.HTTP_401_UNAUTHORIZED,
        "UNAUTHENTICATED",
        "A valid Bearer credential is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _domain_conflict(error: ConflictError) -> A2AProtocolError:
    if isinstance(error, QuotaExceededError):
        return A2AProtocolError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "RESOURCE_EXHAUSTED",
            str(error),
        )
    return A2AProtocolError(
        status.HTTP_409_CONFLICT,
        "ABORTED",
        str(error),
    )


def _version(request: Request, *, optional: bool = False) -> None:
    value = request.headers.get("A2A-Version")
    if value is None:
        value = request.query_params.get("A2A-Version")
    if optional and value is None:
        return
    if value != _SUPPORTED_VERSION:
        raise _protocol_error(
            "VERSION_NOT_SUPPORTED",
            f"A2A-Version {_SUPPORTED_VERSION} is required",
            metadata={"supportedVersions": _SUPPORTED_VERSION},
        )


def _secret(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    try:
        return bearer_secret(authorization)
    except Exception as error:
        raise _authentication_error() from error


async def _body(request: Request) -> dict[str, object]:
    content_type = request.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if content_type != _A2A_MEDIA_TYPE:
        raise _protocol_error(
            "CONTENT_TYPE_NOT_SUPPORTED",
            f"Content-Type must be {_A2A_MEDIA_TYPE}",
            metadata={"supportedMediaTypes": _A2A_MEDIA_TYPE},
        )
    length = request.headers.get("Content-Length")
    if length is not None and length.isdigit() and int(length) > 512 * 1024:
        raise _invalid("A2A request body is too large")
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise _invalid("Request body must be valid JSON") from error
    if not isinstance(value, dict):
        raise _invalid("Request body must be a JSON object")
    return cast(dict[str, object], value)


def _message(body: dict[str, object]) -> A2AMessageInput:
    message_value = body.get("message")
    if not isinstance(message_value, dict):
        raise _invalid("message is required", field="message")
    message = cast(dict[str, object], message_value)
    message_id = message.get("messageId")
    if not isinstance(message_id, str) or not message_id.strip():
        raise _invalid("message.messageId is required", field="message.messageId")
    if len(message_id) > 256:
        raise _invalid("message.messageId is too long", field="message.messageId")
    if message.get("role") != "ROLE_USER":
        raise _invalid("message.role must be ROLE_USER", field="message.role")

    parts_value = message.get("parts")
    if not isinstance(parts_value, list) or not parts_value:
        raise _invalid("message.parts must contain at least one part", field="message.parts")
    text: list[str] = []
    for index, part_value in enumerate(cast(list[object], parts_value)):
        if not isinstance(part_value, dict):
            raise _invalid("Each message part must be an object", field=f"message.parts[{index}]")
        part = cast(dict[str, object], part_value)
        text_value = part.get("text")
        if not isinstance(text_value, str) or any(name in part for name in ("raw", "url", "data")):
            raise _protocol_error(
                "CONTENT_TYPE_NOT_SUPPORTED",
                "This Agent accepts text parts only",
                metadata={"supportedMediaTypes": "text/plain"},
            )
        text.append(text_value)
    prompt = "\n".join(text).strip()
    if not prompt:
        raise _invalid("Text parts must not be empty", field="message.parts")
    if len(prompt) > 200_000:
        raise _invalid("Combined text exceeds 200000 characters", field="message.parts")

    context_id = message.get("contextId")
    task_id = message.get("taskId")
    for field, value in (("contextId", context_id), ("taskId", task_id)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise _invalid(f"message.{field} must be a non-empty string", field=f"message.{field}")

    configuration_value = body.get("configuration")
    if configuration_value is not None and not isinstance(configuration_value, dict):
        raise _invalid("configuration must be an object", field="configuration")
    configuration = (
        cast(dict[str, object], configuration_value)
        if isinstance(configuration_value, dict)
        else {}
    )
    return_immediately = configuration.get("returnImmediately", False)
    if not isinstance(return_immediately, bool):
        raise _invalid(
            "configuration.returnImmediately must be a boolean",
            field="configuration.returnImmediately",
        )
    history_length = configuration.get("historyLength")
    if history_length is not None and (
        not isinstance(history_length, int)
        or isinstance(history_length, bool)
        or history_length < 0
    ):
        raise _invalid(
            "configuration.historyLength must be a non-negative integer",
            field="configuration.historyLength",
        )
    modes_value = configuration.get("acceptedOutputModes")
    if modes_value is not None:
        if not isinstance(modes_value, list) or not all(
            isinstance(mode, str) for mode in cast(list[object], modes_value)
        ):
            raise _invalid(
                "configuration.acceptedOutputModes must be an array of media types",
                field="configuration.acceptedOutputModes",
            )
        requested = set(cast(list[str], modes_value))
        if requested and requested.isdisjoint(_OUTPUT_MODES):
            raise _protocol_error(
                "CONTENT_TYPE_NOT_SUPPORTED",
                "None of the requested output modes are supported",
                metadata={"supportedMediaTypes": ",".join(sorted(_OUTPUT_MODES))},
            )
    if configuration.get("taskPushNotificationConfig") is not None:
        raise _protocol_error(
            "PUSH_NOTIFICATION_NOT_SUPPORTED",
            "Push notifications are not enabled for this Agent",
        )
    return A2AMessageInput(
        message_id=message_id,
        prompt=prompt,
        context_id=cast(str | None, context_id),
        task_id=cast(str | None, task_id),
        return_immediately=return_immediately,
        history_length=history_length,
    )


def _state(status_value: RunStatus) -> str:
    return {
        RunStatus.QUEUED: "TASK_STATE_SUBMITTED",
        RunStatus.WAITING_APPROVAL: "TASK_STATE_AUTH_REQUIRED",
        RunStatus.SUCCEEDED: "TASK_STATE_COMPLETED",
        RunStatus.FAILED: "TASK_STATE_FAILED",
        RunStatus.TIMED_OUT: "TASK_STATE_FAILED",
        RunStatus.CANCELLED: "TASK_STATE_CANCELED",
        RunStatus.REJECTED: "TASK_STATE_REJECTED",
    }.get(status_value, "TASK_STATE_WORKING")


def _timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    milliseconds = normalized.isoformat(timespec="milliseconds")
    return milliseconds.removesuffix("+00:00") + "Z"


def _artifact_url(request: Request, trigger_id: str, run_id: str, artifact_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return (
        f"{base}/a2a/agent-triggers/{quote(trigger_id, safe='')}"
        f"/tasks/{quote(run_id, safe='')}/artifacts/{quote(artifact_id, safe='')}/content"
    )


async def _task_value(
    request: Request,
    trigger_id: str,
    run: Run,
    *,
    include_artifacts: bool = True,
    history_length: int | None = None,
) -> dict[str, object]:
    events = await request.app.state.container.observed_events.list_after(
        run.tenant_id,
        run.run_id,
        0,
    )
    response_text = "".join(
        safe_model_text(str(event.payload.get("text", "")))
        for event in events
        if event.type == "message.delta"
    )
    value: dict[str, object] = {
        "id": run.run_id,
        "contextId": run.session_id,
        "status": {
            "state": _state(run.status),
            "timestamp": _timestamp(run.updated_at),
        },
        "createdAt": _timestamp(run.created_at),
        "lastModified": _timestamp(run.updated_at),
        "metadata": {
            "harnessRunStatus": run.status.value,
            **({"errorCode": run.error_code} if run.error_code else {}),
        },
    }

    if include_artifacts:
        projected: list[dict[str, object]] = []
        if response_text:
            projected.append(
                {
                    "artifactId": f"assistant-response-{run.run_id}",
                    "name": "response.txt",
                    "description": "Agent response",
                    "parts": [{"text": response_text, "mediaType": "text/plain"}],
                }
            )
        artifacts = await request.app.state.container.artifacts.list_for_run(
            run.tenant_id,
            run.run_id,
        )
        projected.extend(
            {
                "artifactId": item.artifact_id,
                "name": item.name,
                "parts": [
                    {
                        "url": _artifact_url(
                            request,
                            trigger_id,
                            run.run_id,
                            item.artifact_id,
                        ),
                        "filename": item.name,
                        "mediaType": item.media_type,
                    }
                ],
                "metadata": {
                    "sha256": item.sha256,
                    "sizeBytes": item.size_bytes,
                },
            }
            for item in artifacts
            if item.status is ArtifactStatus.READY
        )
        value["artifacts"] = projected

    if history_length != 0:
        message_id = str(run.input.get("a2a_message_id", f"user-{run.run_id}"))
        history: list[dict[str, object]] = [
            {
                "messageId": message_id,
                "contextId": run.session_id,
                "taskId": run.run_id,
                "role": "ROLE_USER",
                "parts": [
                    {
                        "text": str(run.input.get("prompt", "")),
                        "mediaType": "text/plain",
                    }
                ],
            }
        ]
        if response_text:
            response_message_id = next(
                (
                    str(event.payload["message_id"])
                    for event in reversed(events)
                    if event.type in {"message.delta", "message.completed"}
                    and event.payload.get("message_id")
                ),
                f"agent-{run.run_id}",
            )
            history.append(
                {
                    "messageId": response_message_id,
                    "contextId": run.session_id,
                    "taskId": run.run_id,
                    "role": "ROLE_AGENT",
                    "parts": [{"text": response_text, "mediaType": "text/plain"}],
                }
            )
        if history_length is not None:
            history = history[-history_length:]
        if history:
            value["history"] = history
    return value


async def _authorized_run(
    service: AgentTriggerService,
    request: Request,
    *,
    trigger_id: str,
    run_id: str,
) -> Run:
    try:
        return await service.run(
            trigger_id=trigger_id,
            secret=_secret(request),
            run_id=run_id,
            kind=TriggerKind.A2A,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error
    except TriggerTaskNotFoundError as error:
        raise _protocol_error(
            "TASK_NOT_FOUND",
            f"Task is not available: {run_id}",
        ) from error


def _start_execution(request: Request, run: Run) -> None:
    container = request.app.state.container
    if not container.auto_execute or run.status is not RunStatus.QUEUED:
        return
    task = asyncio.create_task(
        container.worker.execute(run.tenant_id, run.run_id),
        name=f"a2a-run-{run.run_id}",
    )
    stored_tasks = getattr(request.app.state, "a2a_execution_tasks", None)
    if isinstance(stored_tasks, set):
        tasks = cast(set[asyncio.Task[None]], stored_tasks)
    else:
        tasks: set[asyncio.Task[None]] = set()
        request.app.state.a2a_execution_tasks = tasks
    tasks.add(task)

    def finish(completed: asyncio.Task[None]) -> None:
        tasks.discard(completed)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(finish)


async def _wait_for_run(request: Request, run: Run) -> Run:
    current = run
    while not current.status.is_terminal and current.status is not RunStatus.WAITING_APPROVAL:
        if await request.is_disconnected():
            return current
        await asyncio.sleep(0.05)
        current = await request.app.state.container.runs.get(
            run.tenant_id,
            run.run_id,
        )
    return current


def _sse(payload: Mapping[str, object], *, sequence: int | None = None) -> str:
    prefix = f"id: {sequence}\n" if sequence is not None else ""
    return f"{prefix}data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _text_update(
    run: Run,
    event: RunEvent,
    *,
    append: bool,
    last_chunk: bool,
) -> dict[str, object]:
    return {
        "artifactUpdate": {
            "taskId": run.run_id,
            "contextId": run.session_id,
            "artifact": {
                "artifactId": f"assistant-response-{run.run_id}",
                "name": "response.txt",
                "parts": [
                    {
                        "text": safe_model_text(str(event.payload.get("text", ""))),
                        "mediaType": "text/plain",
                    }
                ],
            },
            "append": append,
            "lastChunk": last_chunk,
        }
    }


async def _stream_events(
    request: Request,
    trigger_id: str,
    run: Run,
    *,
    sequence: int = 0,
) -> AsyncIterator[str]:
    yield _sse(
        {
            "task": await _task_value(
                request,
                trigger_id,
                run,
                history_length=0,
            )
        }
    )
    terminal = run.status.is_terminal or run.status is RunStatus.WAITING_APPROVAL
    prior = await request.app.state.container.observed_events.list_after(
        run.tenant_id,
        run.run_id,
        0,
    )
    text_started = any(
        event.type == "message.delta" and event.sequence <= sequence for event in prior
    )
    pending_text: RunEvent | None = None
    while not terminal:
        events = await request.app.state.container.observed_events.list_after(
            run.tenant_id,
            run.run_id,
            sequence,
        )
        for event in events:
            current = await request.app.state.container.runs.get(
                run.tenant_id,
                run.run_id,
            )
            if event.type == "message.delta":
                text = safe_model_text(str(event.payload.get("text", "")))
                if not text:
                    sequence = event.sequence
                    continue
                if pending_text is not None:
                    yield _sse(
                        _text_update(
                            run,
                            pending_text,
                            append=text_started,
                            last_chunk=False,
                        ),
                        sequence=pending_text.sequence,
                    )
                    text_started = True
                pending_text = event
                sequence = event.sequence
                continue

            if pending_text is not None:
                final_text = event.type == "message.completed" or event.type in _TERMINAL_EVENTS
                yield _sse(
                    _text_update(
                        run,
                        pending_text,
                        append=text_started,
                        last_chunk=final_text,
                    ),
                    sequence=pending_text.sequence,
                )
                text_started = True
                pending_text = None

            sequence = event.sequence
            if event.type == "artifact.ready":
                artifact_id = str(event.payload.get("artifact_id", ""))
                if artifact_id:
                    payload = {
                        "artifactUpdate": {
                            "taskId": run.run_id,
                            "contextId": run.session_id,
                            "artifact": {
                                "artifactId": artifact_id,
                                "name": str(event.payload.get("name", "artifact")),
                                "parts": [
                                    {
                                        "url": _artifact_url(
                                            request,
                                            trigger_id,
                                            run.run_id,
                                            artifact_id,
                                        ),
                                        "filename": str(event.payload.get("name", "artifact")),
                                        "mediaType": str(
                                            event.payload.get(
                                                "media_type",
                                                "application/octet-stream",
                                            )
                                        ),
                                    }
                                ],
                            },
                            "append": False,
                            "lastChunk": True,
                        }
                    }
                    yield _sse(payload, sequence=sequence)
            elif event.type in _STATUS_EVENTS:
                payload = {
                    "statusUpdate": {
                        "taskId": run.run_id,
                        "contextId": run.session_id,
                        "status": {
                            "state": _state(current.status),
                            "timestamp": _timestamp(event.timestamp),
                        },
                    }
                }
                yield _sse(payload, sequence=sequence)
            if event.type in _TERMINAL_EVENTS or current.status is RunStatus.WAITING_APPROVAL:
                terminal = True
        if not terminal:
            await wait_for_run_event(
                request.app.state.container.event_wakeup,
                run.tenant_id,
                run.run_id,
                sequence,
                fallback_poll_seconds=0.05,
            )


def _query_int(
    request: Request,
    name: str,
    *,
    default: int | None = None,
    minimum: int = 0,
    maximum: int | None = None,
) -> int | None:
    value = request.query_params.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise _invalid(f"{name} must be an integer", field=name) from error
    if parsed < minimum or (maximum is not None and parsed > maximum):
        upper = f" and at most {maximum}" if maximum is not None else ""
        raise _invalid(
            f"{name} must be at least {minimum}{upper}",
            field=name,
        )
    return parsed


def _query_bool(request: Request, name: str, *, default: bool) -> bool:
    value = request.query_params.get(name)
    if value is None:
        return default
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    raise _invalid(f"{name} must be true or false", field=name)


def _parse_timestamp(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _invalid(f"{field} must be an ISO 8601 timestamp", field=field) from error
    if parsed.tzinfo is None:
        raise _invalid(f"{field} must include a timezone", field=field)
    return parsed.astimezone(UTC)


def _cursor(trigger_id: str, run: Run) -> str:
    value = json.dumps(
        {
            "triggerId": trigger_id,
            "updatedAt": _timestamp(run.updated_at),
            "runId": run.run_id,
        },
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_cursor(trigger_id: str, value: str) -> tuple[datetime, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw_payload: object = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(raw_payload, dict):
            raise ValueError
        payload = cast(dict[str, object], raw_payload)
        if payload.get("triggerId") != trigger_id:
            raise ValueError
        updated_at = payload.get("updatedAt")
        run_id = payload.get("runId")
        if not isinstance(updated_at, str) or not isinstance(run_id, str):
            raise ValueError
        return _parse_timestamp(updated_at, field="pageToken"), run_id
    except (binascii.Error, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid("pageToken is invalid", field="pageToken") from error


@router.get("/agent-card.json")
async def agent_card(trigger_id: str, request: Request) -> Response:
    descriptor = await get_trigger_service(request).public_descriptor(
        trigger_id,
        kind=TriggerKind.A2A,
    )
    base = str(request.base_url).rstrip("/")
    endpoint = f"{base}/a2a/agent-triggers/{quote(trigger_id, safe='')}"
    card: dict[str, object] = {
        "name": descriptor.display_name,
        "description": descriptor.description,
        "supportedInterfaces": [
            {
                "url": endpoint,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": _SUPPORTED_VERSION,
            }
        ],
        "version": descriptor.agent_version,
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "bearer": {
                "httpAuthSecurityScheme": {
                    "description": "Trigger-scoped Bearer token issued by Agent Studio.",
                    "scheme": "Bearer",
                }
            }
        },
        "securityRequirements": [{"schemes": {"bearer": {"list": []}}}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": sorted(_OUTPUT_MODES),
        "skills": [
            {
                "id": skill.skill_id,
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
                "inputModes": ["text/plain"],
                "outputModes": sorted(_OUTPUT_MODES),
            }
            for skill in descriptor.skills
        ],
    }
    canonical = json.dumps(card, sort_keys=True, separators=(",", ":")).encode()
    etag = f'"{hashlib.sha256(canonical).hexdigest()}"'
    headers = {
        "Cache-Control": "public, max-age=300, must-revalidate",
        "ETag": etag,
    }
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return JSONResponse(card, headers=headers, media_type=_A2A_MEDIA_TYPE)


@router.post("/message:send")
async def send_message(trigger_id: str, request: Request) -> JSONResponse:
    _version(request)
    message = _message(await _body(request))
    service = get_trigger_service(request)
    if message.task_id is not None:
        existing = await _authorized_run(
            service,
            request,
            trigger_id=trigger_id,
            run_id=message.task_id,
        )
        if message.context_id is not None and message.context_id != existing.session_id:
            raise _invalid(
                "message.contextId does not match message.taskId",
                field="message.contextId",
            )
        raise _protocol_error(
            "UNSUPPORTED_OPERATION",
            "Continuing an existing task is not supported; use contextId to create "
            "a new task in the same context",
        )
    try:
        _invocation, run = await service.invoke_a2a(
            trigger_id=trigger_id,
            secret=_secret(request),
            message_id=message.message_id,
            prompt=message.prompt,
            context_id=message.context_id,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error
    except TriggerTaskNotFoundError as error:
        raise _protocol_error(
            "TASK_NOT_FOUND",
            f"Context is not available: {message.context_id}",
        ) from error
    except ConflictError as error:
        raise _domain_conflict(error) from error
    _start_execution(request, run)
    if not message.return_immediately:
        run = await _wait_for_run(request, run)
    return JSONResponse(
        {
            "task": await _task_value(
                request,
                trigger_id,
                run,
                history_length=message.history_length,
            )
        },
        media_type=_A2A_MEDIA_TYPE,
    )


@router.post("/message:stream")
async def stream_message(trigger_id: str, request: Request) -> StreamingResponse:
    _version(request)
    message = _message(await _body(request))
    service = get_trigger_service(request)
    if message.task_id is not None:
        existing = await _authorized_run(
            service,
            request,
            trigger_id=trigger_id,
            run_id=message.task_id,
        )
        if message.context_id is not None and message.context_id != existing.session_id:
            raise _invalid(
                "message.contextId does not match message.taskId",
                field="message.contextId",
            )
        raise _protocol_error(
            "UNSUPPORTED_OPERATION",
            "Continuing an existing task is not supported; use contextId to create "
            "a new task in the same context",
        )
    try:
        _invocation, run = await service.invoke_a2a(
            trigger_id=trigger_id,
            secret=_secret(request),
            message_id=message.message_id,
            prompt=message.prompt,
            context_id=message.context_id,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error
    except TriggerTaskNotFoundError as error:
        raise _protocol_error(
            "TASK_NOT_FOUND",
            f"Context is not available: {message.context_id}",
        ) from error
    except ConflictError as error:
        raise _domain_conflict(error) from error
    _start_execution(request, run)
    return StreamingResponse(
        _stream_events(request, trigger_id, run),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks")
async def list_tasks(trigger_id: str, request: Request) -> JSONResponse:
    _version(request)
    page_size = cast(int, _query_int(request, "pageSize", default=50, minimum=1, maximum=100))
    history_length = _query_int(request, "historyLength", minimum=0)
    include_artifacts = _query_bool(request, "includeArtifacts", default=False)
    context_id = request.query_params.get("contextId")
    state = request.query_params.get("status")
    if state is not None and state not in _TASK_STATES:
        raise _invalid(
            f"status must be one of: {', '.join(sorted(_TASK_STATES))}",
            field="status",
        )
    after_value = request.query_params.get("statusTimestampAfter")
    after = (
        _parse_timestamp(after_value, field="statusTimestampAfter")
        if after_value is not None
        else None
    )
    service = get_trigger_service(request)
    try:
        runs = await service.runs(
            trigger_id=trigger_id,
            secret=_secret(request),
            kind=TriggerKind.A2A,
        )
    except TriggerAuthenticationError as error:
        raise _authentication_error() from error
    filtered = [
        run
        for run in runs
        if (context_id is None or run.session_id == context_id)
        and (state is None or _state(run.status) == state)
        and (after is None or run.updated_at.astimezone(UTC) >= after)
    ]
    total_size = len(filtered)
    page_token = request.query_params.get("pageToken")
    if page_token:
        cursor_key = _decode_cursor(trigger_id, page_token)
        filtered = [
            run for run in filtered if (run.updated_at.astimezone(UTC), run.run_id) < cursor_key
        ]
    page = filtered[:page_size]
    next_page_token = _cursor(trigger_id, page[-1]) if len(filtered) > page_size else ""
    return JSONResponse(
        {
            "tasks": [
                await _task_value(
                    request,
                    trigger_id,
                    run,
                    include_artifacts=include_artifacts,
                    history_length=history_length if history_length is not None else 0,
                )
                for run in page
            ],
            "nextPageToken": next_page_token,
            "pageSize": page_size,
            "totalSize": total_size,
        },
        media_type=_A2A_MEDIA_TYPE,
    )


@router.get("/tasks/{run_id}")
async def get_task(trigger_id: str, run_id: str, request: Request) -> JSONResponse:
    _version(request)
    history_length = _query_int(request, "historyLength", minimum=0)
    run = await _authorized_run(
        get_trigger_service(request),
        request,
        trigger_id=trigger_id,
        run_id=run_id,
    )
    return JSONResponse(
        {
            "task": await _task_value(
                request,
                trigger_id,
                run,
                history_length=history_length,
            )
        },
        media_type=_A2A_MEDIA_TYPE,
    )


@router.post("/tasks/{run_id}:cancel")
async def cancel_task(trigger_id: str, run_id: str, request: Request) -> JSONResponse:
    _version(request)
    run = await _authorized_run(
        get_trigger_service(request),
        request,
        trigger_id=trigger_id,
        run_id=run_id,
    )
    if run.status.is_terminal:
        raise _protocol_error(
            "TASK_NOT_CANCELABLE",
            f"Task is already {run.status.value}: {run_id}",
        )
    try:
        cancelled = await request.app.state.container.runs.cancel(
            run.tenant_id,
            run.run_id,
        )
    except ConflictError as error:
        raise _domain_conflict(error) from error
    return JSONResponse(
        {"task": await _task_value(request, trigger_id, cancelled)},
        media_type=_A2A_MEDIA_TYPE,
    )


@router.post("/tasks/{run_id}:subscribe")
async def subscribe_task(
    trigger_id: str,
    run_id: str,
    request: Request,
) -> StreamingResponse:
    _version(request)
    run = await _authorized_run(
        get_trigger_service(request),
        request,
        trigger_id=trigger_id,
        run_id=run_id,
    )
    if run.status.is_terminal:
        raise _protocol_error(
            "UNSUPPORTED_OPERATION",
            f"Terminal task cannot be subscribed: {run_id}",
        )
    last_event_id = request.headers.get("Last-Event-ID")
    sequence = 0
    if last_event_id is not None:
        if not last_event_id.isdigit():
            raise _invalid("Last-Event-ID must be a durable event sequence")
        sequence = int(last_event_id)
    return StreamingResponse(
        _stream_events(request, trigger_id, run, sequence=sequence),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks/{run_id}/artifacts/{artifact_id}/content")
async def download_artifact(
    trigger_id: str,
    run_id: str,
    artifact_id: str,
    request: Request,
) -> Response:
    _version(request, optional=True)
    run = await _authorized_run(
        get_trigger_service(request),
        request,
        trigger_id=trigger_id,
        run_id=run_id,
    )
    try:
        artifact = await request.app.state.container.artifacts.get(
            run.tenant_id,
            artifact_id,
        )
    except NotFoundError as error:
        raise _protocol_error(
            "TASK_NOT_FOUND",
            f"Artifact is not available: {artifact_id}",
        ) from error
    if artifact.run_id != run.run_id or artifact.status is not ArtifactStatus.READY:
        raise _protocol_error(
            "TASK_NOT_FOUND",
            f"Artifact is not available: {artifact_id}",
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
