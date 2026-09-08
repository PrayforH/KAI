"""Server-owned registrations for capabilities exposed by Agent Manifests."""

import json
from typing import Literal, cast

from claude_agent_sdk import McpServerConfig
from pydantic import SecretStr

from harness.policy.models import ContextTrust
from harness.runtime.mcp_credentials import (
    DynamicMcpCredentialProvider,
    ServerSecretReferenceProvider,
)
from harness.runtime.tools import McpServerRegistration, McpSmokeCheck, ToolResolver
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.models import McpCapability
from harness.studio.web_configuration import WebConfigurationService

TAVILY_REFERENCE = "tavily-readonly"
TAVILY_ALLOWED_TOOLS = (
    "mcp__tavily__tavily_search",
    "mcp__tavily__tavily_extract",
)


def server_secret_credential_provider(
    *,
    references_json: str,
    secrets_json: str,
) -> ServerSecretReferenceProvider:
    """Parse generic logical MCP secret references from server-owned settings."""

    references_raw: object = json.loads(references_json)
    secrets_raw: object = json.loads(secrets_json)
    if not isinstance(references_raw, dict) or not isinstance(secrets_raw, dict):
        raise ValueError("MCP credential settings must be JSON objects")
    references: dict[str, dict[str, str]] = {}
    for server, raw_values in cast(dict[object, object], references_raw).items():
        if not isinstance(raw_values, dict):
            continue
        references[str(server)] = {
            str(key): str(value)
            for key, value in cast(dict[object, object], raw_values).items()
        }
    secrets = {
        str(key): SecretStr(str(value))
        for key, value in cast(dict[object, object], secrets_raw).items()
    }
    return ServerSecretReferenceProvider(references=references, secrets=secrets)


def default_tool_resolver(
    credential_provider: DynamicMcpCredentialProvider | None = None,
    *,
    catalogs: CapabilityCatalogService | None = None,
    web_search_api_key: str = "",
    web_search_provider: Literal["tavily", "minimax"] = "tavily",
    web_enabled: bool = True,
    web_configurations: WebConfigurationService | None = None,
) -> ToolResolver:
    """Build the reviewed capability registry shared by every composition root."""

    async def tenant_registry(
        tenant_id: str,
        user_id: str,
    ) -> dict[str, McpServerRegistration]:
        if catalogs is None:
            return {}
        record = await catalogs.get_for_user(tenant_id, user_id)
        return {
            capability.reference: registration
            for capability in record.catalog.mcp_servers
            if (registration := _catalog_registration(capability)) is not None
        }

    return ToolResolver(
        mcp_registry={
            TAVILY_REFERENCE: McpServerRegistration(
                server_name="tavily",
                config=cast(
                    McpServerConfig,
                    {"type": "http", "url": "https://mcp.tavily.com/mcp/"},
                ),
                allowed_tools=TAVILY_ALLOWED_TOOLS,
                credential_headers=(("Authorization", "api_key"),),
                credential_header_prefixes=(("Authorization", "Bearer "),),
                result_trust=ContextTrust.UNTRUSTED,
                preflight_smoke=McpSmokeCheck(
                    tool="tavily_search",
                    arguments={
                        "query": "Model Context Protocol connectivity check",
                        "max_results": 1,
                        "search_depth": "basic",
                    },
                ),
            )
        },
        mcp_registry_provider=tenant_registry if catalogs is not None else None,
        credential_provider=credential_provider,
        web_search_api_key=web_search_api_key,
        web_search_provider=web_search_provider,
        web_enabled=web_enabled,
        web_configurations=web_configurations,
    )


def _catalog_registration(
    capability: McpCapability,
) -> McpServerRegistration | None:
    if not capability.endpoint_url:
        return None
    credential_headers: tuple[tuple[str, str], ...] = ()
    credential_header_prefixes: tuple[tuple[str, str], ...] = ()
    credential_query_parameters: tuple[tuple[str, str], ...] = ()
    if capability.auth_mode == "bearer":
        credential_headers = (("Authorization", capability.auth_key),)
        credential_header_prefixes = (("Authorization", "Bearer "),)
    elif capability.auth_mode == "header" and capability.auth_name:
        credential_headers = ((capability.auth_name, capability.auth_key),)
    elif capability.auth_mode == "query" and capability.auth_name:
        credential_query_parameters = ((capability.auth_name, capability.auth_key),)
    smoke = (
        McpSmokeCheck(
            tool="tavily_search",
            arguments={
                "query": "Model Context Protocol connectivity check",
                "max_results": 1,
                "search_depth": "basic",
            },
        )
        if capability.reference == TAVILY_REFERENCE
        else None
    )
    return McpServerRegistration(
        server_name=capability.server_name or capability.reference,
        config=cast(
            McpServerConfig,
            {"type": capability.transport, "url": capability.endpoint_url},
        ),
        allowed_tools=capability.tools,
        static_headers=tuple(capability.custom_headers.items()),
        credential_headers=credential_headers,
        credential_header_prefixes=credential_header_prefixes,
        credential_query_parameters=credential_query_parameters,
        result_trust=ContextTrust.UNTRUSTED,
        preflight_smoke=smoke,
    )
