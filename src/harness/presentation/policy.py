"""A UI action can narrow the Run policy; it cannot grant a tool capability."""

from harness.policy.models import PolicyContext, PolicyDecision, PolicyResult
from harness.policy.profiles import read_only_policy_rules
from harness.policy.rules import PolicyEngine


class ReadOnlyActionPolicy(PolicyEngine):
    def __init__(self, policy: PolicyEngine) -> None:
        super().__init__(list(policy.rules))
        self._boundary = PolicyEngine(read_only_policy_rules())

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        original = super().evaluate(context)
        boundary = self._boundary.evaluate(context)
        if original.decision is PolicyDecision.DENY:
            return original
        if boundary.decision is PolicyDecision.DENY:
            return boundary
        return original


def action_policy(policy: PolicyEngine, kind: object) -> PolicyEngine:
    return (
        ReadOnlyActionPolicy(policy)
        if isinstance(kind, str) and kind in {"read", "conversation"}
        else policy
    )
