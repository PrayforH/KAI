"""Resolve logical Agent Manifest tool references for Claude Agent SDK."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from importlib import import_module
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from claude_agent_sdk import (
    McpSdkServerConfig,
    McpServerConfig,
    SdkMcpTool,
    create_sdk_mcp_server,
)

from harness.core.manifest import AgentManifest, AgentManifestSnapshot
from harness.core.models import ExecutionIdentity
from harness.policy.models import ContextTrust
from harness.runtime.mcp_credentials import (
    CredentialValues,
    DynamicMcpCredentialProvider,
    EmptyMcpCredentialProvider,
    McpCredentialError,
)
from harness.runtime.web_tools import PublicWebClient, create_web_mcp_server
from harness.studio.web_configuration import UserWebClient, WebConfigurationService


class ToolResolutionError(ValueError):
    """Raised when a logical tool reference cannot be resolved safely."""


_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


@dataclass(frozen=True)
class McpSmokeCheck:
    """Server-reviewed read-only call used only by live Preflight."""

    tool: str
    arguments: Mapping[str, object]


@dataclass(frozen=True)
class McpServerRegistration:
    """Server-owned MCP configuration referenced by a logical Manifest ID."""

    server_name: str
    config: McpServerConfig
    allowed_tools: tuple[str, ...] = ()
    static_headers: tuple[tuple[str, str], ...] = ()
    credential_headers: tuple[tuple[str, str], ...] = ()
    credential_header_prefixes: tuple[tuple[str, str], ...] = ()
    credential_environment: tuple[tuple[str, str], ...] = ()
    credential_query_parameters: tuple[tuple[str, str], ...] = ()
    preflight_smoke: McpSmokeCheck | None = None
    result_trust: ContextTrust = ContextTrust.UNTRUSTED

    def __post_init__(self) -> None:
        public_config = cast(dict[str, object], self.config)
        if public_config.get("headers") or public_config.get("env"):
            raise ToolResolutionError(
                "MCP registrations cannot contain inline headers or environment"
            )
        transport = public_config.get("type")
        raw_url = public_config.get("url")
        if transport not in {"http", "sse"} or not isinstance(raw_url, str):
            raise ToolResolutionError("MCP registrations must use a registered HTTP(S) endpoint")
        endpoint = urlsplit(raw_url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
        ):
            raise ToolResolutionError("MCP registrations must use a registered HTTP(S) endpoint")
        targets = [name for name, _ in self.credential_headers]
        targets.extend(name for name, _ in self.credential_environment)
        targets.extend(name for name, _ in self.credential_query_parameters)
        if len(targets) != len(set(targets)):
            raise ToolResolutionError("duplicate MCP credential target")
        header_targets = {name for name, _ in self.credential_headers}
        static_header_targets = {name.lower() for name, _ in self.static_headers}
        if len(static_header_targets) != len(self.static_headers):
            raise ToolResolutionError("duplicate MCP static header")
        if any(
            not name
            or len(name) > 128
            or not _HTTP_HEADER_NAME.fullmatch(name)
            or len(value) > 1024
            or "\n" in value
            or "\r" in value
            for name, value in self.static_headers
        ):
            raise ToolResolutionError("invalid MCP static header")
        if static_header_targets.intersection(name.lower() for name in header_targets):
            raise ToolResolutionError("MCP static header conflicts with a credential header")
        prefix_targets = {name for name, _ in self.credential_header_prefixes}
        if not prefix_targets.issubset(header_targets):
            raise ToolResolutionError("MCP credential prefix targets an unknown header")
        if any("\n" in prefix or "\r" in prefix for _, prefix in self.credential_header_prefixes):
            raise ToolResolutionError("MCP credential header prefix contains a newline")


@dataclass(frozen=True)
class ResolvedTools:
    """Deterministic Claude SDK tool configuration for one Agent Manifest."""

    builtin_tools: tuple[str, ...]
    mcp_servers: Mapping[str, McpServerConfig]
    allowed_tools: tuple[str, ...]
    mcp_smokes: Mapping[str, McpSmokeCheck]
    result_trust: Mapping[str, ContextTrust] = field(default_factory=lambda: MappingProxyType({}))
    sensitive_names: frozenset[str] = frozenset()
    sensitive_values: frozenset[str] = frozenset()
    unavailable_mcp: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )


class ToolResolver:
    """Resolve builtins, installed Python SDK tools and registered MCP servers."""

    def __init__(
        self,
        *,
        mcp_registry: Mapping[str, McpServerRegistration] | None = None,
        mcp_registry_provider: (
            Callable[[str, str], Awaitable[Mapping[str, McpServerRegistration]]] | None
        ) = None,
        credential_provider: DynamicMcpCredentialProvider | None = None,
        web_search_api_key: str = "",
        web_search_provider: Literal["tavily", "minimax"] = "tavily",
        web_enabled: bool = True,
        web_configurations: WebConfigurationService | None = None,
    ) -> None:
        self._mcp_registry = MappingProxyType(dict(mcp_registry or {}))
        self._mcp_registry_provider = mcp_registry_provider
        self._credential_provider = credential_provider or EmptyMcpCredentialProvider()
        self._web_search_api_key = web_search_api_key
        self._web_search_provider: Literal["tavily", "minimax"] = web_search_provider
        self._web_enabled = web_enabled
        self._web_configurations = web_configurations

    def web_server(self, names: set[str], identity: ExecutionIdentity | None) -> McpSdkServerConfig:
        if not self._web_enabled:
            raise ToolResolutionError("平台已关闭内置联网能力，请移除联网工具后运行。")

        if self._web_configurations is not None and identity is not None:
            return create_web_mcp_server(names, UserWebClient(self._web_configurations, identity))

        async def key() -> str:
            if self._web_search_api_key:
                return self._web_search_api_key
            if identity is not None and self._web_search_provider == "tavily":
                try:
                    credentials = await self._credential_provider.resolve(
                        "tavily-readonly", identity, frozenset({"api_key"})
                    )
                    return credentials["api_key"].get_secret_value()
                except McpCredentialError:
                    pass
            return ""

        return create_web_mcp_server(names, PublicWebClient(key, self._web_search_provider))

    async def web_allowed(self, identity: ExecutionIdentity | None) -> bool:
        if not self._web_enabled:
            return False
        if self._web_configurations is None or identity is None:
            return True
        configuration = await self._web_configurations.get(identity.tenant_id, identity.user_id)
        return configuration.effective_enabled

    async def resolve(
        self,
        manifest: AgentManifest,
        identity: ExecutionIdentity | None = None,
        *,
        python_tool_overrides: Mapping[str, SdkMcpTool[Any]] | None = None,
        tolerate_unavailable_mcp: bool = False,
    ) -> ResolvedTools:
        builtins: list[str] = []
        python_tools: list[SdkMcpTool[Any]] = []
        mcp_servers: dict[str, McpServerConfig] = {}
        allowed_tools: list[str] = []
        mcp_smokes: dict[str, McpSmokeCheck] = {}
        result_trust: dict[str, ContextTrust] = {}
        sensitive_names: set[str] = set()
        sensitive_values: set[str] = set()
        unavailable_mcp: dict[str, tuple[str, ...]] = {}
        has_python_override = False
        mcp_registry = dict(self._mcp_registry)
        requested_mcp = {tool.mcp for tool in manifest.spec.tools if tool.mcp is not None}
        needs_tenant_registry = bool(requested_mcp.difference(mcp_registry))
        if self._mcp_registry_provider is not None and needs_tenant_registry:
            if identity is None and any(tool.mcp is not None for tool in manifest.spec.tools):
                raise McpCredentialError(
                    "execution identity is required for tenant MCP registrations"
                )
            if identity is not None:
                mcp_registry.update(
                    await self._mcp_registry_provider(
                        identity.tenant_id,
                        identity.user_id,
                    )
                )

        web_allowed = await self.web_allowed(identity)
        python_tool_overrides = python_tool_overrides or {}
        for tool_spec in manifest.spec.tools:
            if tool_spec.builtin is not None:
                if tool_spec.builtin in {"WebSearch", "WebFetch"} and not web_allowed:
                    continue
                if tool_spec.builtin not in builtins:
                    builtins.append(tool_spec.builtin)
                continue
            if tool_spec.python_entry is not None:
                override = python_tool_overrides.get(tool_spec.python_entry)
                if override is not None:
                    python_tools.append(override)
                    has_python_override = True
                elif tool_spec.python_entry.startswith("bundle:"):
                    raise ToolResolutionError(
                        f"Bundle Python tool was not staged: {tool_spec.python_entry}"
                    )
                else:
                    python_tools.extend(self._load_python_tools(tool_spec.python_entry))
                continue

            reference = cast(str, tool_spec.mcp)
            registration = mcp_registry.get(reference)
            if registration is None:
                raise ToolResolutionError(f"MCP tool registration is not configured: {reference}")
            if registration.server_name in mcp_servers:
                raise ToolResolutionError(f"duplicate MCP server name: {registration.server_name}")
            bindings = (
                registration.credential_headers
                + registration.credential_environment
                + registration.credential_query_parameters
            )
            required_keys = frozenset(key for _, key in bindings)
            if required_keys and identity is None:
                raise McpCredentialError(
                    f"execution identity is required for MCP credentials: {reference}"
                )
            try:
                credentials: CredentialValues = (
                    await self._credential_provider.resolve(
                        reference,
                        identity
                        if identity is not None
                        else ExecutionIdentity(
                            tenant_id="none",
                            user_id="none",
                            project_id="none",
                            session_id="none",
                            run_id="none",
                            agent_name=manifest.metadata.name,
                            agent_version=manifest.metadata.version,
                        ),
                        required_keys,
                    )
                    if required_keys
                    else MappingProxyType({})
                )
            except McpCredentialError:
                if not tolerate_unavailable_mcp:
                    raise
                unavailable_mcp[reference] = registration.allowed_tools
                continue
            config = dict(cast(dict[str, object], registration.config))
            if registration.static_headers:
                config["headers"] = dict(registration.static_headers)
            if registration.credential_headers:
                prefixes = dict(registration.credential_header_prefixes)
                headers: dict[str, str] = {
                    target: (prefixes.get(target, "") + credentials[key].get_secret_value())
                    for target, key in registration.credential_headers
                }
                config["headers"] = {
                    **cast(dict[str, str], config.get("headers", {})),
                    **headers,
                }
                sensitive_names.update(headers)
                sensitive_values.update(headers.values())
            if registration.credential_environment:
                environment: dict[str, str] = {
                    target: credentials[key].get_secret_value()
                    for target, key in registration.credential_environment
                }
                config["env"] = environment
                sensitive_names.update(environment)
                sensitive_values.update(environment.values())
            if registration.credential_query_parameters:
                raw_url = config.get("url")
                if not isinstance(raw_url, str):
                    raise ToolResolutionError(
                        f"MCP query credentials require an HTTP URL: {reference}"
                    )
                parsed = urlsplit(raw_url)
                query = parse_qsl(parsed.query, keep_blank_values=True)
                existing_names = {name for name, _ in query}
                requested_names = {name for name, _ in registration.credential_query_parameters}
                duplicates = existing_names.intersection(requested_names)
                if duplicates:
                    names = ", ".join(sorted(duplicates))
                    raise ToolResolutionError(
                        f"MCP query credential already present: {reference}.{names}"
                    )
                injected: dict[str, str] = {
                    target: credentials[key].get_secret_value()
                    for target, key in registration.credential_query_parameters
                }
                config["url"] = urlunsplit(
                    parsed._replace(query=urlencode([*query, *injected.items()]))
                )
                sensitive_names.update(injected)
                sensitive_values.update(injected.values())
            mcp_servers[registration.server_name] = cast(McpServerConfig, config)
            if registration.preflight_smoke is not None:
                mcp_smokes[registration.server_name] = registration.preflight_smoke
            for allowed_tool in registration.allowed_tools:
                if allowed_tool not in allowed_tools:
                    allowed_tools.append(allowed_tool)
                result_trust[allowed_tool] = registration.result_trust

        self._assert_unique_python_tool_names(python_tools)
        if python_tools:
            server_name = (
                python_server_name(manifest.metadata.name)
                if has_python_override
                else "harness-python"
            )
            if server_name in mcp_servers:
                raise ToolResolutionError(f"duplicate MCP server name: {server_name}")
            mcp_servers[server_name] = create_sdk_mcp_server(
                server_name,
                tools=python_tools,
            )
            for sdk_tool in python_tools:
                canonical_name = f"mcp__{server_name}__{sdk_tool.name}"
                allowed_tools.append(canonical_name)
                result_trust[canonical_name] = ContextTrust.SAFE

        return ResolvedTools(
            builtin_tools=tuple(builtins),
            mcp_servers=MappingProxyType(mcp_servers),
            allowed_tools=tuple(allowed_tools),
            mcp_smokes=MappingProxyType(mcp_smokes),
            result_trust=MappingProxyType(result_trust),
            sensitive_names=frozenset(sensitive_names),
            sensitive_values=frozenset(sensitive_values),
            unavailable_mcp=MappingProxyType(unavailable_mcp),
        )

    @staticmethod
    def _load_python_tools(reference: str) -> tuple[SdkMcpTool[Any], ...]:
        module_name, separator, attribute_name = reference.partition(":")
        if not separator or not module_name or not attribute_name or ":" in attribute_name:
            raise ToolResolutionError(f"invalid python tool reference: {reference}")
        try:
            exported = getattr(import_module(module_name), attribute_name)
        except Exception:
            raise ToolResolutionError(f"cannot load python tool: {reference}") from None

        if isinstance(exported, SdkMcpTool):
            return (cast(SdkMcpTool[Any], exported),)
        if isinstance(exported, Sequence) and not isinstance(exported, (str, bytes)):
            values = tuple(cast(Sequence[object], exported))
            if values and all(isinstance(value, SdkMcpTool) for value in values):
                return cast(tuple[SdkMcpTool[Any], ...], values)
        raise ToolResolutionError(
            f"python tool export must be an SdkMcpTool or a sequence of SdkMcpTool: {reference}"
        )

    @staticmethod
    def _assert_unique_python_tool_names(tools: Sequence[SdkMcpTool[Any]]) -> None:
        names: set[str] = set()
        for sdk_tool in tools:
            if sdk_tool.name in names:
                raise ToolResolutionError(f"duplicate python tool name: {sdk_tool.name}")
            names.add(sdk_tool.name)


def python_server_name(agent_name: str) -> str:
    return f"harness-python-{agent_name}"


def enforce_published_tool_directory(
    snapshot: AgentManifestSnapshot,
    resolved: ResolvedTools,
) -> ResolvedTools:
    """Keep runtime tools within the published directory without blocking additions."""

    directory = snapshot.tool_directory
    mode = snapshot.manifest.spec.tool_exposure_mode
    if directory is None:
        if mode == "on_demand":
            raise ToolResolutionError("on-demand tool exposure is missing its published directory")
        return resolved
    if directory.exposure_mode != mode:
        raise ToolResolutionError(
            "published tool directory exposure mode does not match the Manifest"
        )
    expected_builtins = {entry.name for entry in directory.entries if entry.source == "builtin"}
    expected_mcp = {entry.name for entry in directory.entries if entry.source in {"mcp", "python"}}
    actual_builtins = set(resolved.builtin_tools)
    actual_mcp = set(resolved.allowed_tools)
    if expected_builtins != actual_builtins:
        raise ToolResolutionError("runtime builtin tools differ from the published tool directory")
    unavailable_mcp = {tool for tools in resolved.unavailable_mcp.values() for tool in tools}
    missing_mcp = expected_mcp.difference(actual_mcp).difference(unavailable_mcp)
    if missing_mcp:
        missing_names = ", ".join(sorted(missing_mcp))
        raise ToolResolutionError(
            "published MCP tools are no longer available; "
            f"recheck and publish the Agent: {missing_names}"
        )
    if mode == "on_demand" and any(
        tool.python_entry is not None for tool in snapshot.manifest.spec.tools
    ):
        raise ToolResolutionError("on-demand loading does not support in-process Python tools")
    return replace(
        resolved,
        allowed_tools=tuple(tool for tool in resolved.allowed_tools if tool in expected_mcp),
        result_trust=MappingProxyType(
            {tool: trust for tool, trust in resolved.result_trust.items() if tool in expected_mcp}
        ),
    )
