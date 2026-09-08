# pyright: reportPrivateUsage=false

import json
import os
import subprocess
import sys
from dataclasses import replace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from harness.api.app import create_app, create_configured_app
from harness.api.dependencies import build_memory_container
from harness.composition import (
    _manifests_require_remote_cli,
    build_production_container,
)
from harness.config import Settings
from harness.core.manifest import AgentManifest
from harness.core.models import ExecutionIdentity
from harness.execution.credentials import BrokerMcpCredentialProvider, InMemoryCredentialBroker
from harness.runtime.registry_runtime import RegistryClaudeRuntime
from harness.runtime.tools import ToolResolver
from harness.sandbox.deferred import DeferredToolSandboxProvider
from harness.sandbox.e2b import E2BSandboxProvider
from harness.sandbox.kubernetes import KubernetesSandboxProvider
from harness.storage.catalog_repository import PostgresCapabilityCatalogRepository
from harness.storage.redis import RedisTaskQueue
from harness.storage.repositories import PostgresEventRepository
from harness.storage.studio_repository import PostgresAgentDraftRepository
from harness.studio.mcp_credential_store import (
    InMemoryMcpCredentialRepository,
    McpCredentialService,
    StoredMcpCredentialProvider,
)
from harness.studio.preflight import LivePreflightProvisioner, LivePreflightRunner


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "runtime": "claude-sdk",
        "sandbox_provider": "local",
        "allow_unsafe_local_sandbox": True,
        "api_bearer_token": SecretStr("a" * 32),
        "new_api_base_url": "https://gateway.example",
        "new_api_model": "deepseek-chat",
        "new_api_key": SecretStr("model-secret"),
        "minio_access_key": SecretStr("minio-access"),
        "minio_secret_key": SecretStr("minio-secret"),
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportArgumentType]


def tavily_manifest() -> AgentManifest:
    return AgentManifest.model_validate(
        {
            "apiVersion": "harness/v1alpha1",
            "kind": "Agent",
            "metadata": {"name": "web-agent", "version": "1.0.0"},
            "spec": {
                "runtime": "claude-agent-sdk",
                "model": {"route": "default", "model": "gateway-model"},
                "prompt": {"system": "prompts/system.md"},
                "tools": [{"mcp": "tavily-readonly"}],
                "permissions": {"policy": "default"},
            },
        }
    )


def manifest_with_tools(*tools: dict[str, str]) -> AgentManifest:
    payload = tavily_manifest().model_dump(by_alias=True)
    payload["spec"]["tools"] = list(tools)
    return AgentManifest.model_validate(payload)


def execution_identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-a",
        project_id="web-agent",
        session_id="session-a",
        run_id="run-a",
        agent_name="web-agent",
        agent_version="1.0.0",
    )


def test_deferred_lane_accepts_chat_files_and_registered_read_only_mcp() -> None:
    assert (
        _manifests_require_remote_cli(
            (
                manifest_with_tools(
                    {"builtin": "Read"},
                    {"builtin": "Bash"},
                    {"mcp": "company-search"},
                ),
            ),
            read_only_mcp_references=frozenset({"company-search"}),
        )
        is False
    )


@pytest.mark.parametrize(
    "tools",
    (
        ({"python": "bundle:tools/report.py:TOOLS"},),
        ({"mcp": "company-write"},),
    ),
)
def test_python_and_non_read_only_mcp_force_remote_cli(
    tools: tuple[dict[str, str], ...],
) -> None:
    assert (
        _manifests_require_remote_cli(
            (manifest_with_tools(*tools),),
            read_only_mcp_references=frozenset({"company-search"}),
        )
        is True
    )


def test_python_tool_in_subagent_forces_whole_run_to_remote_cli() -> None:
    assert (
        _manifests_require_remote_cli(
            (
                manifest_with_tools({"mcp": "company-search"}),
                manifest_with_tools({"python": "bundle:tools/report.py:TOOLS"}),
            ),
            read_only_mcp_references=frozenset({"company-search"}),
        )
        is True
    )


