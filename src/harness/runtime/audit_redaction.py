"""Redact tool audit copies without changing arguments used for execution."""

import re
from typing import Any, cast

_AUTHORIZATION_TEXT = re.compile(r"(?i)\b(authorization\s*[:=]\s*)([^'\"\n]+)")
_BEARER_TEXT = re.compile(r"(?i)\b(bearer\s+)([^\s'\"&]+)")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|api[_-]?key|secret|password)(\s*(?:[:=]\s*|\s+))([^\s'\"&]+)"
)
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "headers",
    "password",
    "secret",
    "token",
)
_CONTENT_FIELDS_BY_TOOL = {
    "Write": {"content"},
    "Edit": {"old_string", "new_string"},
    "mcp__harness-memory__update_user_memory": {"content"},
    "mcp__harness-memory__propose_memory": {"content"},
}


def redact_text(value: str, *, limit: int = 500) -> str:
    redacted = _AUTHORIZATION_TEXT.sub(r"\1[REDACTED]", value)
    redacted = _BEARER_TEXT.sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
    return redacted if len(redacted) <= limit else f"{redacted[: limit - 1]}…"


def _redact_value(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value, limit=2_000)
    if isinstance(value, dict):
        mapping = cast(dict[object, Any], value)
        return {
            str(child_key): _redact_value(child, key=str(child_key))
            for child_key, child in mapping.items()
        }
    if isinstance(value, list):
        sequence = cast(list[Any], value)
        return [_redact_value(child) for child in sequence]
    return value


def redact_tool_arguments(
    tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    redacted = cast(dict[str, Any], _redact_value(arguments))
    for field in _CONTENT_FIELDS_BY_TOOL.get(tool_name, set()):
        if field in redacted:
            redacted[field] = "[REDACTED]"
    return redacted
