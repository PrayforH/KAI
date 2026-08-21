"""Resolve a published AgentVersion before delegating to Codex app-server."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

from harness.core.errors import ConflictError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentRuntimeType
from harness.core.ports import AgentRegistry
from harness.deployments.boundaries import enforce_runtime_environment
from harness.runtime.base import AgentRuntime, RuntimeContext, RuntimeEvent
from harness.runtime.codex_runtime import (
    CodexAppServerRuntime,
    CodexProcessFactory,
    CodexRuntimeConfig,
    CodexServerRequestHandler,
)
from harness.runtime.execution_contract import VISIBLE_EXECUTION_CONTRACT


class RegistryCodexRuntime:
    """Build a per-Agent Codex runtime from the immutable published snapshot."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        codex_path: Path,
        model_by_route: Mapping[str, str] | None = None,
        provider_by_route: Mapping[str, str] | None = None,
        environment: Mapping[str, str] | None = None,
        approval_policy: str = "untrusted",
        sandbox_mode: str = "workspace-write",
        network_access: bool = False,
        process_factory: CodexProcessFactory | None = None,
        server_request_handler: CodexServerRequestHandler | None = None,
    ) -> None:
        self._registry = registry
        self._codex_path = codex_path
        self._model_by_route = dict(model_by_route or {})
        self._provider_by_route = dict(provider_by_route or {})
        self._environment = environment
        self._approval_policy = approval_policy
        self._sandbox_mode = sandbox_mode
        self._network_access = network_access
        self._process_factory = process_factory
        self._server_request_handler = server_request_handler

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
        model = self._model_by_route.get(model_spec.route, model_spec.model)
        provider = self._provider_by_route.get(model_spec.route)
        runtime = CodexAppServerRuntime(
            CodexRuntimeConfig(
                codex_path=self._codex_path,
                model=model,
                model_provider=provider,
                developer_instructions=(
                    f"{snapshot.system_prompt.rstrip()}\n\n{VISIBLE_EXECUTION_CONTRACT}"
                ),
                environment=self._environment,
                approval_policy=self._approval_policy,
                sandbox_mode=self._sandbox_mode,
                network_access=self._network_access,
                turn_timeout_seconds=snapshot.manifest.spec.limits.timeout_seconds,
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
                "route_id": model_spec.route,
                "provider": provider or "codex",
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
