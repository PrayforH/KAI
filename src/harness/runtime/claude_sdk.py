"""Claude Agent SDK runtime adapter with explicit gateway routing."""

import asyncio
import json
import logging
import shutil
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ContextUsageResponse,
    ProcessError,
    ResultMessage,
    SessionStore,
    StreamEvent,
    TaskUpdatedMessage,
    Transport,
)

from harness.application.memory import UserMemoryService
from harness.core.errors import ConflictError
from harness.core.manifest import (
    AgentManifestSnapshot,
    materialize_python_tool_snapshot_set,
    materialize_skill_snapshot_set,
)
from harness.core.models import AgentVersion, ModelRoute
from harness.knowledge.models import (
    KnowledgeResultTrust,
    KnowledgeSnapshotBinding,
)
from harness.knowledge.runtime import (
    create_knowledge_mcp_server,
    knowledge_execution_context,
)
from harness.knowledge.service import KnowledgeService
from harness.knowledge.workload import RemoteKnowledgeMcpProvider
from harness.memory_bank.service import MemoryBankService
from harness.memory_bank.workload import RemoteMemoryMcpProvider
from harness.observability.provider import Observability
from harness.policy.models import ContextTrust
from harness.runtime.artifact_tools import (
    artifact_execution_context,
    create_artifact_mcp_server,
)
from harness.runtime.base import (
    RuntimeContext,
    RuntimeEvent,
    RuntimeExecutionTimeoutError,
    RuntimeResultError,
)
from harness.runtime.execution_contract import VISIBLE_EXECUTION_CONTRACT
from harness.runtime.hooks import SdkDiagnosticTail, sdk_diagnostic_summary
from harness.runtime.mcp_credentials import redact_mcp_credentials
from harness.runtime.memory_tools import create_memory_mcp_server, memory_execution_context
from harness.runtime.message_mapper import (
    map_sdk_message,
    provider_error_user_message,
    provider_result_error_code,
    result_subtype,
    result_usage,
)
from harness.runtime.model_router import ModelRouter
from harness.runtime.sandbox_tools import (
    COORDINATION_BUILTINS,
    create_bundle_python_tool,
    create_sandbox_tools_mcp_server,
    proxy_tool_name,
)
from harness.runtime.sandbox_tools import (
    SERVER_NAME as SANDBOX_MCP_SERVER_NAME,
)
from harness.runtime.sandbox_tools import (
    SUPPORTED_BUILTINS as SANDBOX_BUILTINS,
)
from harness.runtime.sdk_tool_gate import ToolGate
from harness.runtime.subagent_governance import SubagentRuntimeGovernor
from harness.runtime.tools import (
    ResolvedTools,
    ToolResolutionError,
    ToolResolver,
    enforce_published_tool_directory,
)

SDK_JSON_MAX_BUFFER_SIZE = 32 * 1024 * 1024
CONTEXT_USAGE_CONTROL_TIMEOUT_SECONDS = 1.0
# Daytona/E2B/Kubernetes transports cross at least one control-plane hop after
# the Claude CLI has produced its terminal result.  The local one-second budget
# is intentionally tight, but applying it to a remote transport made the exact
# context observation deterministically time out before the control response
# could traverse the sandbox log stream.  This budget is off the TTFT path and
# remains bounded so a provider that never answers still fails open.
REMOTE_CONTEXT_USAGE_CONTROL_TIMEOUT_SECONDS = 3.0
SDK_STARTUP_RETRY_DELAYS_SECONDS = (0.35, 0.9)
QueryFactory = Callable[[str, ClaudeAgentOptions], AsyncIterator[object]]
logger = logging.getLogger(__name__)
_TEXT_DELTA_FLUSH_CHARS = 64
_TEXT_DELTA_PUNCTUATION_CHARS = 16
_TEXT_DELTA_BOUNDARIES = frozenset("\n。！？.!?")
_ANTHROPIC_AUTO_PERMISSION_MODELS = frozenset(
    {
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "claude-opus-4-7",
    }
)


def permission_mode_for_route(route: ModelRoute) -> Literal["auto", "dontAsk"]:
    """Use Claude Auto only where Anthropic documents and serves it."""

    endpoint = urlsplit(route.base_url)
    official_endpoint = (
        endpoint.scheme.lower() == "https"
        and (endpoint.hostname or "").lower() == "api.anthropic.com"
        and endpoint.port in {None, 443}
        and endpoint.path.rstrip("/") == ""
        and not endpoint.query
        and not endpoint.fragment
    )
    if (
        route.provider.lower() == "anthropic"
        and official_endpoint
        and route.model.lower() in _ANTHROPIC_AUTO_PERMISSION_MODELS
    ):
        return "auto"
    return "dontAsk"


@dataclass(frozen=True)
class ContextWindowObservation:
    """Content-free provider context metrics captured through the SDK control API."""

    phase: str
    total_tokens: int
    max_tokens: int
    raw_max_tokens: int
    percentage: float
    model: str
    auto_compact_enabled: bool
    auto_compact_threshold: int | None
    categories: tuple[tuple[str, int], ...]

    def event(self) -> RuntimeEvent:
        return RuntimeEvent(
            type="context.window.observed",
            payload={
                "phase": self.phase,
                "total_tokens": self.total_tokens,
                "max_tokens": self.max_tokens,
                "raw_max_tokens": self.raw_max_tokens,
                "percentage": self.percentage,
                "model": self.model,
                "auto_compact_enabled": self.auto_compact_enabled,
                "auto_compact_threshold": self.auto_compact_threshold,
                "categories": [
                    {"name": name, "tokens": tokens} for name, tokens in self.categories
                ],
            },
        )


