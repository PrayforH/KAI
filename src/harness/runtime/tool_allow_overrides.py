"""The narrow allow overrides both runtimes' tool gates apply.

A published Agent may declare MCP tools and Bundle Python tools that the selected
policy profile does not enumerate, and a sandboxed Bash call may be provably low
risk. Each case upgrades a pending review or an *implicit* deny into an allow.

Two properties keep this safe, and both are the reason the rules live in one
place instead of one copy per runtime:

* An operator's explicit rule always wins, so every override requires the
  ``implicit-deny`` rule name or ``PolicyDecision.ASK``. A DENY that came from a
  named profile rule is never rewritten.
* The order below is the only place the overrides are decided, so the Claude SDK
  hook and the DeepAgents middleware cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from harness.policy.bash_safety import sandboxed_bash_is_low_risk
from harness.policy.models import PolicyDecision, PolicyResult

IMPLICIT_DENY_RULE = "implicit-deny"
PUBLISHED_MCP_TOOL_RULE = "published-mcp-tool"
BUNDLE_PYTHON_TOOL_RULE = "declared-sandbox-python-tool"
LOW_RISK_BASH_RULE = "sandbox-low-risk-bash"
# Bundle Python tools are published as MCP tools under this namespace, which is
# why they need their own override: the generic MCP rule excludes them on
# purpose, so only a declared tool in an isolated Sandbox qualifies.
BUNDLE_PYTHON_MCP_PREFIX = "mcp__harness-python-"


def apply_allow_overrides(
    result: PolicyResult,
    *,
    raw_tool_name: str,
    tool_name: str,
    arguments: dict[str, Any],
    declared_tools: Collection[str],
    sandbox_command_executor: object | None,
    workspace: str,
    remote_workspace: str | None,
    generated_python_files: Collection[str] = (),
) -> PolicyResult:
    """Return the first override that matches, or ``result`` unchanged."""

    if (
        tool_name == "Bash"
        and result.decision is PolicyDecision.ASK
        and sandboxed_bash_is_low_risk(
            str(arguments.get("command", "")),
            workspace=workspace,
            remote_workspace=remote_workspace,
            generated_python_files=generated_python_files,
        )
    ):
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            rule_name=LOW_RISK_BASH_RULE,
            reason="matched sandbox low-risk Bash policy",
        )
    if result.decision is not PolicyDecision.DENY or result.rule_name != IMPLICIT_DENY_RULE:
        return result
    if (
        raw_tool_name.startswith("mcp__")
        and not raw_tool_name.startswith(BUNDLE_PYTHON_MCP_PREFIX)
        and (raw_tool_name in declared_tools or tool_name in declared_tools)
    ):
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            rule_name=PUBLISHED_MCP_TOOL_RULE,
            reason="matched MCP tool declared by the published Agent tool directory",
        )
    if (
        raw_tool_name.startswith(BUNDLE_PYTHON_MCP_PREFIX)
        and raw_tool_name in declared_tools
        and sandbox_command_executor is not None
    ):
        return PolicyResult(
            decision=PolicyDecision.ALLOW,
            rule_name=BUNDLE_PYTHON_TOOL_RULE,
            reason="matched declared Bundle Python tool in isolated Sandbox",
        )
    return result
