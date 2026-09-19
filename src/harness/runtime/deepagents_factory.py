"""Compose the DeepAgents kernel, which ships as an optional extra.

``deepagents`` pulls in LangGraph and LangChain, so it is not a base dependency:
``uv sync --group dev`` and every single-runtime deployment must keep working
without it. Importing it at module scope would make ``composition.py`` itself
unimportable, so the import is deferred to this factory — the one place that
knows how to say what is missing.

A ``multi`` deployment, by contrast, *does* register every runtime in
``INSTALLED_AGENT_RUNTIMES``; letting it compose without the kernel would
advertise a runtime it cannot run. It therefore fails here, at composition time,
rather than on the first DeepAgents Run.
"""

from __future__ import annotations

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.context.service import ContextService
from harness.core.ports import AgentRegistry
from harness.observability.provider import Observability
from harness.policy.profiles import PolicyProfileRegistry
from harness.policy.rules import PolicyEngine
from harness.quota.service import QuotaService
from harness.runtime.base import AgentRuntime
from harness.runtime.tools import ToolResolver
from harness.studio.model_configuration import ModelConfigurationService


def build_deepagents_runtime(
    *,
    registry: AgentRegistry,
    model_configurations: ModelConfigurationService,
    approvals: ApprovalService,
    events: EventService,
    quotas: QuotaService | None = None,
    context_service: ContextService | None = None,
    observability: Observability | None = None,
    tool_resolver: ToolResolver | None = None,
    policy: PolicyEngine | None = None,
    policy_profiles: PolicyProfileRegistry | None = None,
) -> AgentRuntime:
    """Build the DeepAgents registry wrapper, or explain why it is unavailable.

    Exactly one policy source is required, for the same reason the tool gate
    requires it: every DeepAgents tool call is authorized against the policy of
    the Run being served. A registry is the more useful of the two, because it
    can resolve whichever policy a published snapshot names.
    """

    if (policy is None) == (policy_profiles is None):
        raise ValueError(
            "build_deepagents_runtime needs exactly one of policy or policy_profiles"
        )
    try:
        from harness.runtime.registry_deepagents_runtime import RegistryDeepagentsRuntime
    except ImportError as error:
        raise ValueError(
            "the DeepAgents Agent runtime is an optional extra: install it with "
            "`uv sync --group dev --extra deepagents`, or run this deployment "
            "without HARNESS_RUNTIME=multi"
        ) from error
    return RegistryDeepagentsRuntime(
        registry=registry,
        model_configurations=model_configurations,
        approvals=approvals,
        events=events,
        quotas=quotas,
        context_service=context_service,
        observability=observability,
        tool_resolver=tool_resolver,
        policy=policy,
        policy_profiles=policy_profiles,
    )
