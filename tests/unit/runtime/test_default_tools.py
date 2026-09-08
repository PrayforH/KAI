from collections.abc import Callable
from importlib import import_module, util
from typing import cast

import pytest
from pydantic import SecretStr

from harness.core.manifest import AgentManifest
from harness.core.models import ExecutionIdentity
from harness.policy.models import ContextTrust
from harness.runtime.mcp_credentials import (
    McpCredentialError,
    ServerSecretReferenceProvider,
)
from harness.runtime.tools import ToolResolutionError, ToolResolver
from harness.studio.catalog_repository import InMemoryCapabilityCatalogRepository
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.models import UpsertCatalogResourceRequest
from harness.studio.repositories import InMemoryAgentDraftRepository


def _manifest() -> AgentManifest:
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


def _identity(user_id: str = "user-a") -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a",
        user_id=user_id,
        project_id="web-agent",
        session_id="session-a",
        run_id="run-a",
        agent_name="web-agent",
        agent_version="1.0.0",
    )


def _factory() -> Callable[..., ToolResolver]:
    assert util.find_spec("harness.runtime.default_tools") is not None
    module = import_module("harness.runtime.default_tools")
    return cast(
        Callable[..., ToolResolver],
        vars(module)["default_tool_resolver"],
    )


@pytest.mark.asyncio
async def test_default_resolver_injects_tavily_bearer_key_and_exact_allowlist() -> None:
    provider = ServerSecretReferenceProvider(
        references={"tavily-readonly": {"api_key": "TAVILY_API_KEY"}},
        secrets={"TAVILY_API_KEY": SecretStr("test-key")},
    )

    resolved = await _factory()(provider).resolve(_manifest(), _identity())

    assert resolved.allowed_tools == (
        "mcp__tavily__tavily_search",
        "mcp__tavily__tavily_extract",
    )
    assert resolved.mcp_servers["tavily"] == {
        "type": "http",
        "url": "https://mcp.tavily.com/mcp/",
        "headers": {"Authorization": "Bearer test-key"},
    }
    assert resolved.sensitive_names == frozenset({"Authorization"})
    assert resolved.sensitive_values == frozenset({"Bearer test-key"})
    assert set(resolved.result_trust.values()) == {ContextTrust.UNTRUSTED}


@pytest.mark.asyncio
async def test_default_resolver_fails_before_execution_without_tavily_credentials() -> None:
    with pytest.raises(
        McpCredentialError,
        match=r"missing MCP credentials: tavily-readonly\.api_key",
    ):
        await _factory()(None).resolve(_manifest(), _identity())


@pytest.mark.asyncio
async def test_catalog_registration_resolves_selected_tools_and_bearer_endpoint() -> None:
    catalogs = CapabilityCatalogService(
        InMemoryCapabilityCatalogRepository(),
        InMemoryAgentDraftRepository(),
    )
    await catalogs.upsert(
        tenant_id="tenant-a",
        user_id="owner-a",
        resource_type="mcp",
        resource_id="company-search",
        request=UpsertCatalogResourceRequest.model_validate(
            {
                "expectedRevision": 1,
                "resource": {
                    "reference": "company-search",
                    "serverName": "company",
                    "label": "Company Search",
                    "description": "Search internal documents",
                    "endpointUrl": "https://mcp.example.com/mcp",
                    "transport": "sse",
                    "tools": ["mcp__company__search", "mcp__company__open"],
                    "risk": "medium",
                    "networkAccess": "external",
                    "sendsUserData": True,
                    "executionLocation": "external-mcp",
                    "credentialReference": "COMPANY_MCP_TOKEN",
                    "authMode": "bearer",
                    "authKey": "authorization",
                },
            }
        ),
    )
    provider = ServerSecretReferenceProvider(
        references={"company-search": {"authorization": "COMPANY_MCP_TOKEN"}},
        secrets={"COMPANY_MCP_TOKEN": SecretStr("test-token")},
    )
    manifest_payload = _manifest().model_dump(by_alias=True)
    manifest_payload["spec"]["tools"] = [{"mcp": "company-search"}]
    manifest = AgentManifest.model_validate(manifest_payload)

    resolved = await _factory()(provider, catalogs=catalogs).resolve(
        manifest,
        _identity("owner-a"),
    )

    assert resolved.mcp_servers["company"] == {
        "type": "sse",
        "url": "https://mcp.example.com/mcp",
        "headers": {"Authorization": "Bearer test-token"},
    }
    assert resolved.allowed_tools == (
        "mcp__company__search",
        "mcp__company__open",
    )

    with pytest.raises(
        ToolResolutionError,
        match="MCP tool registration is not configured: company-search",
    ):
        await _factory()(provider, catalogs=catalogs).resolve(
            manifest,
            _identity("other-user"),
        )
