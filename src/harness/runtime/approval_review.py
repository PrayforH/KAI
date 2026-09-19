"""How a pending tool call is presented to the human who must approve it.

The reviewer sees a redacted summary rather than raw arguments, and a coarse
risk label rather than the policy rule that produced it. Both are runtime
neutral: the Claude SDK hook bridge and the DeepAgents middleware ask the same
question ("what does this call look like to a person?") and must answer it
identically, or the same call would be reviewed differently depending on which
kernel happened to run it.
"""

from __future__ import annotations

from typing import Any, cast

from harness.runtime.audit_redaction import redact_text

_APPROVAL_ARGUMENT_KEYS = (
    "command",
    "file_path",
    "path",
    "query",
    "url",
    "urls",
    "description",
    "subagent_type",
    "pattern",
    "glob",
)

_APPROVAL_ARGUMENT_LIST_LIMIT = 5
_APPROVAL_ARGUMENT_TEXT_LIMIT = 200


def approval_argument_summary(arguments: dict[str, Any]) -> dict[str, Any]:
    """Reduce tool arguments to the redacted subset worth showing a reviewer."""

    summary: dict[str, Any] = {}
    for key in _APPROVAL_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            summary[key] = redact_text(value)
        elif isinstance(value, list):
            values = cast(list[object], value)
            if all(isinstance(item, str) for item in values):
                summary[key] = [
                    redact_text(item, limit=_APPROVAL_ARGUMENT_TEXT_LIMIT)
                    for item in values[:_APPROVAL_ARGUMENT_LIST_LIMIT]
                    if isinstance(item, str)
                ]
    return summary


def approval_risk(tool_name: str) -> str:
    """Label a tool call by blast radius, in the platform's builtin vocabulary."""

    if tool_name == "Bash":
        return "high"
    if tool_name in {"Write", "Edit"}:
        return "medium"
    return "low"
