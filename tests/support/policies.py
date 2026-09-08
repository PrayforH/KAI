"""Explicit policy profiles for tests that exercise the approval lifecycle."""

from harness.policy.models import PolicyDecision, PolicyRule
from harness.policy.profiles import PolicyProfileRegistry, read_only_policy_rules
from harness.policy.rules import PolicyEngine, default_policy_rules


def fake_runtime_review_profiles() -> PolicyProfileRegistry:
    """Require review only for the FakeRuntime's deterministic Bash request."""

    review = PolicyEngine(
        [
            *default_policy_rules(),
            PolicyRule(
                name="fake-runtime-reviewed-command",
                tool="Bash",
                command_contains="printf 'reviewed operation'",
                decision=PolicyDecision.ASK,
                priority=1_000,
            ),
        ]
    )
    return PolicyProfileRegistry(
        {
            "production-read-only": PolicyEngine(read_only_policy_rules()),
            "production-standard": review,
            "production-orchestrator": review,
            "local-standard": review,
        }
    )
