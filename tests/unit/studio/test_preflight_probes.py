from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
from claude_agent_sdk import McpServerConfig
from pydantic import SecretStr

from harness.core.manifest import AgentManifest
from harness.core.models import ExecutionIdentity, ModelCompatibility, Run, RunStatus
from harness.runtime.cc_switch import CcSwitchClaudeConfig
from harness.runtime.tools import (
    McpServerRegistration,
    McpSmokeCheck,
    ToolResolver,
)
from harness.sandbox.base import SandboxCommandResult, SandboxHandle
from harness.sandbox.local import LocalSandboxProvider
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler
from harness.studio.factory import create_draft_spec
from harness.studio.models import AgentDraft, AgentTemplate
from harness.studio.preflight_probes import (
    AnthropicSandboxModelProbe,
    PreflightCheckError,
    StreamableHttpMcpProbe,
)

NOW = datetime(2026, 7, 16, 10, tzinfo=UTC)
SECRET = "preflight-private-token-value"


class CommandSandbox(LocalSandboxProvider):
    def __init__(self, tmp_path: Path, result: SandboxCommandResult) -> None:
        super().__init__(root=tmp_path)
        self.result = result
        self.environment: dict[str, str] = {}

    async def execute(
        self,
        handle: SandboxHandle,
        argv: Any,
        *,
        environment: Any = None,
        timeout_seconds: float = 30,
    ) -> SandboxCommandResult:
        del handle, argv, timeout_seconds
        self.environment = dict(environment or {})
        return self.result


def manifest(*, mcp: bool = False) -> AgentManifest:
    spec = create_draft_spec(
        name="probe-agent",
        domain="probe",
        display_name="Probe Agent",
        description="Probe model and MCP protocol compatibility.",
        template=AgentTemplate.ANALYST,
    )
    if mcp:
        spec = spec.model_copy(update={"mcp_servers": ("tavily-readonly",)})
    draft = AgentDraft(
        draftId="draft-probe",
        tenantId="tenant-a",
        revision=1,
        spec=spec,
        createdBy="builder",
        updatedBy="builder",
        createdAt=NOW,
        updatedAt=NOW,
    )
    return AgentDraftCompiler(default_capability_catalog()).compile(
        draft
    ).report.snapshot.manifest


async def handle(sandbox: LocalSandboxProvider) -> SandboxHandle:
    return await sandbox.provision(
        Run(
            run_id="run-probe",
            session_id="session-probe",
            tenant_id="tenant-a",
            status=RunStatus.PROVISIONING,
            idempotency_key="probe",
            created_at=NOW,
            updated_at=NOW,
        )
    )


def gateway(
    *,
    compatibility: ModelCompatibility = ModelCompatibility.FULL,
    capabilities: frozenset[str] = frozenset({"streaming", "tool_use"}),
    auth_scheme: Literal["bearer", "x-api-key"] | None = None,
) -> CcSwitchClaudeConfig:
    return CcSwitchClaudeConfig(
        base_url="https://model.example.test/anthropic",
        model="test-model",
        provider="new-api",
        credential=SecretStr(SECRET),
        auth_scheme=auth_scheme,
        compatibility=compatibility,
        capabilities=capabilities,
    )


def valid_stream() -> str:
    return "\n".join(
        [
            'data: {"type":"message_start","message":{}}',
            'data: {"type":"content_block_start","content_block":{"type":"tool_use"}}',
            'data: {"type":"message_stop"}',
        ]
    )


@pytest.mark.asyncio
async def test_model_probe_requires_streaming_tool_use_without_leaking_secret(
    tmp_path: Path,
) -> None:
    sandbox = CommandSandbox(
        tmp_path, SandboxCommandResult(exit_code=0, stdout=valid_stream())
    )
    evidence = await AnthropicSandboxModelProbe(gateway()).verify(
        "tenant-a", manifest(), sandbox, await handle(sandbox)
    )

    assert evidence.details["streaming"] is True
    assert evidence.details["toolUse"] is True
    assert SECRET in sandbox.environment["HARNESS_PREFLIGHT_AUTH_HEADER"]
    request = json.loads(sandbox.environment["HARNESS_PREFLIGHT_REQUEST"])
    assert request["thinking"] == {"type": "disabled"}
    assert SECRET not in repr(evidence)


@pytest.mark.asyncio
async def test_model_probe_supports_anthropic_api_key_auth_for_compatible_gateway(
    tmp_path: Path,
) -> None:
    sandbox = CommandSandbox(
        tmp_path, SandboxCommandResult(exit_code=0, stdout=valid_stream())
    )

    await AnthropicSandboxModelProbe(gateway(auth_scheme="x-api-key")).verify(
        "tenant-a", manifest(), sandbox, await handle(sandbox)
    )

    assert sandbox.environment["HARNESS_PREFLIGHT_AUTH_HEADER"] == f"x-api-key: {SECRET}"


