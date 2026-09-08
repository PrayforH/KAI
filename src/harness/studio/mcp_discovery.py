"""Guarded MCP endpoint discovery for the Studio capability catalog."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from typing import Literal, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from harness.core.models import ExecutionIdentity
from harness.runtime.mcp_credentials import (
    DynamicMcpCredentialProvider,
    EmptyMcpCredentialProvider,
    McpCredentialError,
)
from harness.studio.models import (
    McpDiscoveredTool,
    McpDiscoveryRequest,
    McpDiscoveryResult,
)


class McpDiscoveryError(RuntimeError):
    def __init__(self, code: str, summary: str, *, status_code: int = 400) -> None:
        self.code = code
        self.summary = summary
        self.status_code = status_code
        super().__init__(summary)


@dataclass(frozen=True)
class DiscoveredServer:
    title: str | None
    version: str | None
    tools: tuple[tuple[str, str | None, str], ...]
    transport: Literal["http", "sse"] = "http"


class McpEndpointConnector(Protocol):
    async def discover(
        self,
        endpoint_url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> DiscoveredServer: ...


class StreamableHttpMcpConnector:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url or None

    async def discover(
        self,
        endpoint_url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> DiscoveredServer:
        async with httpx.AsyncClient(
            headers=dict(headers),
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            proxy=self._proxy_url,
        ) as client:
            async with streamable_http_client(
                endpoint_url,
                http_client=client,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=timeout_seconds),
                ) as session:
                    return await _discover_session(session, transport="http")


class SseMcpConnector:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url or None

    async def discover(
        self,
        endpoint_url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> DiscoveredServer:
        def httpx_client_factory(
            headers: dict[str, str] | None = None,
            timeout: httpx.Timeout | None = None,
            auth: httpx.Auth | None = None,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                headers=headers,
                timeout=timeout,
                auth=auth,
                follow_redirects=False,
                trust_env=False,
                proxy=self._proxy_url,
            )

        async with sse_client(
            endpoint_url,
            headers=dict(headers),
            timeout=timeout_seconds,
            sse_read_timeout=timeout_seconds,
            httpx_client_factory=httpx_client_factory,
        ) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_seconds),
            ) as session:
                return await _discover_session(session, transport="sse")


class AutoDetectMcpConnector:
    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        connectors: tuple[McpEndpointConnector, ...] | None = None,
    ) -> None:
        self._connectors = connectors or (
            StreamableHttpMcpConnector(proxy_url=proxy_url),
            SseMcpConnector(proxy_url=proxy_url),
        )

    async def discover(
        self,
        endpoint_url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> DiscoveredServer:
        first_error: Exception | None = None
        for connector in self._connectors:
            try:
                return await connector.discover(
                    endpoint_url,
                    headers=headers,
                    timeout_seconds=timeout_seconds,
                )
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
        raise RuntimeError("MCP discovery has no configured transport")


async def _discover_session(
    session: ClientSession,
    *,
    transport: Literal["http", "sse"],
) -> DiscoveredServer:
    initialized = await session.initialize()
    listed = await session.list_tools()
    server_info = initialized.serverInfo
    return DiscoveredServer(
        title=getattr(server_info, "title", None) or server_info.name,
        version=server_info.version,
        tools=tuple(
            (
                tool.name,
                getattr(tool, "title", None),
                tool.description or "",
            )
            for tool in listed.tools
        ),
        transport=transport,
    )


type HostResolver = Callable[[str, int], Awaitable[tuple[str, ...]]]


async def _resolve_host(host: str, port: int) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        loop = asyncio.get_running_loop()
        try:
            resolved = await loop.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise McpDiscoveryError(
                "mcp_dns_failed",
                "MCP 地址无法解析，请检查主机名和 Docker 网络。",
            ) from error
        return tuple(sorted({str(item[4][0]) for item in resolved}))
    return (str(literal),)


def _address_allowed(address: str, *, internal: bool) -> bool:
    value = ipaddress.ip_address(address)
    if (
        value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_unspecified
        or value.is_reserved
    ):
        return False
    if internal:
        return True
    return value.is_global


class McpDiscoveryService:
    def __init__(
        self,
        *,
        credentials: DynamicMcpCredentialProvider | None = None,
        connector: McpEndpointConnector | None = None,
        host_resolver: HostResolver | None = None,
        timeout_seconds: float = 12,
    ) -> None:
        self._credentials = credentials or EmptyMcpCredentialProvider()
        self._connector = connector or AutoDetectMcpConnector()
        self._host_resolver = host_resolver or _resolve_host
        self._timeout_seconds = timeout_seconds

    async def discover(
        self,
        request: McpDiscoveryRequest,
        *,
        tenant_id: str,
        user_id: str,
    ) -> McpDiscoveryResult:
        endpoint = await self._validated_endpoint(request)
        headers, endpoint = await self._authentication(
            request,
            endpoint,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        started = monotonic()
        try:
            discovered = await self._connector.discover(
                endpoint,
                headers=headers,
                timeout_seconds=self._timeout_seconds,
            )
        except McpDiscoveryError:
            raise
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {401, 403}:
                raise McpDiscoveryError(
                    "mcp_credentials_rejected",
                    "MCP 地址可访问，但鉴权被拒绝。",
                    status_code=422,
                ) from error
            raise McpDiscoveryError(
                "mcp_http_error",
                f"MCP 地址返回 HTTP {error.response.status_code}。",
                status_code=422,
            ) from error
        except Exception as error:
            raise McpDiscoveryError(
                "mcp_unreachable",
                "未能完成 MCP initialize / tools/list，请检查地址、传输协议和凭据配置。",
                status_code=422,
            ) from error
        if not discovered.tools:
            raise McpDiscoveryError(
                "mcp_tools_empty",
                "连接成功，但 MCP 服务没有返回可用工具。",
                status_code=422,
            )
        if len(discovered.tools) > 200:
            raise McpDiscoveryError(
                "mcp_tools_exceeded",
                "MCP 服务返回超过 200 个工具，请拆分能力或缩小服务端暴露范围。",
                status_code=422,
            )
        tools = tuple(
            McpDiscoveredTool(
                name=name,
                canonicalName=f"mcp__{request.server_name}__{name}",
                title=title,
                description=description,
            )
            for name, title, description in discovered.tools
        )
        return McpDiscoveryResult(
            endpointUrl=self._public_endpoint(endpoint),
            transport=discovered.transport,
            serverName=request.server_name,
            serverTitle=discovered.title,
            serverVersion=discovered.version,
            latencyMs=max(0, round((monotonic() - started) * 1000)),
            tools=tools,
        )

    async def _validated_endpoint(self, request: McpDiscoveryRequest) -> str:
        parsed = urlsplit(request.endpoint_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise McpDiscoveryError(
                "mcp_url_invalid",
                "MCP 地址必须是完整的 HTTP(S) URL。",
            )
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise McpDiscoveryError(
                "mcp_url_sensitive",
                "地址不能包含用户名、密码、查询参数或片段；凭据请使用服务端引用。",
            )
        if request.network_access == "external" and parsed.scheme != "https":
            raise McpDiscoveryError(
                "mcp_https_required",
                "外部 MCP 地址必须使用 HTTPS。",
            )
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = await self._host_resolver(parsed.hostname, port)
        internal = request.network_access == "internal"
        if not addresses or any(
            not _address_allowed(address, internal=internal) for address in addresses
        ):
            raise McpDiscoveryError(
                "mcp_network_denied",
                (
                    "该地址不在允许的内部网络范围，且不能指向本机、链路本地或保留地址。"
                    if internal
                    else "外部 MCP 地址必须解析到公开互联网地址。"
                ),
            )
        return urlunsplit(parsed)

    async def _authentication(
        self,
        request: McpDiscoveryRequest,
        endpoint: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> tuple[dict[str, str], str]:
        headers = dict(request.custom_headers)
        if request.auth_mode == "none":
            return headers, endpoint
        if request.credential_value is not None:
            secret = request.credential_value.get_secret_value()
        else:
            try:
                values = await self._credentials.resolve(
                    request.reference,
                    ExecutionIdentity(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        project_id="studio-mcp-discovery",
                        session_id="studio-mcp-discovery",
                        run_id="studio-mcp-discovery",
                        agent_name="studio-mcp-discovery",
                        agent_version="1",
                    ),
                    frozenset({request.auth_key}),
                )
            except McpCredentialError as error:
                raise McpDiscoveryError(
                    "mcp_credentials_unavailable",
                    (
                        f"尚未配置 {request.reference}.{request.auth_key} 的凭据；"
                        "请在当前页面填写后重试。"
                    ),
                    status_code=422,
                ) from error
            secret = values[request.auth_key].get_secret_value()
        if request.auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
            return headers, endpoint
        if not request.auth_name:
            raise McpDiscoveryError(
                "mcp_auth_name_required",
                "自定义 Header 或 Query 鉴权必须填写参数名称。",
            )
        if "\n" in request.auth_name or "\r" in request.auth_name:
            raise McpDiscoveryError("mcp_auth_name_invalid", "鉴权参数名称不合法。")
        if request.auth_mode == "header":
            headers[request.auth_name] = secret
            return headers, endpoint
        parsed = urlsplit(endpoint)
        endpoint = urlunsplit(
            parsed._replace(
                query=urlencode(
                    [*parse_qsl(parsed.query), (request.auth_name, secret)]
                )
            )
        )
        return headers, endpoint

    @staticmethod
    def _public_endpoint(endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        return urlunsplit(parsed._replace(query="", fragment=""))
