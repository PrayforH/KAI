"""Deterministic matching and precedence for policy rules."""

from fnmatch import fnmatch

from harness.policy.models import (
    ContextTrust,
    PolicyContext,
    PolicyDecision,
    PolicyResult,
    PolicyRule,
)
from harness.sandbox.base import SandboxIsolation

_DECISION_PRECEDENCE = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.ASK: 1,
    PolicyDecision.DENY: 2,
}


def _path(context: PolicyContext) -> str:
    value = context.arguments.get("file_path", context.arguments.get("path", ""))
    return str(value)


def _matches(rule: PolicyRule, context: PolicyContext) -> bool:
    if rule.tenant_id is not None and rule.tenant_id != context.tenant_id:
        return False
    if rule.agent_name is not None and rule.agent_name != context.agent_name:
        return False
    if rule.tool is not None and not fnmatch(context.tool_name, rule.tool):
        return False
    if (
        rule.sandbox_isolation is not None
        and rule.sandbox_isolation is not context.sandbox_isolation
    ):
        return False
    if (
        rule.context_trust is not None
        and rule.context_trust is not context.context_trust
    ):
        return False
    if rule.path_glob is not None and not fnmatch(_path(context), rule.path_glob):
        return False
    if rule.command_contains is not None:
        command = str(context.arguments.get("command", ""))
        if rule.command_contains not in command:
            return False
    return True


def _specificity(rule: PolicyRule) -> int:
    return sum(
        field is not None
        for field in (
            rule.tenant_id,
            rule.agent_name,
            rule.tool,
            rule.path_glob,
            rule.command_contains,
            rule.sandbox_isolation,
            rule.context_trust,
        )
    )


class PolicyEngine:
    def __init__(self, rules: list[PolicyRule]) -> None:
        self._rules = tuple(rules)

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        matches = [rule for rule in self._rules if _matches(rule, context)]
        if not matches:
            return PolicyResult(
                decision=PolicyDecision.DENY,
                rule_name="implicit-deny",
                reason="no policy rule matched",
            )
        selected = max(
            matches,
            key=lambda rule: (
                rule.priority,
                _specificity(rule),
                _DECISION_PRECEDENCE[rule.decision],
                rule.name,
            ),
        )
        return PolicyResult(
            decision=selected.decision,
            rule_name=selected.name,
            reason=f"matched policy rule {selected.name}",
        )

    @property
    def rules(self) -> tuple[PolicyRule, ...]:
        return self._rules


def knowledge_search_read_rules() -> list[PolicyRule]:
    """Allow the reviewed read-only knowledge tools under legacy and current names."""

    tools = (
        "sag_search",
        "sag_explain_search",
        "sag_get_event",
        "sag_get_document",
        "sag_list_chunks",
    )
    return [
        PolicyRule(
            name=f"{server_name}-{tool_name}",
            tool=f"mcp__{server_name}__{tool_name}",
            decision=PolicyDecision.ALLOW,
        )
        for server_name in ("novel-search", "knowledge-search")
        for tool_name in tools
    ]


def sentiment_query_read_rules() -> list[PolicyRule]:
    """Allow only the reviewed read-only public-opinion MCP surface."""

    return [
        PolicyRule(
            name=f"sentiment-query-{tool_name}",
            tool=f"mcp__sentiment_query_mcp__{tool_name}",
            decision=PolicyDecision.ALLOW,
        )
        for tool_name in (
            "get_risk_dashboard",
            "search_risk_subjects",
            "get_risk_subject",
            "query_legal_entity_directory",
            "search_risk_opinions",
            "get_risk_opinion",
            "search_risk_events",
            "get_risk_event",
        )
    ]


def default_policy_rules() -> list[PolicyRule]:
    return [
        PolicyRule(
            name="tool-directory-search",
            tool="ToolSearch",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(
            name="mcp-directory-search",
            tool="MCPSearch",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(name="web-search", tool="WebSearch", decision=PolicyDecision.ALLOW),
        PolicyRule(name="web-fetch", tool="WebFetch", decision=PolicyDecision.ALLOW),
        PolicyRule(name="read", tool="Read", decision=PolicyDecision.ALLOW),
        PolicyRule(name="glob", tool="Glob", decision=PolicyDecision.ALLOW),
        PolicyRule(name="grep", tool="Grep", decision=PolicyDecision.ALLOW),
        PolicyRule(name="delegate", tool="Task", decision=PolicyDecision.ALLOW),
        PolicyRule(name="delegate-agent", tool="Agent", decision=PolicyDecision.ALLOW),
        PolicyRule(
            name="memory-search",
            tool="mcp__harness-memory__search_memory",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(
            name="memory-read",
            tool="mcp__harness-memory__read_memory",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(
            name="untrusted-memory-deny",
            tool="mcp__harness-memory__propose_memory",
            context_trust=ContextTrust.UNTRUSTED,
            decision=PolicyDecision.DENY,
            priority=100,
        ),
        PolicyRule(
            name="sensitive-memory-review",
            tool="mcp__harness-memory__propose_memory",
            context_trust=ContextTrust.SENSITIVE,
            decision=PolicyDecision.ASK,
            priority=100,
        ),
        PolicyRule(
            name="harness-memory-update",
            tool="mcp__harness-memory__propose_memory",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(
            name="harness-artifact-publish",
            tool="mcp__harness-artifacts__publish_artifact",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(
            name="tavily-search",
            tool="mcp__tavily__tavily_search",
            decision=PolicyDecision.ALLOW,
        ),
        PolicyRule(
            name="tavily-extract",
            tool="mcp__tavily__tavily_extract",
            decision=PolicyDecision.ALLOW,
        ),
        *knowledge_search_read_rules(),
        *sentiment_query_read_rules(),
        PolicyRule(name="workspace-write", tool="Write", decision=PolicyDecision.ALLOW),
        PolicyRule(name="workspace-edit", tool="Edit", decision=PolicyDecision.ALLOW),
        PolicyRule(
            name="destructive-rm",
            tool="Bash",
            command_contains="rm ",
            sandbox_isolation=SandboxIsolation.WORKSPACE,
            decision=PolicyDecision.DENY,
        ),
        PolicyRule(
            name="sandbox-bash",
            tool="Bash",
            decision=PolicyDecision.ALLOW,
        ),
    ]
