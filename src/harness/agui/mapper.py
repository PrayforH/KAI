"""Project durable Harness events into standard AG-UI events."""

import json
from collections.abc import Sequence

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from harness.agui.activity import activity_projection
from harness.core.events import RunEvent
from harness.runtime.message_mapper import safe_model_text


def _custom(event: RunEvent, name: str) -> Sequence[BaseEvent]:
    return [CustomEvent(name=name, value=event.payload)]


def _domain_tool(
    event: RunEvent,
    *,
    name: str,
    tool_call_id: str,
    fields: tuple[str, ...],
    complete: bool = False,
) -> Sequence[BaseEvent]:
    arguments = {key: event.payload[key] for key in fields if key in event.payload}
    arguments["run_id"] = event.run_id
    projected: list[BaseEvent] = [
        ToolCallStartEvent(
            tool_call_id=tool_call_id,
            tool_call_name=name,
            parent_message_id=str(
                event.payload.get("message_id", f"assistant-{event.run_id}")
            ),
        ),
        ToolCallArgsEvent(
            tool_call_id=tool_call_id,
            delta=json.dumps(arguments, separators=(",", ":")),
        ),
        ToolCallEndEvent(tool_call_id=tool_call_id),
    ]
    if complete:
        projected.append(
            ToolCallResultEvent(
                message_id=f"tool-result-{event.event_id}",
                tool_call_id=tool_call_id,
                content=json.dumps({"status": "ready"}, separators=(",", ":")),
                role="tool",
            )
        )
    return projected


def _map_standard_event(event: RunEvent) -> Sequence[BaseEvent]:
    message_id = str(event.payload.get("message_id", f"assistant-{event.run_id}"))
    if event.type == "run.queued":
        return [RunStartedEvent(thread_id=event.session_id, run_id=event.run_id)]
    if event.type == "run.succeeded":
        return [RunFinishedEvent(thread_id=event.session_id, run_id=event.run_id)]
    if event.type in {"run.failed", "run.rejected", "run.timed_out"}:
        return [
            RunErrorEvent(
                message=str(event.payload.get("message", event.type)),
                code=str(event.payload.get("error_code", event.type.removeprefix("run."))),
            )
        ]
    if event.type == "message.start":
        return [TextMessageStartEvent(message_id=message_id, role="assistant")]
    if event.type == "message.delta":
        text = safe_model_text(str(event.payload.get("text", "")))
        return [] if not text else [TextMessageContentEvent(message_id=message_id, delta=text)]
    if event.type == "message.completed":
        return [TextMessageEndEvent(message_id=message_id)]
    if event.type == "tool.request":
        tool_call_id = str(event.payload.get("tool_call_id", ""))
        return [
            ToolCallStartEvent(
                tool_call_id=tool_call_id,
                tool_call_name=str(event.payload.get("name", "")),
                parent_message_id=message_id,
            ),
            ToolCallArgsEvent(
                tool_call_id=tool_call_id,
                delta=json.dumps(event.payload.get("arguments", {}), separators=(",", ":")),
            ),
            ToolCallEndEvent(tool_call_id=tool_call_id),
        ]
    if event.type == "tool.result":
        return [
            ToolCallResultEvent(
                message_id=f"tool-result-{event.sequence}",
                tool_call_id=str(event.payload.get("tool_call_id", "")),
                content=json.dumps(event.payload, separators=(",", ":")),
                role="tool",
            )
        ]
    if event.type == "approval.requested":
        approval_id = str(event.payload.get("approval_id", event.event_id))
        return _domain_tool(
            event,
            name="harness_request_approval",
            tool_call_id=f"harness-approval-{approval_id}",
            fields=(
                "approval_id",
                "tool_call_id",
                "reason",
                "expires_at",
                "status",
                "tool_name",
                "argument_summary",
                "sandbox_provider",
                "sandbox_isolation",
                "policy_rule",
                "risk",
            ),
        )
    if event.type in {
        "approval.approved", "approval.rejected", "approval.expired", "approval.cancelled",
    }:
        approval_id = str(event.payload.get("approval_id", event.event_id))
        return [
            ToolCallResultEvent(
                message_id=f"tool-result-{event.event_id}",
                tool_call_id=f"harness-approval-{approval_id}",
                content=json.dumps(
                    {"decision": event.type.removeprefix("approval.")},
                    separators=(",", ":"),
                ),
                role="tool",
            )
        ]
    if event.type.startswith("subagent."):
        return _custom(event, "harness.subagent.v1")
    if event.type == "artifact.ready":
        artifact_id = str(event.payload.get("artifact_id", event.event_id))
        return _domain_tool(
            event,
            name="harness_present_artifact",
            tool_call_id=f"harness-artifact-{artifact_id}",
            fields=(
                "artifact_id",
                "name",
                "media_type",
                "size_bytes",
                "sha256",
                "status",
            ),
            complete=True,
        )
    if event.type.startswith("artifact.") or event.type == "workspace.archived":
        return _custom(event, "harness.artifact.v1")
    if event.type in {"runtime.result", "model.route.selected"}:
        return _custom(event, "harness.runtime.v1")
    if event.type.startswith("run."):
        return [
            StateSnapshotEvent(
                snapshot={
                    "runId": event.run_id,
                    "threadId": event.session_id,
                    "status": event.type.removeprefix("run."),
                    "sequence": event.sequence,
                }
            )
        ]
    return []


def map_harness_event(
    event: RunEvent,
    *,
    project_response_text: bool = True,
    project_artifact: bool = True,
    project_activity: bool = True,
) -> Sequence[BaseEvent]:
    """Project one durable event with explicit channel controls.

    Live AG-UI streams preserve provider text-part boundaries beneath one
    durable assistant turn while deferring artifact cards until the terminal
    projection. The defaults preserve the contract used by other consumers.
    """

    suppress_standard = (
        not project_response_text
        and event.type in {"message.start", "message.delta", "message.completed"}
    ) or (not project_artifact and event.type == "artifact.ready")
    standard = [] if suppress_standard else list(_map_standard_event(event))
    activity = activity_projection(event) if project_activity else []
    if event.type in {
        "run.succeeded",
        "run.failed",
        "run.rejected",
        "run.timed_out",
    }:
        return [*activity, *standard]
    return [*standard, *activity]
