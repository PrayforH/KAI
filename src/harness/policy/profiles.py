"""Server-owned permission policy profiles selected by Agent Manifests."""

from collections.abc import Mapping

from harness.policy.models import PolicyDecision, PolicyRule
from harness.policy.rules import (
    PolicyEngine,
    default_policy_rules,
    knowledge_search_read_rules,
)


class UnknownPolicyProfileError(ValueError):
    """Raised when a Manifest references a profile the server did not register."""


class PolicyProfileRegistry:
    def __init__(self, profiles: Mapping[str, PolicyEngine]) -> None:
        self._profiles = dict(profiles)
        if not self._profiles:
            raise ValueError("at least one permission policy profile is required")

    def resolve(self, profile_id: str) -> PolicyEngine:
        try:
            return self._profiles[profile_id]
        except KeyError as error:
            raise UnknownPolicyProfileError(
                f"unknown permission policy profile: {profile_id}"
            ) from error

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._profiles))


def read_only_policy_rules() -> list[PolicyRule]:
    """Evidence tools plus controlled artifact publication, with implicit deny."""

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
    ]


def default_policy_profiles() -> PolicyProfileRegistry:
    read_only = PolicyEngine(read_only_policy_rules())
    standard = PolicyEngine(default_policy_rules())
    orchestrator = PolicyEngine(default_policy_rules())
    return PolicyProfileRegistry(
        {
            "production-read-only": read_only,
            "production-standard": standard,
            "production-orchestrator": orchestrator,
            "local-standard": standard,
        }
    )
