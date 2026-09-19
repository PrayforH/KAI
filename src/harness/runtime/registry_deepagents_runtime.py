"""Resolve a published AgentVersion before delegating to the DeepAgents runtime.

Everything a DeepAgents graph needs is decided here, so ``DeepagentsRuntime``
stays a pure consumer of one immutable snapshot:

* the **model route** is resolved against the control plane and its wire
  protocol is read back, because DeepAgents speaks both Anthropic- and
  OpenAI-compatible protocols and a route's model name is only an alias;
* the **tool ceiling** is the published tool directory, so a Bundle operator or
  MCP tool that was not reviewed at publish time cannot appear at run time;
* **MCP registrations** are narrowed to streamable HTTP, the only transport the
  DeepAgents catalog entry declares;
* **Studio Bundle operators** are staged here, because ``ToolResolver`` refuses
  to resolve a manifest whose ``bundle:`` entries are not staged, and their
  canonical names only exist once the published directory has been applied.

The runtime emits ``model.route.selected`` itself, through its stream mapper,
so this wrapper does not duplicate that fact.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from claude_agent_sdk import SdkMcpTool

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.context.service import ContextService
from harness.core.errors import ConflictError
from harness.core.manifest import (
    AgentManifestSnapshot,
    materialize_python_tool_snapshot_set,
)
from harness.core.ports import AgentRegistry
from harness.deployments.boundaries import (
    enforce_runtime_environment,
    enforce_runtime_model_route,
)
from harness.observability.provider import Observability
from harness.policy.profiles import PolicyProfileRegistry
from harness.policy.rules import PolicyEngine
from harness.quota.service import QuotaService
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.runtime.deepagents_runtime import (
    BundleOperator,
    DeepagentsRuntime,
    DeepagentsRuntimeConfig,
)
from harness.runtime.sandbox_tools import create_bundle_python_tool
from harness.runtime.tools import (
    ResolvedTools,
    ToolResolutionError,
    ToolResolver,
    enforce_published_tool_directory,
)
from harness.studio.model_configuration import ModelConfigurationService

# How the platform spells a Studio Bundle operator everywhere: the compiler's
# tool directory, the runtime tool gate and the quota ledger all key on it.
_BUNDLE_TOOL_PREFIX = "mcp__harness-python-"
# The manifest tool kind the compiler emits for a Bundle operator.
_BUNDLE_PYTHON_ENTRY_PREFIX = "bundle:"
_MCP_HTTP_TRANSPORT = "http"
# ``sdk`` is the in-process MCP server the resolver builds from staged Bundle
# operators; DeepAgents serves those natively, so it is not an MCP connection.
_MCP_SDK_TRANSPORT = "sdk"


def _reject_unservable_python_tools(snapshot: AgentManifestSnapshot) -> None:
    """Refuse a manifest whose Python tools DeepAgents cannot run.

    DeepAgents has no in-process tool plane: a Bundle operator runs in the
    Sandbox, and anything else would have to run in the Worker. Failing here is
    the only way to avoid silently dropping a capability the Agent declared.
    """

    unsupported = sorted(
        tool.python_entry
        for tool in snapshot.manifest.spec.tools
        if tool.python_entry is not None
        and not tool.python_entry.startswith(_BUNDLE_PYTHON_ENTRY_PREFIX)
    )
    if unsupported:
        raise ToolResolutionError(
            "DeepAgents executes Studio Bundle operators in the Sandbox and has no "
            f"in-process Python tool plane: {', '.join(unsupported)}"
        )


def _bundle_operators(
    snapshot: AgentManifestSnapshot,
    resolved: ResolvedTools,
    *,
    materialized: Mapping[str, Path],
) -> tuple[BundleOperator, ...]:
    """Pair each declared operator with its canonical name and source file.

    The name is read back from the enforced tool directory rather than rebuilt
    from a prefix, so the runtime cannot drift from the vocabulary the policy
    engine and the quota ledger use.
    """

    published = [name for name in resolved.allowed_tools if name.startswith(_BUNDLE_TOOL_PREFIX)]
    operators: list[BundleOperator] = []
    for tool in snapshot.python_tool_snapshots:
        name = next(
            (candidate for candidate in published if candidate.endswith(f"__{tool.name}")),
            None,
        )
        if name is None:
            raise ToolResolutionError(
                f"Studio Bundle operator is missing from the published tool directory: {tool.name}"
            )
        path = materialized.get(tool.reference)
        if path is None:
            raise ToolResolutionError(
                f"Studio Bundle operator was not materialized: {tool.reference}"
            )
        operators.append(BundleOperator(tool=tool, name=name, path=path))
    return tuple(operators)


@dataclass(frozen=True)
class _StagedBundleOperators:
    """The two views of one staging pass over a snapshot's Bundle operators.

    ``overrides`` is what ``ToolResolver`` needs before it will accept a manifest
    carrying ``bundle:`` entries; ``materialized`` is what the runtime needs to
    read each operator's source back. Deriving them together keeps the two from
    drifting, and keeps the workspace write to a single pass.
    """

    overrides: dict[str, SdkMcpTool[Any]]
    materialized: Mapping[str, Path]


def _stage_bundle_operators(
    snapshot: AgentManifestSnapshot,
    context: RuntimeContext,
) -> _StagedBundleOperators:
    """Stage every published Bundle operator so ``ToolResolver`` accepts the manifest.

    A Bundle operator exists to run inside the isolated Sandbox, so a missing
    executor is a hard failure here rather than a reason to fall back to running
    user code in the platform process. A snapshot that publishes no operator
    stages nothing, which also keeps a plain manifest from paying for a
    workspace write it does not need.
    """

    if not snapshot.python_tool_snapshots:
        return _StagedBundleOperators(overrides={}, materialized={})
    executor = context.sandbox_command_executor
    if executor is None:
        raise ToolResolutionError(
            "Studio Bundle operators require isolated Sandbox execution"
        )
    materialized = materialize_python_tool_snapshot_set((snapshot,), context.workspace).get(
        snapshot.content_hash, {}
    )
    overrides = {
        tool.reference: create_bundle_python_tool(
            snapshot=tool,
            materialized_path=materialized.get(tool.reference),
            executor=executor,
        )
        for tool in snapshot.python_tool_snapshots
    }
    return _StagedBundleOperators(overrides=overrides, materialized=materialized)


def _streamable_http_servers(resolved: ResolvedTools) -> dict[str, dict[str, object]]:
    """Narrow reviewed MCP registrations to the transport DeepAgents supports."""

    connections: dict[str, dict[str, object]] = {}
    for server_name, raw_config in resolved.mcp_servers.items():
        config = dict(cast(Mapping[str, object], raw_config))
        transport = config.get("type")
        if transport == _MCP_SDK_TRANSPORT:
            # The staged Bundle operators, served natively as StructuredTools.
            continue
        if transport != _MCP_HTTP_TRANSPORT:
            raise ToolResolutionError(
                "DeepAgents MCP requires a streamable HTTP registration: "
                f"{server_name} declares {transport or 'no transport'}"
            )
        url = config.get("url")
        if not isinstance(url, str) or not url:
            raise ToolResolutionError(f"DeepAgents MCP endpoint is invalid: {server_name}")
        connections[server_name] = config
    return connections


class RegistryDeepagentsRuntime:
    """Build a per-Agent DeepAgents runtime from the immutable published snapshot."""

    def __init__(
        self,
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
    ) -> None:
        # DeepAgents is a new kernel with no legacy deployment to support, so the
        # control plane is required rather than optional: a runtime that fell
        # back to a static route could silently run the wrong protocol.
        self._registry = registry
        self._model_configurations = model_configurations
        self._approvals = approvals
        self._events = events
        self._quotas = quotas
        self._context_service = context_service
        self._observability = observability
        self._tool_resolver = tool_resolver or ToolResolver()
        self._policy = policy
        self._policy_profiles = policy_profiles

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        session = context.session
        version = await self._registry.get(
            session.tenant_id,
            session.resolved_agent_owner_user_id,
            session.agent_name,
            session.agent_version,
        )
        snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
        if snapshot.manifest.spec.runtime != "deepagents":
            raise ConflictError("Agent manifest is not configured for DeepAgents")
        if session.runtime_type != "deepagents":
            raise ConflictError("Session runtime does not match the Agent manifest")
        enforce_runtime_environment(session, snapshot)

        model_spec = snapshot.manifest.spec.model
        raw_override = context.run.input.get("model_route_override")
        route_override = raw_override if isinstance(raw_override, str) else None
        route_id = route_override or model_spec.route
        # No required_api_format: the catalog advertises both text protocols, so
        # the route decides which one this Run speaks.
        selected_config = await self._model_configurations.resolve_runtime(
            session.tenant_id,
            session.agent_name,
            route_id,
            apply_agent_binding=route_override is None,
        )
        if selected_config is None or selected_config.route_id is None:
            raise ConflictError(
                f"task model route is unavailable in the control plane: {route_id}"
            )
        route_id = selected_config.route_id
        if selected_config.api_format is None:
            raise ConflictError(f"model route does not declare a wire protocol: {route_id}")
        enforce_runtime_model_route(session, route_id)

        assert context.identity is not None
        _reject_unservable_python_tools(snapshot)
        staged = _stage_bundle_operators(snapshot, context)
        resolved = enforce_published_tool_directory(
            snapshot,
            await self._tool_resolver.resolve(
                snapshot.manifest,
                context.identity,
                python_tool_overrides=staged.overrides,
                tolerate_unavailable_mcp=True,
            ),
        )
        runtime = DeepagentsRuntime(
            config=DeepagentsRuntimeConfig(
                snapshot=snapshot,
                route_id=route_id,
                provider=selected_config.provider,
                api_format=selected_config.api_format,
                model=selected_config.model,
                base_url=selected_config.base_url,
                api_key=selected_config.credential,
                mcp_servers=_streamable_http_servers(resolved),
                declared_tools=frozenset(resolved.allowed_tools),
                bundle_operators=_bundle_operators(
                    snapshot, resolved, materialized=staged.materialized
                ),
            ),
            approvals=self._approvals,
            events=self._events,
            quotas=self._quotas,
            context_service=self._context_service,
            observability=self._observability,
            policy=self._policy,
            policy_profiles=self._policy_profiles,
        )
        async for event in runtime.execute(context):
            yield event
