"""Shared redaction rules for browser inputs and internal Agent assets."""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from harness.core.events import RunEvent

INPUT_CONTENT_REDACTION = "[Input file content omitted from durable events]"
STAGED_INPUT_READ_MARKER = "staged_input_read"
INTERNAL_AGENT_ASSET_REDACTION = "[Internal Skill or prompt content hidden]"
INTERNAL_AGENT_ASSET_MARKER = "internal_agent_asset_access"


def internal_agent_asset_access(payload: dict[str, Any]) -> bool:
    """Return true when a tool reads non-user-facing Agent instructions."""
    name = str(payload.get("name", "")).casefold()
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        return bool(payload.get(INTERNAL_AGENT_ASSET_MARKER))
    values = cast(dict[str, Any], arguments)
    if name == "read":
        raw = values.get("file_path", values.get("path"))
        candidates = (raw,) if isinstance(raw, str) else ()
    elif name == "bash":
        command = values.get("command")
        if not isinstance(command, str) or not any(
            token in command.casefold()
            for token in ("cat ", "sed ", "head ", "tail ", "grep ", "rg ", "awk ")
        ):
            return bool(payload.get(INTERNAL_AGENT_ASSET_MARKER))
        candidates = (command,)
    else:
        return bool(payload.get(INTERNAL_AGENT_ASSET_MARKER))
    for candidate in candidates:
        normalized = candidate.replace("\\", "/").casefold()
        if any(
            marker in normalized
            for marker in (
                "/.claude/skills/",
                ".claude/skills/",
                "/.claude/agents/",
                ".claude/agents/",
                "/.harness-runtime/",
                ".harness-runtime/",
                "/prompts/system.md",
                "prompts/system.md",
                "/systemprompt.md",
                "systemprompt.md",
                "/system-prompt.md",
                "system-prompt.md",
                "/system_prompt.md",
                "system_prompt.md",
                "/claude.md",
                "claude.md",
            )
        ):
            return True
    return bool(payload.get(INTERNAL_AGENT_ASSET_MARKER))


def redact_internal_agent_asset_events(
    events: Sequence[RunEvent],
    *,
    protected_tool_call_ids: set[str] | None = None,
) -> list[RunEvent]:
    """Hide internal instruction bodies, including during historical replay."""
    protected: set[str] = protected_tool_call_ids if protected_tool_call_ids is not None else set()
    projected: list[RunEvent] = []
    for event in events:
        payload = event.payload
        tool_call_id = str(payload.get("tool_call_id", ""))
        if event.type == "tool.request" and tool_call_id and internal_agent_asset_access(payload):
            protected.add(tool_call_id)
        if event.type == "tool.result" and tool_call_id in protected:
            payload = {
                **payload,
                "content": INTERNAL_AGENT_ASSET_REDACTION,
                "redacted": True,
                "redaction_reason": "internal_agent_asset",
            }
            event = event.model_copy(update={"payload": payload})
        projected.append(event)
    return projected


def staged_input_paths(workspace: Path, input_files: tuple[str, ...]) -> dict[Path, str]:
    return {(workspace / relative_path).resolve(): relative_path for relative_path in input_files}


def staged_read_path(
    payload: dict[str, Any],
    *,
    workspace: Path,
    staged_paths: dict[Path, str],
) -> str | None:
    """Return the safe relative path when a Read targets a staged input exactly."""
    if str(payload.get("name", "")).casefold() != "read":
        return None
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        return None
    typed_arguments = cast(dict[str, Any], arguments)
    raw_path = typed_arguments.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        return staged_paths.get(candidate.resolve())
    except (OSError, RuntimeError):
        return None


def redact_workspace_paths(value: Any, workspace: Path) -> Any:
    """Replace ephemeral sandbox roots without changing the event shape."""
    workspace_path = str(workspace.resolve())
    if isinstance(value, str):
        return value.replace(workspace_path, "/workspace")
    if isinstance(value, list):
        return [redact_workspace_paths(item, workspace) for item in cast(list[Any], value)]
    if isinstance(value, dict):
        return {
            key: redact_workspace_paths(item, workspace)
            for key, item in cast(dict[str, Any], value).items()
        }
    return value
