"""AG-UI agent endpoint and replay stream backed by Harness repositories."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Literal, cast

from ag_ui.core import (
    BaseEvent,
    RunAgentInput,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from harness.agui.activity import build_run_activity
from harness.agui.mapper import map_harness_event
from harness.api.dependencies import (
    ApiContainer,
    Identity,
    ensure_permission,
    get_container,
    require_identity,
    require_owned_run,
)
from harness.api.event_streaming import wait_for_run_event
from harness.context.models import SessionContextDigest, SessionContextOverview
from harness.context.window import context_window_view
from harness.core.errors import ConflictError
from harness.core.events import RunEvent
from harness.core.models import AguiThreadBinding, ApprovalRequest, ApprovalStatus, Run
from harness.runtime.input_redaction import redact_internal_agent_asset_events
from harness.runtime.message_mapper import safe_model_text

router = APIRouter(prefix="/agui", tags=["ag-ui"])

_TERMINAL_EVENT_TYPES = {
    "run.cancelled",
    "run.failed",
    "run.rejected",
    "run.succeeded",
    "run.timed_out",
}

_RESPONSE_BOUNDARY_PREFIXES = ("approval.", "subagent.", "tool.")
_STREAM_HEARTBEAT_SECONDS = 10.0


def _projected_event_cursor(last_event_id: str | None) -> tuple[int, int]:
    """Return the durable sequence and projected child count already received.

    One durable Harness event can project to several AG-UI events whose IDs are
    ``sequence:index``. Resuming after ``12:1`` must replay child 2 from durable
    event 12 instead of skipping the whole durable event.
    """

    raw = (last_event_id or "0").strip()
    sequence_text, separator, child_text = raw.partition(":")
    try:
        sequence = max(0, int(sequence_text))
        child_count = max(0, int(child_text)) if separator else 0
    except ValueError:
        return 0, 0
    return sequence, child_count


def final_response_text(events: list[RunEvent]) -> str:
    """Return only the answer emitted after the last auditable action.

    Providers stream progress commentary and final prose through the same
    message.delta channel. Activity renders the former in the execution
    timeline; history must not concatenate it into the final answer again.
    """

    last_action_index = -1
    for index, event in enumerate(events):
        if event.type.startswith(_RESPONSE_BOUNDARY_PREFIXES):
            last_action_index = index
    return "".join(
        safe_model_text(str(event.payload.get("text", "")))
        for index, event in enumerate(events)
        if index > last_action_index and event.type == "message.delta"
    )


def active_response_text(events: list[RunEvent]) -> str:
    """Restore only the current answer candidate for an active run.

    Progress commentary that was followed by a tool belongs in Activity, not
    in the response slot.  When a user returns from Studio during that tool
    pause there may therefore be no response text yet.  Once the provider
    starts emitting after the latest auditable action, restore only that newest
    message so the response grows in place without replaying earlier progress.
    """

    last_action_index = max(
        (
            index
            for index, event in enumerate(events)
            if event.type.startswith(_RESPONSE_BOUNDARY_PREFIXES)
        ),
        default=-1,
    )

    latest_message_id = next(
        (
            str(event.payload.get("message_id", "")).strip()
            for index, event in reversed(list(enumerate(events)))
            if index > last_action_index
            and event.type == "message.delta"
            and str(event.payload.get("message_id", "")).strip()
        ),
        "",
    )
    if latest_message_id:
        return "".join(
            safe_model_text(str(event.payload.get("text", "")))
            for index, event in enumerate(events)
            if index > last_action_index
            and event.type == "message.delta"
            and str(event.payload.get("message_id", "")).strip()
            == latest_message_id
        )

    latest_start_index = max(
        (
            index
            for index, event in enumerate(events)
            if index > last_action_index and event.type == "message.start"
        ),
        default=last_action_index + 1,
    )
    return "".join(
        safe_model_text(str(event.payload.get("text", "")))
        for index, event in enumerate(events)
        if index >= latest_start_index
        and index > last_action_index
        and event.type == "message.delta"
    )


def _stream_response_message_id(run_id: str) -> str:
    """Return the stable assistant message that owns one Harness Run."""

    return f"assistant-{run_id}"


def _response_message_id(
    run_id: str,
    run_events: list[RunEvent],
) -> str:
    """Return the latest provider text part for terminal deliverables."""

    for item in reversed(run_events):
        if item.type not in {"message.start", "message.delta", "message.completed"}:
            continue
        message_id = str(item.payload.get("message_id", "")).strip()
        if message_id:
            return message_id
    return _stream_response_message_id(run_id)


def _terminal_artifact_projection(
    event: RunEvent,
    run_events: list[RunEvent],
) -> list[BaseEvent]:
    """Attach generated files after the already-streamed provider response."""

    message_id = _response_message_id(event.run_id, run_events)
    projection: list[BaseEvent] = []
    for artifact_event in (item for item in run_events if item.type == "artifact.ready"):
        payload = dict(artifact_event.payload)
        payload["message_id"] = message_id
        projection.extend(
            map_harness_event(
                artifact_event.model_copy(update={"payload": payload}),
                project_response_text=False,
                project_activity=False,
            )
        )
    return projection


def project_stream_event(event: RunEvent, run_events: list[RunEvent]) -> list[BaseEvent]:
    """Keep one durable turn while preserving provider text-part boundaries."""

    message_id = _stream_response_message_id(event.run_id)
    projected = list(
        map_harness_event(
            event,
            project_artifact=False,
        )
    )
    if event.type == "run.queued":
        # Keep one stable assistant message across provider commentary, tools,
        # and final prose. History reconstructs the same ID, so a later turn
        # cannot orphan or hide the previous response.
        return [
            *projected[:1],
            TextMessageStartEvent(message_id=message_id, role="assistant"),
            *projected[1:],
        ]

    if event.type in {"run.failed", "run.rejected", "run.timed_out"}:
        # Provider text has already reached the browser. Generated artifacts
        # still belong before the terminal error when post-processing fails.
        return [
            *projected[:-1],
            TextMessageEndEvent(message_id=message_id),
            *_terminal_artifact_projection(event, run_events),
            projected[-1],
        ]

    if event.type == "run.cancelled":
        return [
            *projected,
            TextMessageEndEvent(message_id=message_id),
        ]

    if event.type != "run.succeeded":
        return projected

    # Activity marks the Run complete first; the standard RUN_FINISHED remains
    # the terminal frame after the response and every generated file card.
    return [
        *projected[:-1],
        TextMessageEndEvent(message_id=message_id),
        *_terminal_artifact_projection(event, run_events),
        projected[-1],
    ]


class AguiThreadSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: str
    session_id: str
    title: str
    agent_name: str
    agent_version: str
    agent_owner_user_id: str
    space_id: str | None = None
    status: str
    run_id: str | None = None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
    pending_approval: ApprovalRequest | None = None


class AguiThreadArchiveInput(BaseModel):
    archived: bool


class AguiThreadArchiveResult(BaseModel):
    thread_id: str
    archived: bool
    archived_at: datetime | None = None


class AguiContextRebaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: str
    previous_session_id: str
    session_id: str
    digest: SessionContextDigest


class AguiThreadContextOverview(SessionContextOverview):
    previous_session_count: int = Field(default=0, ge=0)
    rebase_supported: bool = False
    rollback_supported: bool = False


class AguiHistoryFunction(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    arguments: str


class AguiHistoryToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: Literal["function"] = "function"
    function: AguiHistoryFunction


class AguiHistoryTextPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["text"] = "text"
    text: str


class AguiHistoryInputSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["url"] = "url"
    value: str
    mime_type: str = Field(serialization_alias="mimeType")


class AguiHistoryInputMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    filename: str


class AguiHistoryInputPart(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["image", "audio", "video", "document"]
    source: AguiHistoryInputSource
    metadata: AguiHistoryInputMetadata


class AguiHistoryMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    role: str
    content: str | list[AguiHistoryTextPart | AguiHistoryInputPart]
    tool_calls: list[AguiHistoryToolCall] | None = Field(
        default=None, serialization_alias="toolCalls"
    )
    tool_call_id: str | None = Field(default=None, serialization_alias="toolCallId")


class AguiThreadHistory(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: str
    status: str
    run_id: str | None = None
    messages: list[AguiHistoryMessage]


@router.get(
    "/threads/{thread_id}/context",
    response_model=AguiThreadContextOverview,
)
async def get_agui_thread_context(
    thread_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    before_version: Annotated[int | None, Query(ge=2)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> AguiThreadContextOverview:
    ensure_permission(identity, "tasks:read")
    binding = await container.agui.get_binding(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
    )
    overview = await container.context.overview(
        identity.tenant_id,
        identity.user_id,
        binding.session_id,
        before_version=before_version,
        limit=limit,
    )
    session = await container.sessions.get(identity.tenant_id, binding.session_id)
    window_event = await container.events.latest_for_session_types(
        identity.tenant_id,
        binding.session_id,
        ("context.window.observed", "context.window.unavailable"),
    )
    window, window_status = context_window_view(window_event)
    return AguiThreadContextOverview.model_validate(
        {
            **overview.model_dump(),
            "previous_session_count": len(binding.previous_session_ids),
            "rebase_supported": (
                session.resolved_runtime_thread_id is not None
                and overview.state is not None
                and overview.state.latest_digest_id is not None
            ),
            "rollback_supported": (
                binding.session_id.startswith("session_ctx_") and bool(binding.previous_session_ids)
            ),
            "window": window,
            "window_status": window_status,
        }
    )


@router.post(
    "/threads/{thread_id}/context/rebase",
    response_model=AguiContextRebaseResult,
)
async def rebase_agui_thread_context(
    thread_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AguiContextRebaseResult:
    ensure_permission(identity, "tasks:write")
    result = await container.agui.rebase_context(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
    )
    await container.audit.record(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        action="task.context.rebase",
        resource_type="agui_thread",
        resource_id=thread_id,
        details={
            "previous_session_id": result.previous_session_id,
            "session_id": result.session_id,
            "digest_id": result.digest.digest_id,
        },
    )
    return AguiContextRebaseResult(**result.__dict__)


@router.post(
    "/threads/{thread_id}/context/rebase/rollback",
    response_model=AguiContextRebaseResult,
)
async def rollback_agui_thread_context_rebase(
    thread_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AguiContextRebaseResult:
    ensure_permission(identity, "tasks:write")
    result = await container.agui.rollback_context_rebase(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
    )
    await container.audit.record(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        action="task.context.rebase.rollback",
        resource_type="agui_thread",
        resource_id=thread_id,
        details={
            "previous_session_id": result.previous_session_id,
            "session_id": result.session_id,
            "digest_id": result.digest.digest_id,
        },
    )
    return AguiContextRebaseResult(**result.__dict__)


def _encode_event(event_id: str, event: object) -> str:
    data = json.dumps(
        event.model_dump(mode="json", by_alias=True, exclude_none=True),  # type: ignore[attr-defined]
        separators=(",", ":"),
    )
    return f"id: {event_id}\ndata: {data}\n\n"


@router.post("")
async def run_agui_agent(
    body: RunAgentInput,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    agent_name: Annotated[str, Query()],
    agent_version: Annotated[str, Query()],
    agent_owner_user_id: Annotated[str | None, Query()] = None,
    space_id: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    ensure_permission(identity, "tasks:write")
    resolved_owner = agent_owner_user_id or identity.user_id
    connection_mode = "caller_owned"
    if space_id is not None:
        release = await container.team_spaces.require_agent_access(
            identity.tenant_id,
            identity.user_id,
            space_id,
            resolved_owner,
            agent_name,
            agent_version,
        )
        connection_mode = release.connection_mode.value
    elif resolved_owner != identity.user_id:
        raise ConflictError("agent_owner_user_id requires a team space grant")
    creation = await container.agui.create_run_with_result(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        agent_name=agent_name,
        agent_version=agent_version,
        request=body,
        agent_owner_user_id=resolved_owner,
        space_id=space_id,
        connection_mode=connection_mode,
    )
    run = creation.run
    worker_task = (
        asyncio.create_task(container.worker.execute(identity.tenant_id, run.run_id))
        if container.auto_execute and not creation.reused
        else None
    )

    async def stream() -> AsyncIterator[str]:
        sequence = 0
        terminal_event_seen = False
        run_events: list[RunEvent] = []
        protected_tool_call_ids: set[str] = set()
        last_emission = time.monotonic()
        while True:
            events = await container.observed_events.list_after(
                identity.tenant_id, run.run_id, sequence
            )
            events = redact_internal_agent_asset_events(
                events,
                protected_tool_call_ids=protected_tool_call_ids,
            )
            for event in events:
                run_events.append(event)
                projected = project_stream_event(event, run_events)
                for index, item in enumerate(projected):
                    if isinstance(item, (RunStartedEvent, RunFinishedEvent)):
                        item = item.model_copy(
                            update={"thread_id": body.thread_id, "run_id": body.run_id}
                        )
                    event_id = (
                        str(event.sequence)
                        if len(projected) == 1
                        else f"{event.sequence}:{index + 1}"
                    )
                    yield _encode_event(event_id, item)
                    last_emission = time.monotonic()
                sequence = event.sequence
                if event.type in _TERMINAL_EVENT_TYPES:
                    terminal_event_seen = True
            if terminal_event_seen:
                break
            if time.monotonic() - last_emission >= _STREAM_HEARTBEAT_SECONDS:
                # Keep reverse proxies and browser transports from treating a
                # long model/tool turn as an abandoned response.
                yield ": keep-alive\n\n"
                last_emission = time.monotonic()
            await wait_for_run_event(
                container.event_wakeup,
                identity.tenant_id,
                run.run_id,
                sequence,
                fallback_poll_seconds=0.02,
            )
        if worker_task is not None:
            await worker_task

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "X-Harness-Run-ID": run.run_id,
        "X-Harness-Canonical-Client-Run-ID": creation.canonical_client_run_id,
    }
    if creation.reused:
        headers["X-Harness-Run-Reused"] = "true"
    if creation.deduplicated:
        headers["X-Harness-Run-Deduplicated"] = "true"
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/threads", response_model=list[AguiThreadSummary])
async def list_agui_threads(
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    archived: Annotated[bool, Query()] = False,
) -> list[AguiThreadSummary]:
    ensure_permission(identity, "tasks:read")
    bindings = await container.agui.list_bindings(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        limit=limit,
        archived=archived,
    )
    all_session_ids = [session_id for binding in bindings for session_id in binding.session_ids]
    resolved_sessions, runs = await asyncio.gather(
        container.sessions.list_for_ids(
            identity.tenant_id,
            [binding.session_id for binding in bindings],
        ),
        container.runs.list_for_sessions(
            identity.tenant_id,
            all_session_ids,
            limit=max(limit * 20, 200),
        ),
    )
    sessions = {session.session_id: session for session in resolved_sessions}
    runs_by_session: dict[str, list[Run]] = {}
    for run in runs:
        runs_by_session.setdefault(run.session_id, []).append(run)
    approvals = await container.approvals.list_for_runs(
        identity.tenant_id, [run.run_id for run in runs]
    )
    now = datetime.now(UTC)
    pending_by_run = {
        approval.run_id: approval
        for approval in approvals
        if approval.status is ApprovalStatus.PENDING and approval.expires_at > now
    }

    async def summarize(binding: AguiThreadBinding) -> AguiThreadSummary:
        thread_runs = [
            run for session_id in binding.session_ids for run in runs_by_session.get(session_id, [])
        ]
        visible_runs = _visible_thread_runs(thread_runs)
        latest = max(
            visible_runs,
            key=lambda item: (item.updated_at, item.run_id),
            default=None,
        )
        pending = next(
            (
                pending_by_run[item.run_id]
                for item in sorted(
                    visible_runs,
                    key=lambda value: (value.updated_at, value.run_id),
                    reverse=True,
                )
                if item.run_id in pending_by_run
            ),
            None,
        )
        session = sessions[binding.session_id]
        prompts = _conversation_prompts(visible_runs)
        return AguiThreadSummary(
            thread_id=binding.thread_id,
            session_id=binding.session_id,
            title=await container.agui.resolve_title(binding, prompts),
            agent_name=session.agent_name,
            agent_version=session.agent_version,
            agent_owner_user_id=session.resolved_agent_owner_user_id,
            space_id=session.team_ids[0] if session.team_ids else None,
            status=(
                "waiting_approval"
                if pending is not None
                else latest.status.value
                if latest
                else "idle"
            ),
            run_id=pending.run_id if pending is not None else latest.run_id if latest else None,
            created_at=binding.created_at,
            updated_at=latest.updated_at if latest is not None else binding.updated_at,
            archived_at=binding.archived_at,
            pending_approval=pending,
        )

    summaries = await asyncio.gather(*(summarize(binding) for binding in bindings))
    return sorted(
        summaries,
        key=lambda item: (
            item.pending_approval is not None,
            item.updated_at,
            item.thread_id,
        ),
        reverse=True,
    )[:limit]


@router.patch(
    "/threads/{thread_id}",
    response_model=AguiThreadArchiveResult,
)
async def update_agui_thread(
    thread_id: str,
    body: AguiThreadArchiveInput,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AguiThreadArchiveResult:
    ensure_permission(identity, "tasks:write")
    binding = await container.agui.get_binding(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
    )
    if body.archived:
        runs = await container.runs.list_for_sessions(
            identity.tenant_id, list(binding.session_ids), limit=200
        )
        active = next(
            (run for run in _visible_thread_runs(runs) if not run.status.is_terminal),
            None,
        )
        if active is not None:
            raise ConflictError("Active tasks cannot be archived")
    updated = await container.agui.set_archived(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
        archived=body.archived,
    )
    return AguiThreadArchiveResult(
        thread_id=thread_id,
        archived=updated.archived_at is not None,
        archived_at=updated.archived_at,
    )


@router.get(
    "/threads/{thread_id}/history",
    response_model=AguiThreadHistory,
    response_model_by_alias=True,
    response_model_exclude_none=True,
)
async def get_agui_thread_history(
    thread_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> AguiThreadHistory:
    ensure_permission(identity, "tasks:read")
    binding = await container.agui.get_binding(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
    )
    runs = await container.runs.list_for_sessions(
        identity.tenant_id, list(binding.session_ids), limit=200
    )
    runs = _visible_thread_runs(runs)
    latest = max(
        runs,
        key=lambda item: (item.updated_at, item.run_id),
        default=None,
    )
    messages: list[AguiHistoryMessage] = []
    for run in sorted(runs, key=lambda item: (item.created_at, item.run_id)):
        prompt = run.input.get("prompt")
        if isinstance(prompt, str) and prompt:
            raw_input_ids = run.input.get("input_artifact_ids", [])
            input_ids = (
                [
                    item
                    for item in cast(list[object], raw_input_ids)
                    if isinstance(item, str) and item
                ]
                if isinstance(raw_input_ids, list)
                else []
            )
            input_artifacts = (
                await container.input_artifacts.resolve_for_run(
                    tenant_id=identity.tenant_id,
                    user_id=identity.user_id,
                    input_artifact_ids=input_ids,
                )
                if input_ids
                else []
            )
            content: str | list[AguiHistoryTextPart | AguiHistoryInputPart]
            if input_artifacts:
                content = [
                    AguiHistoryTextPart(text=prompt),
                    *(
                        AguiHistoryInputPart(
                            type=_history_input_type(artifact.media_type),
                            source=AguiHistoryInputSource(
                                value=artifact.input_artifact_id,
                                mime_type=artifact.media_type,
                            ),
                            metadata=AguiHistoryInputMetadata(filename=artifact.name),
                        )
                        for artifact in input_artifacts
                    ),
                ]
            else:
                content = prompt
            messages.append(
                AguiHistoryMessage(id=f"user-{run.run_id}", role="user", content=content)
            )
        events = await container.observed_events.list_after(identity.tenant_id, run.run_id, 0)
        events = redact_internal_agent_asset_events(events)
        response = (
            final_response_text(events)
            if run.status.is_terminal
            else active_response_text(events)
        )
        artifacts = await container.artifacts.list_for_run(identity.tenant_id, run.run_id)
        activity = build_run_activity(events)
        activity_tool_call = (
            AguiHistoryToolCall(
                id=f"harness-activity-{run.run_id}",
                function=AguiHistoryFunction(
                    name="harness_run_activity",
                    arguments=json.dumps({"activity": activity}, separators=(",", ":")),
                ),
            )
            if activity is not None
            else None
        )
        artifact_tool_calls = [
            AguiHistoryToolCall(
                id=f"harness-artifact-{artifact.artifact_id}",
                function=AguiHistoryFunction(
                    name="harness_present_artifact",
                    arguments=json.dumps(
                        {
                            "artifact_id": artifact.artifact_id,
                            "run_id": artifact.run_id,
                            "name": artifact.name,
                            "media_type": artifact.media_type,
                            "size_bytes": artifact.size_bytes,
                            "sha256": artifact.sha256,
                            "status": artifact.status.value,
                        },
                        separators=(",", ":"),
                    ),
                ),
            )
            for artifact in artifacts
        ]
        tool_calls = [
            *([activity_tool_call] if activity_tool_call is not None else []),
            *artifact_tool_calls,
        ]
        if response or tool_calls:
            messages.append(
                AguiHistoryMessage(
                    id=f"assistant-{run.run_id}",
                    role="assistant",
                    content=response,
                    tool_calls=tool_calls or None,
                )
            )
        if activity_tool_call is not None:
            messages.append(
                AguiHistoryMessage(
                    id=f"tool-activity-{run.run_id}",
                    role="tool",
                    content=json.dumps({"status": "ready"}, separators=(",", ":")),
                    tool_call_id=activity_tool_call.id,
                )
            )
        messages.extend(
            AguiHistoryMessage(
                id=f"tool-artifact-{artifact.artifact_id}",
                role="tool",
                content=json.dumps({"status": "ready"}, separators=(",", ":")),
                tool_call_id=f"harness-artifact-{artifact.artifact_id}",
            )
            for artifact in artifacts
        )
    return AguiThreadHistory(
        thread_id=thread_id,
        status=latest.status.value if latest is not None else "idle",
        run_id=latest.run_id if latest is not None else None,
        messages=messages,
    )


def _history_input_type(
    media_type: str,
) -> Literal["image", "audio", "video", "document"]:
    if media_type.startswith("image/"):
        return "image"
    if media_type.startswith("audio/"):
        return "audio"
    if media_type.startswith("video/"):
        return "video"
    return "document"


def _conversation_prompts(runs: list[Run]) -> list[str]:
    if not runs:
        return []
    latest = max(runs, key=lambda item: (item.created_at, item.run_id))
    stored = latest.input.get("conversation_prompts")
    stored_items = cast(list[object], stored) if isinstance(stored, list) else []
    if stored_items and all(isinstance(item, str) for item in stored_items):
        return [item for item in cast(list[str], stored_items) if item.strip()]
    return [
        prompt
        for item in sorted(runs, key=lambda value: (value.created_at, value.run_id))
        if isinstance((prompt := item.input.get("prompt")), str) and prompt.strip()
    ]


def _visible_thread_runs(runs: list[Run]) -> list[Run]:
    ordered = sorted(runs, key=lambda item: (item.created_at, item.run_id))
    if not ordered:
        return []
    latest = ordered[-1]
    stored = latest.input.get("conversation_prompts")
    stored_items = cast(list[object], stored) if isinstance(stored, list) else []
    if not (
        stored_items
        and all(isinstance(item, str) for item in stored_items)
        and latest.input.get("prompt") == stored_items[-1]
    ):
        return ordered

    prompts = [item for item in cast(list[str], stored_items) if item.strip()]
    selected: list[Run] = []
    cursor = 0
    for prompt in prompts[:-1]:
        match_index = next(
            (
                index
                for index in range(cursor, len(ordered) - 1)
                if ordered[index].input.get("prompt") == prompt
            ),
            None,
        )
        if match_index is None:
            return ordered
        selected.append(ordered[match_index])
        cursor = match_index + 1
    selected.append(latest)
    return selected


@router.post(
    "/threads/{thread_id}/runs/{client_run_id}/cancel",
    response_model=Run,
)
async def cancel_agui_run(
    thread_id: str,
    client_run_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Run:
    ensure_permission(identity, "tasks:write")
    return await container.agui.cancel_run(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        thread_id=thread_id,
        client_run_id=client_run_id,
    )


@router.get("/runs/{run_id}/events")
async def stream_agui_events(
    run_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    ensure_permission(identity, "tasks:read")
    run = await require_owned_run(container, identity, run_id)
    client_thread_id, client_run_id = await container.agui.client_coordinates_for_run(
        tenant_id=identity.tenant_id,
        user_id=identity.user_id,
        run=run,
    )
    sequence, delivered_children = _projected_event_cursor(last_event_id)
    events = await container.observed_events.list_after(
        identity.tenant_id,
        run_id,
        sequence - 1 if delivered_children else sequence,
    )
    events = redact_internal_agent_asset_events(events)
    all_events = await container.observed_events.list_after(
        identity.tenant_id,
        run_id,
        0,
    )
    all_events = redact_internal_agent_asset_events(all_events)

    async def stream() -> AsyncIterator[str]:
        for event in events:
            projected = project_stream_event(event, all_events)
            first_child = delivered_children if event.sequence == sequence else 0
            for index, item in enumerate(projected[first_child:], start=first_child):
                if isinstance(item, (RunStartedEvent, RunFinishedEvent)):
                    item = item.model_copy(
                        update={
                            "thread_id": client_thread_id,
                            "run_id": client_run_id,
                        }
                    )
                event_id = (
                    str(event.sequence) if len(projected) == 1 else f"{event.sequence}:{index + 1}"
                )
                yield _encode_event(event_id, item)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/runs/{run_id}/cancel", response_model=Run)
async def cancel_agui_run_by_server_id(
    run_id: str,
    identity: Annotated[Identity, Depends(require_identity)],
    container: Annotated[ApiContainer, Depends(get_container)],
) -> Run:
    """Cancel a resumed run when the browser no longer has its AG-UI client ID."""

    ensure_permission(identity, "tasks:write")
    await require_owned_run(container, identity, run_id)
    return await container.runs.cancel(identity.tenant_id, run_id)