@dataclass(frozen=True)
class ContextWindowUnavailable:
    """Content-free capability outcome when the optional SDK control call fails."""

    phase: str
    reason: Literal["control_timeout", "control_unavailable"]

    def event(self) -> RuntimeEvent:
        return RuntimeEvent(
            type="context.window.unavailable",
            payload={"phase": self.phase, "reason": self.reason},
        )


@dataclass(frozen=True)
class SessionResumeRecovery:
    """Signals that a stale SDK transcript binding must be replaced."""

    previous_session_id: str

    def event(self) -> RuntimeEvent:
        return RuntimeEvent(
            type="runtime.session.recovered",
            payload={"previous_session_id": self.previous_session_id},
        )


def _context_window_observation(
    phase: str,
    usage: ContextUsageResponse,
) -> ContextWindowObservation:
    categories = tuple(
        (str(item["name"]), int(item["tokens"]))
        for item in usage.get("categories", [])
        if item["tokens"] >= 0
    )
    threshold = usage.get("autoCompactThreshold")
    return ContextWindowObservation(
        phase=phase,
        total_tokens=max(0, int(usage.get("totalTokens", 0))),
        max_tokens=max(0, int(usage.get("maxTokens", 0))),
        raw_max_tokens=max(0, int(usage.get("rawMaxTokens", 0))),
        percentage=max(0.0, min(100.0, float(usage.get("percentage", 0.0)))),
        model=str(usage.get("model", "")),
        auto_compact_enabled=bool(usage.get("isAutoCompactEnabled", False)),
        auto_compact_threshold=(
            int(threshold) if threshold is not None and threshold >= 0 else None
        ),
        categories=categories,
    )


async def _observe_context_window(
    client: ClaudeSDKClient,
    phase: str,
    *,
    timeout_seconds: float | None = None,
) -> ContextWindowObservation | ContextWindowUnavailable:
    timeout = CONTEXT_USAGE_CONTROL_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            usage = await client.get_context_usage()
    except TimeoutError:
        return ContextWindowUnavailable(phase=phase, reason="control_timeout")
    except Exception:  # noqa: BLE001 - optional provider control method is fail-open
        return ContextWindowUnavailable(phase=phase, reason="control_unavailable")
    return _context_window_observation(phase, usage)


async def _client_query(
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    transport: Transport | None = None,
    context_usage_timeout_seconds: float | None = None,
) -> AsyncIterator[object]:
    attempt_options = options
    recovery_session_id: str | None = None
    for attempt in range(len(SDK_STARTUP_RETRY_DELAYS_SECONDS) + 1):
        received_message = False
        client = (
            ClaudeSDKClient(options=attempt_options)
            if transport is None
            else ClaudeSDKClient(options=attempt_options, transport=transport)
        )
        try:
            async with client:
                # Fresh sessions have no historical window to govern. On resumed
                # sessions the optional control request runs only after the provider
                # result, so it cannot add latency to first text.
                observe_resumed_context = attempt_options.resume is not None
                terminal_result: ResultMessage | None = None
                await client.query(prompt)
                async for message in client.receive_response():
                    received_message = True
                    if recovery_session_id is not None:
                        yield SessionResumeRecovery(recovery_session_id)
                        recovery_session_id = None
                    if (
                        observe_resumed_context
                        and isinstance(message, ResultMessage)
                        and not message.is_error
                        and message.stop_reason == "end_turn"
                    ):
                        # The Worker treats this result as the protocol boundary and
                        # closes the runtime iterator immediately. Hold it briefly so
                        # the optional control outcome becomes durable first.
                        terminal_result = message
                        continue
                    yield message
                if observe_resumed_context:
                    yield await _observe_context_window(
                        client,
                        "after",
                        timeout_seconds=context_usage_timeout_seconds,
                    )
                if terminal_result is not None:
                    yield terminal_result
            return
        except ProcessError as error:
            can_retry = (
                transport is None
                and not received_message
                and attempt < len(SDK_STARTUP_RETRY_DELAYS_SECONDS)
            )
            logger.warning(
                "Claude CLI exited before its first SDK message; "
                "attempt=%s retry=%s resume=%s exit_code=%s diagnostic=%s",
                attempt + 1,
                can_retry,
                attempt_options.resume is not None,
                error.exit_code,
                sdk_diagnostic_summary(attempt_options.stderr),
            )
            if not can_retry:
                raise
            # A cancelled or interrupted Run can leave a durable Session id
            # without a complete CLI transcript. No model/tool message has
            # been observed, so starting a fresh SDK session is safe; the
            # platform context projection still supplies conversation state.
            if attempt_options.resume is not None:
                recovery_session_id = attempt_options.resume
                attempt_options = replace(attempt_options, resume=None)
            await asyncio.sleep(SDK_STARTUP_RETRY_DELAYS_SECONDS[attempt])


async def _default_query(prompt: str, options: ClaudeAgentOptions) -> AsyncIterator[object]:
    async for message in _client_query(prompt, options):
        yield message


