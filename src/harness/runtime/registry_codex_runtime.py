"""Resolve a published AgentVersion before delegating to Codex app-server."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from harness.memory_bank.workload import RemoteMemoryMcpProvider
from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentRuntimeType
from harness.core.ports import AgentRegistry
from harness.deployments.boundaries import (
    enforce_runtime_environment,
    enforce_runtime_model_route,
)
from harness.runtime.base import AgentRuntime, RuntimeContext, RuntimeEvent
from harness.runtime.codex_runtime import (
    CodexAppServerRuntime,
    CodexProcessFactory,
    CodexRuntimeConfig,
    CodexServerRequestHandler,
)
from harness.runtime.execution_contract import VISIBLE_EXECUTION_CONTRACT
from harness.runtime.tools import (
    ResolvedTools,
    ToolResolutionError,
    ToolResolver,
    enforce_published_tool_directory,
)
from harness.studio.model_configuration import ModelConfigurationService

_CONTROL_PLANE_PROVIDER_ID = "agent_studio"
_CONTROL_PLANE_API_KEY_ENV = "HARNESS_CODEX_PROVIDER_API_KEY"
_TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_OPTIONAL_CODEX_MCP_SERVERS = frozenset({"tavily"})
_CODEX_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh"})


def _toml_string(value: str) -> str:
    """Encode an untrusted control-plane string as a TOML basic string."""

    return json.dumps(value, ensure_ascii=False)


def _toml_key(value: str) -> str:
    return value if _TOML_BARE_KEY.fullmatch(value) else _toml_string(value)


def _codex_reasoning_configuration(snapshot: AgentManifestSnapshot) -> tuple[str, ...]:
    """Apply an optional immutable per-Agent Codex reasoning budget."""

    effort = snapshot.manifest.metadata.labels.get("codex-reasoning-effort")
    if effort is None:
        return ()
    normalized = effort.strip().lower()
    if normalized not in _CODEX_REASONING_EFFORTS:
        raise ConflictError(
            "published Agent has an invalid codex-reasoning-effort label: "
            f"{effort}"
        )
    return (f"model_reasoning_effort={_toml_string(normalized)}",)


def _codex_mcp_configuration(
    resolved: ResolvedTools,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Translate reviewed HTTP MCP registrations into secret-safe Codex config."""

    overrides: list[str] = []
    environment: dict[str, str] = {}
    for server_name, raw_config in resolved.mcp_servers.items():
        config = dict(cast(dict[str, object], raw_config))
        transport = config.get("type")
        url = config.get("url")
        if transport != "http" or not isinstance(url, str):
            raise ToolResolutionError(
                f"Codex MCP requires a streamable HTTP registration: {server_name}"
            )
        if any(secret and secret in url for secret in resolved.sensitive_values):
            raise ToolResolutionError(
                f"Codex MCP does not expose query-string credentials: {server_name}"
            )
        endpoint = urlsplit(url)
        if endpoint.scheme not in {"http", "https"} or endpoint.hostname is None:
            raise ToolResolutionError(f"Codex MCP endpoint is invalid: {server_name}")

        prefix = f"mcp_servers.{_toml_key(server_name)}"
        required = str(server_name not in _OPTIONAL_CODEX_MCP_SERVERS).lower()
        overrides.extend(
            (
                f"{prefix}.url={_toml_string(url)}",
                f"{prefix}.enabled=true",
                f"{prefix}.required={required}",
                f'{prefix}.default_tools_approval_mode="prompt"',
            )
        )

        canonical_prefix = f"mcp__{server_name}__"
        enabled_tools = tuple(
            tool.removeprefix(canonical_prefix)
            for tool in resolved.allowed_tools
            if tool.startswith(canonical_prefix)
        )
        if enabled_tools:
            overrides.append(
                f"{prefix}.enabled_tools="
                + json.dumps(enabled_tools, ensure_ascii=False, separators=(",", ":"))
            )
            overrides.extend(
                f'{prefix}.tools.{_toml_key(tool)}.approval_mode="approve"'
                for tool in enabled_tools
            )

        headers = config.get("headers")
        if headers is not None and not isinstance(headers, dict):
            raise ToolResolutionError(f"Codex MCP headers are invalid: {server_name}")
        for index, (header, raw_value) in enumerate(
            cast(dict[object, object], headers or {}).items()
        ):
            if not isinstance(header, str) or not isinstance(raw_value, str):
                raise ToolResolutionError(f"Codex MCP headers are invalid: {server_name}")
            env_name = f"HARNESS_CODEX_MCP_{len(environment)}_HEADER_{index}"
            environment[env_name] = raw_value
            overrides.append(
                f"{prefix}.env_http_headers.{_toml_key(header)}={_toml_string(env_name)}"
            )
    return tuple(overrides), environment


