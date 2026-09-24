"""Execute one Run as a DeepAgents graph.

The platform's third execution kernel. It is assembled from the same
``DeepagentsPlan`` the code view and the project export render, so the source a
user reads in the console is the source this module runs.

Five platform invariants shape it:

* **The Harness alone flips Run state.** ``interrupt_on`` is never enabled;
  approvals are handled by ``DeepagentsToolGate`` through
  ``ApprovalService(inline=True)``.
* **Every outward fact is a durable ``RunEvent``.** The stream is translated
  into the platform's existing vocabulary by ``DeepagentsStreamMapper``; no
  side channel is introduced.
* **The workspace is real.** ``HarnessSandboxBackend`` routes DeepAgents' file
  semantics through the platform Sandbox, so the workspace can be archived,
  fingerprinted and published like any other Run's.
* **The Manifest is the tool ceiling.** The graph is built from the published
  snapshot only, and ``plan.filesystem_tools`` withholds ``delete``.
* **File-based memory is never enabled.** ``create_deep_agent(memory=...)``
  would read ``AGENTS.md`` from the workspace, bypassing the platform memory
  bank and its trust rules.

Session continuity is deliberately left to the platform: the runtime never
binds a runtime thread, so the Worker keeps replaying the session's durable
history into each Run instead of trusting graph state the platform cannot see.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from deepagents import FilesystemMiddleware, create_deep_agent
from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import SecretStr

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.context.service import ContextService
from harness.core.errors import ConflictError
from harness.core.manifest import (
    AgentManifestSnapshot,
    PythonToolSnapshot,
    materialize_skill_snapshot_set,
)
from harness.observability.model_span import model_observation, model_run_facts
from harness.observability.provider import Observability
from harness.policy.profiles import PolicyProfileRegistry
from harness.policy.rules import PolicyEngine
from harness.quota.service import QuotaService
from harness.runtime.base import (
    RuntimeContext,
    RuntimeEvent,
    RuntimeExecutionTimeoutError,
    RuntimeResultError,
)
from harness.runtime.deepagents_backend import HarnessSandboxBackend
from harness.runtime.deepagents_events import DeepagentsStreamMapper
from harness.runtime.deepagents_plan import DeepagentsPlan, build_deepagents_plan
from harness.runtime.deepagents_tool_gate import DeepagentsToolGate
from harness.runtime.execution_contract import VISIBLE_EXECUTION_CONTRACT
from harness.runtime.sandbox_tools import run_bundle_python_tool

# Where the platform materializes immutable Skills. Deliberately the same root
# the Claude runtime uses: the platform already redacts any tool call that reads
# `.claude/skills/`, so a Skill body can never reach the durable event stream,
# and one location keeps that guarantee for both runtimes.
SKILL_ROOT = ".claude/skills"

_MCP_TRANSPORT = "streamable_http"
# The two text protocols a DeepAgents route may speak. The catalog advertises
# exactly these, and the compiler rejects any other route before publish, so the
# runtime can dispatch on the protocol instead of guessing from the model name.
_ANTHROPIC_API_FORMAT = "anthropic_compatible"
_OPENAI_API_FORMAT = "openai_compatible"


@dataclass(frozen=True)
class BundleOperator:
    """One declared Studio Bundle operator, materialized and canonically named.

    The wrapper resolves it because ``ToolResolver`` refuses to run without the
    operators staged, and the canonical name only exists once the published tool
    directory has been applied.
    """

    tool: PythonToolSnapshot
    name: str
    path: Path


@dataclass(frozen=True)
class DeepagentsRuntimeConfig:
    """The published Agent facts a graph needs, already resolved.

    Building this is the registry wrapper's job: by the time a graph is
    assembled, the model route, its credential, the declared tool ceiling and
    the MCP registrations are all decided, so this runtime never reads a
    registry or a control plane.

    ``api_format`` picks the SDK; ``provider`` is only the routing identity the
    console reports. ``bundle_operators`` arrive already materialized and named,
    so the runtime neither re-derives a tool name nor writes to the workspace.
    """

    snapshot: AgentManifestSnapshot
    route_id: str
    provider: str
    api_format: str
    model: str
    base_url: str
    api_key: SecretStr = field(repr=False)
    mcp_servers: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    declared_tools: frozenset[str] = frozenset()
    bundle_operators: tuple[BundleOperator, ...] = ()
    # The published package hash, carried so a DeepAgents trace describes the
    # same Agent identity a Claude trace does.
    package_hash: str | None = None


class _NoSubagentsMiddleware(AgentMiddleware):
    """Replace the default delegation slot instead of inheriting a child.

    DeepAgents would otherwise install a general-purpose subagent that the
    platform never resolved, governed or counted. Claiming the slot by name is
    how the graph states that this Agent has no children.
    """

    @property
    def name(self) -> str:
        return "SubAgentMiddleware"


class _McpToolsMiddleware(AgentMiddleware):
    """Attach the published streamable-HTTP MCP tools on the first model call.

    Discovery is a remote round-trip, so it cannot happen while the graph is
    being built. Deferring it keeps graph construction synchronous and costs at
    most one discovery per Run.

    Discovered tools are renamed to the platform's canonical
    ``mcp__<server>__<tool>`` form, which is the name the published tool
    directory, the policy engine and the quota ledger all use.
    """

    def __init__(
        self,
        connections: Mapping[str, Mapping[str, object]],
        declared_tools: frozenset[str],
    ) -> None:
        self._connections = {name: dict(config) for name, config in connections.items()}
        self._declared_tools = declared_tools
        self._tools: list[Any] | None = None

    @property
    def name(self) -> str:
        return "harness-mcp-tools"

    def _client(self) -> Any:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        return MultiServerMCPClient(
            {
                server: {
                    "transport": _MCP_TRANSPORT,
                    "url": str(config["url"]),
                    "headers": {
                        str(header): str(value)
                        for header, value in cast(
                            Mapping[str, object], config.get("headers") or {}
                        ).items()
                    },
                }
                for server, config in self._connections.items()
            }
        )

    async def _load(self) -> list[Any]:
        if self._tools is None:
            client = self._client()
            discovered: list[Any] = []
            for server in self._connections:
                for tool in await client.get_tools(server_name=server):
                    name = f"mcp__{server}__{tool.name}"
                    if name not in self._declared_tools:
                        continue
                    discovered.append(tool.model_copy(update={"name": name}))
            self._tools = discovered
        return self._tools

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        tools = await self._load()
        if not tools:
            return await handler(request)
        return await handler(request.override(tools=[*request.tools, *tools]))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        # Tools discovered after graph compilation are absent from ToolNode's
        # static registry. Supply the reviewed instance, then continue through
        # the remaining middleware so the platform gate still authorizes it.
        if request.tool is None:
            name = request.tool_call["name"]
            tool = next((tool for tool in await self._load() if tool.name == name), None)
            if tool is not None:
                request = request.override(tool=tool)
        return await handler(request)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        raise RuntimeError(
            "MCP tools are loaded asynchronously; invoke this graph with "
            "astream()/ainvoke() instead of the sync path."
        )


class DeepagentsRuntime:
    """Run one Agent as a DeepAgents graph inside the platform's Sandbox."""

    def __init__(
        self,
        *,
        config: DeepagentsRuntimeConfig,
        approvals: ApprovalService,
        events: EventService,
        quotas: QuotaService | None = None,
        context_service: ContextService | None = None,
        observability: Observability | None = None,
        policy: PolicyEngine | None = None,
        policy_profiles: PolicyProfileRegistry | None = None,
    ) -> None:
        # Forwarded, not consumed: the gate built in `_build_graph` is what
        # authorizes each tool call, and it owns the exactly-one contract.
        self._config = config
        self._approvals = approvals
        self._events = events
        self._quotas = quotas
        self._context_service = context_service
        self._observability = observability
        self._policy = policy
        self._policy_profiles = policy_profiles

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        config = self._config
        spec = config.snapshot.manifest.spec
        if context.sandbox_command_executor is None:
            raise ConflictError(
                "the DeepAgents runtime keeps its workspace in a Sandbox; "
                "configure a Sandbox provider that exposes command execution"
            )
        plan = build_deepagents_plan(
            builtin_tools=tuple(tool.builtin for tool in spec.tools if tool.builtin),
            permission_policy=spec.permissions.policy,
            model=spec.model.model,
            api_format=config.api_format,
            max_turns=spec.limits.max_turns,
            timeout_seconds=spec.limits.timeout_seconds,
            with_mcp=bool(config.mcp_servers),
            with_skills=bool(config.snapshot.skill_snapshots),
        )
        graph = self._build_graph(
            context,
            plan=plan,
            backend=HarnessSandboxBackend(
                context.sandbox_command_executor,
                sandbox_id=context.run.run_id,
                # The same root the tool gate resolves model paths against, so a
                # path the gate authorizes as `outputs/report.md` is the path
                # this backend writes -- not a second, nested copy of it.
                remote_workspace=context.remote_workspace,
                timeout_seconds=float(plan.shell_timeout),
                file_plane=context.sandbox_file_plane,
            ),
        )
        mapper = DeepagentsStreamMapper(model=config.model, provider=config.provider)
        for event in mapper.start_events(route_id=config.route_id):
            yield event

        tool_call_limit = spec.limits.max_tool_calls
        tool_calls = 0
        started_at = time.monotonic()
        timeout = float(spec.limits.timeout_seconds) if spec.limits.timeout_seconds else None
        prompt = self._prompt(context)
        # The same observation every runtime opens, so Langfuse shows a
        # generation for a DeepAgents Run too. DeepAgents has no SDK permission
        # mode, so that attribute is simply not reported.
        facts = model_run_facts(
            config.snapshot,
            run_id=context.run.run_id,
            route_id=config.route_id,
            model=config.model,
            provider=config.provider,
            package_hash=config.package_hash,
        )
        with model_observation(self._observability, facts, input_value=prompt) as observation:
            # The Worker treats the last completed message as the Run's answer, so
            # the observation reports that same string rather than every delta the
            # run produced -- the two must not disagree about what was said.
            active_text: list[str] = []
            final_text = ""
            try:
                async with asyncio.timeout(timeout):
                    async for mode, payload in graph.astream(
                        {"messages": [{"role": "user", "content": prompt}]},
                        config={"recursion_limit": plan.recursion_limit},
                        stream_mode=["messages", "updates"],
                    ):
                        for event in self._map(mapper, mode=mode, payload=payload):
                            if event.type == "tool.request":
                                tool_calls += 1
                                if tool_call_limit is not None and tool_calls > tool_call_limit:
                                    raise RuntimeResultError(
                                        "deepagents_tool_call_limit",
                                        error_code="deepagents_tool_call_limit",
                                        user_message=(
                                            "本次运行的工具调用超过上限 "
                                            f"{tool_call_limit}，已终止。"
                                        ),
                                    )
                                # Counted, then withheld: the gate is the only writer
                                # of this fact. It names the tool in the platform
                                # vocabulary and redacts the arguments against that
                                # name, whereas this copy would carry the runtime's
                                # own name (`write_file`, not `Write`). The worker's
                                # generic policy pass keys both its re-decision and
                                # its redaction on that name, so a second copy makes
                                # it deny a call the gate already allowed -- and
                                # record the unredacted arguments while doing it.
                                # `ClaudeSdkRuntime` withholds it for the same
                                # reason.
                                continue
                            if event.type == "message.start":
                                active_text = []
                            elif event.type == "message.delta":
                                active_text.append(str(event.payload.get("text") or ""))
                            elif event.type == "message.completed":
                                final_text = "".join(active_text)
                            yield event
            except TimeoutError as error:
                raise RuntimeExecutionTimeoutError(
                    "the DeepAgents runtime exceeded its Manifest timeout"
                ) from error
            # Reported from the same payload the Worker consumes, so the
            # observation and the durable `runtime.result` cannot disagree about
            # what the run cost.
            result = mapper.result_event(
                duration_ms=max(0, round((time.monotonic() - started_at) * 1000))
            )
            observation.record_result(
                usage=cast(Mapping[str, int], result.payload.get("usage") or {}),
                duration_ms=cast(int, result.payload.get("duration_ms")),
                turns=cast(int, result.payload.get("num_turns")),
                stop_reason=cast(str | None, result.payload.get("stop_reason")),
                output=final_text or None,
            )
        yield result

    @staticmethod
    def _map(
        mapper: DeepagentsStreamMapper,
        *,
        mode: str,
        payload: object,
    ) -> list[RuntimeEvent]:
        if mode == "messages":
            chunk, metadata = cast(tuple[object, Mapping[str, Any]], payload)
            return mapper.messages(chunk, metadata)
        return mapper.updates(cast(Mapping[str, Any], payload))

    def _prompt(self, context: RuntimeContext) -> str:
        """Compose the Run's user message from the platform's projections.

        The memory projection is intentionally absent: DeepAgents v1 does not
        connect the platform memory bank, so injecting it would claim a
        capability the runtime does not have.
        """

        prompt = str(context.run.input.get("prompt", ""))
        if context.context_projection:
            prompt = (
                f"{context.context_projection}\n\n"
                f"<current_user_request>\n{prompt}\n</current_user_request>"
            )
        if context.input_files:
            inventory = "\n".join(f"- {path}" for path in context.input_files)
            prompt = (
                f"{prompt}\n\n"
                "Browser-uploaded input files are available in this run workspace:\n"
                f"{inventory}\n"
                "Read them with the available file tools when relevant."
            )
        return prompt

    def _build_graph(
        self,
        context: RuntimeContext,
        *,
        plan: DeepagentsPlan,
        backend: HarnessSandboxBackend,
    ) -> CompiledStateGraph[Any, Any, Any, Any]:
        config = self._config
        snapshot = config.snapshot
        if snapshot.skill_snapshots:
            # Materializing is what makes a Skill readable; the returned names
            # are the Claude runtime's concern, not this one's.
            materialize_skill_snapshot_set((snapshot,), context.workspace)
        middleware: list[AgentMiddleware] = [
            TodoListMiddleware(),
            # Tool selection withholds `delete`. Read-only enforcement belongs
            # to DeepagentsToolGate and the Run's resolved platform policy:
            # DeepAgents 0.7.13 rejects `_permissions` on Sandbox backends even
            # when the execute tool is omitted. The standalone export uses a
            # non-executing backend for that configuration and keeps its rules.
            FilesystemMiddleware(
                backend=backend,
                tools=list(plan.filesystem_tools),
            ),
        ]
        if config.mcp_servers:
            middleware.append(_McpToolsMiddleware(config.mcp_servers, config.declared_tools))
        middleware.append(_NoSubagentsMiddleware())
        middleware.append(
            DeepagentsToolGate(
                context=context,
                approvals=self._approvals,
                events=self._events,
                policy=self._policy,
                profiles=self._policy_profiles,
                quotas=self._quotas,
                context_service=self._context_service,
                observability=self._observability,
                declared_tools=config.declared_tools | frozenset(context.platform_tools.names),
            )
        )
        return create_deep_agent(
            model=self._chat_model(),
            tools=[*self._bundle_tools(context), *self._platform_tools(context)],
            system_prompt=f"{snapshot.system_prompt.rstrip()}\n\n{VISIBLE_EXECUTION_CONTRACT}\n{context.platform_tools.instructions}",
            middleware=middleware,
            skills=[SKILL_ROOT] if snapshot.skill_snapshots else None,
            backend=backend,
            name=snapshot.manifest.metadata.name,
        ).with_config(recursion_limit=plan.recursion_limit)

    def _chat_model(self) -> BaseChatModel:
        """Build the chat model for the route's resolved wire protocol.

        Only the selected protocol's SDK is imported, so an Agent on an
        OpenAI-compatible route never pays for loading the Anthropic client.
        """

        config = self._config
        # Both clients accept a SecretStr, so pass the wrapper through instead of
        # unwrapping it: unwrapping would put the raw key into this frame, where a
        # repr or a traceback could capture it.
        api_key = config.api_key
        if config.api_format == _ANTHROPIC_API_FORMAT:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=config.model,
                base_url=config.base_url,
                api_key=api_key,
            )
        if config.api_format == _OPENAI_API_FORMAT:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=config.model,
                base_url=config.base_url,
                api_key=api_key,
            )
        raise ConflictError(
            f"DeepAgents has no text model for the route protocol: {config.api_format}"
        )

    @staticmethod
    def _platform_tools(context: RuntimeContext) -> list[StructuredTool]:
        def adapt(tool, name):
            async def invoke(**arguments: Any) -> str:
                result = await tool.handler(arguments)
                # Preserve failures as actionable tool results for model correction.
                return json.dumps(result, ensure_ascii=False)
            return StructuredTool.from_function(coroutine=invoke, name=name,
                description=tool.description, args_schema=tool.schema)
        return [adapt(tool, name) for tool, name in zip(
            context.platform_tools.tools, context.platform_tools.names, strict=True
        )]

    def _bundle_tools(self, context: RuntimeContext) -> list[StructuredTool]:
        """Expose Studio Bundle operators while executing them in the Sandbox.

        The export renders the same operators as plain in-process tools, which
        is correct for a project the user runs themselves. On the platform the
        operator is user-authored code, so it keeps the Claude runtime's
        posture and runs inside the Sandbox — never in the Worker.
        """

        operators = self._config.bundle_operators
        if not operators:
            return []
        executor = context.sandbox_command_executor
        assert executor is not None
        return [self._bundle_tool(operator=operator, executor=executor) for operator in operators]

    @staticmethod
    def _bundle_tool(*, operator: BundleOperator, executor: Any) -> StructuredTool:
        async def invoke(**arguments: Any) -> dict[str, Any]:
            return await run_bundle_python_tool(
                snapshot=operator.tool,
                materialized_path=operator.path,
                executor=executor,
                arguments=arguments,
            )

        return StructuredTool.from_function(
            coroutine=invoke,
            name=operator.name,
            description=operator.tool.description,
            args_schema=operator.tool.input_schema,
        )