@pytest.mark.asyncio
async def test_production_container_uses_durable_event_and_queue_adapters() -> None:
    container = build_production_container(
        production_settings(
            new_api_compatibility="degraded",
            new_api_capabilities="streaming",
        )
    )

    try:
        assert isinstance(container.events, PostgresEventRepository)
        assert isinstance(container.agent_drafts, PostgresAgentDraftRepository)
        assert isinstance(
            vars(container.capability_catalogs)["_repository"],
            PostgresCapabilityCatalogRepository,
        )
        assert isinstance(container.task_queue, RedisTaskQueue)
        assert container.auto_execute is False
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        assert vars(runtime)["_config"] is None
        assert vars(runtime)["_route_configs"] == ()
        assert vars(runtime)["_model_configurations"] is container.model_configurations
        imported = vars(container.model_configurations)["_server_routes"]
        assert set(imported) == {"deepseek-v4-flash", "deepseek-v4-pro"}
        assert imported["deepseek-v4-flash"].compatibility.value == "degraded"
        assert imported["deepseek-v4-flash"].capabilities == frozenset({"streaming"})
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_container_can_disable_all_quota_enforcement() -> None:
    container = build_production_container(production_settings(quota_enforcement_enabled=False))

    try:
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        tool_gate = vars(runtime)["_tool_gate"]
        workspaces = vars(container.worker)["_workspaces"]

        # Keep the quota control plane available for visibility and future
        # production policy, while removing every runtime enforcement hook.
        assert container.quotas is not None
        assert vars(container.runs)["_admission"] is None
        assert vars(container.previews)["_quotas"] is None
        assert vars(container.preview_controller)["_quotas"] is None
        assert vars(container.deployments)["_quotas"] is None
        assert vars(container.artifacts)["_quotas"] is None
        assert vars(workspaces)["_quotas"] is None
        assert vars(tool_gate)["_quotas"] is None
        assert vars(container.worker)["_quotas"] is None
        assert vars(container.reliability_controller)["_quotas"] is None
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_composition_uses_server_owned_mcp_registry() -> None:
    container = build_production_container(
        production_settings(
            mcp_secret_references_json=json.dumps(
                {"tavily-readonly": {"api_key": "TAVILY_API_KEY"}}
            ),
            mcp_server_secrets_json=SecretStr(json.dumps({"TAVILY_API_KEY": "production-key"})),
        )
    )
    try:
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        resolver = cast(ToolResolver, vars(runtime)["_tool_resolver"])
        provider = vars(resolver)["_credential_provider"]

        assert isinstance(provider, StoredMcpCredentialProvider)
        assert isinstance(vars(provider)["_fallback"], BrokerMcpCredentialProvider)
        broker = cast(InMemoryCredentialBroker, vars(runtime)["_credential_broker"])
        assert isinstance(broker, InMemoryCredentialBroker)
        assert vars(broker)["_connection_authorizer"] is container.governance
        assert vars(container.worker)["_credential_revoker"] is not None

        # This construction-only unit test does not start the PostgreSQL fixture.
        # Connection authorization is covered by governance repository integration tests.
        vars(broker)["_connection_authorizer"] = None
        credential_service = cast(McpCredentialService, vars(provider)["_service"])
        credential_service.repository = InMemoryMcpCredentialRepository()
        resolved = await resolver.resolve(tavily_manifest(), execution_identity())

        tavily = cast(dict[str, object], resolved.mcp_servers["tavily"])
        assert tavily.get("url") == "https://mcp.tavily.com/mcp/"
        assert tavily.get("headers") == {"Authorization": "Bearer production-key"}
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_container_starts_without_gateway_credentials() -> None:
    container = build_production_container(
        production_settings(new_api_key=SecretStr(""), new_api_model="")
    )
    try:
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        assert vars(runtime)["_config"] is None
        assert vars(runtime)["_model_configurations"] is container.model_configurations
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_container_imports_empty_gateway_capabilities() -> None:
    container = build_production_container(
        production_settings(new_api_capabilities=" , ")
    )
    try:
        imported = vars(container.model_configurations)["_server_routes"]
        assert imported["deepseek-v4-flash"].capabilities == frozenset()
    finally:
        assert container.close is not None
        await container.close()


def test_production_container_rejects_implicit_local_sandbox() -> None:
    with pytest.raises(ValueError, match="ALLOW_UNSAFE_LOCAL_SANDBOX"):
        build_production_container(production_settings(allow_unsafe_local_sandbox=False))


@pytest.mark.asyncio
async def test_production_container_wires_e2b_provider() -> None:
    container = build_production_container(
        production_settings(
            sandbox_provider="e2b",
            allow_unsafe_local_sandbox=False,
            e2b_api_key=SecretStr("e2b-test-key"),
        )
    )
    try:
        assert isinstance(vars(container.worker)["_sandbox"], E2BSandboxProvider)
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_container_can_defer_remote_sandbox_until_tool_use() -> None:
    container = build_production_container(
        production_settings(
            sandbox_provider="e2b",
            sandbox_execution_mode="worker_cli_deferred",
            allow_unsafe_local_sandbox=False,
            e2b_api_key=SecretStr("e2b-test-key"),
        )
    )
    try:
        assert isinstance(
            vars(container.worker)["_sandbox"],
            DeferredToolSandboxProvider,
        )
        preflight = cast(
            LivePreflightProvisioner,
            vars(container.preview_controller)["_provisioner"],
        )
        runner = cast(LivePreflightRunner, vars(preflight)["_runner"])
        assert isinstance(vars(runner)["_sandbox"], E2BSandboxProvider)
    finally:
        assert container.close is not None
        await container.close()


