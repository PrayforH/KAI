from dataclasses import replace
from types import MappingProxyType
from typing import cast

import pytest
from claude_agent_sdk import McpSdkServerConfig, McpServerConfig
from claude_agent_sdk.types import McpHttpServerConfig
from pydantic import SecretStr

from harness.core.manifest import (
    AgentManifest,
    AgentManifestSnapshot,
    ToolDirectoryEntry,
    ToolDirectorySnapshot,
)
from harness.core.models import ExecutionIdentity
from harness.policy.models import ContextTrust
from harness.runtime.mcp_credentials import CredentialValues, McpCredentialError
from harness.runtime.tools import (
    McpServerRegistration,
    ResolvedTools,
    ToolResolutionError,
    ToolResolver,
    enforce_published_tool_directory,
)


class UnexpectedCredentialProvider:
    async def resolve(
        self,
        server_reference: str,
        identity: ExecutionIdentity,
        required_keys: frozenset[str],
    ) -> CredentialValues:
        del server_reference, identity, required_keys
        raise AssertionError("credential provider must not run for an unauthenticated MCP")


class MissingCredentialProvider:
    async def resolve(
        self,
        server_reference: str,
        identity: ExecutionIdentity,
        required_keys: frozenset[str],
    ) -> CredentialValues:
        del identity, required_keys
        raise McpCredentialError(f"missing MCP credentials: {server_reference}.api_key")


def manifest_fixture(*tools: dict[str, str]) -> AgentManifest:
    return AgentManifest.model_validate(
        {
            "apiVersion": "harness/v1alpha1",
            "kind": "Agent",
            "metadata": {"name": "domain-agent", "version": "0.1.0"},
            "spec": {
                "runtime": "claude-agent-sdk",
                "model": {"route": "default", "model": "gateway-model"},
                "prompt": {"system": "prompts/system.md"},
                "tools": list(tools),
                "permissions": {"policy": "policies/default.yaml"},
            },
        }
    )


@pytest.mark.asyncio
async def test_preserves_unique_builtin_tools_in_declaration_order() -> None:
    resolved = await ToolResolver().resolve(
        manifest_fixture({"builtin": "Read"}, {"builtin": "Bash"}, {"builtin": "Read"})
    )

    assert resolved.builtin_tools == ("Read", "Bash")
    assert resolved.mcp_servers == {}
    assert resolved.allowed_tools == ()


@pytest.mark.asyncio
async def test_wraps_python_sdk_tools_in_one_in_process_server() -> None:
    resolved = await ToolResolver().resolve(
        manifest_fixture({"python": "tests.fixtures.runtime.domain_tools:tool_list"})
    )

    server = cast(McpSdkServerConfig, resolved.mcp_servers["harness-python"])
    assert server["type"] == "sdk"
    assert server["name"] == "harness-python"
    assert resolved.allowed_tools == (
        "mcp__harness-python__lookup_customer",
        "mcp__harness-python__summarize_account",
    )


@pytest.mark.asyncio
async def test_resolves_external_mcp_from_server_owned_registry() -> None:
    config = cast(
        McpServerConfig,
        {"type": "http", "url": "https://mcp.example.test"},
    )
    resolver = ToolResolver(
        mcp_registry={
            "crm": McpServerRegistration(
                server_name="crm-prod",
                config=config,
                allowed_tools=("mcp__crm-prod__search",),
            )
        }
    )

    resolved = await resolver.resolve(manifest_fixture({"mcp": "crm"}))

    assert resolved.mcp_servers == {"crm-prod": config}
    assert resolved.allowed_tools == ("mcp__crm-prod__search",)
    assert resolved.result_trust == {"mcp__crm-prod__search": ContextTrust.UNTRUSTED}