class RegistryCodexRuntime:
    """Build a per-Agent Codex runtime from the immutable published snapshot."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        codex_path: Path,
        model_configurations: ModelConfigurationService | None = None,
        model_by_route: Mapping[str, str] | None = None,
        provider_by_route: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
        approval_policy: str = "untrusted",
        sandbox_mode: str = "workspace-write",
        network_access: bool = False,
        tool_output_token_limit: int = 32_000,
        process_factory: CodexProcessFactory | None = None,
        server_request_handler: CodexServerRequestHandler | None = None,
        tool_resolver: ToolResolver | None = None,
        remote_memory_mcp: RemoteMemoryMcpProvider | None = None,
    ) -> None:
        self._registry = registry
        self._codex_path = codex_path
        self._model_configurations = model_configurations
        self._model_by_route = dict(model_by_route or {})
        self._provider_by_route = dict(provider_by_route or {})
        self._environment = environment
        self._approval_policy = approval_policy
        self._sandbox_mode = sandbox_mode
        self._network_access = network_access
        if tool_output_token_limit < 1_000:
            raise ValueError("Codex tool-output token limit must be at least 1000")
        self._tool_output_token_limit = tool_output_token_limit
        self._process_factory = process_factory
        self._server_request_handler = server_request_handler
        self._tool_resolver = tool_resolver or ToolResolver()
        self._remote_memory_mcp = remote_memory_mcp

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        session = context.session
        version = await self._registry.get(
            session.tenant_id,
            session.resolved_agent_owner_user_id,
            session.agent_name,
            session.agent_version,
        )
        snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
        if snapshot.manifest.spec.runtime != "codex-app-server":
            raise ConflictError("Agent manifest is not configured for Codex app-server")
        if session.runtime_type != "codex-app-server":
            raise ConflictError("Session runtime does not match the Agent manifest")
        enforce_runtime_environment(session, snapshot)
        model_spec = snapshot.manifest.spec.model
        raw_override = context.run.input.get("model_route_override")
        route_override = raw_override if isinstance(raw_override, str) else None
        route_id = route_override or model_spec.route
        provider_config_overrides: tuple[str, ...] = ()
        environment = self._environment
        if self._model_configurations is not None:
            selected_config = await self._model_configurations.resolve_runtime(
                session.tenant_id,
                session.agent_name,
                route_id,
                # Published Codex versions pin a Responses route. The legacy
                # Agent-wide binding is shared with Claude versions, so applying
                # it here makes one runtime's route silently break the other.
                apply_agent_binding=False,
                required_api_format="openai_compatible",
            )
            if selected_config is None or selected_config.route_id is None:
                raise ConflictError(
                    f"task model route is unavailable in the control plane: {route_id}"
                )
            route_id = selected_config.route_id
            if selected_config.resolved_auth_scheme != "bearer":
                raise ConflictError(
                    "Codex app-server requires a Responses-compatible bearer model route"
                )
            model = selected_config.model
            provider = _CONTROL_PLANE_PROVIDER_ID
            environment = {
                **dict(self._environment or {}),
                _CONTROL_PLANE_API_KEY_ENV: selected_config.credential.get_secret_value(),
            }
            provider_config_overrides = (
                f"model_provider={_toml_string(provider)}",
                (f"model_providers.{provider}.name={_toml_string('Agent Studio control plane')}"),
                (f"model_providers.{provider}.base_url={_toml_string(selected_config.base_url)}"),
                (f"model_providers.{provider}.env_key={_toml_string(_CONTROL_PLANE_API_KEY_ENV)}"),
                f'model_providers.{provider}.wire_api="responses"',
                f"model_providers.{provider}.requires_openai_auth=false",
            )
            event_provider = selected_config.provider
        else:
            model = self._model_by_route.get(route_id, model_spec.model)
            provider = self._provider_by_route.get(route_id)
            event_provider = provider or "codex"
        enforce_runtime_model_route(session, route_id)
        assert context.identity is not None
        resolved_tools = enforce_published_tool_directory(
            snapshot,
            await self._tool_resolver.resolve(snapshot.manifest, context.identity),
        )
        if self._remote_memory_mcp is not None:
            resolved_tools = self._remote_memory_mcp.attach(resolved_tools, context.identity)
        mcp_config_overrides, mcp_environment = _codex_mcp_configuration(resolved_tools)
        reasoning_config_overrides = _codex_reasoning_configuration(snapshot)
        limits = snapshot.manifest.spec.limits
        subagent_config_overrides = (
            "agents.enabled=true",
            (f"agents.max_concurrent_threads_per_session={limits.max_concurrent_subagents}"),
        )
        if mcp_environment:
            environment = {**dict(environment or {}), **mcp_environment}
        runtime = CodexAppServerRuntime(
            CodexRuntimeConfig(
                codex_path=self._codex_path,
                model=model,
                model_provider=provider,
                developer_instructions=(
                    f"{snapshot.system_prompt.rstrip()}\n\n{VISIBLE_EXECUTION_CONTRACT}"
                ),
                environment=environment,
                config_overrides=(
                    *provider_config_overrides,
                    *reasoning_config_overrides,
                    *mcp_config_overrides,
                    *subagent_config_overrides,
                    f"tool_output_token_limit={self._tool_output_token_limit}",
                ),
                approval_policy=self._approval_policy,
                sandbox_mode=self._sandbox_mode,
                network_access=self._network_access,
                turn_timeout_seconds=snapshot.manifest.spec.limits.timeout_seconds,
                max_tool_calls=limits.max_tool_calls,
            ),
            **(
                {"process_factory": self._process_factory}
                if self._process_factory is not None
                else {}
            ),
            server_request_handler=self._server_request_handler,
        )
        yield RuntimeEvent(
            type="model.route.selected",
            payload={
                "route_id": route_id,
                "provider": event_provider,
                "model": model,
                "runtime": "codex-app-server",
            },
        )
        async for event in runtime.execute(context):
            yield event


class RegistryRuntimeRouter:
    """Dispatch a pinned Session to one of the installed Agent runtimes."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        runtimes: Mapping[AgentRuntimeType, AgentRuntime],
    ) -> None:
        self._registry = registry
        self._runtimes = dict(runtimes)

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        session = context.session
        version = await self._registry.get(
            session.tenant_id,
            session.resolved_agent_owner_user_id,
            session.agent_name,
            session.agent_version,
        )
        snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
        runtime_type = snapshot.manifest.spec.runtime
        if session.runtime_type != runtime_type:
            raise ConflictError("Session runtime does not match the Agent manifest")
        runtime = self._runtimes.get(runtime_type)
        if runtime is None:
            raise ConflictError(f"Agent runtime is not installed: {runtime_type}")
        async for event in runtime.execute(context):
            yield event