def test_deferred_execution_rejects_unsafe_local_backend() -> None:
    with pytest.raises(ValueError, match="requires Daytona, E2B, or Kubernetes"):
        build_production_container(
            production_settings(sandbox_execution_mode="worker_cli_deferred")
        )


def test_production_container_requires_e2b_key() -> None:
    with pytest.raises(ValueError, match="HARNESS_E2B_API_KEY"):
        build_production_container(
            production_settings(
                sandbox_provider="e2b",
                allow_unsafe_local_sandbox=False,
                e2b_api_key=SecretStr(""),
            )
        )


@pytest.mark.asyncio
async def test_production_container_wires_kubernetes_reaper_without_local_fallback() -> None:
    container = build_production_container(
        production_settings(
            sandbox_provider="kubernetes",
            allow_unsafe_local_sandbox=False,
            kubernetes_image="registry.example/sandbox@sha256:" + "b" * 64,
            kubernetes_egress_proxy_url="http://proxy.harness-system.svc:3128",
        )
    )
    try:
        assert isinstance(vars(container.worker)["_sandbox"], KubernetesSandboxProvider)
        assert container.sandbox_maintenance is not None
        assert vars(container.worker)["_sandbox_resolver"] is not None
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_composition_ignores_legacy_anthropic_environment() -> None:
    container = build_production_container(
        production_settings(
            anthropic_api_key=SecretStr("anthropic-secret"),
            anthropic_model="claude-fallback",
        )
    )
    try:
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        assert vars(runtime)["_fallback_config"] is None
        assert vars(runtime)["_route_configs"] == ()
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_composition_imports_minimax_into_model_control_plane() -> None:
    container = build_production_container(
        production_settings(
            minimax_m3_base_url="https://api.minimaxi.com/anthropic",
            minimax_m3_api_key=SecretStr("minimax-secret"),
        )
    )
    try:
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        assert vars(runtime)["_config"] is None
        assert vars(runtime)["_fallback_config"] is None
        assert vars(runtime)["_route_configs"] == ()
        imported = vars(container.model_configurations)["_server_routes"]
        assert imported["minimax-m3"].base_url == "https://api.minimaxi.com/anthropic"
        assert imported["minimax-m3"].model == "MiniMax-M3"
    finally:
        assert container.close is not None
        await container.close()


@pytest.mark.asyncio
async def test_production_composition_imports_glm_into_model_control_plane() -> None:
    container = build_production_container(
        production_settings(
            glm_5_2_base_url="http://172.20.109.112:31300",
            glm_5_2_api_key=SecretStr("glm-secret"),
        )
    )
    try:
        runtime = cast(RegistryClaudeRuntime, container.runtime)
        assert vars(runtime)["_config"] is None
        assert vars(runtime)["_route_configs"] == ()
        imported = vars(container.model_configurations)["_server_routes"]
        assert imported["glm-5-2"].base_url == "http://172.20.109.112:31300"
        assert imported["glm-5-2"].model == "shdata-glm"
    finally:
        assert container.close is not None
        await container.close()


def test_configured_app_selects_production_composition() -> None:
    app = create_configured_app(production_settings())

    assert isinstance(app.state.container.events, PostgresEventRepository)
    assert app.state.container.auto_execute is False


def test_production_app_fails_fast_without_strong_api_credential() -> None:
    with pytest.raises(ValueError, match="HARNESS_API_BEARER_TOKEN"):
        create_configured_app(production_settings(api_bearer_token=SecretStr("short")))


def test_app_lifespan_closes_composed_resources() -> None:
    closed = False

    async def close() -> None:
        nonlocal closed
        closed = True

    container = replace(build_memory_container(), close=close)

    with TestClient(create_app(container)):
        pass

    assert closed is True


def test_production_composition_imports_in_clean_worker_process() -> None:
    environment = {
        **os.environ,
        "HARNESS_ENVIRONMENT": "production",
        "HARNESS_RUNTIME": "claude-sdk",
        "HARNESS_NEW_API_BASE_URL": "https://gateway.example",
        "HARNESS_NEW_API_MODEL": "deepseek-chat",
        "HARNESS_NEW_API_KEY": "model-secret",
        "HARNESS_MINIO_ACCESS_KEY": "minio-access",
        "HARNESS_MINIO_SECRET_KEY": "minio-secret",
    }

    result = subprocess.run(
        [sys.executable, "-c", "import harness.composition"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
