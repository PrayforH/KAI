"""Resolve a published AgentVersion before delegating to Claude Agent SDK."""

from collections.abc import AsyncIterator, Callable, Sequence

from harness.application.agent_assets import resolve_published_agent_versions
from harness.application.memory import UserMemoryService
from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import ModelRoute, Session
from harness.core.ports import AgentRegistry
from harness.deployments.boundaries import (
    enforce_runtime_environment,
    enforce_runtime_model_route,
)
from harness.execution.credentials import (
    CredentialBroker,
    CredentialLease,
    CredentialResourceKind,
)
from harness.knowledge.service import KnowledgeService
from harness.knowledge.workload import RemoteKnowledgeMcpProvider
from harness.memory_bank.service import MemoryBankService
from harness.memory_bank.workload import RemoteMemoryMcpProvider
from harness.observability.provider import Observability
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.runtime.cc_switch import CcSwitchClaudeConfig
from harness.runtime.claude_sdk import ClaudeSdkRuntime, QueryFactory
from harness.runtime.mcp_credentials import DynamicMcpCredentialProvider
from harness.runtime.sdk_tool_gate import ToolGate
from harness.runtime.tools import ToolResolver
from harness.studio.model_configuration import ModelConfigurationService


class RegistryClaudeRuntime:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        config: CcSwitchClaudeConfig | None = None,
        fallback_config: CcSwitchClaudeConfig | None = None,
        route_configs: Sequence[CcSwitchClaudeConfig] = (),
        query_factory: QueryFactory | None = None,
        tool_resolver: ToolResolver | None = None,
        mcp_credential_provider: DynamicMcpCredentialProvider | None = None,
        tool_gate: ToolGate | None = None,
        memory_service: UserMemoryService | None = None,
        memory_bank: MemoryBankService | None = None,
        remote_memory_mcp: RemoteMemoryMcpProvider | None = None,
        knowledge: KnowledgeService | None = None,
        remote_knowledge_mcp: RemoteKnowledgeMcpProvider | None = None,
        session_store_factory: Callable[[Session], object] | None = None,
        observability: Observability | None = None,
        credential_broker: CredentialBroker | None = None,
        model_configurations: ModelConfigurationService | None = None,
    ) -> None:
        if config is None and model_configurations is None:
            raise ValueError(
                "RegistryClaudeRuntime requires a static model or the model control plane"
            )
        self._registry = registry
        self._config = config
        self._fallback_config = fallback_config
        ordered_configs = (
            *((config,) if config is not None else ()),
            *(
                item
                for item in route_configs
                if config is None or item.route_id != config.route_id
            ),
            *(
                (fallback_config,)
                if fallback_config is not None
                and (config is None or fallback_config.route_id != config.route_id)
                else ()
            ),
        )
        self._route_configs = tuple({item.route_id: item for item in ordered_configs}.values())
        self._query_factory = query_factory
        self._tool_resolver = tool_resolver or ToolResolver(
            credential_provider=mcp_credential_provider
        )
        self._tool_gate = tool_gate
        self._memory_service = memory_service
        self._memory_bank = memory_bank
        self._remote_memory_mcp = remote_memory_mcp
        self._knowledge = knowledge
        self._remote_knowledge_mcp = remote_knowledge_mcp
        self._session_store_factory = session_store_factory
        self._observability = observability
        self._credential_broker = credential_broker
        self._model_configurations = model_configurations

    def _config_for_route(
        self,
        route_id: str,
        *,
        legacy_fallback: bool = False,
        strict: bool = False,
    ) -> CcSwitchClaudeConfig:
        candidates = self._route_configs
        exact = next(
            (config for config in candidates if config.route_id == route_id),
            None,
        )
        if exact is not None:
            return exact
        if (
            legacy_fallback
            and self._fallback_config is not None
            and self._fallback_config.route_id is None
        ):
            return self._fallback_config
        if strict:
            raise ConflictError(f"task model route is not configured: {route_id}")
        if self._config is None:
            raise ConflictError(f"task model route is not configured: {route_id}")
        return self._config

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        session = context.session
        agent_version, subagent_versions = await resolve_published_agent_versions(
            self._registry,
            tenant_id=session.tenant_id,
            owner_user_id=session.resolved_agent_owner_user_id,
            agent_name=session.agent_name,
            agent_version=session.agent_version,
            allow_validated_graph=session.environment == "preview",
        )
        snapshot = AgentManifestSnapshot.model_validate(agent_version.snapshot)
        enforce_runtime_environment(session, snapshot)
        configured_route = snapshot.manifest.spec.model.route
        raw_override = context.run.input.get("model_route_override")
        route_override = raw_override if isinstance(raw_override, str) else None
        route_id = route_override or configured_route
        dynamic_config: CcSwitchClaudeConfig | None = None
        if self._model_configurations is not None:
            dynamic_config = await self._model_configurations.resolve_runtime(
                session.tenant_id,
                session.agent_name,
                route_id,
                apply_agent_binding=route_override is None,
            )
            if dynamic_config is None:
                raise ConflictError(
                    f"task model route is unavailable in the control plane: {route_id}"
                )
            selected_config = dynamic_config
            assert selected_config.route_id is not None
            route_id = selected_config.route_id
        else:
            selected_config = self._config_for_route(
                route_id,
                strict=route_override is not None,
            )
        enforce_runtime_model_route(session, route_id)
        route = ModelRoute(
            route_id=route_id,
            provider=selected_config.provider,
            base_url=selected_config.base_url,
            # The manifest selects a logical route. The deployment binding is
            # authoritative for the provider model so the same published Agent
            # can run against a locally mapped or production model.
            model=selected_config.model,
            compatibility=selected_config.compatibility,
            capabilities=selected_config.capabilities,
            auth_scheme=selected_config.resolved_auth_scheme,
        )
        routes = [route]
        issued_leases: list[CredentialLease] = []
        if dynamic_config is not None or self._credential_broker is None:
            route_secret = selected_config.credential.get_secret_value()
        else:
            assert context.identity is not None
            lease = await self._credential_broker.issue(
                identity=context.identity,
                resource_kind=CredentialResourceKind.MODEL,
                resource_reference=route_id,
                required_keys=frozenset({"api_key"}),
            )
            issued_leases.append(lease)
            values = await self._credential_broker.resolve(lease.lease_id, context.identity)
            route_secret = values["api_key"].get_secret_value()
        route_secrets = {route_id: route_secret}
        fallback_route_id = (
            None if route_override is not None else snapshot.manifest.spec.model.fallback_route
        )
        selected_fallback: CcSwitchClaudeConfig | None = None
        if fallback_route_id is not None:
            if self._model_configurations is not None:
                selected_fallback = await self._model_configurations.resolve_runtime(
                    session.tenant_id,
                    session.agent_name,
                    fallback_route_id,
                    apply_agent_binding=False,
                )
            else:
                try:
                    selected_fallback = self._config_for_route(
                        fallback_route_id,
                        legacy_fallback=True,
                        strict=True,
                    )
                except ConflictError:
                    pass
        if selected_fallback is not None:
            assert fallback_route_id is not None
            fallback_route = ModelRoute(
                route_id=fallback_route_id,
                provider=selected_fallback.provider,
                base_url=selected_fallback.base_url,
                model=selected_fallback.model,
                compatibility=selected_fallback.compatibility,
                capabilities=selected_fallback.capabilities,
                auth_scheme=selected_fallback.resolved_auth_scheme,
            )
            routes.append(fallback_route)
            if self._model_configurations is not None or self._credential_broker is None:
                fallback_secret = selected_fallback.credential.get_secret_value()
            else:
                assert context.identity is not None
                fallback_lease = await self._credential_broker.issue(
                    identity=context.identity,
                    resource_kind=CredentialResourceKind.MODEL,
                    resource_reference=fallback_route_id,
                    required_keys=frozenset({"api_key"}),
                )
                issued_leases.append(fallback_lease)
                fallback_values = await self._credential_broker.resolve(
                    fallback_lease.lease_id, context.identity
                )
                fallback_secret = fallback_values["api_key"].get_secret_value()
            route_secrets[fallback_route_id] = fallback_secret
        if self._query_factory is None:
            runtime = ClaudeSdkRuntime(
                agent_version=agent_version,
                routes=routes,
                route_secrets=route_secrets,
                subagent_versions=subagent_versions,
                tool_resolver=self._tool_resolver,
                tool_gate=self._tool_gate,
                memory_service=self._memory_service,
                memory_bank=self._memory_bank,
                remote_memory_mcp=self._remote_memory_mcp,
                knowledge=self._knowledge,
                remote_knowledge_mcp=self._remote_knowledge_mcp,
                observability=self._observability,
                session_store=(
                    self._session_store_factory(session)
                    if self._session_store_factory is not None
                    else None
                ),
            )
        else:
            runtime = ClaudeSdkRuntime(
                agent_version=agent_version,
                routes=routes,
                route_secrets=route_secrets,
                subagent_versions=subagent_versions,
                query_factory=self._query_factory,
                tool_resolver=self._tool_resolver,
                tool_gate=self._tool_gate,
                memory_service=self._memory_service,
                memory_bank=self._memory_bank,
                remote_memory_mcp=self._remote_memory_mcp,
                knowledge=self._knowledge,
                remote_knowledge_mcp=self._remote_knowledge_mcp,
                observability=self._observability,
                session_store=(
                    self._session_store_factory(session)
                    if self._session_store_factory is not None
                    else None
                ),
            )
        for lease in issued_leases:
            yield RuntimeEvent(
                type="credential.lease.issued",
                payload=lease.audit_record(),
            )
        effective_context = context
        if route_override is None and route_id != configured_route:
            effective_context = context.model_copy(
                update={
                    "run": context.run.model_copy(
                        update={
                            "input": {
                                **context.run.input,
                                "model_route_override": route_id,
                            }
                        }
                    )
                }
            )
        async for event in runtime.execute(effective_context):
            yield event
