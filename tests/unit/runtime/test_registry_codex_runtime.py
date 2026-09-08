from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import cast

import pytest
from claude_agent_sdk import McpServerConfig
from pydantic import SecretStr

from harness.adapters.memory import InMemoryAgentRegistry
from harness.core.manifest import load_manifest
from harness.core.models import AgentVersion, AgentVersionStatus, Run, RunStatus, Session
from harness.runtime.base import RuntimeContext
from harness.runtime.codex_app_server import CodexAppServerOptions
from harness.runtime.codex_protocol import CodexMessage, CodexMessageKind
from harness.runtime.codex_runtime import CodexProcess, CodexRpcConnection
from harness.runtime.registry_codex_runtime import (
    RegistryCodexRuntime,
    _codex_mcp_configuration,
)
from harness.runtime.tools import ResolvedTools, ToolResolutionError
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.mcp_credential_store import (
    InMemoryMcpCredentialRepository,
    McpCredentialCipher,
    McpCredentialService,
)
from harness.studio.model_configuration import (
    BindAgentModelRequest,
    ConfigureModelRequest,
    ModelConfigurationService,
)
from harness.studio.repositories import InMemoryAgentDraftRepository


class _Client:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, object]]] = []

    async def request(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> object:
        del timeout_seconds
        values = dict(params or {})
        self.requests.append((method, values))
        if method == "thread/start":
            return {"thread": {"id": "thread-control-plane"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-control-plane"}}
        raise AssertionError(f"unexpected request: {method}")

    async def respond(self, request_id: int | str, result: object) -> None:
        del request_id, result

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
    ) -> None:
        del request_id, code, message

    async def inbound(self) -> AsyncIterator[CodexMessage]:
        yield CodexMessage(
            CodexMessageKind.NOTIFICATION,
            {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed"}},
            },
        )


class _Process:
    def __init__(self, client: _Client) -> None:
        self.client: CodexRpcConnection | None = cast(CodexRpcConnection, client)

    async def start(self) -> Mapping[str, object]:
        return {}

    async def close(self) -> None:
        return None


def test_codex_mcp_configuration_uses_env_headers_and_exact_tool_allowlist() -> None:
    resolved = ResolvedTools(
        builtin_tools=("Read",),
        mcp_servers=MappingProxyType(
            {
                "sentiment-query": cast(
                    McpServerConfig,
                    {
                        "type": "http",
                        "url": "https://mcp.example.test/mcp",
                        "headers": {
                            "Authorization": "Bearer private-token",
                            "X-Tenant": "tenant-a",
                        },
                    },
                )
            }
        ),
        allowed_tools=(
            "mcp__sentiment-query__search_risk_subjects",
            "mcp__sentiment-query__query_legal_entity_directory",
        ),
        mcp_smokes=MappingProxyType({}),
        sensitive_values=frozenset({"Bearer private-token"}),
    )

    overrides, environment = _codex_mcp_configuration(resolved)

    assert 'mcp_servers.sentiment-query.url="https://mcp.example.test/mcp"' in overrides
    assert "mcp_servers.sentiment-query.required=true" in overrides
    assert (
        "mcp_servers.sentiment-query.enabled_tools="
        '["search_risk_subjects","query_legal_entity_directory"]'
    ) in overrides
    assert (
        'mcp_servers.sentiment-query.tools.search_risk_subjects.approval_mode="approve"'
    ) in overrides
    assert set(environment.values()) == {"Bearer private-token", "tenant-a"}
    assert all("private-token" not in override for override in overrides)
    assert any("env_http_headers.Authorization" in override for override in overrides)


def test_codex_mcp_configuration_keeps_default_tavily_non_blocking() -> None:
    resolved = ResolvedTools(
        builtin_tools=(),
        mcp_servers=MappingProxyType(
            {
                "tavily": cast(
                    McpServerConfig,
                    {
                        "type": "http",
                        "url": "https://mcp.tavily.com/mcp/",
                        "headers": {"Authorization": "Bearer private-token"},
                    },
                )
            }
        ),
        allowed_tools=("mcp__tavily__tavily_search",),
        mcp_smokes=MappingProxyType({}),
        sensitive_values=frozenset({"Bearer private-token"}),
    )

    overrides, _environment = _codex_mcp_configuration(resolved)

    assert "mcp_servers.tavily.required=false" in overrides


def test_codex_mcp_configuration_rejects_query_string_credentials() -> None:
    resolved = ResolvedTools(
        builtin_tools=(),
        mcp_servers=MappingProxyType(
            {
                "private": cast(
                    McpServerConfig,
                    {
                        "type": "http",
                        "url": "https://mcp.example.test/mcp?token=private-token",
                    },
                )
            }
        ),
        allowed_tools=(),
        mcp_smokes=MappingProxyType({}),
        sensitive_values=frozenset({"private-token"}),
    )

    with pytest.raises(ToolResolutionError, match="query-string credentials"):
        _codex_mcp_configuration(resolved)


@pytest.mark.asyncio
async def test_control_plane_model_becomes_secret_safe_codex_provider(
    tmp_path: Path,
) -> None:
    snapshot = load_manifest("agents/helper-agent/agent.yaml")
    manifest = snapshot.manifest.model_copy(
        update={
            "metadata": snapshot.manifest.metadata.model_copy(
                update={
                    "labels": {
                        **snapshot.manifest.metadata.labels,
                        "codex-reasoning-effort": "low",
                    }
                }
            ),
            "spec": snapshot.manifest.spec.model_copy(
                update={
                    "runtime": "codex-app-server",
                    "model": snapshot.manifest.spec.model.model_copy(
                        update={"route": "codex-responses"}
                    ),
                }
            )
        }
    )
    snapshot = snapshot.model_copy(update={"manifest": manifest})
    registry = InMemoryAgentRegistry()
    await registry.add(
        AgentVersion(
            tenant_id="tenant-a",
            owner_user_id="user-a",
            name="helper-agent",
            version="1.0.0",
            status=AgentVersionStatus.PUBLISHED,
            manifest_hash=snapshot.content_hash,
            snapshot=snapshot.model_dump(mode="json"),
            created_at=datetime.now(UTC),
        )
    )
    catalogs = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
    )
    model_configurations = ModelConfigurationService(
        catalogs,
        McpCredentialService(
            InMemoryMcpCredentialRepository(),
            McpCredentialCipher(SecretStr("test-model-encryption")),
        ),
        environment="test",
    )
    configured = await model_configurations.configure(
        "tenant-a",
        "admin-a",
        "codex-responses",
        ConfigureModelRequest(
            expectedRevision=1,
            label="Codex Responses",
            modelType="chat",
            provider="Example Responses",
            model="gpt-control-plane",
            baseUrl="https://models.example.test/v1",
            apiFormat="openai_compatible",
            authScheme="bearer",
            apiKey=SecretStr("control-plane-secret"),
        ),
    )
    assert configured.revision == 2
    configured = await model_configurations.configure(
        "tenant-a",
        "admin-a",
        "claude-messages",
        ConfigureModelRequest(
            expectedRevision=configured.revision,
            label="Claude Messages",
            modelType="chat",
            provider="Example Messages",
            model="claude-control-plane",
            baseUrl="https://messages.example.test/v1",
            apiFormat="anthropic_compatible",
            authScheme="bearer",
            apiKey=SecretStr("claude-control-plane-secret"),
        ),
    )
    await model_configurations.bind_agent(
        "tenant-a",
        "admin-a",
        "helper-agent",
        BindAgentModelRequest(
            expectedRevision=configured.revision,
            routeId="claude-messages",
        ),
    )
    client = _Client()
    options_seen: list[CodexAppServerOptions] = []

    def process_factory(options: CodexAppServerOptions) -> CodexProcess:
        options_seen.append(options)
        return cast(CodexProcess, _Process(client))

    runtime = RegistryCodexRuntime(
        registry=registry,
        codex_path=tmp_path / "codex",
        model_configurations=model_configurations,
        environment={"PATH": "/usr/bin"},
        process_factory=process_factory,
    )
    now = datetime.now(UTC)
    context = RuntimeContext(
        run=Run(
            run_id="run-codex",
            session_id="session-codex",
            tenant_id="tenant-a",
            status=RunStatus.RUNNING,
            idempotency_key="codex",
            created_at=now,
            updated_at=now,
            input={"prompt": "respond"},
        ),
        session=Session(
            session_id="session-codex",
            tenant_id="tenant-a",
            user_id="developer",
            agent_owner_user_id="user-a",
            agent_name="helper-agent",
            agent_version="1.0.0",
            runtime_type="codex-app-server",
            created_at=now,
        ),
        workspace=tmp_path,
    )

    events = [event async for event in runtime.execute(context)]

    options = options_seen[0]
    assert options.environment is not None
    assert options.environment["HARNESS_CODEX_PROVIDER_API_KEY"] == ("control-plane-secret")
    assert options.environment["PATH"] == "/usr/bin"
    assert any(override == 'model_provider="agent_studio"' for override in options.config_overrides)
    assert any(
        override == 'model_providers.agent_studio.base_url="https://models.example.test/v1"'
        for override in options.config_overrides
    )
    assert all("control-plane-secret" not in value for value in options.config_overrides)
    assert all("claude-control-plane-secret" not in value for value in options.config_overrides)
    assert "agents.enabled=true" in options.config_overrides
    assert "agents.max_concurrent_threads_per_session=4" in options.config_overrides
    assert 'model_reasoning_effort="low"' in options.config_overrides
    assert "tool_output_token_limit=32000" in options.config_overrides
    thread_start = client.requests[0][1]
    assert thread_start["model"] == "gpt-control-plane"
    assert thread_start["modelProvider"] == "agent_studio"
    selected = events[0]
    assert selected.payload["route_id"] == "codex-responses"
    assert selected.payload["provider"] == "new-api"
    assert "control-plane-secret" not in repr(events)