class ClaudeSdkRuntime:
    def __init__(
        self,
        *,
        agent_version: AgentVersion,
        routes: list[ModelRoute],
        route_secrets: dict[str, str],
        subagent_versions: dict[str, AgentVersion] | None = None,
        query_factory: QueryFactory = _default_query,
        session_store: object | None = None,
        tool_resolver: ToolResolver | None = None,
        tool_gate: ToolGate | None = None,
        memory_service: UserMemoryService | None = None,
        memory_bank: MemoryBankService | None = None,
        remote_memory_mcp: RemoteMemoryMcpProvider | None = None,
        knowledge: KnowledgeService | None = None,
        remote_knowledge_mcp: RemoteKnowledgeMcpProvider | None = None,
        observability: Observability | None = None,
    ) -> None:
        self._agent_version = agent_version
        self._snapshot = AgentManifestSnapshot.model_validate(agent_version.snapshot)
        self._router = ModelRouter(routes)
        self._route_secrets = route_secrets
        self._subagent_versions = subagent_versions or {}
        self._query = query_factory
        self._session_store = session_store
        self._tool_resolver = tool_resolver or ToolResolver()
        self._tool_gate = tool_gate
        self._memory_service = memory_service
        self._memory_bank = memory_bank
        self._remote_memory_mcp = remote_memory_mcp
        self._knowledge = knowledge
        self._remote_knowledge_mcp = remote_knowledge_mcp
        self._observability = observability

    def _span(
        self,
        name: str,
        *,
        run_id: str,
        attributes: Mapping[str, str | bool | int | float] | None = None,
    ) -> AbstractContextManager[None]:
        if self._observability is None:
            return nullcontext()
        return self._observability.span(
            name,
            attributes={"run.id": run_id, **dict(attributes or {})},
        )

    async def _model_messages(
        self,
        messages: AsyncIterator[object],
        *,
        run_id: str,
        route: ModelRoute,
        prompt: str,
    ) -> AsyncIterator[object]:
        manifest = self._snapshot.manifest
        base_attributes: dict[str, str | bool | int | float] = {
            "agent.name": manifest.metadata.name,
            "agent.version": manifest.metadata.version,
            "agent.content_hash": self._snapshot.content_hash,
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": route.provider,
            "gen_ai.request.model": route.model,
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": route.model,
            "langfuse.observation.metadata.provider": route.provider,
            "langfuse.observation.metadata.route_id": route.route_id,
            "langfuse.version": manifest.metadata.version,
            "harness.model.route": route.route_id,
            "harness.model.permission_mode": permission_mode_for_route(route),
            "harness.policy.profile": manifest.spec.permissions.policy,
            "harness.skill.count": len(self._snapshot.skill_snapshots),
        }
        if self._agent_version.package_hash is not None:
            base_attributes["agent.package_hash"] = self._agent_version.package_hash
        with self._span(
            "harness.model.run",
            run_id=run_id,
            attributes=base_attributes,
        ):
            if self._observability is not None:
                self._observability.annotate_current_io(input_value=prompt)
            async for message in messages:
                if isinstance(message, ResultMessage) and self._observability is not None:
                    subtype = result_subtype(message)
                    usage = result_usage(message)
                    result_attributes: dict[str, str | bool | int | float] = {
                        "harness.model.duration_ms": message.duration_ms,
                        "harness.model.api_duration_ms": message.duration_api_ms,
                        "harness.model.turns": message.num_turns,
                        "harness.model.is_error": message.is_error,
                    }
                    if message.total_cost_usd is not None:
                        result_attributes["harness.model.cost_usd"] = message.total_cost_usd
                    if message.stop_reason is not None:
                        result_attributes["harness.model.stop_reason"] = message.stop_reason
                    if message.api_error_status is not None:
                        result_attributes["harness.model.api_error_status"] = (
                            message.api_error_status
                        )
                    for source, target in (
                        ("input_tokens", "gen_ai.usage.input_tokens"),
                        ("output_tokens", "gen_ai.usage.output_tokens"),
                        (
                            "cache_creation_input_tokens",
                            "harness.usage.cache_creation_input_tokens",
                        ),
                        (
                            "cache_read_input_tokens",
                            "harness.usage.cache_read_input_tokens",
                        ),
                    ):
                        if source in usage:
                            result_attributes[target] = usage[source]
                    usage_details = {
                        target: usage[source]
                        for source, target in (
                            ("input_tokens", "input"),
                            ("output_tokens", "output"),
                            ("cache_creation_input_tokens", "cache_creation_input"),
                            ("cache_read_input_tokens", "cache_read_input"),
                        )
                        if source in usage
                    }
                    if usage_details:
                        result_attributes["langfuse.observation.usage_details"] = json.dumps(
                            usage_details, separators=(",", ":")
                        )
                    if message.total_cost_usd is not None:
                        result_attributes["langfuse.observation.cost_details"] = json.dumps(
                            {"total": message.total_cost_usd},
                            separators=(",", ":"),
                        )
                    result_attributes["langfuse.observation.level"] = (
                        "ERROR" if message.is_error else "DEFAULT"
                    )
                    result_attributes["langfuse.observation.status_message"] = (
                        subtype if message.is_error else "模型处理完成"
                    )
                    self._observability.annotate_current_span(result_attributes)
                    self._observability.annotate_current_io(
                        output_value=message.result,
                    )
                    self._observability.annotate_current_io(
                        output_value=message.result,
                        trace_level=True,
                    )
                    if message.is_error:
                        self._observability.mark_current_span_error(subtype)
                yield message
                if isinstance(message, ResultMessage) and message.is_error:
                    provider_result = message.result if isinstance(message.result, str) else ""
                    error_code = provider_result_error_code(
                        provider_result,
                        message.api_error_status,
                    )
                    raise RuntimeResultError(
                        result_subtype(message),
                        api_error_status=message.api_error_status,
                        error_code=error_code,
                        user_message=provider_error_user_message(error_code),
                    )

    async def _options(
        self, context: RuntimeContext, route: ModelRoute
    ) -> tuple[ClaudeAgentOptions, ResolvedTools]:
        manifest = self._snapshot.manifest
        permission_mode = permission_mode_for_route(route)
        subagent_snapshots = {
            name: AgentManifestSnapshot.model_validate(version.snapshot)
            for name, version in self._subagent_versions.items()
        }
        materialized_skill_names = (
            materialize_skill_snapshot_set(
                (self._snapshot, *subagent_snapshots.values()), context.workspace
            )
            if self._snapshot.skill_snapshots
            or any(snapshot.skill_snapshots for snapshot in subagent_snapshots.values())
            else tuple(Path(skill).name for skill in manifest.spec.skills)
        )
        del materialized_skill_names
        # The workspace contains every immutable child Skill, but the Lead
        # advertises only its own names. Each AgentDefinition below receives
        # the Skills pinned to that child version.
        skill_names = tuple(skill.name for skill in self._snapshot.skill_snapshots) or tuple(
            Path(skill).name for skill in manifest.spec.skills
        )
        all_snapshots = (self._snapshot, *subagent_snapshots.values())
        materialized_python_tools = (
            materialize_python_tool_snapshot_set(all_snapshots, context.workspace)
            if any(snapshot.python_tool_snapshots for snapshot in all_snapshots)
            else {}
        )

        def python_overrides(snapshot: AgentManifestSnapshot) -> dict[str, object]:
            if not snapshot.python_tool_snapshots:
                return {}
            if context.sandbox_command_executor is None:
                raise ToolResolutionError(
                    "self-contained Bundle Python tools require isolated Sandbox execution"
                )
            paths = materialized_python_tools.get(snapshot.content_hash, {})
            return {
                item.reference: create_bundle_python_tool(
                    snapshot=item,
                    materialized_path=paths.get(item.reference),
                    executor=context.sandbox_command_executor,
                )
                for item in snapshot.python_tool_snapshots
            }

        secret = self._route_secrets.get(route.route_id)
        if not secret:
            raise ConflictError(f"credentials are not configured for route: {route.route_id}")
        environment = {
            "ANTHROPIC_BASE_URL": route.base_url,
            "CLAUDE_AGENT_SDK_CLIENT_APP": "claude-agent-harness/0.1.0",
        }
        on_demand_snapshots = tuple(
            snapshot
            for snapshot in (self._snapshot, *subagent_snapshots.values())
            if snapshot.manifest.spec.tool_exposure_mode == "on_demand"
        )
        if on_demand_snapshots:
            if "tool_search" not in route.capabilities:
                raise ToolResolutionError(
                    "selected model route does not support on-demand tool loading"
                )
            environment["ENABLE_TOOL_SEARCH"] = "true"
        auth_scheme = route.auth_scheme or (
            "bearer" if route.provider == "new-api" else "x-api-key"
        )
        if auth_scheme == "bearer":
            environment["ANTHROPIC_AUTH_TOKEN"] = secret
        else:
            environment["ANTHROPIC_API_KEY"] = secret
        resolved_tools = await self._tool_resolver.resolve(
            manifest,
            context.identity,
            python_tool_overrides=cast(Any, python_overrides(self._snapshot)),
            tolerate_unavailable_mcp=True,
        )
        resolved_tools = enforce_published_tool_directory(
            self._snapshot,
            resolved_tools,
        )
        mcp_servers = dict(resolved_tools.mcp_servers)
        allowed_tools = list(resolved_tools.allowed_tools)
        builtin_tools = list(resolved_tools.builtin_tools)
        remote_transport = context.runtime_transport_factory is not None
        knowledge_bindings = tuple(
            KnowledgeSnapshotBinding.model_validate(item)
            for item in context.session.knowledge_snapshot_bindings
        )
        if (
            remote_transport
            and self._remote_memory_mcp is not None
            and context.identity is not None
        ):
            resolved_tools = self._remote_memory_mcp.attach(resolved_tools, context.identity)
            mcp_servers = dict(resolved_tools.mcp_servers)
            allowed_tools = list(resolved_tools.allowed_tools)
            builtin_tools = list(resolved_tools.builtin_tools)
        if remote_transport and knowledge_bindings:
            if self._remote_knowledge_mcp is None:
                raise ToolResolutionError(
                    "remote knowledge MCP is unavailable for pinned Session knowledge"
                )
            assert context.identity is not None
            resolved_tools = self._remote_knowledge_mcp.attach(
                resolved_tools,
                context.identity,
                knowledge_bindings,
            )
            mcp_servers = dict(resolved_tools.mcp_servers)
            allowed_tools = list(resolved_tools.allowed_tools)
            builtin_tools = list(resolved_tools.builtin_tools)
        child_resolutions: dict[str, ResolvedTools] = {}
        resolution_by_hash: dict[str, ResolvedTools] = {}
        server_owner: dict[str, str] = {name: self._snapshot.content_hash for name in mcp_servers}
        result_trust = dict(resolved_tools.result_trust)
        sensitive_names = set(resolved_tools.sensitive_names)
        sensitive_values = set(resolved_tools.sensitive_values)
        for name, snapshot in subagent_snapshots.items():
            child_resolved = resolution_by_hash.get(snapshot.content_hash)
            if child_resolved is None:
                child_resolved = await self._tool_resolver.resolve(
                    snapshot.manifest,
                    context.identity,
                    python_tool_overrides=cast(Any, python_overrides(snapshot)),
                    tolerate_unavailable_mcp=True,
                )
                child_resolved = enforce_published_tool_directory(
                    snapshot,
                    child_resolved,
                )
                resolution_by_hash[snapshot.content_hash] = child_resolved
            child_resolutions[name] = child_resolved
            for server_name, config in child_resolved.mcp_servers.items():
                owner = server_owner.get(server_name)
                if owner is not None and owner != snapshot.content_hash:
                    raise ToolResolutionError(
                        f"MCP server name conflicts across Lead/Sub Agents: {server_name}"
                    )
                if owner is None:
                    mcp_servers[server_name] = config
                    server_owner[server_name] = snapshot.content_hash
            allowed_tools.extend(child_resolved.allowed_tools)
            result_trust.update(child_resolved.result_trust)
            sensitive_names.update(child_resolved.sensitive_names)
            sensitive_values.update(child_resolved.sensitive_values)
        allowed_tools = list(dict.fromkeys(allowed_tools))
        # In local Colima validation the worker container is the execution
        # boundary. Keep SDK-native builtins so Read retains multimodal image
        # support, while the command executor is reserved for custom operators.
        sandbox_proxy_enabled = (
            context.sandbox_command_executor is not None and context.sandbox_provider != "local"
        )
        if sandbox_proxy_enabled:
            if remote_transport:
                raise ToolResolutionError(
                    "deferred sandbox tools require the Claude CLI to run in the worker"
                )
            declared_builtins = set(builtin_tools)
            for snapshot in subagent_snapshots.values():
                declared_builtins.update(
                    tool.builtin
                    for tool in snapshot.manifest.spec.tools
                    if tool.builtin is not None
                )
            unsupported = declared_builtins - SANDBOX_BUILTINS - COORDINATION_BUILTINS
            if unsupported:
                names = ", ".join(sorted(unsupported))
                raise ToolResolutionError(
                    f"builtins cannot run through deferred sandbox tools: {names}"
                )
            proxied = declared_builtins.intersection(SANDBOX_BUILTINS)
            if proxied:
                if SANDBOX_MCP_SERVER_NAME in mcp_servers:
                    raise ToolResolutionError(
                        f"duplicate MCP server name: {SANDBOX_MCP_SERVER_NAME}"
                    )
                assert context.sandbox_command_executor is not None
                mcp_servers[SANDBOX_MCP_SERVER_NAME] = create_sandbox_tools_mcp_server(
                    context.sandbox_command_executor,
                    proxied,
                )
                for builtin in sorted(proxied):
                    allowed_tools.append(proxy_tool_name(builtin))
                allowed_tools = list(dict.fromkeys(allowed_tools))
            builtin_tools = [
                builtin for builtin in builtin_tools if builtin in COORDINATION_BUILTINS
            ]
        if not remote_transport:
            # The production container runs as an unprivileged user whose HOME
            # is the read-only application directory. Claude CLI needs a
            # writable config directory to create the transcript files that
            # back SessionStore mirror frames. Keep it inside the disposable
            # Run workspace and remove it before workspace archival.
            runtime_config_dir = context.workspace / ".harness-runtime" / "claude-config"
            runtime_config_dir.mkdir(parents=True, exist_ok=True)
            environment["CLAUDE_CONFIG_DIR"] = str(runtime_config_dir)
        in_process_servers = tuple(
            name
            for name, config in mcp_servers.items()
            if cast(dict[str, object], config).get("type") == "sdk"
        )
        if remote_transport and in_process_servers:
            names = ", ".join(sorted(in_process_servers))
            raise ToolResolutionError(
                "in-process Python SDK MCP tools cannot run through a remote "
                f"sandbox transport ({names}); expose them as authenticated HTTP MCP"
            )
        # SDK MCP servers hold live Python objects and are valid only when the
        # Claude CLI is a child of this worker. A Daytona CLI is a separate
        # process on a separate host, so durable memory is projected read-only
        # and artifacts are collected from files unless an HTTP MCP is used.
        if self._memory_bank is not None and not remote_transport:
            if "harness-memory" in mcp_servers:
                raise ToolResolutionError("duplicate MCP server name: harness-memory")
            mcp_servers["harness-memory"] = create_memory_mcp_server()
            allowed_tools.append("mcp__harness-memory__propose_memory")
        if knowledge_bindings and not remote_transport:
            if self._knowledge is None:
                raise ToolResolutionError(
                    "knowledge service is unavailable for pinned Session knowledge"
                )
            if "harness-knowledge" in mcp_servers:
                raise ToolResolutionError("duplicate MCP server name: harness-knowledge")
            knowledge_tool = "mcp__harness-knowledge__query_knowledge_sources"
            mcp_servers["harness-knowledge"] = create_knowledge_mcp_server()
            allowed_tools.append(knowledge_tool)
            knowledge_trust = (
                ContextTrust.UNTRUSTED
                if any(item.trust is KnowledgeResultTrust.UNTRUSTED for item in knowledge_bindings)
                else ContextTrust.SENSITIVE
            )
            result_trust[knowledge_tool] = knowledge_trust
        if context.artifact_publisher is not None and not remote_transport:
            if "harness-artifacts" in mcp_servers:
                raise ToolResolutionError("duplicate MCP server name: harness-artifacts")
            mcp_servers["harness-artifacts"] = create_artifact_mcp_server()
            allowed_tools.append("mcp__harness-artifacts__publish_artifact")
        agents: dict[str, AgentDefinition] = {}
        subagent_bindings = {
            subagent.runtime_name: subagent for subagent in manifest.spec.subagents
        }
        for name in self._subagent_versions:
            snapshot = subagent_snapshots[name]
            subagent_manifest = snapshot.manifest
            binding = subagent_bindings.get(name)
            subagent_tools = [
                (
                    proxy_tool_name(tool.builtin)
                    if sandbox_proxy_enabled and tool.builtin in SANDBOX_BUILTINS
                    else tool.builtin
                )
                for tool in subagent_manifest.spec.tools
                if tool.builtin is not None
            ]
            subagent_tools.extend(child_resolutions[name].allowed_tools)
            agents[name] = AgentDefinition(
                description=(
                    binding.description
                    if binding is not None and binding.description is not None
                    else f"Delegated {name} agent"
                ),
                prompt=(f"{snapshot.system_prompt.rstrip()}\n\n{VISIBLE_EXECUTION_CONTRACT}"),
                tools=subagent_tools,
                model="inherit",
                maxTurns=subagent_manifest.spec.limits.max_turns,
                skills=[skill.name for skill in snapshot.skill_snapshots] or None,
                background=binding.background if binding is not None else False,
            )
        resolved_tools = replace(
            resolved_tools,
            mcp_servers=MappingProxyType(mcp_servers),
            allowed_tools=tuple(dict.fromkeys(allowed_tools)),
            result_trust=MappingProxyType(result_trust),
            sensitive_names=frozenset(sensitive_names),
            sensitive_values=frozenset(sensitive_values),
        )
        store = cast(SessionStore, self._session_store) if self._session_store is not None else None
        options = ClaudeAgentOptions(
            tools=builtin_tools,
            # allowed_tools are unconditional permission grants in Claude Code.
            # Leave them empty in Auto mode so its classifier remains the
            # second gate after Harness policy instead of being shadowed.
            allowed_tools=[] if permission_mode == "auto" else allowed_tools,
            mcp_servers=mcp_servers,
            system_prompt=(
                f"{self._snapshot.system_prompt.rstrip()}\n\n{VISIBLE_EXECUTION_CONTRACT}"
            ),
            model=route.model,
            fallback_model=None,
            cwd=context.workspace,
            max_turns=manifest.spec.limits.max_turns,
            max_budget_usd=manifest.spec.limits.max_budget_usd,
            permission_mode=permission_mode,
            include_partial_messages=True,
            strict_mcp_config=True,
            agents=agents or None,
            hooks=(
                self._tool_gate.hooks(
                    context,
                    policy_id=manifest.spec.permissions.policy,
                    subagent_policy_ids={
                        name: snapshot.manifest.spec.permissions.policy
                        for name, snapshot in subagent_snapshots.items()
                    },
                    result_trust_by_tool=resolved_tools.result_trust,
                    delegate_allowed_to_sdk_permissions=(permission_mode == "auto"),
                )
                if self._tool_gate is not None
                else None
            ),
            skills=list(skill_names),
            env=environment,
            session_store=store,
            session_store_flush="eager",
            resume=context.session.resolved_runtime_thread_id,
            stderr=SdkDiagnosticTail(),
            # Native Read returns image blocks as base64 inside one SDK JSON
            # message. The upstream 1 MiB default rejects ordinary phone
            # photos before a vision-capable model can inspect them.
            max_buffer_size=SDK_JSON_MAX_BUFFER_SIZE,
        )
        return options, resolved_tools

    @staticmethod
    def _redact_event(event: RuntimeEvent, resolved_tools: ResolvedTools) -> RuntimeEvent:
        if not resolved_tools.sensitive_names and not resolved_tools.sensitive_values:
            return event
        payload = cast(
            dict[str, Any],
            redact_mcp_credentials(
                event.payload,
                sensitive_names=resolved_tools.sensitive_names,
                sensitive_values=resolved_tools.sensitive_values,
            ),
        )
        return event.model_copy(update={"payload": payload})

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        timeout_seconds = self._snapshot.manifest.spec.limits.timeout_seconds
        if timeout_seconds is None:
            async for event in self._execute(context):
                yield event
            return
        timeout = asyncio.timeout(timeout_seconds)
        try:
            async with timeout:
                async for event in self._execute(context):
                    yield event
        except TimeoutError as error:
            if not timeout.expired():
                raise
            raise RuntimeExecutionTimeoutError(
                f"Agent runtime exceeded {timeout_seconds} seconds"
            ) from error

    async def _execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        model = self._snapshot.manifest.spec.model
        raw_override = context.run.input.get("model_route_override")
        route_override = raw_override if isinstance(raw_override, str) else None
        run_capabilities = context.run.input.get("required_model_capabilities")
        required_capabilities = set(model.required_capabilities)
        if isinstance(run_capabilities, list):
            required_capabilities.update(
                value for value in cast(list[object], run_capabilities) if isinstance(value, str)
            )
        decision = self._router.resolve(
            route_override or model.route,
            required_capabilities=frozenset(required_capabilities),
            fallback_route_id=None if route_override is not None else model.fallback_route,
        )
        yield RuntimeEvent(
            type="model.route.selected",
            payload={
                **decision.event_payload,
                "permission_mode": permission_mode_for_route(decision.route),
                "selection_source": (
                    "task_override" if route_override is not None else "agent_default"
                ),
                "agent_default_route": model.route,
            },
        )
        prompt = str(context.run.input.get("prompt", ""))
        if context.memory_projection:
            prompt = f"<user_memory>\n{context.memory_projection}\n</user_memory>\n\n{prompt}"
        if context.context_projection:
            prompt = (
                f"{context.context_projection}\n\n"
                f"<current_user_request>\n{prompt}\n</current_user_request>"
            )
        if context.input_files:
            processed = set(context.processed_input_paths)
            originals = tuple(path for path in context.input_files if path not in processed)
            inventory_sections: list[str] = []
            if context.processed_input_paths:
                processed_inventory = "\n".join(
                    f"- {path}" for path in context.processed_input_paths
                )
                inventory_sections.append(
                    "Preferred model-readable representations:\n"
                    f"{processed_inventory}\n"
                    "Read these exact relative paths first. When a processed "
                    "representation is listed, do not call Read on its source "
                    "PDF or Office binary."
                )
            if originals:
                original_inventory = "\n".join(f"- {path}" for path in originals)
                inventory_sections.append(
                    "Original uploads:\n"
                    f"{original_inventory}\n"
                    "Read an original directly only when no processed "
                    "representation exists, such as for an image."
                )
            prompt = (
                f"{prompt}\n\n"
                "Browser-uploaded input files are available in this run workspace:\n"
                f"{'\n\n'.join(inventory_sections)}\n"
                "Use the available file tools to inspect them when relevant."
            )
        with self._span(
            "harness.mcp.resolve",
            run_id=context.run.run_id,
            attributes={
                "harness.policy.profile": self._snapshot.manifest.spec.permissions.policy,
                "harness.declared_tool.count": len(self._snapshot.manifest.spec.tools),
                "harness.tool.exposure_mode": (self._snapshot.manifest.spec.tool_exposure_mode),
            },
        ):
            options, resolved_tools = await self._options(context, decision.route)
            if self._observability is not None:
                self._observability.annotate_current_span(
                    {
                        "harness.resolved_builtin.count": len(resolved_tools.builtin_tools),
                        "harness.resolved_mcp.count": len(resolved_tools.mcp_servers),
                        "harness.tool.directory_hash": (
                            self._snapshot.tool_directory.content_hash
                            if self._snapshot.tool_directory is not None
                            else "legacy-eager"
                        ),
                    }
                )
        if self._snapshot.tool_directory is not None:
            yield RuntimeEvent(
                type="tool.directory.loaded",
                payload={
                    "exposure_mode": (self._snapshot.tool_directory.exposure_mode),
                    "catalog_revision": (self._snapshot.tool_directory.catalog_revision),
                    "content_hash": self._snapshot.tool_directory.content_hash,
                    "entry_count": len(self._snapshot.tool_directory.entries),
                },
            )
        if resolved_tools.unavailable_mcp:
            yield RuntimeEvent(
                type="tool.directory.degraded",
                payload={
                    "references": sorted(resolved_tools.unavailable_mcp),
                    "tool_count": sum(
                        len(tools) for tools in resolved_tools.unavailable_mcp.values()
                    ),
                    "reason": "credential_unavailable",
                },
            )
        subagent_governor = SubagentRuntimeGovernor(
            root=self._snapshot,
            subagent_versions=self._subagent_versions,
            observability=self._observability,
        )
        partial_text_seen = False
        stream_message_open = False
        pending_text = ""
        first_text_delta_flushed = False
        pending_task_terminals: dict[str, RuntimeEvent] = {}
        context_window_outcome_seen = False
        with ExitStack() as execution_context:
            execution_context.callback(
                shutil.rmtree,
                context.workspace / ".harness-runtime",
                ignore_errors=True,
            )
            if self._memory_bank is not None and context.identity is not None:
                execution_context.enter_context(
                    memory_execution_context(self._memory_bank, context.identity)
                )
            if (
                self._knowledge is not None
                and context.identity is not None
                and context.session.knowledge_snapshot_bindings
            ):
                execution_context.enter_context(
                    knowledge_execution_context(
                        self._knowledge,
                        context.identity,
                        tuple(
                            KnowledgeSnapshotBinding.model_validate(item)
                            for item in context.session.knowledge_snapshot_bindings
                        ),
                    )
                )
            if context.artifact_publisher is not None:
                execution_context.enter_context(
                    artifact_execution_context(context.artifact_publisher)
                )
            if context.runtime_transport_factory is None:
                query_messages = self._query(prompt, options)
            else:
                transport = context.runtime_transport_factory(options)
                if not isinstance(transport, Transport):
                    raise TypeError("runtime transport factory did not return an SDK Transport")
                # Use the bidirectional Client even for remote transports so
                # resumed Runs can issue the same bounded context-usage
                # control request as local execution. The one-shot query()
                # helper exposes only model messages and made exact remote
                # window governance impossible by construction.
                query_messages = _client_query(
                    prompt,
                    options,
                    transport=transport,
                    context_usage_timeout_seconds=(REMOTE_CONTEXT_USAGE_CONTROL_TIMEOUT_SECONDS),
                )
            async for message in self._model_messages(
                query_messages,
                run_id=context.run.run_id,
                route=decision.route,
                prompt=prompt,
            ):
                if (
                    isinstance(message, ResultMessage)
                    and not message.is_error
                    and message.stop_reason == "end_turn"
                    and options.resume is not None
                    and not context_window_outcome_seen
                ):
                    # Custom query factories and remote transports may not
                    # expose the SDK control API. The terminal result is the
                    # last event the Worker accepts, so publish capability
                    # availability immediately before it.
                    context_window_outcome_seen = True
                    yield ContextWindowUnavailable(
                        phase="after",
                        reason="control_unavailable",
                    ).event()
                if isinstance(message, (ContextWindowObservation, ContextWindowUnavailable)):
                    context_window_outcome_seen = True
                mapped = (
                    [message.event()]
                    if isinstance(
                        message,
                        (
                            ContextWindowObservation,
                            ContextWindowUnavailable,
                            SessionResumeRecovery,
                        ),
                    )
                    else [
                        self._redact_event(event, resolved_tools)
                        for event in map_sdk_message(message)
                    ]
                )
                if isinstance(message, TaskUpdatedMessage):
                    immediate: list[RuntimeEvent] = []
                    for event in mapped:
                        if event.type in {
                            "runtime.task.completed",
                            "runtime.task.failed",
                        }:
                            task_id = str(event.payload.get("task_id", ""))
                            if task_id:
                                pending_task_terminals[task_id] = event
                                continue
                        immediate.append(event)
                    mapped = immediate
                else:
                    for event in mapped:
                        if event.type in {
                            "runtime.task.completed",
                            "runtime.task.failed",
                        }:
                            task_id = str(event.payload.get("task_id", ""))
                            if task_id:
                                pending_task_terminals.pop(task_id, None)
                if isinstance(message, ResultMessage) and pending_task_terminals:
                    mapped = [*pending_task_terminals.values(), *mapped]
                    pending_task_terminals.clear()
                governed: list[RuntimeEvent] = []
                for event in mapped:
                    governed.extend(
                        subagent_governor.process(
                            event,
                            run_id=context.run.run_id,
                        )
                    )
                mapped = governed
                if isinstance(message, ResultMessage) and subagent_governor.active_tasks:
                    mapped = [
                        *subagent_governor.fail_unfinished(
                            reason="missing_terminal_event",
                            run_id=context.run.run_id,
                        ),
                        *mapped,
                    ]
                if self._tool_gate is not None:
                    mapped = [event for event in mapped if event.type != "tool.request"]
                if isinstance(message, StreamEvent):
                    for event in mapped:
                        if event.type == "message.start":
                            if not stream_message_open:
                                stream_message_open = True
                                first_text_delta_flushed = False
                                yield event
                        elif event.type == "message.delta":
                            partial_text_seen = True
                            if not stream_message_open:
                                stream_message_open = True
                                first_text_delta_flushed = False
                                yield RuntimeEvent(type="message.start")
                            text = str(event.payload.get("text", ""))
                            if text and not first_text_delta_flushed:
                                # TTFT takes priority over event coalescing. Flush the
                                # provider's first visible text immediately, then batch
                                # later character-sized deltas to avoid one durable DB
                                # event per token.
                                first_text_delta_flushed = True
                                yield RuntimeEvent(
                                    type="message.delta",
                                    payload={"text": text},
                                )
                                continue
                            pending_text += text
                            should_flush = len(pending_text) >= _TEXT_DELTA_FLUSH_CHARS or (
                                len(pending_text) >= _TEXT_DELTA_PUNCTUATION_CHARS
                                and pending_text[-1:] in _TEXT_DELTA_BOUNDARIES
                            )
                            if should_flush:
                                yield RuntimeEvent(
                                    type="message.delta",
                                    payload={"text": pending_text},
                                )
                                pending_text = ""
                        elif event.type == "message.completed":
                            if stream_message_open:
                                if pending_text:
                                    yield RuntimeEvent(
                                        type="message.delta",
                                        payload={"text": pending_text},
                                    )
                                    pending_text = ""
                                stream_message_open = False
                                yield event
                        else:
                            if pending_text:
                                yield RuntimeEvent(
                                    type="message.delta",
                                    payload={"text": pending_text},
                                )
                                pending_text = ""
                            yield event
                    continue
                if isinstance(message, ResultMessage) and stream_message_open:
                    if pending_text:
                        yield RuntimeEvent(type="message.delta", payload={"text": pending_text})
                        pending_text = ""
                    stream_message_open = False
                    yield RuntimeEvent(type="message.completed")
                if isinstance(message, AssistantMessage):
                    if partial_text_seen:
                        if pending_text:
                            yield RuntimeEvent(type="message.delta", payload={"text": pending_text})
                            pending_text = ""
                        for event in mapped:
                            if event.type != "message.delta":
                                yield event
                        partial_text_seen = False
                        continue
                    contains_text = any(event.type == "message.delta" for event in mapped)
                    if contains_text:
                        yield RuntimeEvent(type="message.start")
                    for event in mapped:
                        yield event
                    if contains_text:
                        yield RuntimeEvent(type="message.completed")
                    continue
                for event in mapped:
                    yield event
        for event in pending_task_terminals.values():
            for governed_event in subagent_governor.process(
                event,
                run_id=context.run.run_id,
            ):
                yield governed_event
        for event in subagent_governor.fail_unfinished(
            reason="stream_closed",
            run_id=context.run.run_id,
        ):
            yield event
        if stream_message_open:
            if pending_text:
                yield RuntimeEvent(type="message.delta", payload={"text": pending_text})
            yield RuntimeEvent(type="message.completed")