@pytest.mark.asyncio
async def test_merges_static_routing_headers_with_managed_credentials() -> None:
    config = cast(
        McpServerConfig,
        {"type": "http", "url": "https://mcp.example.test"},
    )

    class CredentialProvider:
        async def resolve(
            self,
            server_reference: str,
            identity: ExecutionIdentity,
            required_keys: frozenset[str],
        ) -> CredentialValues:
            del server_reference, identity
            assert required_keys == frozenset({"api_key"})
            return MappingProxyType({"api_key": SecretStr("managed-secret")})

    resolver = ToolResolver(
        mcp_registry={
            "crm": McpServerRegistration(
                server_name="crm-prod",
                config=config,
                static_headers=(("X-Tenant-ID", "tenant-public"),),
                credential_headers=(("Authorization", "api_key"),),
                credential_header_prefixes=(("Authorization", "Bearer "),),
            )
        },
        credential_provider=CredentialProvider(),
    )
    identity = ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-a",
        project_id="project-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="domain-agent",
        agent_version="0.1.0",
    )

    resolved = await resolver.resolve(manifest_fixture({"mcp": "crm"}), identity)

    server = cast(McpHttpServerConfig, resolved.mcp_servers["crm-prod"])
    assert server.get("headers") == {
        "X-Tenant-ID": "tenant-public",
        "Authorization": "Bearer managed-secret",
    }


def test_rejects_static_headers_that_conflict_with_managed_authentication() -> None:
    with pytest.raises(ToolResolutionError, match="conflicts with a credential header"):
        McpServerRegistration(
            server_name="crm",
            config={"type": "http", "url": "https://mcp.example.test"},
            static_headers=(("authorization", "public-value"),),
            credential_headers=(("Authorization", "api_key"),),
        )


@pytest.mark.asyncio
async def test_unauthenticated_mcp_does_not_request_a_credential_lease() -> None:
    config = cast(
        McpServerConfig,
        {"type": "http", "url": "https://mcp.example.test"},
    )
    resolver = ToolResolver(
        mcp_registry={
            "public-docs": McpServerRegistration(
                server_name="public-docs",
                config=config,
                allowed_tools=("mcp__public-docs__search",),
            )
        },
        credential_provider=UnexpectedCredentialProvider(),
    )

    resolved = await resolver.resolve(manifest_fixture({"mcp": "public-docs"}))

    assert resolved.mcp_servers == {"public-docs": config}


@pytest.mark.asyncio
async def test_runtime_can_degrade_only_an_mcp_with_unavailable_credentials() -> None:
    resolver = ToolResolver(
        mcp_registry={
            "search": McpServerRegistration(
                server_name="search",
                config={"type": "http", "url": "https://mcp.example.test"},
                allowed_tools=("mcp__search__query",),
                credential_headers=(("Authorization", "api_key"),),
            )
        },
        credential_provider=MissingCredentialProvider(),
    )
    manifest = manifest_fixture({"builtin": "Read"}, {"mcp": "search"})
    identity = ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-a",
        project_id="project-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="domain-agent",
        agent_version="0.1.0",
    )

    with pytest.raises(McpCredentialError, match="search.api_key"):
        await resolver.resolve(manifest, identity)

    resolved = await resolver.resolve(
        manifest,
        identity,
        tolerate_unavailable_mcp=True,
    )

    assert resolved.builtin_tools == ("Read",)
    assert resolved.mcp_servers == {}
    assert resolved.allowed_tools == ()
    assert resolved.unavailable_mcp == {
        "search": ("mcp__search__query",),
    }


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("missing", "MCP tool registration is not configured: missing"),
        ("no-colon", "invalid python tool reference: no-colon"),
        (
            "tests.fixtures.runtime.domain_tools:missing",
            "cannot load python tool: tests.fixtures.runtime.domain_tools:missing",
        ),
        (
            "tests.fixtures.runtime.domain_tools:not_a_tool",
            "python tool export must be an SdkMcpTool or a sequence of SdkMcpTool",
        ),
    ],
)
@pytest.mark.asyncio
async def test_rejects_unresolvable_tool_references(reference: str, message: str) -> None:
    key = "mcp" if reference == "missing" else "python"

    with pytest.raises(ToolResolutionError, match=message):
        await ToolResolver().resolve(manifest_fixture({key: reference}))


@pytest.mark.asyncio
async def test_rejects_duplicate_python_tool_names() -> None:
    with pytest.raises(
        ToolResolutionError,
        match="duplicate python tool name: lookup_customer",
    ):
        await ToolResolver().resolve(
            manifest_fixture({"python": "tests.fixtures.runtime.domain_tools:duplicate_tools"})
        )