@pytest.mark.parametrize(
    ("config", "result", "error_code"),
    [
        (
            gateway(compatibility=ModelCompatibility.UNSUPPORTED),
            SandboxCommandResult(exit_code=0, stdout=valid_stream()),
            "model_incompatible",
        ),
        (
            gateway(capabilities=frozenset({"streaming"})),
            SandboxCommandResult(exit_code=0, stdout=valid_stream()),
            "model_capability_mismatch",
        ),
        (
            gateway(),
            SandboxCommandResult(exit_code=127, stderr="curl: not found"),
            "model_probe_dependency_missing",
        ),
        (
            gateway(),
            SandboxCommandResult(exit_code=23, stderr="credential rejected"),
            "model_unreachable",
        ),
        (
            gateway(),
            SandboxCommandResult(
                exit_code=0,
                stdout='data: {"type":"message_start"}\n'
                'data: {"type":"message_stop"}',
            ),
            "model_tool_use_unsupported",
        ),
    ],
)
@pytest.mark.asyncio
async def test_model_probe_returns_stable_compatibility_errors(
    tmp_path: Path,
    config: CcSwitchClaudeConfig,
    result: SandboxCommandResult,
    error_code: str,
) -> None:
    sandbox = CommandSandbox(tmp_path, result)
    with pytest.raises(PreflightCheckError) as captured:
        await AnthropicSandboxModelProbe(config).verify(
            "tenant-a", manifest(), sandbox, await handle(sandbox)
        )
    assert captured.value.error_code == error_code
    assert SECRET not in str(captured.value)


def mcp_resolver(
    transport: Literal["http", "sse"] = "http",
) -> ToolResolver:
    return ToolResolver(
        mcp_registry={
            "tavily-readonly": McpServerRegistration(
                server_name="tavily",
                config=cast(
                    McpServerConfig,
                    {"type": transport, "url": "https://mcp.example.test/mcp"},
                ),
                allowed_tools=("mcp__tavily__tavily_search",),
                preflight_smoke=McpSmokeCheck(
                    tool="tavily_search", arguments={"query": "probe"}
                ),
            )
        }
    )


def identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="builder",
        project_id="probe",
        session_id="preflight",
        run_id="preflight",
        agent_name="probe-agent",
        agent_version="0.1.0",
    )


@pytest.mark.asyncio
async def test_mcp_probe_reports_target_network_failure(tmp_path: Path) -> None:
    sandbox = CommandSandbox(
        tmp_path, SandboxCommandResult(exit_code=7, stderr="private network detail")
    )
    probe = StreamableHttpMcpProbe(mcp_resolver())

    with pytest.raises(PreflightCheckError) as captured:
        await probe.verify(manifest(mcp=True), identity(), sandbox, await handle(sandbox))

    assert captured.value.error_code == "mcp_target_network_unreachable"
    assert "private" not in str(captured.value)


@pytest.mark.parametrize(
    ("status", "error_code"),
    [
        ("401", "mcp_credentials_rejected"),
        ("404", "mcp_endpoint_not_found"),
        ("503", "mcp_target_unavailable"),
        ("invalid", "mcp_target_response_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_mcp_probe_maps_target_http_failures(
    tmp_path: Path, status: str, error_code: str
) -> None:
    sandbox = CommandSandbox(
        tmp_path, SandboxCommandResult(exit_code=0, stdout=status)
    )

    with pytest.raises(PreflightCheckError) as captured:
        await StreamableHttpMcpProbe(mcp_resolver()).verify(
            manifest(mcp=True), identity(), sandbox, await handle(sandbox)
        )

    assert captured.value.error_code == error_code


@pytest.mark.asyncio
async def test_mcp_probe_reports_tools_list_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def fake_transport(
        *_args: object, **_kwargs: object
    ) -> AsyncGenerator[tuple[object, object, Callable[[], None]]]:
        yield object(), object(), lambda: None

    class FakeSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(tools=[SimpleNamespace(name="different_tool")])

    monkeypatch.setattr(
        "harness.studio.preflight_probes.streamable_http_client", fake_transport
    )
    monkeypatch.setattr("harness.studio.preflight_probes.ClientSession", FakeSession)
    sandbox = CommandSandbox(
        tmp_path, SandboxCommandResult(exit_code=0, stdout="200")
    )

    with pytest.raises(PreflightCheckError) as captured:
        await StreamableHttpMcpProbe(mcp_resolver()).verify(
            manifest(mcp=True), identity(), sandbox, await handle(sandbox)
        )

    assert captured.value.error_code == "mcp_tool_mismatch"


@pytest.mark.asyncio
async def test_mcp_probe_supports_sse_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def fake_sse(
        *_args: object,
        **_kwargs: object,
    ) -> AsyncGenerator[tuple[object, object]]:
        yield object(), object()

    class FakeSession:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def list_tools(self) -> SimpleNamespace:
            return SimpleNamespace(
                tools=[SimpleNamespace(name="tavily_search")]
            )

        async def call_tool(
            self,
            _name: str,
            _arguments: dict[str, object],
        ) -> SimpleNamespace:
            return SimpleNamespace(isError=False)

    monkeypatch.setattr("harness.studio.preflight_probes.sse_client", fake_sse)
    monkeypatch.setattr("harness.studio.preflight_probes.ClientSession", FakeSession)
    sandbox = CommandSandbox(
        tmp_path,
        SandboxCommandResult(exit_code=0, stdout="200"),
    )

    result = await StreamableHttpMcpProbe(mcp_resolver("sse")).verify(
        manifest(mcp=True),
        identity(),
        sandbox,
        await handle(sandbox),
    )

    assert result.details == {"serverCount": 1, "toolCount": 1}
