from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import pytest
from pydantic import SecretStr

from harness.runtime.mcp_credentials import ServerSecretReferenceProvider
from harness.studio.mcp_discovery import (
    AutoDetectMcpConnector,
    DiscoveredServer,
    McpDiscoveryError,
    McpDiscoveryService,
)
from harness.studio.models import McpDiscoveryRequest


class RecordingConnector:
    def __init__(
        self,
        *,
        transport: Literal["http", "sse"] = "http",
        fail: bool = False,
    ) -> None:
        self.endpoint = ""
        self.headers: Mapping[str, str] = {}
        self.transport: Literal["http", "sse"] = transport
        self.fail = fail

    async def discover(
        self,
        endpoint_url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> DiscoveredServer:
        assert timeout_seconds == 12
        self.endpoint = endpoint_url
        self.headers = headers
        if self.fail:
            raise RuntimeError("transport rejected")
        return DiscoveredServer(
            title="Company MCP",
            version="2.1.0",
            tools=(
                ("search", "Search", "Search documents"),
                ("open", "Open", "Open one document"),
                ("summarize", None, "Summarize results"),
            ),
            transport=self.transport,
        )


async def public_host(_host: str, _port: int) -> tuple[str, ...]:
    return ("1.1.1.1",)


async def private_host(_host: str, _port: int) -> tuple[str, ...]:
    return ("10.20.0.8",)


def request(**updates: object) -> McpDiscoveryRequest:
    values: dict[str, object] = {
        "reference": "company-search",
        "serverName": "company",
        "endpointUrl": "https://mcp.example.com/mcp",
        "networkAccess": "external",
        "authMode": "none",
        "authKey": "authorization",
    }
    values.update(updates)
    return McpDiscoveryRequest.model_validate(values)


@pytest.mark.asyncio
async def test_discovers_multiple_tools_and_returns_canonical_names() -> None:
    connector = RecordingConnector()
    service = McpDiscoveryService(
        connector=connector,
        host_resolver=public_host,
    )

    result = await service.discover(
        request(),
        tenant_id="tenant-a",
        user_id="owner-a",
    )

    assert result.server_title == "Company MCP"
    assert result.server_version == "2.1.0"
    assert result.transport == "http"
    assert [tool.name for tool in result.tools] == ["search", "open", "summarize"]
    assert [tool.canonical_name for tool in result.tools] == [
        "mcp__company__search",
        "mcp__company__open",
        "mcp__company__summarize",
    ]


@pytest.mark.asyncio
async def test_discovery_preserves_single_underscores_in_mcp_identifiers() -> None:
    connector = RecordingConnector()
    service = McpDiscoveryService(
        connector=connector,
        host_resolver=private_host,
    )

    result = await service.discover(
        request(
            reference="sentiment_query_mcp",
            serverName="sentiment_query_mcp",
            endpointUrl="http://company-mcp:8001/mcp",
            networkAccess="internal",
        ),
        tenant_id="tenant-a",
        user_id="owner-a",
    )

    assert result.server_name == "sentiment_query_mcp"
    assert result.tools[0].canonical_name == "mcp__sentiment_query_mcp__search"


@pytest.mark.asyncio
async def test_external_discovery_rejects_private_and_sensitive_urls() -> None:
    connector = RecordingConnector()
    service = McpDiscoveryService(
        connector=connector,
        host_resolver=private_host,
    )

    with pytest.raises(McpDiscoveryError, match="公开互联网"):
        await service.discover(
            request(),
            tenant_id="tenant-a",
            user_id="owner-a",
        )
    with pytest.raises(McpDiscoveryError, match="查询参数"):
        await McpDiscoveryService(
            connector=connector,
            host_resolver=public_host,
        ).discover(
            request(endpointUrl="https://mcp.example.com/mcp?token=secret"),
            tenant_id="tenant-a",
            user_id="owner-a",
        )
    assert connector.endpoint == ""


@pytest.mark.asyncio
async def test_internal_discovery_accepts_private_network_and_injects_bearer() -> None:
    connector = RecordingConnector()
    credentials = ServerSecretReferenceProvider(
        references={"company-search": {"authorization": "COMPANY_MCP_TOKEN"}},
        secrets={"COMPANY_MCP_TOKEN": SecretStr("test-secret")},
    )
    service = McpDiscoveryService(
        credentials=credentials,
        connector=connector,
        host_resolver=private_host,
    )

    await service.discover(
        request(
            endpointUrl="http://company-mcp:8080/mcp",
            networkAccess="internal",
            authMode="bearer",
        ),
        tenant_id="tenant-a",
        user_id="owner-a",
    )

    assert connector.endpoint == "http://company-mcp:8080/mcp"
    assert connector.headers == {"Authorization": "Bearer test-secret"}


@pytest.mark.asyncio
async def test_discovery_accepts_a_transient_page_credential() -> None:
    connector = RecordingConnector()
    service = McpDiscoveryService(
        connector=connector,
        host_resolver=private_host,
    )

    await service.discover(
        request(
            endpointUrl="http://company-mcp:8080/mcp",
            networkAccess="internal",
            authMode="header",
            authName="X-API-Key",
            credentialValue="page-only-secret",
        ),
        tenant_id="tenant-a",
        user_id="owner-a",
    )

    assert connector.headers == {"X-API-Key": "page-only-secret"}


@pytest.mark.asyncio
async def test_discovery_merges_public_headers_with_managed_authentication() -> None:
    connector = RecordingConnector()
    service = McpDiscoveryService(
        connector=connector,
        host_resolver=private_host,
    )

    await service.discover(
        request(
            endpointUrl="http://company-mcp:8080/mcp",
            networkAccess="internal",
            customHeaders={"X-Tenant-ID": "tenant-public"},
            authMode="header",
            authName="X-API-Key",
            credentialValue="page-only-secret",
        ),
        tenant_id="tenant-a",
        user_id="owner-a",
    )

    assert connector.headers == {
        "X-Tenant-ID": "tenant-public",
        "X-API-Key": "page-only-secret",
    }


@pytest.mark.parametrize("name", ("Authorization", "Cookie", "X-API-Key"))
def test_discovery_rejects_secrets_in_custom_headers(name: str) -> None:
    with pytest.raises(ValueError, match="managed authentication"):
        request(customHeaders={name: "must-not-be-stored-here"})


@pytest.mark.asyncio
async def test_auto_detect_falls_back_to_sse_and_reports_transport() -> None:
    streamable = RecordingConnector(fail=True)
    sse = RecordingConnector(transport="sse")
    service = McpDiscoveryService(
        connector=AutoDetectMcpConnector(connectors=(streamable, sse)),
        host_resolver=private_host,
    )

    result = await service.discover(
        request(
            endpointUrl="http://company-mcp:8080/mcp",
            networkAccess="internal",
        ),
        tenant_id="tenant-a",
        user_id="owner-a",
    )

    assert streamable.endpoint == "http://company-mcp:8080/mcp"
    assert sse.endpoint == "http://company-mcp:8080/mcp"
    assert result.transport == "sse"