@pytest.mark.asyncio
async def test_rejects_inline_registry_secrets() -> None:
    secret = "registry-super-secret"
    config = cast(
        McpServerConfig,
        {"type": "http", "url": "https://mcp.example.test", "headers": {"X-Key": secret}},
    )
    with pytest.raises(ToolResolutionError) as captured:
        McpServerRegistration(server_name="same", config=config)

    assert str(captured.value) == "MCP registrations cannot contain inline headers or environment"
    assert secret not in repr(captured.value)


@pytest.mark.parametrize(
    "config",
    (
        {"type": "stdio", "command": "arbitrary-server"},
        {"type": "http", "url": "file:///tmp/server.sock"},
        {"type": "http", "url": "https://user:secret@mcp.example.test"},
    ),
)
def test_rejects_non_http_or_credentialed_mcp_registration(
    config: dict[str, object],
) -> None:
    with pytest.raises(
        ToolResolutionError,
        match=r"must use a registered HTTP\(S\) endpoint",
    ):
        McpServerRegistration(
            server_name="unsafe",
            config=cast(McpServerConfig, config),
        )


def test_published_tool_directory_filters_additions_and_rejects_missing_tools() -> None:
    manifest = manifest_fixture({"builtin": "Read"}, {"mcp": "crm"}).model_copy(
        update={
            "spec": manifest_fixture({"builtin": "Read"}, {"mcp": "crm"}).spec.model_copy(
                update={"tool_exposure_mode": "on_demand"}
            )
        }
    )
    directory = ToolDirectorySnapshot.create(
        catalog_revision=3,
        exposure_mode="on_demand",
        entries=(
            ToolDirectoryEntry(
                name="Read",
                source="builtin",
                logicalReference="Read",
                description="Read isolated workspace files.",
                risk="low",
                resultTrust="safe",
            ),
            ToolDirectoryEntry(
                name="mcp__crm-prod__search",
                source="mcp",
                logicalReference="crm",
                description="Search reviewed CRM records.",
                risk="medium",
                resultTrust="sensitive",
            ),
        ),
    )
    snapshot = AgentManifestSnapshot(
        manifest=manifest,
        system_prompt="Use only reviewed tools.",
        tool_directory=directory,
        content_hash="a" * 64,
    )

    def resolved(
        *,
        builtins: tuple[str, ...] = ("Read",),
        allowed: tuple[str, ...] = ("mcp__crm-prod__search",),
    ) -> ResolvedTools:
        return ResolvedTools(
            builtin_tools=builtins,
            mcp_servers=MappingProxyType({}),
            allowed_tools=allowed,
            mcp_smokes=MappingProxyType({}),
            result_trust=MappingProxyType({tool: ContextTrust.SENSITIVE for tool in allowed}),
        )

    unchanged = enforce_published_tool_directory(snapshot, resolved())
    assert unchanged.allowed_tools == ("mcp__crm-prod__search",)
    with pytest.raises(ToolResolutionError, match="builtin tools differ"):
        enforce_published_tool_directory(snapshot, resolved(builtins=("Read", "Bash")))
    with pytest.raises(
        ToolResolutionError,
        match="published MCP tools are no longer available.*mcp__crm-prod__search",
    ):
        enforce_published_tool_directory(snapshot, resolved(allowed=()))
    degraded = enforce_published_tool_directory(
        snapshot,
        replace(
            resolved(allowed=()),
            unavailable_mcp=MappingProxyType({"crm": ("mcp__crm-prod__search",)}),
        ),
    )
    assert degraded.allowed_tools == ()
    filtered = enforce_published_tool_directory(
        snapshot,
        resolved(
            allowed=(
                "mcp__crm-prod__search",
                "mcp__crm-prod__delete",
            )
        ),
    )
    assert filtered.allowed_tools == ("mcp__crm-prod__search",)
    assert filtered.result_trust == {"mcp__crm-prod__search": ContextTrust.SENSITIVE}
